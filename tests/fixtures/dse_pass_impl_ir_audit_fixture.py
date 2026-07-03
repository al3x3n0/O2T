#!/usr/bin/env python3
"""Regression fixture for DSE source facts aligned with implementation IR."""

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
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--ast-miner", type=Path, required=True)
    parser.add_argument("--ir-miner", type=Path, required=True)
    return parser.parse_args()


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")


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


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source = (args.repo / "tests" / "fixtures" / "dse_real_source_audit_snippet.cpp").resolve()
    compile_db_dir = args.work_dir / "compile-db"
    compile_db_dir.mkdir(parents=True, exist_ok=True)
    compile_db = compile_db_dir / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(args.repo.resolve()),
                    "command": f"{args.compiler} -std=c++17 {source}",
                    "file": str(source),
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out_dir = args.work_dir / "audit"
    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-run-pass-source-audit.py"),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(out_dir),
            "--ast-miner",
            str(args.ast_miner),
            "--mine-pass-impl-ir",
            "--ir-miner",
            str(args.ir_miner),
            "--pass-impl-ir-slice-window",
            "6",
            "--marker",
            "probe.dse.dead-store",
            "--marker",
            "probe.dse.overwritten-store",
            str(source),
        ]
    )

    findings = json.loads((out_dir / "findings.json").read_text(encoding="utf-8"))
    summary = json.loads((out_dir / "run-summary.json").read_text(encoding="utf-8"))
    manifest = load_jsonl(out_dir / "source-manifest.jsonl")
    assert len(findings) == 28
    assert manifest[0]["pass_impl_ir"] == "present"
    assert manifest[0]["pass_impl_ir_slice_matched"] == 28
    assert summary["pass_impl_ir"]["slice_status"] == {"matched": 28}
    assert summary["pass_impl_ir"]["intent_check_status"] == {
        "blocked": 12,
        "matched": 14,
        "source-incomplete": 2,
    }
    assert summary["pass_impl_ir"]["intent_check_family_status"] == {
        "dse": {"blocked": 12, "matched": 14, "source-incomplete": 2}
    }

    dead = by_source(findings, "isRemovable(&Store) && MSSA.getMemoryAccess")
    dead_check = dead["pass_impl_ir_intent_check"]
    assert dead_check["status"] == "matched"
    assert dead_check["intent_shape"] == "dse-analysis-facts"
    assert dead_check["required_analysis_facts"] == ["memoryssa.dead-store"]
    assert_evidence(dead_check, "memoryssa.dead-store")
    assert_evidence(dead_check, "alias.noalias")
    assert dead_check["rewrite_evidence"]

    overwrite = by_source(findings, "isOverwrite(&Store) && getLocForWrite")
    overwrite_check = overwrite["pass_impl_ir_intent_check"]
    assert overwrite_check["status"] == "matched"
    assert overwrite_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
        "memory.overwrite.full",
    ]
    assert_evidence(overwrite_check, "memoryssa.clobber")
    assert_evidence(overwrite_check, "memory.no-intervening-store")
    assert_evidence(overwrite_check, "memory.no-intervening-read")
    assert_evidence(overwrite_check, "memory.no-intervening-memory-effect")
    assert_evidence(overwrite_check, "memory.overwrite.size.known")
    assert_evidence(overwrite_check, "memory.overwrite.size.bounded-four-lane")
    assert_evidence(overwrite_check, "memory.overwrite.full")
    assert_evidence(overwrite_check, "alias.noalias")
    assert overwrite_check["rewrite_evidence"]

    volatile = by_source(findings, "Store.isVolatile() || Store.isAtomic()")
    volatile_check = volatile["pass_impl_ir_intent_check"]
    assert volatile_check["status"] == "blocked"
    assert volatile_check["expected_rewrite"] is False
    assert volatile_check["source_analysis_fact_blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.volatile-blocker",
        "memory.atomic-ordering-unknown-blocker",
    ]
    assert_evidence(volatile_check, "memory.volatile-atomic-blocker")
    assert_evidence(volatile_check, "memory.volatile-blocker")
    assert_evidence(volatile_check, "memory.atomic-ordering-unknown-blocker")

    volatile_only = by_source(findings, "\n        Store.isVolatile()")
    volatile_only_check = volatile_only["pass_impl_ir_intent_check"]
    assert volatile_only_check["status"] == "blocked"
    assert volatile_only_check["expected_rewrite"] is False
    assert volatile_only_check["source_analysis_fact_blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.volatile-blocker",
    ]
    assert_evidence(volatile_only_check, "memory.volatile-atomic-blocker")
    assert_evidence(volatile_only_check, "memory.volatile-blocker")

    unordered_atomic = by_source(findings, "AtomicOrdering::Unordered")
    unordered_atomic_check = unordered_atomic["pass_impl_ir_intent_check"]
    assert unordered_atomic_check["status"] == "blocked"
    assert unordered_atomic_check["expected_rewrite"] is False
    assert unordered_atomic_check["source_analysis_fact_blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.atomic-unordered-blocker",
    ]
    assert_evidence(unordered_atomic_check, "memory.volatile-atomic-blocker")
    assert_evidence(unordered_atomic_check, "memory.atomic-unordered-blocker")

    ordered_atomic = by_source(findings, "AtomicOrdering::SequentiallyConsistent")
    ordered_atomic_check = ordered_atomic["pass_impl_ir_intent_check"]
    assert ordered_atomic_check["status"] == "blocked"
    assert ordered_atomic_check["expected_rewrite"] is False
    assert ordered_atomic_check["source_analysis_fact_blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.atomic-ordered-blocker",
    ]
    assert_evidence(ordered_atomic_check, "memory.volatile-atomic-blocker")
    assert_evidence(ordered_atomic_check, "memory.atomic-ordered-blocker")

    unknown_atomic = by_source(findings, "unknownAtomicOrdering(&Store)")
    unknown_atomic_check = unknown_atomic["pass_impl_ir_intent_check"]
    assert unknown_atomic_check["status"] == "blocked"
    assert unknown_atomic_check["expected_rewrite"] is False
    assert unknown_atomic_check["source_analysis_fact_blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.atomic-ordering-unknown-blocker",
    ]
    assert_evidence(unknown_atomic_check, "memory.volatile-atomic-blocker")
    assert_evidence(unknown_atomic_check, "memory.atomic-ordering-unknown-blocker")

    ambiguous = by_source(findings, "isOverwrite(&Store) &&\n        getClobberingMemoryAccess")
    ambiguous_check = ambiguous["pass_impl_ir_intent_check"]
    assert ambiguous_check["status"] == "blocked"
    assert ambiguous_check["expected_rewrite"] is False
    assert ambiguous_check["source_analysis_fact_blockers"] == ["alias.unknown"]
    assert_evidence(ambiguous_check, "alias.unknown")
    partial = by_source(findings, "isPartialOverwrite(&Store, &KillingStore)")
    partial_check = partial["pass_impl_ir_intent_check"]
    assert partial_check["status"] == "blocked"
    assert partial_check["source_analysis_fact_blockers"] == ["memory.overwrite.partial"]
    assert_evidence(partial_check, "memory.overwrite.partial")

    def assert_fixed_partial_matched(
        source_text: str,
        size_bound: str = "memory.overwrite.size.bounded-four-lane",
    ) -> None:
        fixed_partial = by_source(findings, source_text)
        fixed_partial_check = fixed_partial["pass_impl_ir_intent_check"]
        assert fixed_partial_check["status"] == "matched"
        assert fixed_partial_check["required_analysis_facts"] == [
            "memoryssa.clobber",
            "memory.no-intervening-store",
            "memory.no-intervening-read",
            "memory.no-intervening-memory-effect",
            "memory.overwrite.size.known",
            size_bound,
            "memory.overwrite.partial.fixed-byte-mask",
        ]
        assert_evidence(fixed_partial_check, "memoryssa.clobber")
        assert_evidence(fixed_partial_check, "memory.no-intervening-store")
        assert_evidence(fixed_partial_check, "memory.no-intervening-read")
        assert_evidence(fixed_partial_check, "memory.no-intervening-memory-effect")
        assert_evidence(fixed_partial_check, "memory.overwrite.size.known")
        assert_evidence(fixed_partial_check, size_bound)
        assert_evidence(fixed_partial_check, "memory.overwrite.partial.fixed-byte-mask")
        assert fixed_partial_check["rewrite_evidence"]

    assert_fixed_partial_matched("fixedPartialOverwrite(&Store, &KillingStore)")
    assert_fixed_partial_matched("partialOverwriteByteMask(&Store, &KillingStore, 2, 2)")
    assert_fixed_partial_matched("knownPartialOverwriteByteMask(&Store, &KillingStore, 0x9)")
    assert_fixed_partial_matched("partialOverwriteByteMask(&Store, &KillingStore, 2, 1)")
    assert_fixed_partial_matched("partialOverwriteByteMask(&Store, &KillingStore, 0, 3)")
    assert_fixed_partial_matched("knownPartialOverwriteByteMask(&Store, &KillingStore, 0xd)")
    assert_fixed_partial_matched(
        "partialOverwriteByteMask(&Store, &KillingStore, 0, 1, 3)",
        "memory.overwrite.size.bounded-eight-lane",
    )
    assert_fixed_partial_matched(
        "knownPartialOverwriteByteMask(&Store, &KillingStore, 0x2a, 8)",
        "memory.overwrite.size.bounded-eight-lane",
    )

    full_mask_partial = by_source(findings, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0xf)")
    full_mask_partial_check = full_mask_partial["pass_impl_ir_intent_check"]
    assert full_mask_partial_check["status"] == "blocked"
    assert full_mask_partial_check["source_analysis_fact_blockers"] == [
        "memory.overwrite.partial.fixed-byte-mask.unsupported-mask"
    ]

    nonoverlap = by_source(findings, "nonOverlapping(&Store, &KillingStore)")
    nonoverlap_check = nonoverlap["pass_impl_ir_intent_check"]
    assert nonoverlap_check["status"] == "blocked"
    assert nonoverlap_check["source_analysis_fact_blockers"] == ["memory.overwrite.nonoverlap"]
    assert_evidence(nonoverlap_check, "memory.overwrite.nonoverlap")

    unknown_size = by_source(findings, "unknownSize(&Store)")
    unknown_size_check = unknown_size["pass_impl_ir_intent_check"]
    assert unknown_size_check["status"] == "blocked"
    assert unknown_size_check["source_analysis_fact_blockers"] == ["memory.overwrite.unknown-size"]
    assert_evidence(unknown_size_check, "memory.overwrite.unknown-size")

    symbolic_size = by_source(findings, "sameSize(&Store, &KillingStore)")
    symbolic_size_check = symbolic_size["pass_impl_ir_intent_check"]
    assert symbolic_size_check["status"] == "matched"
    assert symbolic_size_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.symbolic-bounded-eight-lane",
        "memory.overwrite.size.bounded-eight-lane",
        "memory.overwrite.full",
    ]
    assert_evidence(symbolic_size_check, "memory.overwrite.size.symbolic-bounded-eight-lane")
    assert_evidence(symbolic_size_check, "memory.overwrite.size.symbolic-equal")
    assert_evidence(symbolic_size_check, "memory.overwrite.size.symbolic-upper-bound")
    assert_evidence(symbolic_size_check, "memory.overwrite.size.bounded-eight-lane")
    assert_evidence(symbolic_size_check, "memory.overwrite.unknown-size")
    assert symbolic_size_check["rewrite_evidence"]

    symbolic_value_size = by_source(findings, "StoreSize.getValue() == KillingSize.getValue()")
    symbolic_value_size_check = symbolic_value_size["pass_impl_ir_intent_check"]
    assert symbolic_value_size_check["status"] == "matched"
    assert symbolic_value_size_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.symbolic-bounded-eight-lane",
        "memory.overwrite.size.bounded-eight-lane",
        "memory.overwrite.full",
    ]
    assert_evidence(symbolic_value_size_check, "memory.overwrite.size.symbolic-bounded-eight-lane")
    assert_evidence(symbolic_value_size_check, "memory.overwrite.size.symbolic-equal")
    assert_evidence(symbolic_value_size_check, "memory.overwrite.size.symbolic-upper-bound")
    assert_evidence(symbolic_value_size_check, "memory.overwrite.size.bounded-eight-lane")
    assert_evidence(symbolic_value_size_check, "memory.overwrite.unknown-size")
    assert symbolic_value_size_check["rewrite_evidence"]

    symbolic_four_size = by_source(findings, "StoreSize.getValue() <= 4")
    symbolic_four_size_check = symbolic_four_size["pass_impl_ir_intent_check"]
    assert symbolic_four_size_check["status"] == "matched"
    assert symbolic_four_size_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.symbolic-bounded-four-lane",
        "memory.overwrite.full",
    ]
    assert_evidence(symbolic_four_size_check, "memory.overwrite.size.symbolic-bounded-four-lane")
    assert_evidence(symbolic_four_size_check, "memory.overwrite.size.symbolic-equal")
    assert_evidence(symbolic_four_size_check, "memory.overwrite.size.symbolic-upper-bound")
    assert_evidence(symbolic_four_size_check, "memory.overwrite.unknown-size")
    assert symbolic_four_size_check["rewrite_evidence"]

    symbolic_too_wide = by_source(findings, "StoreSize.getValue() <= 16")
    symbolic_too_wide_check = symbolic_too_wide["pass_impl_ir_intent_check"]
    assert symbolic_too_wide_check["status"] == "blocked"
    assert symbolic_too_wide_check["source_analysis_fact_blockers"] == ["memory.overwrite.unknown-size"]
    assert_evidence(symbolic_too_wide_check, "memory.overwrite.size.symbolic-equal")
    assert_evidence(symbolic_too_wide_check, "memory.overwrite.size.symbolic-upper-bound")
    assert_evidence(symbolic_too_wide_check, "memory.overwrite.unknown-size")

    location_size = by_source(findings, "LocationSize::precise(8).getValue() <= 8")
    location_size_check = location_size["pass_impl_ir_intent_check"]
    assert location_size_check["status"] == "matched"
    assert location_size_check["required_analysis_facts"] == [
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-eight-lane",
        "memory.overwrite.full",
    ]
    assert_evidence(location_size_check, "memory.overwrite.size.known")
    assert_evidence(location_size_check, "memory.overwrite.size.bounded-eight-lane")
    assert location_size_check["rewrite_evidence"]

    intervening_read = by_source(findings, "noInterveningStore(&Store, &KillingStore) &&\n        fullyOverwrites")
    intervening_read_check = intervening_read["pass_impl_ir_intent_check"]
    assert intervening_read_check["status"] == "source-incomplete"
    assert intervening_read_check["missing_source_analysis_facts"] == [
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
    ]

    unknown_effect = by_source(findings, "mayReadOrWriteMemory(&Call)")
    unknown_effect_check = unknown_effect["pass_impl_ir_intent_check"]
    assert unknown_effect_check["status"] == "blocked"
    assert unknown_effect_check["source_analysis_fact_blockers"] == ["memory.unknown-intervening-effect"]
    assert_evidence(unknown_effect_check, "memory.unknown-intervening-effect")

    missing_size_checks = [
        finding["pass_impl_ir_intent_check"]
        for finding in findings
        if finding["pass_impl_ir_intent_check"]["status"] == "source-incomplete"
        and finding["pass_impl_ir_intent_check"]["missing_source_analysis_facts"]
        == [
            "memory.overwrite.size.known",
            "memory.overwrite.size.bounded-four-lane",
        ]
    ]
    assert len(missing_size_checks) == 1
    missing_size_check = missing_size_checks[0]
    assert missing_size_check["status"] == "source-incomplete"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
