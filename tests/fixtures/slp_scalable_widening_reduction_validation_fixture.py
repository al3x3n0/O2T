#!/usr/bin/env python3
"""Validate AST-backed scalable widening reduction formalization."""

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
    parser.add_argument("--ast-miner", type=Path, required=True)
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def run(cmd: list[str], stdout: Path | None = None) -> None:
    if stdout is None:
        subprocess.run(cmd, check=True)
        return
    with stdout.open("w", encoding="utf-8") as handle:
        subprocess.run(cmd, check=True, stdout=handle)


def load_first_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))[0]


def load_first_jsonl(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def contains_op(value: Any, op: str) -> bool:
    if isinstance(value, dict):
        if value.get("op") == op:
            return True
        return any(contains_op(child, op) for child in value.get("args", []))
    if isinstance(value, list):
        return any(contains_op(child, op) for child in value)
    return False


def prove_case(
    repo: Path,
    work: Path,
    miner: Path,
    z3: str,
    stem: str,
    fixture: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    findings = work / f"{stem}-findings.json"
    inferred = work / f"{stem}.jsonl"
    validated = work / f"{stem}-validated.jsonl"
    formalization = work / f"{stem}-formalization.json"
    run([
        str(miner),
        "--registry",
        str(repo / "constraints/pass_constraints.json"),
        "--require-marker",
        MARKER,
        str(repo / fixture),
        "--",
        "-std=c++17",
    ], findings)
    run([
        sys.executable,
        str(repo / "tools/cv-infer-optimization-intent.py"),
        "--findings",
        str(findings),
        "--format",
        "jsonl",
        "--min-confidence",
        "high",
        "--out",
        str(inferred),
        "--require-marker",
        MARKER,
    ])
    run([
        sys.executable,
        str(repo / "tools/cv-validate-intent-candidates.py"),
        "--z3",
        str(z3),
        "--input",
        str(inferred),
        "--out",
        str(validated),
    ])
    run([
        sys.executable,
        str(repo / "tools/cv-verify-transaction-formalization.py"),
        "--input",
        str(validated),
        "--out",
        str(formalization),
        "--require-clean",
        "--require-provenance-complete",
    ])
    return load_first_json(findings), load_first_jsonl(validated), json.loads(formalization.read_text(encoding="utf-8"))


def assert_positive_case(
    record: dict[str, Any],
    validated: dict[str, Any],
    formalization: dict[str, Any],
    *,
    input_bits: int,
    accumulator_bits: int,
    result_bits: int,
    extend_kind: str,
    lane_map: list[int] | None = None,
    required_width_kinds: set[str] | None = None,
) -> None:
    tx = record["optimization_transaction"]
    params = validated["evidence"]["formal_parameters"]
    formal = validated["intent_candidate"]["formal"]
    provenance = params["transaction.reduction_width_provenance"]
    provenance_kinds = {item["kind"] for item in provenance if isinstance(item, dict)}
    assert tx["scalable"] is True
    assert tx["consistency"] == "ok"
    assert tx["unsupported_reduction_reasons"] == []
    assert validated["proof_status"] == "proved"
    assert validated["promotion_status"] == "ready"
    assert validated["evidence"]["transaction_lowering"] == "formal-ir"
    assert [item["vscale"] for item in validated["proof_instances"]] == [1, 2, 4]
    assert formal["domain"] == "scalable-scalar-bv32"
    assert formal["base_lanes"] == 4
    assert params["transaction.scalable"] is True
    assert params["transaction.reduction_width_status"] == "complete"
    assert params["transaction.reduction_input_bits"] == input_bits
    assert params["transaction.reduction_accumulator_bits"] == accumulator_bits
    assert params["transaction.reduction_result_bits"] == result_bits
    assert params["transaction.reduction_extend_kind"] == extend_kind
    assert contains_op(formal["after"], "svreduce_add")
    if accumulator_bits != input_bits:
        expected_extend = "vzext" if extend_kind == "zext" else "vsext"
        assert contains_op(formal["after"], expected_extend)
    if result_bits != accumulator_bits:
        assert contains_op(formal["after"], "trunc")
    if lane_map is not None:
        assert params["transaction.lane_mapping.map"] == lane_map
        assert params["transaction.scalable_lane_mapping"]["map"] == lane_map
        assert contains_op(formal["after"], "svshuffle")
    if required_width_kinds:
        assert required_width_kinds <= provenance_kinds
    assert formalization["summary"]["status"] == {"passed": 1}
    assert formalization["summary"]["provenance_coverage"] == {"passed": 1}


def assert_ambiguous_case(repo: Path, work: Path, miner: Path) -> None:
    findings = work / "scalable-widening-ambiguous-findings.json"
    run([
        str(miner),
        "--registry",
        str(repo / "constraints/pass_constraints.json"),
        "--require-marker",
        MARKER,
        str(repo / "tests/fixtures/slp_scalable_widening_ambiguous_reduction_transaction_snippet.cpp"),
        "--",
        "-std=c++17",
    ], findings)
    tx = load_first_json(findings)["optimization_transaction"]
    assert tx["scalable"] is True
    assert tx["consistency"] == "failed"
    assert tx["reduction_width_status"] == "ambiguous"
    assert "unsupported-scalable-widening-reduction" in tx["unsupported_reduction_reasons"]


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        (
            "scalable-widening-zext",
            "tests/fixtures/slp_scalable_widening_reduction_transaction_snippet.cpp",
            {"input_bits": 16, "accumulator_bits": 32, "result_bits": 32, "extend_kind": "zext"},
        ),
        (
            "scalable-widening-sext",
            "tests/fixtures/slp_scalable_sext_reduction_transaction_snippet.cpp",
            {"input_bits": 16, "accumulator_bits": 32, "result_bits": 32, "extend_kind": "sext"},
        ),
        (
            "scalable-widening-trunc",
            "tests/fixtures/slp_scalable_widening_trunc_reduction_transaction_snippet.cpp",
            {"input_bits": 16, "accumulator_bits": 32, "result_bits": 16, "extend_kind": "zext"},
        ),
        (
            "scalable-widening-permutation",
            "tests/fixtures/slp_scalable_widening_permutation_reduction_transaction_snippet.cpp",
            {
                "input_bits": 16,
                "accumulator_bits": 32,
                "result_bits": 32,
                "extend_kind": "zext",
                "lane_map": [2, 0, 3, 1],
            },
        ),
        (
            "scalable-widening-alias",
            "tests/fixtures/slp_scalable_widening_alias_reduction_transaction_snippet.cpp",
            {
                "input_bits": 16,
                "accumulator_bits": 32,
                "result_bits": 32,
                "extend_kind": "zext",
                "required_width_kinds": {"type-alias-width", "extension-target-width"},
            },
        ),
    ]
    for stem, fixture, expected in cases:
        record, validated, formalization = prove_case(args.repo, args.work_dir, args.ast_miner, args.z3, stem, fixture)
        assert_positive_case(record, validated, formalization, **expected)
    assert_ambiguous_case(args.repo, args.work_dir, args.ast_miner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
