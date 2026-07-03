#!/usr/bin/env python3
"""Shared structural contract checks for mined source program graphs."""

from __future__ import annotations

from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def graph_edge_kinds(graph: dict[str, Any], edge_array: str) -> set[str]:
    return {
        str(edge.get("kind") or "")
        for edge in as_list(graph.get(edge_array))
        if isinstance(edge, dict)
    }


def graph_valid_ids(graph: dict[str, Any]) -> set[str]:
    ids = {
        str(node.get("id"))
        for node in as_list(graph.get("nodes"))
        if isinstance(node, dict) and node.get("id")
    }
    ids.update(
        str(block.get("id"))
        for block in as_list(graph.get("cfg_blocks"))
        if isinstance(block, dict) and block.get("id")
    )
    for function in as_list(graph.get("functions")):
        if not isinstance(function, dict):
            continue
        if function.get("entry"):
            ids.add(str(function["entry"]))
        if function.get("exit"):
            ids.add(str(function["exit"]))
    return ids


def graph_endpoint_failures(graph: dict[str, Any]) -> list[dict[str, str]]:
    ids = graph_valid_ids(graph)
    failures: list[dict[str, str]] = []
    for edge_array in ["cfg_edges", "dfg_edges", "call_edges"]:
        for edge in as_list(graph.get(edge_array)):
            if not isinstance(edge, dict):
                continue
            for field in ["from", "to"]:
                endpoint = str(edge.get(field) or "")
                if endpoint and endpoint in ids:
                    continue
                failures.append(
                    {
                        "edge_array": edge_array,
                        "field": field,
                        "endpoint": endpoint,
                        "kind": str(edge.get("kind") or ""),
                    }
                )
    return failures


def graph_access_path_failures(graph: dict[str, Any]) -> list[dict[str, str]]:
    ids = graph_valid_ids(graph)
    failures: list[dict[str, str]] = []
    for fact in as_list(graph.get("access_path_facts")):
        if not isinstance(fact, dict):
            continue
        node = str(fact.get("node") or "")
        symbol = str(fact.get("symbol") or "")
        if not node or node not in ids:
            failures.append(
                {
                    "kind": "dangling-access-path-fact-node",
                    "node": node,
                    "symbol": symbol,
                }
            )
        if not symbol or not str(fact.get("base") or "") or not as_list(fact.get("segments")):
            failures.append(
                {
                    "kind": "malformed-access-path-fact",
                    "node": node,
                    "symbol": symbol,
                }
            )
    for edge in as_list(graph.get("dfg_edges")):
        if not isinstance(edge, dict):
            continue
        access_path = as_dict(edge.get("access_path"))
        if not access_path:
            continue
        definition_match = str(access_path.get("definition_match") or "")
        malformed = (
            str(access_path.get("symbol") or "") != str(edge.get("symbol") or "")
            or not str(access_path.get("base") or "")
            or not as_list(access_path.get("segments"))
            or (definition_match == "base-fallback" and not str(access_path.get("matched_base") or ""))
        )
        if malformed:
            failures.append(
                {
                    "kind": "malformed-access-path-edge",
                    "from": str(edge.get("from") or ""),
                    "to": str(edge.get("to") or ""),
                    "symbol": str(edge.get("symbol") or ""),
                }
            )
    return failures


