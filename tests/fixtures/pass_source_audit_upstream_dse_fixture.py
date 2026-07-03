#!/usr/bin/env python3
"""Upstream-shaped DSE source audit fixture."""

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
    parser.add_argument("--ir-miner", type=Path, required=True)
    parser.add_argument("--compiler", required=True)
    return parser.parse_args()


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_source(records: list[dict[str, Any]], text: str) -> dict[str, Any]:
    for record in records:
        source = str(record.get("predicate_source") or record.get("source") or "")
        if text in source:
            return record
    raise AssertionError(f"missing source containing {text}")


def assert_evidence(check: dict[str, Any], kind: str) -> None:
    evidence = check.get("analysis_fact_impl_ir_evidence")
    assert isinstance(evidence, dict)
    assert evidence.get(kind), f"missing implementation IR evidence for {kind}"


def fact_by_kind(record: dict[str, Any], kind: str) -> dict[str, Any]:
    for fact in record.get("analysis_facts", []):
        if isinstance(fact, dict) and fact.get("kind") == kind:
            return fact
    raise AssertionError(f"missing analysis fact {kind}")


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source = (args.repo / "tests" / "fixtures" / "upstream_dse_like_pass.cpp").resolve()
    compile_db = args.work_dir / "compile-db" / "compile_commands.json"
    compile_db.parent.mkdir(parents=True, exist_ok=True)
    compile_db.write_text(
        json.dumps([
            {
                "directory": str(args.repo.resolve()),
                "command": f"{args.compiler} -std=c++17 {source}",
                "file": str(source),
            }
        ]),
        encoding="utf-8",
    )

    out = args.work_dir / "audit"
    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-run-pass-source-audit.py"),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--mine-pass-impl-ir",
            "--pass-impl-ir-slice-window",
            "8",
            "--marker",
            "probe.dse.dead-store",
            "--marker",
            "probe.dse.overwritten-store",
            str(source),
        ]
    )

    findings = load_json(out / "findings.json")
    summary = load_json(out / "run-summary.json")
    manifest = load_jsonl(out / "source-manifest.jsonl")
    readiness = load_json(out / "real-pass-readiness.json")

    assert len(findings) == 7
    assert manifest[0]["pass_impl_ir"] == "present"
    assert manifest[0]["pass_impl_ir_slice_matched"] == 7
    assert summary["pass_impl_ir"]["slice_status"] == {"matched": 7}
    assert summary["pass_impl_ir"]["intent_check_family_status"] == {
        "dse": {"blocked": 3, "matched": 3, "source-incomplete": 1}
    }
    assert summary["pass_impl_ir"]["intent_check_status"] == {
        "blocked": 3,
        "matched": 3,
        "source-incomplete": 1,
    }
    assert readiness["model"] == "o2t-real-pass-readiness-v1"

    dead = by_source(findings, "isRemovable(&DeadInst) && MSSA.getMemoryAccess")
    dead_check = dead["pass_impl_ir_intent_check"]
    assert dead_check["status"] == "matched"
    assert_evidence(dead_check, "memoryssa.dead-store")
    assert_evidence(dead_check, "alias.noalias")
    assert dead_check["rewrite_evidence"]

    full = by_source(findings, "fullyOverwrites(&DeadInst, &KillingI)")
    full_check = full["pass_impl_ir_intent_check"]
    assert full_check["status"] == "matched"
    assert full_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
        "memory.overwrite.full",
    ]
    for kind in full_check["required_analysis_facts"]:
        assert_evidence(full_check, kind)
    assert_evidence(full_check, "alias.noalias")
    assert full_check["rewrite_evidence"]

    partial = by_source(findings, "partialOverwriteByteMask(&DeadInst, &KillingI, 2, 2)")
    partial_check = partial["pass_impl_ir_intent_check"]
    assert partial_check["status"] == "matched"
    assert fact_by_kind(partial, "memory.overwrite.partial.fixed-byte-mask")["byte_mask"] == "lanes-2-3-of-4"
    assert partial_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
        "memory.overwrite.partial.fixed-byte-mask",
    ]
    for kind in partial_check["required_analysis_facts"]:
        assert_evidence(partial_check, kind)
    assert partial_check["rewrite_evidence"]

    unknown_effect = by_source(findings, "mayReadOrWriteMemory(&MaybeCall)")
    unknown_effect_check = unknown_effect["pass_impl_ir_intent_check"]
    assert unknown_effect_check["status"] == "blocked"
    assert unknown_effect_check["source_analysis_fact_blockers"] == ["memory.unknown-intervening-effect"]
    assert_evidence(unknown_effect_check, "memory.unknown-intervening-effect")

    unknown_size = by_source(findings, "unknownSize(&DeadInst)")
    unknown_size_check = unknown_size["pass_impl_ir_intent_check"]
    assert unknown_size_check["status"] == "blocked"
    assert unknown_size_check["source_analysis_fact_blockers"] == ["memory.overwrite.unknown-size"]
    assert_evidence(unknown_size_check, "memory.overwrite.unknown-size")

    nonoverlap = by_source(findings, "nonOverlapping(&DeadInst, &KillingI)")
    nonoverlap_check = nonoverlap["pass_impl_ir_intent_check"]
    assert nonoverlap_check["status"] == "blocked"
    assert nonoverlap_check["source_analysis_fact_blockers"] == ["memory.overwrite.nonoverlap"]
    assert_evidence(nonoverlap_check, "memory.overwrite.nonoverlap")

    missing_size_checks = [
        finding["pass_impl_ir_intent_check"]
        for finding in findings
        if finding["pass_impl_ir_intent_check"]["status"] == "source-incomplete"
    ]
    assert len(missing_size_checks) == 1
    missing_size_check = missing_size_checks[0]
    assert missing_size_check["status"] == "source-incomplete"
    assert missing_size_check["missing_source_analysis_facts"] == [
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
    ]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
