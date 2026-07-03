#!/usr/bin/env python3
"""Verify source program graph contract gates graph-derived transaction formalization."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ast-miner", type=Path, required=True)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")
    return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        record
        for record in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if isinstance(record, dict)
    ]


def infer(repo: Path, findings: Path, out: Path) -> list[dict[str, Any]]:
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-infer-optimization-intent.py"),
            "--findings",
            str(findings),
            "--format",
            "jsonl",
            "--out",
            str(out),
            "--require-marker",
            "probe.slp.vectorize-binop",
        ]
    )
    return load_jsonl(out)


def slp_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("marker") == "probe.slp.vectorize-binop":
            return record
    raise AssertionError("missing probe.slp.vectorize-binop record")


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source = args.repo / "tests" / "fixtures" / "slp_transaction_helper_pack_snippet.cpp"
    findings_path = args.work_dir / "findings.json"
    with findings_path.open("w", encoding="utf-8") as output:
        result = run(
            [
                str(args.ast_miner),
                "--registry",
                str(args.repo / "constraints" / "pass_constraints.json"),
                "--require-marker",
                "probe.slp.vectorize-binop",
                str(source),
                "--",
                "-std=c++17",
            ]
        )
        output.write(result.stdout)

    clean = slp_record(infer(args.repo, findings_path, args.work_dir / "clean-intent.jsonl"))
    clean_evidence = clean["evidence"]
    clean_params = clean_evidence["formal_parameters"]
    assert clean_evidence["formal_inference"] == "source-derived-transaction"
    assert clean_evidence["transaction_lowering"] == "formal-ir"
    assert clean_params["source_program_graph_contract.status"] == "passed"
    assert clean_params["source_program_graph_contract.interprocedural_dfg"] is True
    assert clean_params["source_program_graph_contract.dfg_edges"] > 0
    assert clean["intent_candidate"]["formal"]["domain"] == "vector-bv32x4"

    tampered_records = json.loads(findings_path.read_text(encoding="utf-8"))
    tx = tampered_records[0]["optimization_transaction"]
    graph = tx["source_program_graph"]
    graph["dfg_edges"] = [
        edge for edge in graph["dfg_edges"] if edge.get("kind") != "interproc-return"
    ]
    tampered_findings = args.work_dir / "tampered-findings.json"
    tampered_findings.write_text(json.dumps(tampered_records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tampered = slp_record(infer(args.repo, tampered_findings, args.work_dir / "tampered-intent.jsonl"))
    tampered_evidence = tampered["evidence"]
    tampered_params = tampered_evidence["formal_parameters"]
    assert tampered_evidence.get("formal_inference") != "source-derived-transaction"
    assert tampered_evidence["transaction_lowering"] == "fallback"
    assert "formal" not in tampered["intent_candidate"]
    assert tampered_params["source_program_graph_contract.status"] == "failed"
    assert "source-graph:interprocedural-dfg" in tampered_params[
        "source_program_graph_contract.failed_checks"
    ]
    assert tampered_params["source_program_graph_contract.failure_reasons"] == {
        "missing-interprocedural-dfg-edges": 1
    }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
