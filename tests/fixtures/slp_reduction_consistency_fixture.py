#!/usr/bin/env python3
"""Exercise malformed SLP reduction transaction fallback behavior."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MARKER = "probe.slp.vectorize-reduction"


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def base_transaction(error: str) -> dict[str, Any]:
    opcode = "smin" if error == "reduction-opcode-mismatch:smax!=smin" else "add"
    tx = {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-reduction",
        "opcode": opcode,
        "reduction_opcode": opcode,
        "lanes": 4,
        "reduction_lanes": 4,
        "lane_mapping": {"kind": "identity", "lanes": 4, "map": [0, 1, 2, 3], "inverse_map": [0, 1, 2, 3]},
        "operand_lane_mappings": {
            "lhs": {"kind": "identity", "lanes": 4, "map": [0, 1, 2, 3], "inverse_map": [0, 1, 2, 3]}
        },
        "result_lane_mapping": {},
        "scalar_lane_pairs": [],
        "reduction_sources": [{"line": 23, "source": "Builder.CreateAddReduce(LHS)"}],
        "reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
        "consistency": "failed",
        "consistency_errors": [error],
        "actions": [{"kind": "pack-scalars"}, {"kind": "emit-vector-reduction"}, {"kind": "replace-scalar-uses"}],
    }
    if error == "reduction-opcode-mismatch:mul!=add":
        tx["reduction_opcode"] = "mul"
    elif error == "reduction-opcode-mismatch:smax!=smin":
        tx["reduction_opcode"] = "smax"
        tx["reduction_sources"] = [{"line": 23, "source": "Builder.CreateSMaxReduce(LHS)"}]
    elif error == "reduction-lane-count-mismatch:8!=4":
        tx["reduction_lanes"] = 8
    elif error == "missing-reduction-source":
        tx["reduction_sources"] = []
    elif error == "missing-reduction-result":
        tx.pop("reduction_result")
    elif error == "incomplete-pack-builder":
        tx["operand_lane_mappings"]["lhs"]["pack_builder"] = {"status": "incomplete"}
    return tx


def finding(repo: Path, error: str, line: int) -> dict[str, Any]:
    return {
        "file": str(repo / "tests" / "fixtures" / "slp_reduction_transaction_snippet.cpp"),
        "line": line,
        "marker": MARKER,
        "pass": "slp-vectorizer",
        "predicate_kind": "transaction",
        "predicate_source": "canVectorize(Entry)",
        "rewrite_source": "emit vector add reduction and replace scalar result",
        "matched_pattern": "slp-vectorize-reduction-transaction",
        "optimization_transaction": base_transaction(error),
    }


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    errors = [
        "reduction-opcode-mismatch:mul!=add",
        "reduction-opcode-mismatch:smax!=smin",
        "reduction-lane-count-mismatch:8!=4",
        "missing-reduction-source",
        "missing-reduction-result",
        "incomplete-pack-builder",
    ]
    findings = args.work_dir / "findings.json"
    candidates = args.work_dir / "candidates.jsonl"
    validated = args.work_dir / "validated.jsonl"
    write_json(findings, [finding(args.repo, error, 21) for error in errors])
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
    candidates_records = [json.loads(line) for line in candidates.read_text(encoding="utf-8").splitlines() if line]
    assert len(candidates_records) == len(errors)
    for record, error in zip(candidates_records, errors, strict=True):
        assert "formal" not in record["intent_candidate"]
        evidence = record["evidence"]
        assert evidence["transaction_lowering"] == "fallback"
        assert evidence["formal_parameters"]["transaction.consistency"] == "failed"
        assert error in evidence["formal_parameters"]["transaction.consistency_errors"]
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
    validated_records = [json.loads(line) for line in validated.read_text(encoding="utf-8").splitlines() if line]
    assert all(record["proof_status"] == "unsupported" for record in validated_records)
    assert all(record["promotion_status"] == "blocked" for record in validated_records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
