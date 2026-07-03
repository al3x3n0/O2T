#!/usr/bin/env python3
"""Verify mined source-slice contracts independently from miner check status."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from cv_source_graph_contract import source_graph_checks_for_record


REQUIRED_ROLES = [
    "candidate-tree",
    "legality",
    "profitability",
    "vector-emission",
    "scalar-replacement",
    "lane-mapping",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-clean", action="store_true")
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


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def string_set(value: Any) -> set[str]:
    return {str(item) for item in as_list(value) if str(item)}


def check_key(check: dict[str, Any]) -> str:
    return str(check.get("id") or check.get("kind") or "unknown")


def path_edges(path: list[Any]) -> list[tuple[str, str]]:
    names = [str(item) for item in path if str(item)]
    return list(zip(names, names[1:]))


def edge_set(source_slice: dict[str, Any]) -> set[tuple[str, str]]:
    edges = set()
    for edge in as_list(source_slice.get("call_graph")):
        if isinstance(edge, dict) and edge.get("caller") and edge.get("callee"):
            edges.add((str(edge["caller"]), str(edge["callee"])))
    return edges


def valid_path(path: list[Any], edges: set[tuple[str, str]]) -> bool:
    names = [str(item) for item in path if str(item)]
    if not names:
        return False
    return all(edge in edges for edge in path_edges(names))


def make_check(
    check_id: str,
    kind: str,
    status: str,
    role: str = "",
    witness: dict[str, Any] | None = None,
    counterexample: dict[str, Any] | None = None,
) -> dict[str, Any]:
    check: dict[str, Any] = {"id": check_id, "kind": kind, "status": status}
    if role:
        check["role"] = role
    if status == "passed":
        check["witness"] = witness or {}
    else:
        check["counterexample"] = counterexample or {}
    return check


def role_paths_by_role(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    for item in as_list(contract.get("role_paths")):
        if isinstance(item, dict) and item.get("role"):
            paths[str(item["role"])] = item
    return paths


def recompute_checks(record: dict[str, Any]) -> list[dict[str, Any]]:
    transaction = as_dict(record.get("optimization_transaction"))
    source_slice = as_dict(transaction.get("source_slice"))
    contract = as_dict(source_slice.get("contract"))
    completeness = as_dict(source_slice.get("completeness"))
    missing_roles = string_set(contract.get("missing_roles"))
    reachable_roles = string_set(contract.get("reachable_roles"))
    paths = role_paths_by_role(contract)
    edges = edge_set(source_slice)
    root = str(contract.get("control_root_function") or source_slice.get("control_root_function") or "")
    checks: list[dict[str, Any]] = []

    for role in REQUIRED_ROLES:
        role_path = paths.get(role, {})
        path = as_list(role_path.get("path"))
        function = str(role_path.get("function") or "")
        is_present = role in reachable_roles and role not in missing_roles and bool(function)
        has_valid_path = bool(path) and (len(path) == 1 or valid_path(path, edges))
        if is_present and has_valid_path:
            checks.append(
                make_check(
                    f"role-reachability:{role}",
                    "role-reachability",
                    "passed",
                    role,
                    {"function": function, "path": [str(item) for item in path]},
                )
            )
        else:
            reason = "missing-role-evidence" if role in missing_roles or not function else "role-not-reachable"
            checks.append(
                make_check(
                    f"role-reachability:{role}",
                    "role-reachability",
                    "failed",
                    role,
                    counterexample={
                        "reason": reason,
                        "control_root_function": root,
                        **({"function": function} if function else {}),
                    },
                )
            )

    predicate_expansion = as_list(source_slice.get("predicate_expansion"))
    predicate_roles = {
        str(item.get("role"))
        for item in predicate_expansion
        if isinstance(item, dict) and item.get("role")
    }
    has_expanded_legality = "legality" in predicate_roles or not predicate_expansion
    checks.append(
        make_check(
            "predicate-expands-legality",
            "predicate-expansion",
            "passed" if has_expanded_legality else "failed",
            "legality",
            {"control_root_function": root} if has_expanded_legality else None,
            {"reason": "missing-expanded-legality", "control_root_function": root}
            if not has_expanded_legality
            else None,
        )
    )

    for check_id, role, reason in [
        ("emission-reachable-from-control-root", "vector-emission", "emitter-not-reachable"),
        ("replacement-reachable-from-control-root", "scalar-replacement", "replacement-not-reachable"),
    ]:
        role_path = paths.get(role, {})
        path = as_list(role_path.get("path"))
        function = str(role_path.get("function") or "")
        ok = bool(path) and (len(path) == 1 or valid_path(path, edges))
        checks.append(
            make_check(
                check_id,
                "control-flow",
                "passed" if ok else "failed",
                role,
                {"function": function, "path": [str(item) for item in path]} if ok else None,
                {"reason": reason, "control_root_function": root, "function": function} if not ok else None,
            )
        )

    has_lane_mapping = bool(completeness.get("has_lane_mapping"))
    lane_mapping = transaction.get("lane_mapping")
    if not isinstance(lane_mapping, dict):
        lane_mapping = {}
    lane_map = lane_mapping.get("map")
    lanes = transaction.get("lanes")
    lane_map_ok = (
        has_lane_mapping
        and isinstance(lane_map, list)
        and isinstance(lanes, int)
        and len(lane_map) == lanes
    )
    emitter = str(paths.get("vector-emission", {}).get("function") or "")
    checks.append(
        make_check(
            "lane-map-bound-to-emitter",
            "lane-map-binding",
            "passed" if lane_map_ok else "failed",
            "lane-mapping",
            {"function": emitter} if lane_map_ok else None,
            {"reason": "invalid-lane-mapping", "function": emitter} if not lane_map_ok else None,
        )
    )
    checks.extend(source_graph_checks_for_record(record))
    return checks


def compare_checks(emitted: list[dict[str, Any]], recomputed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    emitted_by_id = {check_key(check): check for check in emitted if isinstance(check, dict)}
    recomputed_by_id = {check_key(check): check for check in recomputed}
    mismatches: list[dict[str, Any]] = []
    for check_id in sorted(set(emitted_by_id) | set(recomputed_by_id)):
        emitted_check = emitted_by_id.get(check_id)
        recomputed_check = recomputed_by_id.get(check_id)
        if emitted_check is None:
            mismatches.append({"id": check_id, "kind": "missing-emitted-check"})
            continue
        if recomputed_check is None:
            mismatches.append({"id": check_id, "kind": "unexpected-emitted-check"})
            continue
        emitted_status = str(emitted_check.get("status") or "")
        recomputed_status = str(recomputed_check.get("status") or "")
        if emitted_status != recomputed_status:
            mismatches.append(
                {
                    "id": check_id,
                    "kind": "status-mismatch",
                    "emitted": emitted_status,
                    "recomputed": recomputed_status,
                }
            )
    return mismatches


def verify_record(record: dict[str, Any], index: int) -> dict[str, Any]:
    transaction = as_dict(record.get("optimization_transaction"))
    source_slice = as_dict(transaction.get("source_slice"))
    contract = as_dict(source_slice.get("contract"))
    emitted_checks = [dict(check) for check in as_list(contract.get("checks")) if isinstance(check, dict)]
    recomputed_checks = recompute_checks(record)
    mismatches = compare_checks(emitted_checks, recomputed_checks)
    failed_checks = [
        check_key(check)
        for check in recomputed_checks
        if str(check.get("status") or "") == "failed"
    ]
    expected_status = "failed" if failed_checks else "complete"
    emitted_status = str(contract.get("status") or "")
    if emitted_status != expected_status:
        mismatches.append(
            {
                "id": "contract-status",
                "kind": "status-mismatch",
                "emitted": emitted_status,
                "recomputed": expected_status,
            }
        )
    return {
        "index": index,
        "marker": str(record.get("marker") or ""),
        "contract_verification": {
            "status": "failed" if mismatches else "passed",
            "contract_status": emitted_status,
            "recomputed_contract_status": expected_status,
            "mismatches": mismatches,
            "failed_checks": failed_checks,
            "emitted_checks": emitted_checks,
            "recomputed_checks": recomputed_checks,
        },
    }


def report_text(results: list[dict[str, Any]]) -> str:
    statuses = collections.Counter(
        str(result["contract_verification"]["status"]) for result in results
    )
    failed_checks = collections.Counter(
        check
        for result in results
        for check in result["contract_verification"]["failed_checks"]
    )
    mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for result in results
        for mismatch in result["contract_verification"]["mismatches"]
    )
    recomputed_checks = collections.Counter(
        check_key(check)
        for result in results
        for check in result["contract_verification"]["recomputed_checks"]
    )
    lines = ["O2T Source-Slice Contract Verification", f"records: {len(results)}"]
    lines.append("Verification status")
    for key, value in sorted(statuses.items()):
        lines.append(f"  {key}: {value}")
    if not statuses:
        lines.append("  none")
    lines.append("Recomputed failed checks")
    if failed_checks:
        for key, value in sorted(failed_checks.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Recomputed checks")
    if recomputed_checks:
        for key, value in sorted(recomputed_checks.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Mismatches")
    if mismatch_kinds:
        for key, value in sorted(mismatch_kinds.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        records = load_records(args.findings)
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    results = [verify_record(record, index) for index, record in enumerate(records)]
    report = {"summary": {"records": len(results)}, "records": results}
    report["summary"]["status"] = dict(
        sorted(collections.Counter(result["contract_verification"]["status"] for result in results).items())
    )
    report["summary"]["failed_checks"] = dict(
        sorted(
            collections.Counter(
                check
                for result in results
                for check in result["contract_verification"]["failed_checks"]
            ).items()
        )
    )
    report["summary"]["mismatches"] = dict(
        sorted(
            collections.Counter(
                str(mismatch.get("kind") or "unknown")
                for result in results
                for mismatch in result["contract_verification"]["mismatches"]
            ).items()
        )
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text(results), encoding="utf-8")
    has_mismatch = any(result["contract_verification"]["mismatches"] for result in results)
    if args.require_clean and has_mismatch:
        print("source-slice contract verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
