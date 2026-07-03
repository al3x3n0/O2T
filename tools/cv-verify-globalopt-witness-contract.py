#!/usr/bin/env python3
"""Verify canonical GlobalOpt witness contracts."""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from cv_globalopt_witness import (
    DEFAULT_REQUIRED_WITNESS_CASES,
    MARKER,
    WITNESS_CONTRACT_MODEL,
    WITNESS_MODEL,
    is_default_initializer,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--intent-registry", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--emit-smt", type=Path)
    parser.add_argument("--z3")
    parser.add_argument("--alive2-checker", type=Path, default=ROOT / "tools" / "cv-alive2-check-ir.py")
    parser.add_argument("--alive-tv")
    parser.add_argument("--emit-alive2", type=Path)
    parser.add_argument("--require-alive2-proved", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    return parser.parse_args()


def load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def registry_required_cases(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_REQUIRED_WITNESS_CASES)
    if not isinstance(data, list):
        return list(DEFAULT_REQUIRED_WITNESS_CASES)
    for record in data:
        if not isinstance(record, dict) or record.get("marker") != MARKER:
            continue
        formal = record.get("formal")
        cases = formal.get("required_witness_cases") if isinstance(formal, dict) else []
        if isinstance(cases, list) and all(isinstance(case, str) and case for case in cases):
            return [str(case) for case in cases]
    return list(DEFAULT_REQUIRED_WITNESS_CASES)


def input_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [record for record in data if isinstance(record, dict) and record.get("marker") == MARKER]
    if not isinstance(data, dict):
        return []
    witnesses = data.get("witnesses")
    if isinstance(witnesses, dict) and isinstance(witnesses.get("records"), list):
        return [
            record
            for record in witnesses["records"]
            if isinstance(record, dict) and str(record.get("marker") or MARKER) == MARKER
        ]
    if data.get("marker") == MARKER:
        return [data]
    return []


def contract_for(record: dict[str, Any]) -> dict[str, Any]:
    contract = record.get("globalopt_witness_contract")
    if isinstance(contract, dict):
        return contract
    contract = record.get("witness_contract")
    if isinstance(contract, dict):
        return contract
    witness = record.get("globalopt_witness")
    if isinstance(witness, dict):
        contract = witness.get("witness_contract")
        if isinstance(contract, dict):
            return contract
    return {}


def stable_key(record: dict[str, Any], index: int) -> str:
    if record.get("key"):
        return str(record.get("key") or "")
    return "|".join([
        str(record.get("file") or ""),
        str(int(record.get("line") or 0)),
        str(record.get("marker") or MARKER),
        str(index),
    ])


def filename_key(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value)
    return cleaned.strip("_") or "globalopt_witness_contract"


def case_details(case: dict[str, Any]) -> dict[str, Any]:
    details = case.get("structural_details")
    return details if isinstance(details, dict) else {}


def verify_case(case: dict[str, Any], required_name: str) -> list[str]:
    checks: list[str] = []
    name = str(case.get("name") or "")
    prefix = name or required_name
    if name != required_name:
        checks.append(f"{prefix}-case-name-mismatch")
    if str(case.get("status") or "") != "passed":
        checks.append(f"{prefix}-case-status-not-passed")
    if str(case.get("structural_checks") or "") != "passed":
        checks.append(f"{prefix}-structural-checks-not-passed")
    details = case_details(case)
    if str(details.get("global_name") or "") != "@cv_dead_init":
        checks.append(f"{prefix}-global-name-unsupported")
    if str(details.get("linkage") or "") != "internal":
        checks.append(f"{prefix}-linkage-not-internal")
    if not str(details.get("initializer_type") or ""):
        checks.append(f"{prefix}-initializer-type-missing")
    before_initializer = str(details.get("before_initializer") or "")
    after_initializer = str(details.get("after_initializer") or "")
    if not before_initializer or is_default_initializer(before_initializer):
        checks.append(f"{prefix}-before-initializer-not-nondefault")
    if not is_default_initializer(after_initializer):
        checks.append(f"{prefix}-after-initializer-not-default")
    try:
        changed_line_count = int(details.get("changed_line_count") or 0)
    except (TypeError, ValueError):
        changed_line_count = 0
    changed_lines = details.get("changed_lines")
    changed_lines = changed_lines if isinstance(changed_lines, list) else []
    if changed_line_count != 1 or len(changed_lines) != 1:
        checks.append(f"{prefix}-initializer-change-count-not-one")
    return checks


def smt_bool(value: bool) -> str:
    return "true" if value else "false"


def case_contract_smt(case: dict[str, Any], required_name: str) -> str:
    details = case_details(case)
    clauses = [
        ("case-name", str(case.get("name") or "") == required_name),
        ("case-status", str(case.get("status") or "") == "passed"),
        ("structural-checks", str(case.get("structural_checks") or "") == "passed"),
        ("global-name", str(details.get("global_name") or "") == "@cv_dead_init"),
        ("linkage", str(details.get("linkage") or "") == "internal"),
        ("initializer-type", bool(str(details.get("initializer_type") or ""))),
        ("before-nondefault", bool(str(details.get("before_initializer") or "")) and not is_default_initializer(str(details.get("before_initializer") or ""))),
        ("after-default", is_default_initializer(str(details.get("after_initializer") or ""))),
        ("changed-line-count", int(details.get("changed_line_count") or 0) == 1),
        ("changed-lines", isinstance(details.get("changed_lines"), list) and len(details.get("changed_lines") or []) == 1),
    ]
    bindings = "\n".join(f"(define-fun {name} () Bool {smt_bool(value)})" for name, value in clauses)
    names = " ".join(name for name, _ in clauses)
    return "\n".join([
        "; O2T GlobalOpt witness contract obligation",
        f"; case: {required_name}",
        bindings,
        f"(assert (not (and {names})))",
        "(check-sat)",
        "",
    ])


def run_z3(z3: str, smt: str) -> tuple[str, str]:
    proc = subprocess.run([z3, "-in"], input=smt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        return "error", proc.stderr.strip() or proc.stdout.strip() or f"z3 exited {proc.returncode}"
    result = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if result == "unsat":
        return "proved", ""
    return "failed", proc.stdout.strip()


def checker_command(checker: Path, before: Path, after: Path, alive_tv: str, out: Path, log: Path) -> list[str]:
    command = [str(checker)]
    if checker.suffix == ".py":
        command = [sys.executable, str(checker)]
    return [
        *command,
        "--before",
        str(before),
        "--after",
        str(after),
        "--alive-tv",
        alive_tv,
        "--out",
        str(out),
        "--output-log",
        str(log),
    ]


def verify_case_semantic(
    case: dict[str, Any],
    required_name: str,
    key: str,
    checker: Path | None,
    alive_tv: str,
    emit_alive2: Path | None,
    structural_errors: list[str],
) -> dict[str, Any]:
    before = Path(str(case.get("before") or ""))
    after = Path(str(case.get("after") or ""))
    out_file = ""
    log_file = ""
    if emit_alive2 is not None:
        emit_alive2.mkdir(parents=True, exist_ok=True)
        base = f"{filename_key(key)}-{filename_key(required_name)}"
        out_file = str(emit_alive2 / f"{base}.json")
        log_file = str(emit_alive2 / f"{base}.log")
    if structural_errors:
        return {
            "case": required_name,
            "semantic_status": "not-run",
            "reason": "structural-checks-failed",
            "before": str(before),
            "after": str(after),
            "result_file": out_file,
            "log_file": log_file,
            "message": "",
        }
    if checker is None or not alive_tv:
        return {
            "case": required_name,
            "semantic_status": "not-run",
            "reason": "alive2-not-configured",
            "before": str(before),
            "after": str(after),
            "result_file": out_file,
            "log_file": log_file,
            "message": "",
        }
    if not checker.is_file():
        return {
            "case": required_name,
            "semantic_status": "error",
            "reason": "alive2-checker-not-found",
            "before": str(before),
            "after": str(after),
            "result_file": out_file,
            "log_file": log_file,
            "message": str(checker),
        }
    for label, path in (("before", before), ("after", after)):
        if not path.is_file():
            return {
                "case": required_name,
                "semantic_status": "error",
                "reason": f"{label}-ir-missing",
                "before": str(before),
                "after": str(after),
                "result_file": out_file,
                "log_file": log_file,
                "message": str(path),
            }
    if emit_alive2 is None:
        temp_root = Path("/tmp") / "o2t-globalopt-alive2"
        temp_root.mkdir(parents=True, exist_ok=True)
        base = f"{filename_key(key)}-{filename_key(required_name)}"
        out = temp_root / f"{base}.json"
        log = temp_root / f"{base}.log"
    else:
        out = Path(out_file)
        log = Path(log_file)
    proc = subprocess.run(
        checker_command(checker, before, after, alive_tv, out, log),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    try:
        result = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = {}
    status = str(result.get("alive2_status") or ("error" if proc.returncode else "not-run"))
    return {
        "case": required_name,
        "semantic_status": status,
        "reason": "" if status == "proved" else str(result.get("alive2_message") or proc.stderr.strip() or "alive2-check-failed"),
        "before": str(before),
        "after": str(after),
        "result_file": out_file,
        "log_file": log_file,
        "message": str(result.get("alive2_message") or ""),
    }


def aggregate_semantic_status(obligations: list[dict[str, Any]]) -> str:
    statuses = [str(obligation.get("semantic_status") or "unset") for obligation in obligations]
    if not statuses:
        return "not-run"
    if any(status == "error" for status in statuses):
        return "error"
    if any(status == "failed" for status in statuses):
        return "failed"
    if all(status == "proved" for status in statuses):
        return "proved"
    if any(status == "unsupported" for status in statuses):
        return "unsupported"
    if all(status == "not-run" for status in statuses):
        return "not-run"
    return "not-run"


def verify_case_formal(
    case: dict[str, Any],
    required_name: str,
    key: str,
    z3: str,
    emit_smt: Path | None,
    structural_errors: list[str],
) -> dict[str, Any]:
    if structural_errors:
        return {
            "case": required_name,
            "formal_status": "not-run",
            "reason": "structural-checks-failed",
            "smt_file": "",
            "counterexample": "",
        }
    smt = case_contract_smt(case, required_name)
    smt_file = ""
    if emit_smt is not None:
        emit_smt.mkdir(parents=True, exist_ok=True)
        smt_path = emit_smt / f"{filename_key(key)}-{filename_key(required_name)}.smt2"
        smt_path.write_text(smt, encoding="utf-8")
        smt_file = str(smt_path)
    if not z3:
        return {
            "case": required_name,
            "formal_status": "not-run",
            "reason": "z3-not-configured",
            "smt_file": smt_file,
            "counterexample": "",
        }
    status, message = run_z3(z3, smt)
    return {
        "case": required_name,
        "formal_status": status,
        "reason": "" if status == "proved" else "z3-result",
        "smt_file": smt_file,
        "counterexample": message.replace("\n", "\\n") if message else "",
    }


def flat_diagnostics(record: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    flat_structural = record.get("globalopt_witness_structural_status")
    if flat_structural is not None and str(flat_structural) != str(contract.get("structural_status") or ""):
        diagnostics.append("flat-structural-status-drift")
    flat_cases = record.get("globalopt_required_witness_cases")
    if isinstance(flat_cases, list) and [str(case) for case in flat_cases] != list(contract.get("required_cases") or []):
        diagnostics.append("flat-required-cases-drift")
    flat_missing = record.get("globalopt_missing_required_witness_cases")
    if isinstance(flat_missing, list) and [str(case) for case in flat_missing] != list(contract.get("missing_required_cases") or []):
        diagnostics.append("flat-missing-required-cases-drift")
    return diagnostics


def verify_record(
    record: dict[str, Any],
    required_cases: list[str],
    index: int,
    z3: str,
    emit_smt: Path | None,
    alive2_checker: Path | None,
    alive_tv: str,
    emit_alive2: Path | None,
    require_alive2_proved: bool,
) -> dict[str, Any]:
    contract = contract_for(record)
    key = stable_key(record, index)
    checks: list[str] = []
    if not contract:
        checks.append("globalopt-witness-contract-absent")
    if contract and contract.get("model") != WITNESS_CONTRACT_MODEL:
        checks.append("contract-model-mismatch")
    if contract and contract.get("witness_model") != WITNESS_MODEL:
        checks.append("witness-model-mismatch")
    if contract and contract.get("required_cases") != required_cases:
        checks.append("required-cases-mismatch")
    if contract and contract.get("status") != "passed":
        checks.append("contract-status-not-passed")
    if contract and contract.get("structural_status") != "passed":
        checks.append("structural-status-not-passed")
    missing_required = contract.get("missing_required_cases") if isinstance(contract, dict) else []
    if isinstance(missing_required, list) and missing_required:
        checks.extend(f"{case}-required-case-missing" for case in missing_required if str(case))
    cases = contract.get("cases") if isinstance(contract, dict) else []
    cases = [case for case in cases if isinstance(case, dict)] if isinstance(cases, list) else []
    by_name = {str(case.get("name") or ""): case for case in cases if str(case.get("name") or "")}
    formal_obligations: list[dict[str, Any]] = []
    semantic_obligations: list[dict[str, Any]] = []
    for required_name in required_cases:
        case = by_name.get(required_name)
        if case is None:
            checks.append(f"{required_name}-required-case-missing")
            formal_obligations.append({
                "case": required_name,
                "formal_status": "not-run",
                "reason": "required-case-missing",
                "smt_file": "",
                "counterexample": "",
            })
            semantic_obligations.append({
                "case": required_name,
                "semantic_status": "not-run",
                "reason": "required-case-missing",
                "before": "",
                "after": "",
                "result_file": "",
                "log_file": "",
                "message": "",
            })
            continue
        case_checks = verify_case(case, required_name)
        checks.extend(case_checks)
        formal_obligations.append(verify_case_formal(case, required_name, key, z3, emit_smt, case_checks))
        semantic_obligations.append(
            verify_case_semantic(case, required_name, key, alive2_checker, alive_tv, emit_alive2, case_checks)
        )
    diagnostics = flat_diagnostics(record, contract) if contract else []
    formal_failures = [
        obligation
        for obligation in formal_obligations
        if obligation.get("formal_status") in {"failed", "error"}
    ]
    for obligation in formal_failures:
        checks.append(f"{obligation.get('case')}-formal-{obligation.get('formal_status')}")
    semantic_status = aggregate_semantic_status(semantic_obligations)
    semantic_failed_checks: list[str] = []
    for obligation in semantic_obligations:
        status = str(obligation.get("semantic_status") or "unset")
        if status in {"failed", "error"} or (require_alive2_proved and status != "proved"):
            check = f"{obligation.get('case')}-semantic-{status}"
            checks.append(check)
            semantic_failed_checks.append(check)
    return {
        "key": key,
        "marker": str(record.get("marker") or MARKER),
        "status": "failed" if checks else "passed",
        "failed_checks": checks,
        "diagnostics": diagnostics,
        "formal_obligations": formal_obligations,
        "semantic_status": semantic_status,
        "semantic_obligations": semantic_obligations,
        "semantic_failed_checks": semantic_failed_checks,
        "contract_status": str(contract.get("status") or "absent") if contract else "absent",
        "structural_status": str(contract.get("structural_status") or "absent") if contract else "absent",
        "required_cases": list(contract.get("required_cases") or []) if contract else [],
        "missing_required_cases": list(contract.get("missing_required_cases") or []) if contract else [],
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    failed_checks = collections.Counter(
        check
        for record in records
        for check in record.get("failed_checks", [])
        if str(check)
    )
    diagnostics = collections.Counter(
        diagnostic
        for record in records
        for diagnostic in record.get("diagnostics", [])
        if str(diagnostic)
    )
    formal_status = collections.Counter(
        str(obligation.get("formal_status") or "unset")
        for record in records
        for obligation in record.get("formal_obligations", [])
        if isinstance(obligation, dict)
    )
    semantic_status = collections.Counter(
        str(obligation.get("semantic_status") or "unset")
        for record in records
        for obligation in record.get("semantic_obligations", [])
        if isinstance(obligation, dict)
    )
    record_semantic_status = collections.Counter(str(record.get("semantic_status") or "unset") for record in records)
    return {
        "records": len(records),
        "status": dict(sorted(collections.Counter(str(record.get("status") or "unset") for record in records).items())),
        "contract_status": dict(sorted(collections.Counter(str(record.get("contract_status") or "unset") for record in records).items())),
        "structural_status": dict(sorted(collections.Counter(str(record.get("structural_status") or "unset") for record in records).items())),
        "formal_status": dict(sorted(formal_status.items())),
        "semantic_status": dict(sorted(semantic_status.items())),
        "record_semantic_status": dict(sorted(record_semantic_status.items())),
        "failed_checks": dict(sorted(failed_checks.items())),
        "diagnostics": dict(sorted(diagnostics.items())),
    }


def format_report(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        "O2T GlobalOpt Witness Contract Verification",
        f"records: {int(summary.get('records') or 0)}",
        "status: " + (", ".join(f"{key}={value}" for key, value in summary.get("status", {}).items()) or "none"),
        "contract_status: " + (", ".join(f"{key}={value}" for key, value in summary.get("contract_status", {}).items()) or "none"),
        "structural_status: " + (", ".join(f"{key}={value}" for key, value in summary.get("structural_status", {}).items()) or "none"),
        "formal_status: " + (", ".join(f"{key}={value}" for key, value in summary.get("formal_status", {}).items()) or "none"),
        "semantic_status: " + (", ".join(f"{key}={value}" for key, value in summary.get("semantic_status", {}).items()) or "none"),
        "record_semantic_status: " + (", ".join(f"{key}={value}" for key, value in summary.get("record_semantic_status", {}).items()) or "none"),
        "failed_checks: " + (", ".join(f"{key}={value}" for key, value in summary.get("failed_checks", {}).items()) or "none"),
        "diagnostics: " + (", ".join(f"{key}={value}" for key, value in summary.get("diagnostics", {}).items()) or "none"),
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
    data = load_json_or_jsonl(args.input)
    required_cases = registry_required_cases(args.intent_registry)
    z3 = ""
    if args.z3:
        z3_path = Path(args.z3)
        z3 = str(z3_path) if z3_path.is_file() else str(shutil.which(args.z3) or "")
        if not z3:
            print(f"z3 not found: {args.z3}", file=sys.stderr)
            return 2
    records = [
        verify_record(
            record,
            required_cases,
            index,
            z3,
            args.emit_smt,
            args.alive2_checker if args.alive_tv else None,
            str(args.alive_tv or ""),
            args.emit_alive2,
            args.require_alive2_proved,
        )
        for index, record in enumerate(input_records(data))
    ]
    result = {
        "model": "o2t-globalopt-witness-contract-verification-v1",
        "input": str(args.input),
        "required_cases": required_cases,
        "summary": summarize(records),
        "records": records,
    }
    write_json(args.out, result)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(format_report(result), encoding="utf-8")
    failed = int(result["summary"]["status"].get("failed", 0))
    if args.require_clean and failed:
        print(f"globalopt witness contract verification failed: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
