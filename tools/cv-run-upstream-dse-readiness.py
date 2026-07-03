#!/usr/bin/env python3
"""Run an exact/upstream DSE readiness audit when a DSE.cpp source is available."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-dse-source", type=Path, required=True)
    parser.add_argument("--compile-commands", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ast-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-source-ast")
    parser.add_argument("--ir-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-impl-ir")
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--z3", default="z3")
    parser.add_argument("--mine-pass-impl-ir", action="store_true")
    parser.add_argument("--pass-impl-ir-slice-window", type=int, default=8)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--min-dse-matched", type=int)
    parser.add_argument("--max-dse-blocked", type=int)
    parser.add_argument("--max-dse-source-incomplete", type=int)
    parser.add_argument("--max-new-dse-unsupported", type=int)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_minimal_compile_db(path: Path, source: Path, compiler: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "directory": str(source.parent),
                    "command": f"{compiler} -std=c++17 {source}",
                    "file": str(source),
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def counter_dict(counter: collections.Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def predicate_hash(record: dict[str, Any]) -> str:
    text = str(record.get("predicate_source") or record.get("source") or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def dse_baseline_record(record: dict[str, Any]) -> dict[str, Any]:
    check = record.get("pass_impl_ir_intent_check")
    check = check if isinstance(check, dict) else {}
    blockers = check.get("source_analysis_fact_blockers")
    missing = check.get("missing_source_analysis_facts")
    source = str(record.get("predicate_source") or record.get("source") or "")
    item = {
        "file": str(record.get("file") or ""),
        "line": int(record.get("line") or 0),
        "marker": str(record.get("marker") or ""),
        "predicate_hash": predicate_hash(record),
        "status": str(check.get("status") or record.get("proof_status") or "unset"),
        "blockers": sorted(str(value) for value in blockers) if isinstance(blockers, list) else [],
        "missing_facts": sorted(str(value) for value in missing) if isinstance(missing, list) else [],
        "source": source[:240],
    }
    item["identity_key"] = "|".join(
        [item["file"], str(item["line"]), item["marker"], item["predicate_hash"]]
    )
    item["record_key"] = "|".join(
        [
            item["identity_key"],
            item["status"],
            ",".join(item["blockers"]),
            ",".join(item["missing_facts"]),
        ]
    )
    return item


def dse_baseline(findings: list[dict[str, Any]]) -> dict[str, Any]:
    records = [
        dse_baseline_record(record)
        for record in findings
        if str(record.get("marker") or "").startswith("probe.dse.")
    ]
    return {
        "model": "o2t-upstream-dse-baseline-v1",
        "records": sorted(records, key=lambda item: str(item.get("identity_key") or "")),
    }


def is_unsupported_baseline_record(record: dict[str, Any]) -> bool:
    return str(record.get("status") or "") != "matched"


def baseline_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_records = previous.get("records") if isinstance(previous.get("records"), list) else []
    current_records = current.get("records") if isinstance(current.get("records"), list) else []
    previous_by_id = {
        str(record.get("identity_key") or ""): record
        for record in previous_records
        if isinstance(record, dict) and str(record.get("identity_key") or "")
    }
    current_by_id = {
        str(record.get("identity_key") or ""): record
        for record in current_records
        if isinstance(record, dict) and str(record.get("identity_key") or "")
    }
    previous_unsupported = {
        key: record for key, record in previous_by_id.items() if is_unsupported_baseline_record(record)
    }
    current_unsupported = {
        key: record for key, record in current_by_id.items() if is_unsupported_baseline_record(record)
    }
    new_unsupported = [
        current_unsupported[key]
        for key in sorted(set(current_unsupported) - set(previous_unsupported))
    ]
    resolved_unsupported = [
        previous_unsupported[key]
        for key in sorted(set(previous_unsupported) - set(current_unsupported))
    ]
    changed: list[dict[str, Any]] = []
    for key in sorted(set(previous_by_id) & set(current_by_id)):
        old = previous_by_id[key]
        new = current_by_id[key]
        changed_fields = [
            field
            for field in ("status", "blockers", "missing_facts")
            if old.get(field) != new.get(field)
        ]
        if changed_fields:
            changed.append(
                {
                    "identity_key": key,
                    "changed_fields": changed_fields,
                    "previous": old,
                    "current": new,
                }
            )
    return {
        "model": "o2t-upstream-dse-baseline-diff-v1",
        "previous_records": len(previous_records),
        "current_records": len(current_records),
        "new_unsupported": new_unsupported,
        "resolved_unsupported": resolved_unsupported,
        "changed": changed,
        "counts": {
            "new_unsupported": len(new_unsupported),
            "resolved_unsupported": len(resolved_unsupported),
            "changed": len(changed),
        },
    }


def format_baseline_diff(diff: dict[str, Any]) -> str:
    counts = diff.get("counts") if isinstance(diff.get("counts"), dict) else {}
    lines = [
        "O2T Upstream DSE Baseline Diff",
        f"previous_records: {int(diff.get('previous_records') or 0)}",
        f"current_records: {int(diff.get('current_records') or 0)}",
        f"new_unsupported: {int(counts.get('new_unsupported') or 0)}",
        f"resolved_unsupported: {int(counts.get('resolved_unsupported') or 0)}",
        f"changed: {int(counts.get('changed') or 0)}",
    ]
    for record in diff.get("new_unsupported", [])[:10] if isinstance(diff.get("new_unsupported"), list) else []:
        if isinstance(record, dict):
            lines.append(
                f"  new {record.get('marker')}:{record.get('line')} status={record.get('status')}"
            )
    return "\n".join(lines) + "\n"


def budget_violations(args: argparse.Namespace, summary: dict[str, Any], diff: dict[str, Any] | None) -> list[dict[str, Any]]:
    dse = summary.get("dse") if isinstance(summary.get("dse"), dict) else {}
    status = dse.get("intent_check_status") if isinstance(dse.get("intent_check_status"), dict) else {}
    checks = [
        ("min-dse-matched", args.min_dse_matched, int(status.get("matched") or 0), "min"),
        ("max-dse-blocked", args.max_dse_blocked, int(status.get("blocked") or 0), "max"),
        (
            "max-dse-source-incomplete",
            args.max_dse_source_incomplete,
            int(status.get("source-incomplete") or 0),
            "max",
        ),
    ]
    violations: list[dict[str, Any]] = []
    for name, limit, actual, mode in checks:
        if limit is None:
            continue
        failed = actual < int(limit) if mode == "min" else actual > int(limit)
        if failed:
            violations.append({"budget": name, "limit": int(limit), "actual": actual})
    if args.max_new_dse_unsupported is not None:
        actual = 0
        if isinstance(diff, dict):
            counts = diff.get("counts") if isinstance(diff.get("counts"), dict) else {}
            actual = int(counts.get("new_unsupported") or 0)
        if actual > int(args.max_new_dse_unsupported):
            violations.append(
                {"budget": "max-new-dse-unsupported", "limit": int(args.max_new_dse_unsupported), "actual": actual}
            )
    return violations


def dse_readiness_summary(
    source: Path,
    out: Path,
    audit_out: Path,
    audit_exit_code: int,
    audit_stderr: str,
) -> dict[str, Any]:
    run_summary = load_json(audit_out / "run-summary.json") if (audit_out / "run-summary.json").is_file() else {}
    findings = load_json(audit_out / "findings.json") if (audit_out / "findings.json").is_file() else []
    if not isinstance(findings, list):
        findings = []
    dse_findings = [record for record in findings if str(record.get("marker") or "").startswith("probe.dse.")]
    status = collections.Counter()
    blocked_reasons: collections.Counter[str] = collections.Counter()
    missing_facts: collections.Counter[str] = collections.Counter()
    unsupported_idioms: collections.Counter[str] = collections.Counter()
    samples: list[dict[str, Any]] = []

    for record in dse_findings:
        check = record.get("pass_impl_ir_intent_check")
        check = check if isinstance(check, dict) else {}
        current_status = str(check.get("status") or record.get("proof_status") or "unset")
        status[current_status] += 1
        blockers = check.get("source_analysis_fact_blockers")
        if isinstance(blockers, list) and blockers:
            for blocker in blockers:
                reason = str(blocker)
                blocked_reasons[reason] += 1
                unsupported_idioms[reason] += 1
        missing = check.get("missing_source_analysis_facts")
        if current_status == "source-incomplete" and isinstance(missing, list) and missing:
            for fact in missing:
                reason = str(fact)
                missing_facts[reason] += 1
                unsupported_idioms[f"missing:{reason}"] += 1
        if not blockers and not missing:
            recommendation = str(record.get("recommendation") or "")
            if recommendation and recommendation != "covered by source-derived DSE analysis facts":
                unsupported_idioms[recommendation] += 1
        if current_status != "matched" and len(samples) < 10:
            samples.append(
                {
                    "file": str(record.get("file") or ""),
                    "line": int(record.get("line") or 0),
                    "marker": str(record.get("marker") or ""),
                    "status": current_status,
                    "source": str(record.get("predicate_source") or record.get("source") or "")[:240],
                    "blockers": [str(item) for item in blockers] if isinstance(blockers, list) else [],
                    "missing": [str(item) for item in missing] if isinstance(missing, list) else [],
                }
            )

    pass_impl_ir = run_summary.get("pass_impl_ir") if isinstance(run_summary.get("pass_impl_ir"), dict) else {}
    baseline = dse_baseline(dse_findings)
    return {
        "model": "o2t-upstream-dse-readiness-v1",
        "source": str(source),
        "source_status": "present",
        "audit_exit_code": audit_exit_code,
        "audit_stderr": audit_stderr.strip(),
        "audit_out": str(audit_out),
        "artifacts": {
            "findings": str(audit_out / "findings.json") if (audit_out / "findings.json").is_file() else "",
            "run_summary": str(audit_out / "run-summary.json") if (audit_out / "run-summary.json").is_file() else "",
            "readiness": str(audit_out / "real-pass-readiness.json") if (audit_out / "real-pass-readiness.json").is_file() else "",
        },
        "dse": {
            "findings": len(dse_findings),
            "markers": counter_dict(collections.Counter(str(record.get("marker") or "") for record in dse_findings)),
            "intent_check_status": counter_dict(status),
            "blocked_reasons": counter_dict(blocked_reasons),
            "source_incomplete_missing_facts": counter_dict(missing_facts),
            "top_unsupported_idioms": dict(unsupported_idioms.most_common(10)),
            "samples": samples,
        },
        "baseline": baseline,
        "budget_violations": [],
        "pass_impl_ir": pass_impl_ir,
    }


def missing_summary(source: Path, out: Path) -> dict[str, Any]:
    return {
        "model": "o2t-upstream-dse-readiness-v1",
        "source": str(source),
        "source_status": "missing",
        "audit_exit_code": 0,
        "audit_stderr": "",
        "audit_out": "",
        "artifacts": {},
        "dse": {
            "findings": 0,
            "markers": {},
            "intent_check_status": {},
            "blocked_reasons": {},
            "source_incomplete_missing_facts": {},
            "top_unsupported_idioms": {},
            "samples": [],
        },
        "baseline": {"model": "o2t-upstream-dse-baseline-v1", "records": []},
        "budget_violations": [],
        "pass_impl_ir": {},
    }


def format_summary(summary: dict[str, Any]) -> str:
    dse = summary.get("dse") if isinstance(summary.get("dse"), dict) else {}
    lines = [
        "O2T Upstream DSE Readiness",
        f"source_status: {summary.get('source_status') or ''}",
        f"source: {summary.get('source') or ''}",
        f"audit_exit_code: {int(summary.get('audit_exit_code') or 0)}",
        f"audit_out: {summary.get('audit_out') or ''}",
        f"dse_findings: {int(dse.get('findings') or 0)}",
        "DSE intent check status",
    ]
    status = dse.get("intent_check_status") if isinstance(dse.get("intent_check_status"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(status.items())) if status else lines.append("  none")
    lines.append("DSE blocked reasons")
    blocked = dse.get("blocked_reasons") if isinstance(dse.get("blocked_reasons"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(blocked.items())) if blocked else lines.append("  none")
    lines.append("DSE source-incomplete missing facts")
    missing = dse.get("source_incomplete_missing_facts") if isinstance(dse.get("source_incomplete_missing_facts"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(missing.items())) if missing else lines.append("  none")
    violations = summary.get("budget_violations") if isinstance(summary.get("budget_violations"), list) else []
    lines.append(f"budget_violations: {len(violations)}")
    for violation in violations:
        if isinstance(violation, dict):
            lines.append(
                f"  {violation.get('budget')}: actual={violation.get('actual')} limit={violation.get('limit')}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / "upstream-dse-readiness.json"
    text_path = args.out / "upstream-dse-readiness.txt"
    source = args.upstream_dse_source.resolve()
    if not source.is_file():
        summary = missing_summary(source, args.out)
        violations = budget_violations(args, summary, None)
        summary["budget_violations"] = violations
        write_json(summary_path, summary)
        text_path.write_text(format_summary(summary), encoding="utf-8")
        if args.allow_missing:
            return 1 if violations else 0
        print(f"upstream DSE source not found: {source}", file=sys.stderr)
        return 2

    compile_db = args.compile_commands
    if compile_db is None:
        compile_db = args.out / "compile-db" / "compile_commands.json"
        write_minimal_compile_db(compile_db, source, args.compiler)
    audit_out = args.out / "audit"
    command = [
        sys.executable,
        str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
        "--compile-commands",
        str(compile_db),
        "--out",
        str(audit_out),
        "--ast-miner",
        str(args.ast_miner),
        "--ir-miner",
        str(args.ir_miner),
        "--z3",
        args.z3,
        "--pass-impl-ir-slice-window",
        str(args.pass_impl_ir_slice_window),
        "--marker",
        "probe.dse.dead-store",
        "--marker",
        "probe.dse.overwritten-store",
    ]
    if args.mine_pass_impl_ir:
        command.append("--mine-pass-impl-ir")
    command.append(str(source))
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    summary = dse_readiness_summary(source, args.out, audit_out, result.returncode, result.stderr)
    baseline = summary.get("baseline") if isinstance(summary.get("baseline"), dict) else {"records": []}
    if args.write_baseline is not None:
        write_json(args.write_baseline, baseline)
    diff: dict[str, Any] | None = None
    if args.baseline is not None and args.baseline.is_file():
        previous = load_json(args.baseline)
        previous = previous if isinstance(previous, dict) else {}
        diff = baseline_diff(previous, baseline)
        write_json(args.out / "upstream-dse-baseline-diff.json", diff)
        (args.out / "upstream-dse-baseline-diff.txt").write_text(format_baseline_diff(diff), encoding="utf-8")
        summary["baseline_diff"] = {
            "json": str(args.out / "upstream-dse-baseline-diff.json"),
            "text": str(args.out / "upstream-dse-baseline-diff.txt"),
            "counts": diff.get("counts", {}),
        }
    violations = budget_violations(args, summary, diff)
    summary["budget_violations"] = violations
    write_json(summary_path, summary)
    text_path.write_text(format_summary(summary), encoding="utf-8")
    if result.returncode != 0:
        return result.returncode
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