def source_graph_checks_for_graph(graph: dict[str, Any]) -> list[dict[str, Any]]:
    if not graph:
        return [
            make_check(
                "source-graph:present",
                "source-graph",
                "failed",
                counterexample={"reason": "missing-source-program-graph"},
            )
        ]

    checks: list[dict[str, Any]] = []
    model_ok = str(graph.get("model") or "") == "llvm-pass-source-program-graph-v1"
    checks.append(
        make_check(
            "source-graph:present",
            "source-graph",
            "passed" if model_ok else "failed",
            witness={"model": str(graph.get("model") or "")} if model_ok else None,
            counterexample={"reason": "unexpected-source-graph-model", "model": str(graph.get("model") or "")}
            if not model_ok
            else None,
        )
    )

    cfg_ok = (
        str(graph.get("cfg_precision") or "") == "clang-cfg-block-v1"
        and bool(as_list(graph.get("cfg_blocks")))
    )
    checks.append(
        make_check(
            "source-graph:cfg-precision",
            "source-graph",
            "passed" if cfg_ok else "failed",
            witness={
                "cfg_precision": str(graph.get("cfg_precision") or ""),
                "cfg_blocks": len(as_list(graph.get("cfg_blocks"))),
            }
            if cfg_ok
            else None,
            counterexample={
                "reason": "missing-clang-cfg-blocks",
                "cfg_precision": str(graph.get("cfg_precision") or ""),
                "cfg_blocks": len(as_list(graph.get("cfg_blocks"))),
            }
            if not cfg_ok
            else None,
        )
    )

    dfg_ok = (
        str(graph.get("dfg_precision") or "") == "clang-ast-decl-use-v1"
        and bool(as_list(graph.get("dfg_edges")))
    )
    checks.append(
        make_check(
            "source-graph:dfg-precision",
            "source-graph",
            "passed" if dfg_ok else "failed",
            witness={
                "dfg_precision": str(graph.get("dfg_precision") or ""),
                "dfg_edges": len(as_list(graph.get("dfg_edges"))),
            }
            if dfg_ok
            else None,
            counterexample={
                "reason": "missing-clang-ast-dfg",
                "dfg_precision": str(graph.get("dfg_precision") or ""),
                "dfg_edges": len(as_list(graph.get("dfg_edges"))),
            }
            if not dfg_ok
            else None,
        )
    )

    dfg_kinds = graph_edge_kinds(graph, "dfg_edges")
    interproc_ok = (
        graph.get("interprocedural_dfg") is True
        and "interproc-argument" in dfg_kinds
        and "interproc-return" in dfg_kinds
    )
    checks.append(
        make_check(
            "source-graph:interprocedural-dfg",
            "source-graph",
            "passed" if interproc_ok else "failed",
            witness={"edge_kinds": sorted(dfg_kinds)} if interproc_ok else None,
            counterexample={
                "reason": "missing-interprocedural-dfg-edges",
                "interprocedural_dfg": graph.get("interprocedural_dfg"),
                "edge_kinds": sorted(dfg_kinds),
            }
            if not interproc_ok
            else None,
        )
    )

    endpoint_failures = graph_endpoint_failures(graph)
    checks.append(
        make_check(
            "source-graph:node-edge-integrity",
            "source-graph",
            "passed" if not endpoint_failures else "failed",
            witness={"checked_edge_arrays": ["cfg_edges", "dfg_edges", "call_edges"]}
            if not endpoint_failures
            else None,
            counterexample={"reason": "dangling-edge-endpoints", "failures": endpoint_failures[:10]}
            if endpoint_failures
            else None,
        )
    )

    access_path_failures = graph_access_path_failures(graph)
    checks.append(
        make_check(
            "source-graph:access-path-provenance",
            "source-graph",
            "passed" if not access_path_failures else "failed",
            witness={"access_path_facts": len(as_list(graph.get("access_path_facts")))}
            if not access_path_failures
            else None,
            counterexample={
                "reason": "invalid-access-path-provenance",
                "failures": access_path_failures[:10],
            }
            if access_path_failures
            else None,
        )
    )
    return checks


def source_graph_checks_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    transaction = as_dict(record.get("optimization_transaction"))
    if not transaction:
        return []
    return source_graph_checks_for_graph(as_dict(transaction.get("source_program_graph")))


def source_graph_contract_summary(graph: dict[str, Any]) -> dict[str, Any]:
    if not graph:
        return {
            "status": "absent",
            "failed_checks": [],
            "check_status": {},
            "failure_reasons": {},
            "cfg_blocks": 0,
            "dfg_edges": 0,
            "interprocedural_dfg": False,
            "access_path_facts": 0,
        }
    checks = source_graph_checks_for_graph(graph)
    failed = [check for check in checks if str(check.get("status") or "") == "failed"]
    reasons: dict[str, int] = {}
    for check in failed:
        reason = str(as_dict(check.get("counterexample")).get("reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    status_by_check = {
        str(check.get("id") or "unknown"): str(check.get("status") or "unknown")
        for check in checks
    }
    return {
        "status": "failed" if failed else "passed",
        "failed_checks": [str(check.get("id") or "unknown") for check in failed],
        "check_status": status_by_check,
        "failure_reasons": dict(sorted(reasons.items())),
        "cfg_blocks": len(as_list(graph.get("cfg_blocks"))),
        "dfg_edges": len(as_list(graph.get("dfg_edges"))),
        "interprocedural_dfg": graph.get("interprocedural_dfg") is True,
        "access_path_facts": len(as_list(graph.get("access_path_facts"))),
    }


def source_graph_contract_parameters(
    graph: dict[str, Any],
    prefix: str = "source_program_graph_contract.",
) -> dict[str, Any]:
    summary = source_graph_contract_summary(graph)
    return {
        prefix + "status": summary["status"],
        prefix + "failed_checks": list(summary["failed_checks"]),
        prefix + "failure_reasons": dict(summary["failure_reasons"]),
        prefix + "cfg_blocks": int(summary["cfg_blocks"]),
        prefix + "dfg_edges": int(summary["dfg_edges"]),
        prefix + "interprocedural_dfg": bool(summary["interprocedural_dfg"]),
        prefix + "access_path_facts": int(summary["access_path_facts"]),
    }
