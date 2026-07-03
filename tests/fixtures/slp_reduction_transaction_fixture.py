#!/usr/bin/env python3
"""Exercise SLP reduction transaction inference and validation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MARKER = "probe.slp.vectorize-reduction"
REDUCE_CALLS = {
    "add": "CreateAddReduce",
    "mul": "CreateMulReduce",
    "and": "CreateAndReduce",
    "or": "CreateOrReduce",
    "xor": "CreateXorReduce",
    "smin": "CreateSMinReduce",
    "smax": "CreateSMaxReduce",
    "umin": "CreateUMinReduce",
    "umax": "CreateUMaxReduce",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{cmd} returned {result.returncode}")
    return result


def lane_mapping() -> dict[str, Any]:
    return {
        "kind": "permutation",
        "lanes": 4,
        "map": [2, 0, 3, 1],
        "inverse_map": [1, 3, 0, 2],
        "source": {"kind": "explicit-lane-map", "line": 1, "source": "ReorderMask"},
    }


def finding(repo: Path, opcode: str) -> dict[str, Any]:
    mapping = lane_mapping()
    reduce_call = REDUCE_CALLS[opcode]
    return {
        "file": str(repo / "tests" / "fixtures" / "slp_reduction_transaction_snippet.cpp"),
        "line": 21,
        "marker": MARKER,
        "pass": "slp-vectorizer",
        "predicate_kind": "transaction",
        "predicate_source": "canVectorize(Entry)",
        "rewrite_source": f"emit vector {opcode} reduction and replace scalar result",
        "matched_pattern": "slp-vectorize-reduction-transaction",
        "optimization_transaction": {
            "model": "optimization-transaction-v1",
            "kind": "slp-vectorize-reduction",
            "opcode": opcode,
            "reduction_opcode": opcode,
            "lanes": 4,
            "reduction_lanes": 4,
            "functions": ["vectorizeReduction"],
            "role_provenance": [
                {"role": "candidate-tree", "function": "vectorizeReduction", "line": 10, "source": "TreeEntry"},
                {"role": "legality", "function": "vectorizeReduction", "line": 11, "source": "canVectorize"},
                {"role": "vector-emission", "function": "vectorizeReduction", "line": 14, "opcode": opcode, "source": reduce_call},
                {"role": "scalar-replacement", "function": "vectorizeReduction", "line": 15, "source": "replaceScalarUses"},
            ],
            "opcode_sources": [
                {"role": "vector-emission", "function": "vectorizeReduction", "line": 14, "opcode": opcode, "source": reduce_call}
            ],
            "lane_source": {"kind": "tree-entry-scalars", "lanes": 4, "line": 5, "source": "Scalars[4]"},
            "lane_mapping": mapping,
            "operand_lane_mappings": {
                "lhs": {
                    **mapping,
                    "pack_source": {"kind": "direct-pack-operand", "line": 12, "source": "packOperand(Entry, 0)", "operand_index": 0},
                }
            },
            "result_lane_mapping": {},
            "scalar_lane_pairs": [],
            "reduction_sources": [{"line": 14, "source": reduce_call}],
            "reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
            "consistency": "ok",
            "consistency_errors": [],
            "legality": {"valid_element_type": True},
            "profitability": {"cost_model": True},
            "actions": [
                {"kind": "pack-scalars", "source": "TreeEntry.Scalars"},
                {"kind": "emit-vector-reduction", "opcode": opcode},
                {"kind": "replace-scalar-uses"},
            ],
            "preserves": "scalar reduction result",
        },
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    findings = args.work_dir / "findings.json"
    candidates = args.work_dir / "candidates.jsonl"
    validated = args.work_dir / "validated.jsonl"
    opcodes = ("add", "mul", "and", "or", "xor", "smin", "smax", "umin", "umax")
    write_json(findings, [finding(args.repo, opcode) for opcode in opcodes])
    run([
        sys.executable,
        str(args.repo / "tools" / "cv-infer-optimization-intent.py"),
        "--findings",
        str(findings),
        "--format",
        "jsonl",
        "--out",
        str(candidates),
    ])
    run([
        sys.executable,
        str(args.repo / "tools" / "cv-validate-intent-candidates.py"),
        "--z3",
        args.z3,
        "--input",
        str(candidates),
        "--out",
        str(validated),
    ])
    records = [json.loads(line) for line in validated.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {record["evidence"]["formal_parameters"]["transaction.opcode"] for record in records} == set(opcodes)
    assert all(record["proof_status"] == "proved" for record in records)
    assert all(record["promotion_status"] == "ready" for record in records)
    assert all(record["evidence"]["formal_parameters"]["transaction.kind"] == "slp-vectorize-reduction" for record in records)
    assert all(record["evidence"]["formal_parameters"]["transaction.reduction_lanes"] == 4 for record in records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
