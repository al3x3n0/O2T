#!/usr/bin/env python3
"""Validate AST-backed scalable floating-point reduction formalization."""

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


def mine_case(repo: Path, work: Path, miner: Path, stem: str, fixture: str) -> Path:
    findings = work / f"{stem}-findings.json"
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
    return findings


def infer_case(repo: Path, work: Path, findings: Path, stem: str, fmt: str = "jsonl") -> Path:
    inferred = work / f"{stem}.{'json' if fmt == 'json' else 'jsonl'}"
    run([
        sys.executable,
        str(repo / "tools/cv-infer-optimization-intent.py"),
        "--findings",
        str(findings),
        "--format",
        fmt,
        "--min-confidence",
        "high",
        "--out",
        str(inferred),
        "--require-marker",
        MARKER,
    ])
    return inferred


def validate_case(repo: Path, work: Path, z3: str, inferred: Path, stem: str, emit_smt: bool = False) -> Path:
    validated = work / f"{stem}-validated.jsonl"
    cmd = [
        sys.executable,
        str(repo / "tools/cv-validate-intent-candidates.py"),
        "--z3",
        str(z3),
        "--input",
        str(inferred),
        "--out",
        str(validated),
    ]
    if emit_smt:
        cmd.extend(["--emit-smt", str(work / f"{stem}-smt")])
    run(cmd)
    return validated


def verify_formalization(repo: Path, validated: Path, work: Path, stem: str) -> dict[str, Any]:
    formalization = work / f"{stem}-formalization.json"
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
    return json.loads(formalization.read_text(encoding="utf-8"))


def formal_validation_case(
    repo: Path,
    work: Path,
    miner: Path,
    z3: str,
    stem: str,
    fixture: str,
    expected_op: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    findings = mine_case(repo, work, miner, stem, fixture)
    inferred = infer_case(repo, work, findings, stem)
    validated = validate_case(repo, work, z3, inferred, stem, emit_smt=True)
    formalization = verify_formalization(repo, validated, work, stem)
    record = load_first_json(findings)
    validated_record = load_first_jsonl(validated)
    tx = record["optimization_transaction"]
    params = validated_record["evidence"]["formal_parameters"]
    formal = validated_record["intent_candidate"]["formal"]
    assert tx["scalable"] is True
    assert tx["consistency"] == "ok"
    assert tx["unsupported_reduction_reasons"] == []
    assert validated_record["proof_status"] == "proved"
    assert validated_record["promotion_status"] == "ready"
    assert validated_record["evidence"]["transaction_lowering"] == "formal-ir"
    assert [item["vscale"] for item in validated_record["proof_instances"]] == [1, 2, 4]
    assert formal["domain"] == "scalable-scalar-fp32"
    assert formal["after"]["op"] == expected_op
    assert params["transaction.scalable"] is True
    assert params["transaction.fp_semantics"] == "ordered-fp32"
    assert params["transaction.fp_rounding"] == "rne"
    assert formalization["summary"]["status"] == {"passed": 1}
    assert formalization["summary"]["provenance_coverage"] == {"passed": 1}
    return record, validated_record, formalization


def policy_validation_case(
    repo: Path,
    work: Path,
    miner: Path,
    z3: str,
    stem: str,
    fixture: str,
    semantics: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    findings = mine_case(repo, work, miner, stem, fixture)
    inferred = infer_case(repo, work, findings, stem)
    validated = validate_case(repo, work, z3, inferred, stem)
    record = load_first_json(findings)
    validated_record = load_first_jsonl(validated)
    tx = record["optimization_transaction"]
    policy = validated_record["intent_candidate"]["relaxed_fp_policy"]
    params = validated_record["evidence"]["formal_parameters"]
    assert tx["scalable"] is True
    assert tx["consistency"] == "ok"
    assert tx["unsupported_reduction_reasons"] == []
    assert tx["fp_policy"]["semantics"] == semantics
    assert validated_record["proof_status"] == "proved"
    assert validated_record["proof_result"] == "policy-contract"
    assert validated_record["promotion_status"] == "ready"
    assert validated_record["evidence"]["transaction_lowering"] == "relaxed-fp-policy"
    assert policy["semantics"] == semantics
    assert policy["scalable"] is True
    assert policy["base_lanes"] == 4
    assert policy["vscale_values"] == [1, 2, 4]
    assert params["transaction.fp_policy.semantics"] == semantics
    assert params["transaction.fp_policy"]["scalable"] is True
    return record, validated_record


def assert_permutation_without_policy(repo: Path, work: Path, miner: Path) -> None:
    findings = mine_case(
        repo,
        work,
        miner,
        "scalable-fp-permutation",
        "tests/fixtures/slp_scalable_fp_reduction_permutation_transaction_snippet.cpp",
    )
    inferred = infer_case(repo, work, findings, "scalable-fp-permutation", fmt="json")
    record = load_first_json(findings)
    candidate = load_first_json(inferred)
    tx = record["optimization_transaction"]
    assert tx["scalable"] is True
    assert tx["consistency"] == "failed"
    assert "unsupported-reduction-fp-permutation" in tx["unsupported_reduction_reasons"]
    assert "formal" not in candidate["intent_candidate"]
    assert candidate["evidence"]["transaction_lowering"] == "fallback"
    assert "unsupported-reduction-fp-permutation" in candidate["evidence"]["formal_parameters"]["transaction.unsupported_reduction_reasons"]


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    formal_validation_case(
        args.repo,
        args.work_dir,
        args.ast_miner,
        args.z3,
        "scalable-fp-fadd",
        "tests/fixtures/slp_scalable_fp_reduction_transaction_snippet.cpp",
        "svfpreduce_add",
    )
    formal_validation_case(
        args.repo,
        args.work_dir,
        args.ast_miner,
        args.z3,
        "scalable-fp-fmul",
        "tests/fixtures/slp_scalable_fmul_reduction_transaction_snippet.cpp",
        "svfpreduce_mul",
    )
    assert_permutation_without_policy(args.repo, args.work_dir, args.ast_miner)
    policy_validation_case(
        args.repo,
        args.work_dir,
        args.ast_miner,
        args.z3,
        "scalable-fp-reassoc",
        "tests/fixtures/slp_scalable_fp_reduction_permutation_reassoc_transaction_snippet.cpp",
        "relaxed-reassoc",
    )
    policy_validation_case(
        args.repo,
        args.work_dir,
        args.ast_miner,
        args.z3,
        "scalable-fp-fast",
        "tests/fixtures/slp_scalable_fp_reduction_fast_transaction_snippet.cpp",
        "fast-math-fp-reduction",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
