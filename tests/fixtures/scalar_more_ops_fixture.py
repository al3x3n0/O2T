#!/usr/bin/env python3
"""End-to-end fixture for additional scalar InstCombine ops."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


MARKERS = [
    "probe.instcombine.sub-zero",
    "probe.instcombine.or-zero",
    "probe.instcombine.and-allones",
    "probe.instcombine.and-self",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ast-miner", type=Path, required=True)
    parser.add_argument("--z3", required=True)
    return parser.parse_args()


def run(command: list[str], stdout: Path | None = None) -> None:
    stdout_handle = stdout.open("w", encoding="utf-8") if stdout is not None else subprocess.PIPE
    try:
        result = subprocess.run(command, check=False, text=True, stdout=stdout_handle, stderr=subprocess.PIPE)
    finally:
        if stdout is not None:
            stdout_handle.close()
    if result.returncode != 0:
        if stdout is None:
            print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source = args.repo / "tests" / "fixtures" / "scalar_more_ops_snippet.cpp"
    findings_path = args.work_dir / "findings.json"
    candidates_path = args.work_dir / "candidates.jsonl"
    validated_path = args.work_dir / "validated.jsonl"

    run(
        [
            str(args.ast_miner),
            "--registry",
            str(args.repo / "constraints" / "pass_constraints.json"),
            "--llvm-idioms",
            str(args.repo / "constraints" / "llvm_idioms.json"),
            *[item for marker in MARKERS for item in ("--require-marker", marker)],
            str(source),
            "--",
            "-std=c++17",
        ],
        stdout=findings_path,
    )
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert {finding["marker"] for finding in findings} == set(MARKERS)
    assert all(finding.get("semantic_facts", {}).get("shape") == "scalar" for finding in findings)

    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-infer-optimization-intent.py"),
            "--findings",
            str(findings_path),
            "--format",
            "jsonl",
            "--out",
            str(candidates_path),
            *[item for marker in MARKERS for item in ("--require-marker", marker)],
        ]
    )
    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-validate-intent-candidates.py"),
            "--z3",
            args.z3,
            "--input",
            str(candidates_path),
            "--out",
            str(validated_path),
        ]
    )
    records = [json.loads(line) for line in validated_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {record["marker"] for record in records} == set(MARKERS)
    assert all(record["proof_status"] == "proved" for record in records)

    configs = {
        "probe.instcombine.sub-zero": ["arith_opcode=1", "rhs_mode=0"],
        "probe.instcombine.or-zero": ["arith_opcode=4", "rhs_mode=0"],
        "probe.instcombine.and-allones": ["arith_opcode=5", "rhs_mode=3", "const_a=-1"],
        "probe.instcombine.and-self": ["extra_opcode=5"],
    }
    for marker, lines in configs.items():
        cfg = args.work_dir / (marker.replace("probe.", "").replace(".", "_").replace("-", "_") + ".cfg")
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = args.work_dir / (cfg.stem + ".formal.txt")
        run(
            [
                sys.executable,
                str(args.repo / "tools" / "cv-formal-check-config.py"),
                "--z3",
                args.z3,
                "--config",
                str(cfg),
                "--marker",
                marker,
            ],
            stdout=out,
        )
        assert f"formal_status=proved marker={marker}" in out.read_text(encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
