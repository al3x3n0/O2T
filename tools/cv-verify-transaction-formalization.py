#!/usr/bin/env python3
"""Verify transaction-to-formal-IR lowering independently from validation."""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INFER_TOOL = ROOT / "tools" / "cv-infer-optimization-intent.py"
MAX_DIFFS_PER_MISMATCH = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-provenance-complete", action="store_true")
    return parser.parse_args()


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


def load_infer_module() -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from o2t.intent import infer
    return infer


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def nested_dict(record: dict[str, Any], key: str) -> dict[str, Any]:
    value = record.get(key)
    return value if isinstance(value, dict) else {}


def mismatch(
    kind: str,
    expected: Any = None,
    actual: Any = None,
    diffs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind}
    if expected is not None:
        out["expected"] = expected
    if actual is not None:
        out["actual"] = actual
    if diffs:
        out["path"] = str(diffs[0].get("path") or "")
        out["diff_count"] = len(diffs)
        out["diffs"] = diffs[:MAX_DIFFS_PER_MISMATCH]
    return out


def parent_path(path: str) -> str:
    if not path:
        return ""
    if path.endswith("]"):
        bracket = path.rfind("[")
        if bracket > 0:
            return path[:bracket]
    dot = path.rfind(".")
    return path[:dot] if dot > 0 else ""


def provenance_for_path(path: str, provenance: dict[str, Any]) -> dict[str, Any] | None:
    current = path
    while current:
        value = provenance.get(current)
        if isinstance(value, dict):
            return dict(value)
        current = parent_path(current)
    return None


