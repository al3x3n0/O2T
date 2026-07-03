#!/usr/bin/env python3
"""Run focused coverage for GlobalOpt dead initializer intent."""

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
    WITNESS_MODEL,
    compact_witness,
    global_initializer_contract_details,
    is_default_initializer,
    only_initializer_changed,
    parse_global_initializer_line,
    split_global_type_initializer,
    validate_witness_text,
)

ROOT = Path(__file__).resolve().parents[1]
MARKER = "probe.globalopt.dead-initializer"
GLOBALOPT_COVERAGE_BASELINE_MODEL = "o2t-globalopt-coverage-baseline-v1"
LEGACY_GLOBALOPT_COVERAGE_BASELINE_MODEL = "compilerverif-globalopt-coverage-baseline-v1"
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"
DISCOVERY_ROOTS = [
    ROOT / "build-clang-tools" / "playbook" / "llvm-src",
    ROOT / "build-clang-tools" / "instrumented-runner" / "llvm-src",
    ROOT / "build-clang-tools" / "workflow-verify" / "llvm-src",
    ROOT / "build" / "playbook" / "llvm-src",
    ROOT / "build" / "instrumented-runner" / "llvm-src",
    ROOT / "build" / "workflow-verify" / "llvm-src",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ast-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-source-ast")
    parser.add_argument("--intent-inferer", type=Path, default=ROOT / "tools" / "cv-infer-optimization-intent.py")
    parser.add_argument("--intent-validator", type=Path, default=ROOT / "tools" / "cv-validate-intent-candidates.py")
    parser.add_argument("--coverage-auditor", type=Path, default=ROOT / "tools" / "cv-audit-intent-coverage.py")
    parser.add_argument("--registry", type=Path, default=ROOT / "constraints" / "pass_constraints.json")
    parser.add_argument("--intent-registry", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--z3", default="z3")
    parser.add_argument("--keep-intermediates", action="store_true")
    parser.add_argument("--min-findings", type=int)
    parser.add_argument("--min-graph-derived", type=int)
    parser.add_argument("--max-unsupported", type=int)
    parser.add_argument("--max-incomplete-safety", type=int)
    parser.add_argument("--max-missing-fact", action="append", default=[])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--max-new-unsupported", type=int)
    parser.add_argument("--max-new-incomplete-safety", type=int)
    parser.add_argument("--emit-witnesses", action="store_true")
    parser.add_argument("--min-witnesses", type=int)
    parser.add_argument("--max-witness-failures", type=int)
    parser.add_argument("--host-llvm-as", type=Path)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []
    return [
        record
        for record in (json.loads(line) for line in text.splitlines() if line.strip())
        if isinstance(record, dict)
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def required_witness_cases_from_registry(path: Path) -> list[str]:
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


def discover_globalopt() -> Path | None:
    for root in DISCOVERY_ROOTS:
        if not root.exists():
            continue
        candidates = sorted(root.rglob("GlobalOpt.cpp"))
        if candidates:
            return candidates[0]
    return None


def selected_source(source: Path | None) -> tuple[Path | None, str]:
    if source is not None:
        return (source.resolve(), "explicit") if source.is_file() else (None, "source-not-found")
    discovered = discover_globalopt()
    return (discovered.resolve(), "discovered") if discovered is not None else (None, "source-not-found")


def count(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(record.get(key) or "unset") for record in records).items()))


def formal_parameters(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        return {}
    params = evidence.get("formal_parameters")
    return params if isinstance(params, dict) else {}


def fact_counter(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: collections.Counter[str] = collections.Counter()
    for record in records:
        value = formal_parameters(record).get(key)
        if isinstance(value, list):
            counter.update(str(item) for item in value if str(item))
    return dict(sorted(counter.items()))


def parameter_counter(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: collections.Counter[str] = collections.Counter()
    for record in records:
        value = formal_parameters(record).get(key)
        if value:
            counter[str(value)] += 1
    return dict(sorted(counter.items()))


def int_from_path(mapping: dict[str, Any], *keys: str) -> int:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return 0
        value = value.get(key)
    return int(value or 0)


def nested_dict(mapping: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def sorted_strings(value: Any) -> list[str]:
    return sorted(str(item) for item in value if str(item)) if isinstance(value, list) else []


def stable_key(record: dict[str, Any]) -> str:
    file_name = str(record.get("file") or "")
    line = int(record.get("line") or 0)
    marker = str(record.get("marker") or "")
    return "|".join([file_name, str(line), marker])


def filename_key(key: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in key)
    return cleaned.strip("_") or "globalopt_witness"


def baseline_record(record: dict[str, Any]) -> dict[str, Any]:
    params = formal_parameters(record)
    file_name = str(record.get("file") or "")
    line = int(record.get("line") or 0)
    marker = str(record.get("marker") or "")
    key = stable_key(record)
    return {
        "key": key,
        "file": file_name,
        "line": line,
        "marker": marker,
        "proof_status": str(record.get("proof_status") or "unset"),
        "proof_result": str(record.get("proof_result") or ""),
        "formal_inference": str(nested_dict(record, "evidence").get("formal_inference") or ""),
        "safety_status": str(params.get("global.initializer.safety_status") or "unset"),
        "safety_provenance_status": str(params.get("global.initializer.safety_provenance_status") or "absent"),
        "observed_safety_facts": sorted_strings(params.get("global.initializer.observed_safety_facts")),
        "missing_safety_facts": sorted_strings(params.get("global.initializer.missing_safety_facts")),
    }


def predicate_provenance_record(record: dict[str, Any]) -> dict[str, Any]:
    params = formal_parameters(record)
    provenance = params.get("global.initializer.safety_provenance")
    return {
        "key": stable_key(record),
        "file": str(record.get("file") or ""),
        "line": int(record.get("line") or 0),
        "marker": str(record.get("marker") or ""),
        "formal_parameters": {
            "global.initializer.safety_provenance": list(provenance)
            if isinstance(provenance, list)
            else [],
            "global.initializer.safety_provenance_status": str(
                params.get("global.initializer.safety_provenance_status") or "absent"
            ),
        },
    }


def baseline_from_validated(validated: list[dict[str, Any]]) -> dict[str, Any]:
    records = sorted(
        [baseline_record(record) for record in validated if record.get("marker") == MARKER],
        key=lambda item: str(item.get("key") or ""),
    )
    return {
        "model": GLOBALOPT_COVERAGE_BASELINE_MODEL,
        "records": records,
        "marker_counts": dict(sorted(collections.Counter(record.get("marker", "") for record in records).items())),
        "proof_status": dict(sorted(collections.Counter(record.get("proof_status", "") for record in records).items())),
        "safety_status": dict(sorted(collections.Counter(record.get("safety_status", "") for record in records).items())),
        "missing_facts": dict(
            sorted(collections.Counter(fact for record in records for fact in record["missing_safety_facts"]).items())
        ),
    }


def empty_baseline() -> dict[str, Any]:
    return {
        "model": GLOBALOPT_COVERAGE_BASELINE_MODEL,
        "records": [],
        "marker_counts": {},
        "proof_status": {},
        "safety_status": {},
        "missing_facts": {},
    }


def is_globalopt_coverage_baseline_model(model: object) -> bool:
    return model in {
        GLOBALOPT_COVERAGE_BASELINE_MODEL,
        LEGACY_GLOBALOPT_COVERAGE_BASELINE_MODEL,
    }


def load_baseline(path: Path | None) -> dict[str, Any]:
    if path is None:
        return empty_baseline()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_baseline()
    if is_globalopt_coverage_baseline_model(data.get("model")):
        return data
    if isinstance(data.get("baseline"), dict):
        return data["baseline"]
    return empty_baseline()


def baseline_map(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = baseline.get("records")
    if not isinstance(records, list):
        return {}
    return {
        str(record.get("key") or ""): record
        for record in records
        if isinstance(record, dict) and str(record.get("key") or "")
    }


def compare_baselines(previous: dict[str, Any], current: dict[str, Any], baseline_present: bool) -> dict[str, Any]:
    previous_by_key = baseline_map(previous)
    current_by_key = baseline_map(current)
    if not baseline_present:
        return {
            "model": "o2t-globalopt-coverage-baseline-diff-v1",
            "baseline_present": False,
            "summary": {
                "previous_records": 0,
                "current_records": len(current_by_key),
                "new": 0,
                "resolved": 0,
                "changed": 0,
                "new_unsupported": 0,
                "new_incomplete_safety": 0,
                "new_missing_facts": {},
            },
            "new": [],
            "resolved": [],
            "changed": [],
        }
    new = [current_by_key[key] for key in sorted(set(current_by_key) - set(previous_by_key))]
    resolved = [previous_by_key[key] for key in sorted(set(previous_by_key) - set(current_by_key))]
    changed: list[dict[str, Any]] = []
    fields = [
        "proof_status",
        "proof_result",
        "formal_inference",
        "safety_status",
        "observed_safety_facts",
        "missing_safety_facts",
    ]
    for key in sorted(set(previous_by_key) & set(current_by_key)):
        before = previous_by_key[key]
        after = current_by_key[key]
        changes = {
            field: {"before": before.get(field), "after": after.get(field)}
            for field in fields
            if before.get(field) != after.get(field)
        }
        if changes:
            changed.append({"key": key, "before": before, "after": after, "changes": changes})
    regressions = new + [item["after"] for item in changed if isinstance(item.get("after"), dict)]
    new_unsupported = sum(1 for record in regressions if record.get("proof_status") == "unsupported")
    new_incomplete = sum(1 for record in regressions if record.get("safety_status") == "incomplete")
    missing = collections.Counter(
        fact
        for record in regressions
        for fact in record.get("missing_safety_facts", [])
        if str(fact)
    )
    return {
        "model": "o2t-globalopt-coverage-baseline-diff-v1",
        "baseline_present": True,
        "summary": {
            "previous_records": len(previous_by_key),
            "current_records": len(current_by_key),
            "new": len(new),
            "resolved": len(resolved),
            "changed": len(changed),
            "new_unsupported": new_unsupported,
            "new_incomplete_safety": new_incomplete,
            "new_missing_facts": dict(sorted(missing.items())),
        },
        "new": new,
        "resolved": resolved,
        "changed": changed,
    }


def format_baseline_diff(diff: dict[str, Any]) -> str:
    summary = nested_dict(diff, "summary")
    missing = nested_dict(summary, "new_missing_facts")
    lines = [
        "O2T GlobalOpt Baseline Diff",
        f"baseline_present: {str(bool(diff.get('baseline_present'))).lower()}",
        "records: "
        + " ".join(
            [
                f"previous={int(summary.get('previous_records') or 0)}",
                f"current={int(summary.get('current_records') or 0)}",
                f"new={int(summary.get('new') or 0)}",
                f"resolved={int(summary.get('resolved') or 0)}",
                f"changed={int(summary.get('changed') or 0)}",
            ]
        ),
        f"new_unsupported: {int(summary.get('new_unsupported') or 0)}",
        f"new_incomplete_safety: {int(summary.get('new_incomplete_safety') or 0)}",
        "new_missing_facts: " + (", ".join(f"{key}={value}" for key, value in missing.items()) or "none"),
        "Top new incomplete safety",
    ]
    new_records = [record for record in diff.get("new", []) if isinstance(record, dict)]
    changed_after = [
        item.get("after")
        for item in diff.get("changed", [])
        if isinstance(item, dict) and isinstance(item.get("after"), dict)
    ]
    incomplete = [record for record in new_records + changed_after if record.get("safety_status") == "incomplete"]
    for record in incomplete[:10]:
        facts = ",".join(record.get("missing_safety_facts", [])) or "none"
        lines.append(f"  {record.get('marker')} {record.get('file')}:{record.get('line')} missing={facts}")
    if not incomplete:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def complete_proved_global_initializer(record: dict[str, Any]) -> bool:
    params = formal_parameters(record)
    return (
        record.get("marker") == MARKER
        and record.get("proof_status") == "proved"
        and params.get("global.initializer.safety_status") == "complete"
    )


WITNESS_CASES = [
    {
        "name": "i32",
        "before_global": "@cv_dead_init = internal global i32 42",
        "after_global": "@cv_dead_init = internal global i32 0",
        "preamble": [],
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    },
    {
        "name": "ptr",
        "before_global": "@cv_dead_init = internal global ptr @cv_target",
        "after_global": "@cv_dead_init = internal global ptr null",
        "preamble": ["@cv_target = internal global i32 7"],
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    },
    {
        "name": "array",
        "before_global": "@cv_dead_init = internal global [2 x i32] [i32 1, i32 2]",
        "after_global": "@cv_dead_init = internal global [2 x i32] zeroinitializer",
        "preamble": [],
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    },
]


def witness_ir(case: dict[str, Any], global_line: str) -> str:
    lines = [
        "; O2T GlobalOpt dead initializer witness",
        f"; witness_model: {WITNESS_MODEL}",
    ]
    lines.extend(str(line) for line in case.get("preamble", []))
    lines.extend([
        global_line,
        "",
        str(case.get("function") or ""),
        "",
    ])
    return "\n".join(
        lines
    )

def assemble(path: Path, llvm_as: Path) -> tuple[bool, str]:
    try:
        proc = run([str(llvm_as), str(path)])
    except OSError as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr.strip() or proc.stdout.strip() or f"llvm-as exited {proc.returncode}")


def emit_witnesses(
    out: Path,
    validated: list[dict[str, Any]],
    emit: bool,
    host_llvm_as: Path | None,
    required_witness_cases: list[str],
) -> dict[str, Any]:
    eligible = [record for record in validated if complete_proved_global_initializer(record)]
    records: list[dict[str, Any]] = []
    if not emit:
        return {
            "enabled": False,
            "total": 0,
            "passed": 0,
            "skipped": len(validated) - len(eligible),
            "failed": 0,
            "failure_reasons": {},
            "required_cases": required_witness_cases,
            "records": records,
        }
    witness_root = out / "witnesses"
    witness_root.mkdir(parents=True, exist_ok=True)
    for record in eligible:
        key = stable_key(record)
        witness_dir = witness_root / filename_key(key)
        witness_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = witness_dir / "witness.json"
        cases: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, case in enumerate(WITNESS_CASES):
            case_name = str(case["name"])
            case_dir = witness_dir if index == 0 else witness_dir / case_name
            case_dir.mkdir(parents=True, exist_ok=True)
            before_path = case_dir / "before.ll"
            after_path = case_dir / "after.ll"
            before = witness_ir(case, str(case["before_global"]))
            after = witness_ir(case, str(case["after_global"]))
            before_path.write_text(before, encoding="utf-8")
            after_path.write_text(after, encoding="utf-8")
            case_errors, structural_details = global_initializer_contract_details(before, after, case)
            structural_status = "passed" if not case_errors else "failed"
            if host_llvm_as is not None:
                for label, path in [("before", before_path), ("after", after_path)]:
                    ok, message = assemble(path, host_llvm_as)
                    if not ok:
                        case_errors.append(f"{case_name}-{label}-llvm-as-failed: {message}")
            case_status = "failed" if case_errors else "passed"
            errors.extend(case_errors)
            cases.append({
                "name": case_name,
                "before": str(before_path),
                "after": str(after_path),
                "status": case_status,
                "structural_checks": structural_status,
                "structural_details": structural_details,
                "failure_reasons": case_errors,
            })
        status = "failed" if errors else "passed"
        case_status_by_name = {
            str(case.get("name") or ""): str(case.get("status") or "unset")
            for case in cases
            if str(case.get("name") or "")
        }
        missing_required_cases = [
            case for case in required_witness_cases if case_status_by_name.get(case) != "passed"
        ]
        if missing_required_cases:
            status = "failed"
            for case_name in missing_required_cases:
                if case_name not in case_status_by_name:
                    errors.append(f"{case_name}-required-witness-case-missing")
        primary = cases[0]
        params = formal_parameters(record)
        source_provenance = {
            "rewrite_callee": str(params.get("global.initializer.rewrite_callee") or ""),
            "replacement_expr": str(params.get("global.initializer.replacement_expr") or ""),
            "value_type_expr": str(params.get("global.initializer.value_type_expr") or ""),
            "subject": str(params.get("global.initializer.subject") or ""),
            "rewrite_provenance_status": str(params.get("global.initializer.rewrite_provenance_status") or ""),
        }
        witness = {
            "key": key,
            "marker": MARKER,
            "file": str(record.get("file") or ""),
            "line": int(record.get("line") or 0),
            "status": status,
            "before": str(primary.get("before") or ""),
            "after": str(primary.get("after") or ""),
            "witness_model": WITNESS_MODEL,
            "required_cases": required_witness_cases,
            "missing_required_cases": missing_required_cases,
            "source_provenance": source_provenance,
            "cases": cases,
            "structural_checks": "passed" if all(case["structural_checks"] == "passed" for case in cases) else "failed",
            "host_llvm_as": str(host_llvm_as) if host_llvm_as is not None else "",
            "failure_reasons": errors,
        }
        witness["witness_contract"] = compact_witness(witness, required_witness_cases)["witness_contract"]
        write_json(manifest_path, witness)
        records.append(witness)
    failures = collections.Counter(
        reason.split(":", 1)[0]
        for witness in records
        for reason in witness.get("failure_reasons", [])
        if str(reason)
    )
    passed = sum(1 for witness in records if witness.get("status") == "passed")
    failed = sum(1 for witness in records if witness.get("status") == "failed")
    return {
        "enabled": True,
        "total": len(records),
        "passed": passed,
        "skipped": len(validated) - len(eligible),
        "failed": failed,
        "failure_reasons": dict(sorted(failures.items())),
        "required_cases": required_witness_cases,
        "records": records,
    }


def parse_missing_fact_budgets(values: list[str]) -> list[tuple[str, int]]:
    budgets: list[tuple[str, int]] = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"--max-missing-fact must use FACT=N, got {value!r}")
        fact, limit_text = value.split("=", 1)
        fact = fact.strip()
        if not fact:
            raise ValueError(f"--max-missing-fact must name a fact, got {value!r}")
        try:
            limit = int(limit_text)
        except ValueError as exc:
            raise ValueError(f"--max-missing-fact limit must be an integer, got {value!r}") from exc
        budgets.append((fact, limit))
    return budgets


def budget_violations(args: argparse.Namespace, summary: dict[str, Any], source_selected: bool) -> list[dict[str, Any]]:
    if not source_selected:
        return []
    checks: list[tuple[str, int, int | None, str]] = [
        ("min-findings", int_from_path(summary, "findings", "total"), args.min_findings, "min"),
        ("min-graph-derived", int_from_path(summary, "candidates", "graph_derived"), args.min_graph_derived, "min"),
        (
            "max-unsupported",
            int_from_path(summary, "validation", "proof_status", "unsupported"),
            args.max_unsupported,
            "max",
        ),
        (
            "max-incomplete-safety",
            int_from_path(summary, "candidates", "safety_status", "incomplete"),
            args.max_incomplete_safety,
            "max",
        ),
        (
            "max-new-unsupported",
            int_from_path(summary, "baseline_diff", "new_unsupported"),
            args.max_new_unsupported,
            "max",
        ),
        (
            "max-new-incomplete-safety",
            int_from_path(summary, "baseline_diff", "new_incomplete_safety"),
            args.max_new_incomplete_safety,
            "max",
        ),
        ("min-witnesses", int_from_path(summary, "witnesses", "passed"), args.min_witnesses, "min"),
        (
            "max-witness-failures",
            int_from_path(summary, "witnesses", "failed"),
            args.max_witness_failures,
            "max",
        ),
    ]
    violations: list[dict[str, Any]] = []
    for name, actual, limit, mode in checks:
        if limit is None:
            continue
        failed = actual < limit if mode == "min" else actual > limit
        if failed:
            violations.append({"budget": name, "actual": actual, "limit": limit})
    missing_facts = summary.get("candidates", {}).get("missing_facts", {})
    missing_facts = missing_facts if isinstance(missing_facts, dict) else {}
    for fact, limit in parse_missing_fact_budgets(args.max_missing_fact):
        actual = int(missing_facts.get(fact) or 0)
        if actual > limit:
            violations.append({
                "budget": "max-missing-fact",
                "fact": fact,
                "actual": actual,
                "limit": limit,
            })
    return violations


def summary_for(
    source: Path | None,
    source_status: str,
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    validated: list[dict[str, Any]],
    audit: dict[str, Any],
    commands: list[list[str]],
    budget_violations: list[dict[str, Any]] | None = None,
    baseline: dict[str, Any] | None = None,
    baseline_diff: dict[str, Any] | None = None,
    witnesses: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global_safety = audit.get("summary", {}).get("global_initializer_safety", {}) if isinstance(audit, dict) else {}
    graph_derived = sum(
        1
        for record in candidates
        if isinstance(record.get("evidence"), dict)
        and record["evidence"].get("formal_inference") == "source-derived-intent-graph"
    )
    return {
        "model": "o2t-globalopt-coverage-v1",
        "source": str(source) if source is not None else "",
        "source_status": source_status,
        "findings": {
            "total": len(findings),
            "by_marker": count(findings, "marker"),
        },
        "candidates": {
            "total": len(candidates),
            "graph_derived": graph_derived,
            "safety_status": count([
                {"status": formal_parameters(record).get("global.initializer.safety_status")}
                for record in candidates
            ], "status"),
            "observed_facts": fact_counter(candidates, "global.initializer.observed_safety_facts"),
            "missing_facts": fact_counter(candidates, "global.initializer.missing_safety_facts"),
            "safety_provenance_status": parameter_counter(
                candidates, "global.initializer.safety_provenance_status"
            ),
            "rewrite_provenance_status": parameter_counter(
                candidates, "global.initializer.rewrite_provenance_status"
            ),
            "rewrite_callee": parameter_counter(candidates, "global.initializer.rewrite_callee"),
            "replacement_expr": parameter_counter(candidates, "global.initializer.replacement_expr"),
            "value_type_expr": parameter_counter(candidates, "global.initializer.value_type_expr"),
        },
        "validation": {
            "total": len(validated),
            "proof_status": count(validated, "proof_status"),
            "unsupported_reason": count(validated, "proof_result"),
        },
        "audit": {
            "global_initializer_safety": global_safety,
            "recommendation": audit.get("summary", {}).get("recommendation", {}) if isinstance(audit, dict) else {},
        },
        "commands": len(commands),
        "budget_violations": budget_violations or [],
        "baseline": baseline or empty_baseline(),
        "baseline_diff": nested_dict(baseline_diff or {}, "summary"),
        "witnesses": witnesses or {
            "enabled": False,
            "total": 0,
            "passed": 0,
            "skipped": 0,
            "failed": 0,
            "failure_reasons": {},
            "required_cases": ["i32", "ptr", "array"],
        },
        "predicate_provenance": {
            "records": [
                predicate_provenance_record(record)
                for record in validated
                if record.get("marker") == MARKER
            ],
        },
    }


def format_summary(summary: dict[str, Any]) -> str:
    candidates = summary.get("candidates", {})
    validation = summary.get("validation", {})
    audit = summary.get("audit", {})
    global_safety = audit.get("global_initializer_safety", {}) if isinstance(audit, dict) else {}
    lines = [
        "O2T GlobalOpt Coverage Summary",
        f"source_status: {summary.get('source_status', '')}",
        f"source: {summary.get('source', '') or 'none'}",
        f"findings: {int(summary.get('findings', {}).get('total') or 0)}",
        f"candidates: {int(candidates.get('total') or 0)}",
        f"graph_derived: {int(candidates.get('graph_derived') or 0)}",
        "candidate_safety: "
        + ", ".join(f"{key}={value}" for key, value in (candidates.get("safety_status") or {}).items()),
        "observed_facts: "
        + (", ".join(f"{key}={value}" for key, value in (candidates.get("observed_facts") or {}).items()) or "none"),
        "missing_facts: "
        + (", ".join(f"{key}={value}" for key, value in (candidates.get("missing_facts") or {}).items()) or "none"),
        "safety_provenance: "
        + (
            ", ".join(
                f"{key}={value}" for key, value in (candidates.get("safety_provenance_status") or {}).items()
            )
            or "none"
        ),
        "rewrite_provenance: "
        + (
            ", ".join(
                f"{key}={value}" for key, value in (candidates.get("rewrite_provenance_status") or {}).items()
            )
            or "none"
        ),
        "rewrite_callee: "
        + (", ".join(f"{key}={value}" for key, value in (candidates.get("rewrite_callee") or {}).items()) or "none"),
        "replacement_expr: "
        + (
            ", ".join(f"{key}={value}" for key, value in (candidates.get("replacement_expr") or {}).items())
            or "none"
        ),
        "value_type_expr: "
        + (
            ", ".join(f"{key}={value}" for key, value in (candidates.get("value_type_expr") or {}).items())
            or "none"
        ),
        "proof_status: "
        + (", ".join(f"{key}={value}" for key, value in (validation.get("proof_status") or {}).items()) or "none"),
        "audit_safety: "
        + (", ".join(f"{key}={value}" for key, value in (global_safety.get("status") or {}).items()) or "none"),
    ]
    recommendations = audit.get("recommendation", {}) if isinstance(audit, dict) else {}
    lines.append(
        "recommendations: "
        + (", ".join(f"{key}={value}" for key, value in recommendations.items()) if recommendations else "none")
    )
    baseline_diff = nested_dict(summary, "baseline_diff")
    if baseline_diff:
        lines.append(
            "baseline_diff: "
            + " ".join(
                [
                    f"new={int(baseline_diff.get('new') or 0)}",
                    f"resolved={int(baseline_diff.get('resolved') or 0)}",
                    f"changed={int(baseline_diff.get('changed') or 0)}",
                    f"new_unsupported={int(baseline_diff.get('new_unsupported') or 0)}",
                    f"new_incomplete_safety={int(baseline_diff.get('new_incomplete_safety') or 0)}",
                ]
            )
        )
    witnesses = nested_dict(summary, "witnesses")
    if witnesses:
        lines.append(
            "witnesses: "
            + " ".join(
                [
                    f"enabled={str(bool(witnesses.get('enabled'))).lower()}",
                    f"total={int(witnesses.get('total') or 0)}",
                    f"passed={int(witnesses.get('passed') or 0)}",
                    f"skipped={int(witnesses.get('skipped') or 0)}",
                    f"failed={int(witnesses.get('failed') or 0)}",
                ]
            )
        )
    lines.append("Budget violations")
    violations = summary.get("budget_violations", [])
    if isinstance(violations, list) and violations:
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            fact = f" fact={violation.get('fact')}" if violation.get("fact") else ""
            lines.append(
                f"  {violation.get('budget')}{fact}: actual={violation.get('actual')} limit={violation.get('limit')}"
            )
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def empty_summary(out: Path, source_status: str, write_baseline: Path | None = None) -> None:
    baseline = empty_baseline()
    diff = compare_baselines(baseline, baseline, baseline_present=False)
    required_witness_cases = required_witness_cases_from_registry(DEFAULT_INTENTS)
    witnesses = emit_witnesses(out, [], False, None, required_witness_cases)
    summary = summary_for(None, source_status, [], [], [], {}, [], baseline=baseline, baseline_diff=diff, witnesses=witnesses)
    write_json(out / "globalopt-coverage.json", summary)
    write_json(out / "globalopt-baseline.json", baseline)
    write_json(out / "globalopt-baseline-diff.json", diff)
    if write_baseline:
        write_json(write_baseline, baseline)
    (out / "globalopt-baseline-diff.txt").write_text(format_baseline_diff(diff), encoding="utf-8")
    (out / "globalopt-coverage.txt").write_text(format_summary(summary), encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        parse_missing_fact_budgets(args.max_missing_fact)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    source, source_status = selected_source(args.source)
    if source is None:
        empty_summary(args.out, source_status, args.write_baseline)
        print(f"globalopt coverage skipped: {source_status}")
        print(f"summary: {args.out / 'globalopt-coverage.json'}")
        return 0

    work = args.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    findings_path = work / "findings.json"
    candidates_path = work / "candidates.jsonl"
    validated_path = work / "validated.jsonl"
    audit_path = work / "audit.json"
    audit_report = work / "audit.txt"
    commands: list[list[str]] = []

    mine_command = [
        str(args.ast_miner),
        "--registry",
        str(args.registry),
        str(source),
        "--",
        "-std=c++17",
    ]
    commands.append(mine_command)
    mined = run(mine_command)
    if mined.returncode != 0:
        print(mined.stderr, file=sys.stderr, end="")
        return mined.returncode or 1
    findings_path.write_text(mined.stdout, encoding="utf-8")

    infer_command = [
        sys.executable,
        str(args.intent_inferer),
        "--findings",
        str(findings_path),
        "--format",
        "jsonl",
        "--min-confidence",
        "high",
        "--out",
        str(candidates_path),
        "--require-marker",
        MARKER,
    ]
    commands.append(infer_command)
    inferred = run(infer_command)
    if inferred.returncode != 0:
        print(inferred.stderr, file=sys.stderr, end="")
        return inferred.returncode
    if inferred.stdout:
        print(inferred.stdout, end="")

    validate_command = [
        sys.executable,
        str(args.intent_validator),
        "--z3",
        args.z3,
        "--input",
        str(candidates_path),
        "--out",
        str(validated_path),
    ]
    commands.append(validate_command)
    validated_proc = run(validate_command)
    if validated_proc.returncode != 0 and not validated_path.exists():
        print(validated_proc.stderr, file=sys.stderr, end="")
        return validated_proc.returncode
    if validated_proc.stdout:
        print(validated_proc.stdout, end="")

    audit_command = [
        sys.executable,
        str(args.coverage_auditor),
        "--validated",
        str(validated_path),
        "--out",
        str(audit_path),
        "--report",
        str(audit_report),
    ]
    commands.append(audit_command)
    audited = run(audit_command)
    if audited.returncode != 0:
        print(audited.stderr, file=sys.stderr, end="")
        return audited.returncode
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}

    findings = [record for record in load_records(findings_path) if record.get("marker") == MARKER]
    candidates = load_records(candidates_path)
    validated = load_records(validated_path)
    baseline = baseline_from_validated(validated)
    previous_baseline = load_baseline(args.baseline)
    diff = compare_baselines(previous_baseline, baseline, baseline_present=args.baseline is not None)
    required_witness_cases = required_witness_cases_from_registry(args.intent_registry)
    witnesses = emit_witnesses(args.out, validated, args.emit_witnesses, args.host_llvm_as, required_witness_cases)
    summary = summary_for(
        source,
        source_status,
        findings,
        candidates,
        validated,
        audit,
        commands,
        baseline=baseline,
        baseline_diff=diff,
        witnesses=witnesses,
    )
    violations = budget_violations(args, summary, source_selected=True)
    summary["budget_violations"] = violations
    write_json(args.out / "globalopt-coverage.json", summary)
    write_json(args.out / "globalopt-baseline.json", baseline)
    write_json(args.out / "globalopt-baseline-diff.json", diff)
    (args.out / "globalopt-baseline-diff.txt").write_text(format_baseline_diff(diff), encoding="utf-8")
    if args.write_baseline:
        write_json(args.write_baseline, baseline)
    (args.out / "globalopt-coverage.txt").write_text(format_summary(summary), encoding="utf-8")
    if not args.keep_intermediates:
        shutil.rmtree(work, ignore_errors=True)
    print(f"summary: {args.out / 'globalopt-coverage.json'}")
    print(f"report: {args.out / 'globalopt-coverage.txt'}")
    if violations:
        for violation in violations:
            fact = f" fact={violation.get('fact')}" if violation.get("fact") else ""
            print(
                f"budget violation: {violation['budget']}{fact} actual={violation['actual']} limit={violation['limit']}",
                file=sys.stderr,
            )
        return 1
    return validated_proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
