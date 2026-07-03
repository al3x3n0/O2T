#!/usr/bin/env python3
"""Validate shared source program graph contract checks and audit summary wiring."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_graph() -> dict[str, Any]:
    return {
        "model": "llvm-pass-source-program-graph-v1",
        "cfg_precision": "clang-cfg-block-v1",
        "dfg_precision": "clang-ast-decl-use-v1",
        "interprocedural_dfg": True,
        "functions": [
            {"name": "caller", "entry": "caller:entry", "exit": "caller:exit"},
            {"name": "callee", "entry": "callee:entry", "exit": "callee:exit"},
        ],
        "nodes": [
            {"id": "caller:n1", "function": "caller", "source": "Value *Tmp = callee(A);"},
            {"id": "callee:n1", "function": "callee", "source": "return A;"},
        ],
        "cfg_blocks": [
            {"id": "caller:bb1", "successors": ["caller:bb2"]},
            {"id": "caller:bb2", "successors": []},
            {"id": "callee:bb1", "successors": []},
        ],
        "cfg_edges": [
            {"from": "caller:bb1", "to": "caller:bb2", "kind": "clang-cfg-successor"}
        ],
        "dfg_edges": [
            {
                "from": "caller:entry",
                "to": "caller:n1",
                "kind": "clang-ast-decl-use",
                "symbol": "Entry.Scalars[0]",
                "access_path": {
                    "symbol": "Entry.Scalars[0]",
                    "base": "Entry",
                    "segments": [
                        {"kind": "member", "name": "Scalars"},
                        {"kind": "index", "source": "0"},
                    ],
                    "definition_match": "base-fallback",
                    "matched_base": "Entry",
                },
            },
            {
                "from": "caller:n1",
                "to": "callee:entry",
                "kind": "interproc-argument",
                "symbol": "A",
            },
            {
                "from": "callee:n1",
                "to": "caller:n1",
                "kind": "interproc-return",
                "symbol": "Tmp",
            },
        ],
        "call_edges": [{"from": "caller:n1", "to": "callee:entry", "kind": "call"}],
        "access_path_facts": [
            {
                "function": "caller",
                "node": "caller:n1",
                "role": "use",
                "symbol": "Entry.Scalars[0]",
                "base": "Entry",
                "segments": [
                    {"kind": "member", "name": "Scalars"},
                    {"kind": "index", "source": "0"},
                ],
            }
        ],
    }


def check_status(checks: list[dict[str, Any]], check_id: str) -> str:
    for check in checks:
        if check.get("id") == check_id:
            return str(check.get("status") or "")
    raise AssertionError(f"missing check {check_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.repo / "tools"))
    source_contract = load_module(
        args.repo / "tools" / "cv_source_graph_contract.py",
        "cv_source_graph_contract",
    )
    audit = load_module(
        args.repo / "tools" / "cv-audit-intent-coverage.py",
        "cv_audit_intent_coverage",
    )
    build_evidence = load_module(
        args.repo / "tools" / "cv-build-intent-evidence.py",
        "cv_build_intent_evidence",
    )
    promote = load_module(
        args.repo / "tools" / "cv-promote-intent-candidates.py",
        "cv_promote_intent_candidates",
    )

    graph = valid_graph()
    checks = source_contract.source_graph_checks_for_graph(graph)
    assert {check["status"] for check in checks} == {"passed"}
    summary = source_contract.source_graph_contract_summary(graph)
    assert summary["status"] == "passed"
    assert summary["cfg_blocks"] == 3
    assert summary["dfg_edges"] == 3
    assert summary["interprocedural_dfg"] is True
    assert summary["access_path_facts"] == 1

    missing_return = valid_graph()
    missing_return["dfg_edges"] = [
        edge for edge in missing_return["dfg_edges"] if edge["kind"] != "interproc-return"
    ]
    checks = source_contract.source_graph_checks_for_graph(missing_return)
    assert check_status(checks, "source-graph:interprocedural-dfg") == "failed"
    summary = source_contract.source_graph_contract_summary(missing_return)
    assert summary["status"] == "failed"
    assert "source-graph:interprocedural-dfg" in summary["failed_checks"]
    assert summary["failure_reasons"] == {"missing-interprocedural-dfg-edges": 1}

    malformed_access = valid_graph()
    malformed_access["dfg_edges"][0]["access_path"]["symbol"] = "Other.Scalars[0]"
    checks = source_contract.source_graph_checks_for_graph(malformed_access)
    assert check_status(checks, "source-graph:access-path-provenance") == "failed"
    summary = source_contract.source_graph_contract_summary(malformed_access)
    assert "source-graph:access-path-provenance" in summary["failed_checks"]
    assert summary["failure_reasons"]["invalid-access-path-provenance"] == 1

    audited = audit.source_program_graph_contract_summary(
        {"optimization_transaction": {"source_program_graph": valid_graph()}}
    )
    assert audited["source_program_graph_contract_status"] == "passed"
    assert audited["source_program_graph_cfg_blocks"] == 3
    assert audited["source_program_graph_interprocedural_dfg"] is True

    evidence_audited = audit.source_program_graph_contract_summary(
        {"evidence": {"source_program_graph": malformed_access}}
    )
    assert evidence_audited["source_program_graph_contract_status"] == "failed"
    assert evidence_audited["source_program_graph_contract_failure_reasons"] == {
        "invalid-access-path-provenance": 1
    }

    candidate_evidence = {"optimization_transaction": {"source_program_graph": valid_graph()}}
    build_compact = build_evidence.compact_source_program_graph_contract(candidate_evidence)
    promote_compact = promote.compact_source_program_graph_contract(candidate_evidence)
    assert build_compact == promote_compact
    assert build_compact["status"] == "passed"
    assert build_compact["cfg_blocks"] == 3
    assert build_compact["interprocedural_dfg"] is True

    params_evidence = {
        "formal_parameters": {
            "source_program_graph_contract.status": "failed",
            "source_program_graph_contract.failed_checks": [
                "source-graph:interprocedural-dfg"
            ],
            "source_program_graph_contract.failure_reasons": {
                "missing-interprocedural-dfg-edges": 1
            },
            "source_program_graph_contract.cfg_blocks": 3,
            "source_program_graph_contract.dfg_edges": 2,
            "source_program_graph_contract.interprocedural_dfg": False,
            "source_program_graph_contract.access_path_facts": 1,
        }
    }
    assert build_evidence.compact_source_program_graph_contract(params_evidence) == {
        "status": "failed",
        "failed_checks": ["source-graph:interprocedural-dfg"],
        "failure_reasons": {"missing-interprocedural-dfg-edges": 1},
        "cfg_blocks": 3,
        "dfg_edges": 2,
        "interprocedural_dfg": False,
        "access_path_facts": 1,
    }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