def attach_provenance(
    mismatches: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    if not provenance:
        return mismatches
    out: list[dict[str, Any]] = []
    for item in mismatches:
        enriched = dict(item)
        path = str(enriched.get("path") or "")
        source = provenance_for_path(path, provenance)
        if source:
            enriched["provenance"] = source
        diffs = enriched.get("diffs")
        if isinstance(diffs, list):
            enriched_diffs: list[dict[str, Any]] = []
            for diff in diffs:
                if not isinstance(diff, dict):
                    continue
                enriched_diff = dict(diff)
                diff_source = provenance_for_path(str(diff.get("path") or ""), provenance)
                if diff_source:
                    enriched_diff["provenance"] = diff_source
                enriched_diffs.append(enriched_diff)
            enriched["diffs"] = enriched_diffs
        out.append(enriched)
    return out


def collect_formal_paths(formal: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for key in ("domain", "vector_width", "base_lanes", "vscale_values"):
        if key in formal:
            paths.add(key)

    def visit(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            return
        op = value.get("op")
        if isinstance(op, str) and op:
            paths.add(path)
            paths.add(path_key(path, "op"))
            if op in {"var", "fpvar", "svar", "sfpvar", "memvar"}:
                paths.add(path_key(path, "name"))
            if op in {"vshuffle", "svshuffle"}:
                if "mask" in value:
                    paths.add(path_key(path, "mask"))
                if "base_mask" in value:
                    paths.add(path_key(path, "base_mask"))
            if op in {"zext", "sext", "trunc", "vzext", "vsext", "vtrunc"} and "bits" in value:
                paths.add(path_key(path, "bits"))
        args = value.get("args")
        if isinstance(args, list):
            for index, child in enumerate(args):
                visit(child, path_index(path_key(path, "args"), index))

    visit(formal.get("before"), "before")
    visit(formal.get("after"), "after")
    return sorted(paths)


def formal_provenance_coverage(formal: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    paths = collect_formal_paths(formal)
    covered: list[str] = []
    missing: list[str] = []
    roles: collections.Counter[str] = collections.Counter()
    for path in paths:
        source = provenance_for_path(path, provenance)
        if source:
            covered.append(path)
            roles[str(source.get("role") or "unknown")] += 1
        else:
            missing.append(path)
    return {
        "status": "passed" if not missing else "incomplete",
        "covered_paths": covered,
        "missing_paths": missing,
        "roles": dict(sorted(roles.items())),
    }


def provenance_label(provenance: Any) -> str:
    if not isinstance(provenance, dict):
        return ""
    role = str(provenance.get("role") or "")
    field = str(provenance.get("transaction_field") or "")
    if role and field:
        return f"source={role}/{field}"
    return f"source={role or field}" if role or field else ""


def path_key(base: str, key: str) -> str:
    return f"{base}.{key}" if base else key


def path_index(base: str, index: int) -> str:
    return f"{base}[{index}]" if base else f"[{index}]"


def leaf_diff(path: str, reason: str, expected: Any = None, actual: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"path": path, "reason": reason}
    if expected is not None:
        out["expected"] = expected
    if actual is not None:
        out["actual"] = actual
    return out


def formal_diffs(expected: Any, actual: Any, path: str) -> list[dict[str, Any]]:
    if canonical(expected) == canonical(actual):
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs: list[dict[str, Any]] = []
        for key in sorted(set(expected) | set(actual)):
            child_path = path_key(path, str(key))
            if key not in actual:
                diffs.append(leaf_diff(child_path, "missing-actual", expected=expected.get(key)))
            elif key not in expected:
                diffs.append(leaf_diff(child_path, "unexpected-actual", actual=actual.get(key)))
            else:
                diffs.extend(formal_diffs(expected.get(key), actual.get(key), child_path))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        diffs = []
        shared = min(len(expected), len(actual))
        for index in range(shared):
            diffs.extend(formal_diffs(expected[index], actual[index], path_index(path, index)))
        for index in range(shared, len(expected)):
            diffs.append(leaf_diff(path_index(path, index), "missing-actual", expected=expected[index]))
        for index in range(shared, len(actual)):
            diffs.append(leaf_diff(path_index(path, index), "unexpected-actual", actual=actual[index]))
        return diffs
    if type(expected) is not type(actual):
        return [leaf_diff(path, "type-mismatch", expected=expected, actual=actual)]
    return [leaf_diff(path, "value-mismatch", expected=expected, actual=actual)]


def compare_formal(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for key, kind in [
        ("domain", "domain-mismatch"),
        ("before", "before-mismatch"),
        ("after", "after-mismatch"),
        ("equivalence", "equivalence-mismatch"),
        ("refinement", "refinement-mismatch"),
        ("vector_width", "vector-width-mismatch"),
        ("base_lanes", "base-lanes-mismatch"),
        ("vscale_values", "vscale-values-mismatch"),
    ]:
        if key in expected or key in actual:
            if canonical(expected.get(key)) != canonical(actual.get(key)):
                diffs = formal_diffs(expected.get(key), actual.get(key), key)
                mismatches.append(mismatch(kind, expected.get(key), actual.get(key), diffs))
    return mismatches


def verify_record(record: dict[str, Any], index: int, infer_module: Any) -> dict[str, Any]:
    evidence = nested_dict(record, "evidence")
    transaction = evidence.get("optimization_transaction")
    intent = nested_dict(record, "intent_candidate")
    claimed_formal = intent.get("formal")
    claimed_lowering = str(evidence.get("transaction_lowering") or "")
    marker = str(record.get("marker") or "")
    result: dict[str, Any] = {"index": index, "marker": marker}

    if not isinstance(transaction, dict):
        result["transaction_formalization_verification"] = {
            "status": "unsupported",
            "reason": "missing-transaction",
            "mismatches": [],
        }
        return result
    if not isinstance(claimed_formal, dict):
        status = "unsupported" if claimed_lowering != "formal-ir" else "failed"
        result["transaction_formalization_verification"] = {
            "status": status,
            "reason": "missing-formal",
            "mismatches": [] if status == "unsupported" else [mismatch("missing-formal")],
        }
        return result

    recomputed = infer_module.transaction_formal_for({"optimization_transaction": transaction})
    if recomputed is None:
        status = "unsupported" if claimed_lowering != "formal-ir" else "failed"
        result["transaction_formalization_verification"] = {
            "status": status,
            "reason": "unsupported-transaction-kind",
            "mismatches": [] if status == "unsupported" else [mismatch("unsupported-transaction-kind")],
        }
        return result

    recomputed_formal, params = recomputed
    provenance = params.get("transaction.formal_provenance")
    mismatches = compare_formal(recomputed_formal, claimed_formal)
    coverage = None
    if isinstance(provenance, dict):
        mismatches = attach_provenance(mismatches, provenance)
        coverage = formal_provenance_coverage(recomputed_formal, provenance)
    result["transaction_formalization_verification"] = {
        "status": "failed" if mismatches else "passed",
        "reason": "" if not mismatches else "formal-mismatch",
        "mismatches": mismatches,
        "recomputed_formal": recomputed_formal,
    }
    if coverage is not None:
        result["transaction_formalization_verification"]["provenance_coverage"] = coverage
    return result


def report_text(results: list[dict[str, Any]]) -> str:
    statuses = collections.Counter(
        str(result["transaction_formalization_verification"].get("status") or "unknown")
        for result in results
    )
    reasons = collections.Counter(
        str(result["transaction_formalization_verification"].get("reason") or "none")
        for result in results
    )
    mismatch_kinds = collections.Counter(
        str(item.get("kind") or "unknown")
        for result in results
        for item in result["transaction_formalization_verification"].get("mismatches", [])
        if isinstance(item, dict)
    )
    coverage_statuses = collections.Counter(
        str(coverage.get("status") or "absent")
        for result in results
        for coverage in [result["transaction_formalization_verification"].get("provenance_coverage", {})]
        if isinstance(coverage, dict)
    )
    coverage_roles = collections.Counter(
        str(role)
        for result in results
        for coverage in [result["transaction_formalization_verification"].get("provenance_coverage", {})]
        if isinstance(coverage, dict)
        for role, count in coverage.get("roles", {}).items()
        for _ in range(int(count))
    )
    missing_paths = [
        str(path)
        for result in results
        for coverage in [result["transaction_formalization_verification"].get("provenance_coverage", {})]
        if isinstance(coverage, dict)
        for path in coverage.get("missing_paths", [])
    ]
    lines = ["O2T Transaction Formalization Verification", f"records: {len(results)}"]
    lines.append("Verification status")
    if statuses:
        for key, value in sorted(statuses.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Reasons")
    if reasons:
        for key, value in sorted(reasons.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Mismatches")
    if mismatch_kinds:
        for key, value in sorted(mismatch_kinds.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Formal provenance coverage")
    if coverage_statuses:
        for key, value in sorted(coverage_statuses.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Formal provenance roles")
    if coverage_roles:
        for key, value in sorted(coverage_roles.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Top missing provenance paths")
    if missing_paths:
        for path in missing_paths[:MAX_DIFFS_PER_MISMATCH]:
            lines.append(f"  {path}")
    else:
        lines.append("  none")
    lines.append("Top mismatch paths")
    top_paths = [
        (str(item.get("kind") or "unknown"), str(item.get("path") or ""), item)
        for result in results
        for item in result["transaction_formalization_verification"].get("mismatches", [])
        if isinstance(item, dict) and item.get("path")
    ]
    if top_paths:
        for kind, path, item in top_paths:
            diffs = item.get("diffs")
            first_diff = diffs[0] if isinstance(diffs, list) and diffs and isinstance(diffs[0], dict) else {}
            expected = canonical(first_diff.get("expected")) if "expected" in first_diff else "<missing>"
            actual = canonical(first_diff.get("actual")) if "actual" in first_diff else "<missing>"
            source = provenance_label(first_diff.get("provenance") or item.get("provenance"))
            suffix = f" {source}" if source else ""
            lines.append(f"  {kind} {path}: expected {expected}, actual {actual}{suffix}")
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        records = load_records(args.input)
        infer_module = load_infer_module()
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    results = [verify_record(record, index, infer_module) for index, record in enumerate(records)]
    summary = {
        "records": len(results),
        "status": dict(
            sorted(
                collections.Counter(
                    str(result["transaction_formalization_verification"].get("status") or "unknown")
                    for result in results
                ).items()
            )
        ),
        "mismatch_kinds": dict(
            sorted(
                collections.Counter(
                    str(item.get("kind") or "unknown")
                    for result in results
                    for item in result["transaction_formalization_verification"].get("mismatches", [])
                    if isinstance(item, dict)
                ).items()
            )
        ),
        "provenance_coverage": dict(
            sorted(
                collections.Counter(
                    str(coverage.get("status") or "absent")
                    for result in results
                    for coverage in [result["transaction_formalization_verification"].get("provenance_coverage", {})]
                    if isinstance(coverage, dict)
                ).items()
            )
        ),
    }
    report = {"summary": summary, "records": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text(results), encoding="utf-8")
    failed = any(
        result["transaction_formalization_verification"].get("status") == "failed"
        for result in results
    )
    if args.require_clean and failed:
        print("transaction formalization verification failed", file=sys.stderr)
        return 1
    incomplete_provenance = any(
        result["transaction_formalization_verification"].get("provenance_coverage", {}).get("status")
        == "incomplete"
        for result in results
        if isinstance(result["transaction_formalization_verification"].get("provenance_coverage"), dict)
    )
    if args.require_provenance_complete and incomplete_provenance:
        print("transaction formal provenance coverage incomplete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
