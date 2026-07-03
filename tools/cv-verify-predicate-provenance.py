#!/usr/bin/env python3
"""Verify mined predicate provenance records."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"
CONTRACT_MODEL = "predicate-provenance-contract-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--intents", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--require-clean", action="store_true")
    return parser.parse_args()


def load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        return json.loads(text)
    if stripped.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_contracts(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("intent registry must contain a JSON array")
    contracts: dict[str, dict[str, Any]] = {}
    for record in data:
        if not isinstance(record, dict):
            continue
        marker = str(record.get("marker") or "")
        formal = record.get("formal")
        if not marker or not isinstance(formal, dict):
            continue
        contract = formal.get("predicate_provenance")
        if not isinstance(contract, dict):
            continue
        facts = contract.get("facts")
        if not isinstance(facts, list):
            continue
        sources = contract.get("provenance_sources")
        if not isinstance(sources, list):
            continue
        compact_facts: list[dict[str, str]] = []
        for item in facts:
            if not isinstance(item, dict):
                continue
            fact = str(item.get("fact") or "")
            predicate_family = str(item.get("predicate_family") or "")
            if fact and predicate_family:
                compact_facts.append({"fact": fact, "predicate_family": predicate_family})
        if compact_facts:
            contracts[marker] = {
                "model": str(contract.get("model") or CONTRACT_MODEL),
                "marker": marker,
                "provenance_sources": [str(source) for source in sources if str(source)],
                "facts": compact_facts,
            }
    return contracts


def stable_key(record: dict[str, Any], index: int) -> str:
    if record.get("key"):
        return str(record.get("key") or "")
    file_name = str(record.get("file") or "")
    marker = str(record.get("marker") or "")
    if file_name or marker:
        return "|".join([
            file_name,
            str(int(record.get("line") or 0)),
            marker,
        ])
    return "|".join([
        str(index),
    ])


def input_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [record for record in data if isinstance(record, dict)]
    if not isinstance(data, dict):
        return []
    provenance = data.get("predicate_provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("records"), list):
        return [record for record in provenance["records"] if isinstance(record, dict)]
    if isinstance(data.get("records"), list):
        return [record for record in data["records"] if isinstance(record, dict)]
    if data.get("marker"):
        return [data]
    return []


def formal_parameters(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("evidence")
    if isinstance(evidence, dict) and isinstance(evidence.get("formal_parameters"), dict):
        return evidence["formal_parameters"]
    params = record.get("formal_parameters")
    return params if isinstance(params, dict) else {}


def path_value(root: dict[str, Any], path: str) -> Any:
    if path in root:
        return root[path]
    value: Any = root
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def compact_fact(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact": str(item.get("fact") or ""),
        "status": str(item.get("status") or ""),
        "predicate_family": str(item.get("predicate_family") or ""),
        "source": str(item.get("source") or ""),
        "source_range": dict(item.get("source_range") or {}) if isinstance(item.get("source_range"), dict) else {},
        "subject": str(item.get("subject") or ""),
    }


def provenance_from(record: dict[str, Any], contract: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    for source in contract.get("provenance_sources", []):
        source_name = str(source or "")
        if not source_name:
            continue
        if source_name == "facts":
            raw = record.get("facts")
        elif source_name.startswith("evidence."):
            raw = path_value(record, source_name)
        elif source_name.startswith("optimization_transaction."):
            raw = path_value(record, source_name)
        else:
            raw = path_value(formal_parameters(record), source_name)
        if isinstance(raw, list):
            return [compact_fact(item) for item in raw if isinstance(item, dict)], source_name
    return [], ""


def verify_contract_record(record: dict[str, Any], contract: dict[str, Any], index: int) -> dict[str, Any]:
    key = stable_key(record, index)
    marker = str(record.get("marker") or contract.get("marker") or "")
    facts, provenance_source = provenance_from(record, contract)
    by_fact = {str(item.get("fact") or ""): item for item in facts}
    failed_checks: list[str] = []
    observed: list[str] = []
    missing: list[str] = []
    required_facts = [
        str(item.get("fact") or "")
        for item in contract.get("facts", [])
        if isinstance(item, dict) and str(item.get("fact") or "")
    ]
    fact_predicates = {
        str(item.get("fact") or ""): str(item.get("predicate_family") or "")
        for item in contract.get("facts", [])
        if isinstance(item, dict)
    }
    for fact in required_facts:
        item = by_fact.get(fact)
        if not item or str(item.get("status") or "") != "observed":
            failed_checks.append(f"{fact}-provenance-missing")
            missing.append(fact)
            continue
        observed.append(fact)
        expected = fact_predicates.get(fact, "")
        if str(item.get("predicate_family") or "") != expected:
            failed_checks.append(f"{fact}-predicate-family-mismatch")
        if expected and expected not in str(item.get("source") or ""):
            failed_checks.append(f"{fact}-source-mismatch")
        source_range = item.get("source_range")
        if not isinstance(source_range, dict) or int(source_range.get("begin_line") or 0) <= 0:
            failed_checks.append(f"{fact}-source-range-missing")
    status = "failed" if failed_checks else "passed"
    return {
        "key": key,
        "marker": marker,
        "contract_model": str(contract.get("model") or CONTRACT_MODEL),
        "contract_marker": str(contract.get("marker") or marker),
        "provenance_source": provenance_source,
        "status": status,
        "predicate_provenance_status": status,
        "required_facts": required_facts,
        "observed_facts": observed,
        "missing_facts": missing,
        "failed_checks": failed_checks,
        "facts": facts,
    }


def verify_record(record: dict[str, Any], contracts: dict[str, dict[str, Any]], index: int) -> dict[str, Any] | None:
    marker = str(record.get("marker") or "")
    contract = contracts.get(marker)
    if contract is None:
        return None
    return verify_contract_record({**record, "marker": marker}, contract, index)


def summarize(records: list[dict[str, Any]], ignored: int) -> dict[str, Any]:
    failed_checks = collections.Counter(
        check
        for record in records
        for check in record.get("failed_checks", [])
        if str(check)
    )
    return {
        "records": len(records),
        "ignored": ignored,
        "status": dict(sorted(collections.Counter(str(record.get("status") or "unset") for record in records).items())),
        "predicate_provenance_status": dict(
            sorted(collections.Counter(str(record.get("predicate_provenance_status") or "unset") for record in records).items())
        ),
        "failed_checks": dict(sorted(failed_checks.items())),
    }


def format_report(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        "O2T Predicate Provenance Verification",
        f"records: {int(summary.get('records') or 0)}",
        f"ignored: {int(summary.get('ignored') or 0)}",
        "status: " + (", ".join(f"{key}={value}" for key, value in summary.get("status", {}).items()) or "none"),
        "predicate_provenance_status: "
        + (", ".join(f"{key}={value}" for key, value in summary.get("predicate_provenance_status", {}).items()) or "none"),
        "failed_checks: " + (", ".join(f"{key}={value}" for key, value in summary.get("failed_checks", {}).items()) or "none"),
        "Top failures",
    ]
    failed = [record for record in result.get("records", []) if record.get("status") == "failed"]
    for record in failed[:10]:
        lines.append(f"  {record.get('key')}: {','.join(record.get('failed_checks', []))}")
    if not failed:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        contracts = load_contracts(args.intents)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    raw_records = input_records(load_json_or_jsonl(args.input))
    records: list[dict[str, Any]] = []
    ignored = 0
    for index, record in enumerate(raw_records):
        verified = verify_record(record, contracts, index)
        if verified is None:
            ignored += 1
            continue
        records.append(verified)
    result = {
        "model": "o2t-predicate-provenance-verification-v1",
        "input": str(args.input),
        "intents": str(args.intents),
        "summary": summarize(records, ignored),
        "records": records,
    }
    write_json(args.out, result)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(format_report(result), encoding="utf-8")
    failed = int(result["summary"]["status"].get("failed", 0))
    if args.require_clean and failed:
        print(f"predicate provenance verification failed: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
