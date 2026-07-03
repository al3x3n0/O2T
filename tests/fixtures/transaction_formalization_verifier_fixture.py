#!/usr/bin/env python3
"""Fixture checks for transaction formalization diff diagnostics."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    return parser.parse_args()


def load_tool(repo: Path, path: str, module_name: str) -> Any:
    tool_path = repo / path
    tools_dir = str(repo / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {tool_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_single_path(module: Any, expected: dict[str, Any], actual: dict[str, Any], path: str, reason: str) -> None:
    mismatches = module.compare_formal(expected, actual)
    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch["kind"] == "after-mismatch"
    assert mismatch["path"] == path
    assert mismatch["diff_count"] == 1
    assert mismatch["diffs"][0]["path"] == path
    assert mismatch["diffs"][0]["reason"] == reason


def binop_transaction() -> dict[str, Any]:
    lane_map = [2, 0, 3, 1]
    return {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "consistency": "ok",
        "opcode_sources": [{"function": "vectorizeTree", "source": "CreateAdd"}],
        "lane_mapping": {"kind": "permutation", "map": lane_map, "inverse_map": [1, 3, 0, 2]},
        "result_lane_mapping": {"kind": "permutation", "map": lane_map, "inverse_map": [1, 3, 0, 2]},
        "scalar_lane_pairs": [
            {"result": "r2", "lhs": "a2", "rhs": "b2"},
            {"result": "r0", "lhs": "a0", "rhs": "b0"},
            {"result": "r3", "lhs": "a3", "rhs": "b3"},
            {"result": "r1", "lhs": "a1", "rhs": "b1"},
        ],
    }


def reduction_transaction() -> dict[str, Any]:
    return {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-reduction",
        "opcode": "add",
        "reduction_opcode": "add",
        "lanes": 4,
        "reduction_lanes": 4,
        "consistency": "ok",
        "lane_mapping": {"kind": "identity", "map": [0, 1, 2, 3], "inverse_map": [0, 1, 2, 3]},
        "reduction_sources": [{"line": 42, "source": "CreateAddReduce(LHS)"}],
        "reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
    }


def graph_transaction() -> dict[str, Any]:
    lane_map = {"kind": "identity", "map": [0, 1, 2, 3], "inverse_map": [0, 1, 2, 3]}
    return {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "consistency": "ok",
        "opcode_sources": [{"function": "vectorizeTree", "source": "CreateAdd/CreateMul"}],
        "lane_mapping": dict(lane_map),
        "result_lane_mapping": dict(lane_map),
        "transaction_graph": {
            "model": "optimization-transaction-graph-v1",
            "kind": "slp-binop-chain",
            "lanes": 4,
            "lane_mapping": dict(lane_map),
            "operands": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
            "nodes": [
                {
                    "id": "n0",
                    "kind": "binop",
                    "opcode": "add",
                    "operands": [{"kind": "pack", "name": "a"}, {"kind": "pack", "name": "b"}],
                },
                {
                    "id": "n1",
                    "kind": "binop",
                    "opcode": "mul",
                    "operands": [{"kind": "node", "id": "n0"}, {"kind": "pack", "name": "c"}],
                },
            ],
            "edges": [{"from": "n0", "to": "n1", "operand": 0}],
            "outputs": [{"node": "n1", "result_lane_mapping": dict(lane_map)}],
        },
    }


def mismatches_with_provenance(verifier: Any, formal: dict[str, Any], actual: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    return verifier.attach_provenance(verifier.compare_formal(formal, actual), provenance)


def main() -> int:
    repo = parse_args().repo
    verifier = load_tool(repo, "tools/cv-verify-transaction-formalization.py", "cv_verify_transaction_formalization")
    infer = load_tool(repo, "tools/cv-infer-optimization-intent.py", "cv_infer_optimization_intent")
    formal_ir = load_tool(repo, "tools/cv_formal_ir.py", "cv_formal_ir")
    assert_single_path(
        verifier,
        {"after": {"op": "vadd", "args": []}},
        {"after": {"op": "vec", "args": []}},
        "after.op",
        "value-mismatch",
    )
    assert_single_path(
        verifier,
        {
            "after": {
                "op": "vadd",
                "args": [{"op": "vec", "args": [{"op": "var", "name": "a0"}, {"op": "var", "name": "a1"}]}],
            }
        },
        {
            "after": {
                "op": "vadd",
                "args": [{"op": "vec", "args": [{"op": "var", "name": "a0"}, {"op": "var", "name": "b1"}]}],
            }
        },
        "after.args[0].args[1].name",
        "value-mismatch",
    )
    assert_single_path(
        verifier,
        {"after": {"op": "vec", "args": [{"op": "var", "name": "a0"}, {"op": "var", "name": "a1"}]}},
        {"after": {"op": "vec", "args": [{"op": "var", "name": "a0"}]}},
        "after.args[1]",
        "missing-actual",
    )
    binop_formal, binop_params = infer.transaction_formal_for({"optimization_transaction": binop_transaction()})
    binop_provenance = binop_params["transaction.formal_provenance"]
    binop_coverage = verifier.formal_provenance_coverage(binop_formal, binop_provenance)
    assert binop_coverage["status"] == "passed"
    assert binop_coverage["roles"]["opcode"] > 0
    assert binop_coverage["roles"]["lane-mapping"] > 0
    assert binop_coverage["roles"]["result-lane-mapping"] > 0
    assert binop_coverage["roles"]["scalar-lane-pair"] > 0
    assert binop_coverage["roles"]["domain"] > 0

    tampered_opcode = dict(binop_formal)
    tampered_opcode["after"] = {"op": "vec", "args": []}
    mismatches = mismatches_with_provenance(verifier, binop_formal, tampered_opcode, binop_provenance)
    assert mismatches[0]["path"] == "after.args[0]"
    assert mismatches[0]["provenance"]["role"] == "opcode"
    assert mismatches[0]["provenance"]["transaction_field"] == "opcode_sources"

    tampered_mask = dict(binop_formal)
    tampered_mask["after"] = dict(binop_formal["after"])
    tampered_mask["after"]["mask"] = [0, 1, 2, 3]
    mismatches = mismatches_with_provenance(verifier, binop_formal, tampered_mask, binop_provenance)
    assert mismatches[0]["diffs"][0]["path"] == "after.mask[0]"
    assert mismatches[0]["diffs"][0]["provenance"]["role"] == "result-lane-mapping"

    tampered_lane = dict(binop_formal)
    tampered_lane["before"] = {
        "op": "vec",
        "args": [dict(item) for item in binop_formal["before"]["args"]],
    }
    tampered_lane["before"]["args"][0] = {
        "op": "bvadd",
        "args": [{"op": "var", "name": "x0"}, {"op": "var", "name": "b0"}],
    }
    mismatches = mismatches_with_provenance(verifier, binop_formal, tampered_lane, binop_provenance)
    assert mismatches[0]["diffs"][0]["path"] == "before.args[0].args[0].name"
    assert mismatches[0]["diffs"][0]["provenance"]["role"] == "scalar-lane-pair"

    reduction_formal, reduction_params = infer.transaction_formal_for({"optimization_transaction": reduction_transaction()})
    reduction_coverage = verifier.formal_provenance_coverage(
        reduction_formal,
        reduction_params["transaction.formal_provenance"],
    )
    assert reduction_coverage["status"] == "passed"
    assert reduction_coverage["roles"]["reduction-source"] > 0
    assert reduction_coverage["roles"]["width"] > 0
    assert reduction_coverage["roles"]["domain"] > 0

    graph_formal, graph_params = infer.transaction_formal_for({"optimization_transaction": graph_transaction()})
    graph_provenance = graph_params["transaction.formal_provenance"]
    graph_coverage = verifier.formal_provenance_coverage(graph_formal, graph_provenance)
    assert graph_coverage["status"] == "passed", graph_coverage["missing_paths"]
    assert graph_coverage["roles"]["transaction-graph-node"] > 0
    assert graph_coverage["roles"]["transaction-graph-edge"] > 0
    assert graph_coverage["roles"]["transaction-graph-operand"] > 0

    tuple_mask = {
        "op": "svmask_tuple",
        "base_lanes": 4,
        "entries": [
            {"op": "const", "value": True},
            {
                "op": "icmp",
                "predicate": "ne",
                "lhs": {"kind": "lane", "name": "M"},
                "rhs": {"kind": "const", "value": 0},
            },
            {
                "op": "select",
                "args": [
                    {
                        "op": "icmp",
                        "predicate": "ne",
                        "lhs": {"kind": "lane", "name": "C"},
                        "rhs": {"kind": "const", "value": 0},
                    },
                    {
                        "op": "icmp",
                        "predicate": "ne",
                        "lhs": {"kind": "indexed", "name": "Mask", "index": 0},
                        "rhs": {"kind": "const", "value": 0},
                    },
                    {"op": "const", "value": False},
                ],
            },
            {
                "op": "icmp",
                "predicate": "eq",
                "lhs": {"kind": "indexed", "name": "Mask", "index": 1},
                "rhs": {"kind": "lane", "name": "M"},
            },
        ],
    }
    tuple_formal = {
        "domain": "scalable-vector-bv32",
        "base_lanes": 4,
        "vscale_values": [1, 2, 4],
        "variables": ["a", "b", "M", "C", "Mask"],
        "before": {"op": "svselect", "args": [tuple_mask, {"op": "svar", "name": "a"}, {"op": "svar", "name": "b"}]},
        "after": {"op": "svselect", "args": [tuple_mask, {"op": "svar", "name": "a"}, {"op": "svar", "name": "b"}]},
        "equivalence": "vector-result",
    }
    tuple_instances = formal_ir.pair_instances_for_formal(tuple_formal)
    assert [vscale for vscale, _ in tuple_instances] == [1, 2, 4]
    assert [len(pair.variables) for _, pair in tuple_instances] == [20, 40, 80]
    assert any("Mask5" in pair.before for vscale, pair in tuple_instances if vscale == 2)
    assert any("M15" in pair.before for vscale, pair in tuple_instances if vscale == 4)

    tampered_graph_opcode = dict(graph_formal)
    tampered_graph_opcode["after"] = dict(graph_formal["after"])
    tampered_graph_opcode["after"]["op"] = "vadd"
    mismatches = mismatches_with_provenance(verifier, graph_formal, tampered_graph_opcode, graph_provenance)
    assert mismatches[0]["diffs"][0]["path"] == "after.op"
    assert mismatches[0]["diffs"][0]["provenance"]["role"] == "transaction-graph-node"

    tampered_graph_edge = dict(graph_formal)
    tampered_graph_edge["after"] = dict(graph_formal["after"])
    tampered_graph_edge["after"]["args"] = []
    mismatches = mismatches_with_provenance(verifier, graph_formal, tampered_graph_edge, graph_provenance)
    assert mismatches[0]["path"] == "after.args[0]"
    assert mismatches[0]["provenance"]["role"] == "transaction-graph-edge"

    tampered_reduction = dict(reduction_formal)
    tampered_reduction["after"] = dict(reduction_formal["after"])
    tampered_reduction["after"]["op"] = "vreduce_mul"
    mismatches = mismatches_with_provenance(
        verifier,
        reduction_formal,
        tampered_reduction,
        reduction_params["transaction.formal_provenance"],
    )
    assert mismatches[0]["diffs"][0]["path"] == "after.op"
    assert mismatches[0]["diffs"][0]["provenance"]["role"] == "reduction-source"

    incomplete_coverage = verifier.formal_provenance_coverage(binop_formal, {"domain": binop_provenance["domain"]})
    assert incomplete_coverage["status"] == "incomplete"
    assert "after.op" in incomplete_coverage["missing_paths"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
