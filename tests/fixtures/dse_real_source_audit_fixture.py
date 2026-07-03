#!/usr/bin/env python3
"""Audit DSE source-fact mining on LLVM-like pass source."""

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
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--reducer", type=Path, required=True)
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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def fact_kinds(record: dict[str, Any]) -> set[str]:
    return {
        str(fact.get("kind") or "")
        for fact in record.get("analysis_facts", [])
        if isinstance(fact, dict)
    }


def by_source(records: list[dict[str, Any]], text: str) -> dict[str, Any]:
    for record in records:
        source = str(record.get("predicate_source") or record.get("source") or "")
        if text in source:
            return record
    raise AssertionError(f"missing source containing {text}")


def write_audit_input(candidates: list[dict[str, Any]], path: Path) -> None:
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        record = dict(candidate)
        params = record.get("evidence", {}).get("formal_parameters", {})
        if params.get("dse.analysis_facts.complete") is True:
            record["proof_status"] = "proved"
            record["proof_result"] = "unsat"
            record["promotion_status"] = "ready"
        else:
            record["proof_status"] = "unsupported"
            record["proof_result"] = "unsupported-formal-ir"
            record["promotion_status"] = "blocked"
        records.append(record)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source = args.repo / "tests" / "fixtures" / "dse_real_source_audit_snippet.cpp"
    findings_path = args.work_dir / "findings.json"
    candidates_path = args.work_dir / "candidates.jsonl"
    audit_input_path = args.work_dir / "audit-input.jsonl"
    audit_path = args.work_dir / "audit.json"
    configs_dir = args.work_dir / "configs"
    summary_path = args.work_dir / "configs-summary.json"
    unsupported_path = args.work_dir / "configs-unsupported.jsonl"

    run(
        [
            str(args.ast_miner),
            "--registry",
            str(args.repo / "constraints" / "pass_constraints.json"),
            "--require-marker",
            "probe.dse.dead-store",
            "--require-marker",
            "probe.dse.overwritten-store",
            str(source),
            "--",
            "-std=c++17",
        ],
        stdout=findings_path,
    )
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert len(findings) == 28

    dead = by_source(findings, "isRemovable(&Store) && MSSA.getMemoryAccess")
    assert dead["marker"] == "probe.dse.dead-store"
    assert {"memoryssa.dead-store", "alias.noalias"} <= fact_kinds(dead)
    assert dead["source_intent_graph"]["analysis_facts"]

    overwrite = by_source(findings, "isOverwrite(&Store) && getLocForWrite")
    assert overwrite["marker"] == "probe.dse.overwritten-store"
    assert {
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.full",
        "alias.noalias",
    } <= fact_kinds(overwrite)

    side_effect = by_source(findings, "Store.isVolatile() || Store.isAtomic()")
    assert "memory.volatile-atomic-blocker" in fact_kinds(side_effect)
    assert "memory.volatile-blocker" in fact_kinds(side_effect)
    assert "memory.atomic-ordering-unknown-blocker" in fact_kinds(side_effect)
    volatile = by_source(findings, "Store.isVolatile()")
    assert "memory.volatile-atomic-blocker" in fact_kinds(volatile)
    assert "memory.volatile-blocker" in fact_kinds(volatile)
    unordered_atomic = by_source(findings, "AtomicOrdering::Unordered")
    assert "memory.volatile-atomic-blocker" in fact_kinds(unordered_atomic)
    assert "memory.atomic-unordered-blocker" in fact_kinds(unordered_atomic)
    ordered_atomic = by_source(findings, "AtomicOrdering::SequentiallyConsistent")
    assert "memory.volatile-atomic-blocker" in fact_kinds(ordered_atomic)
    assert "memory.atomic-ordered-blocker" in fact_kinds(ordered_atomic)
    unknown_atomic = by_source(findings, "unknownAtomicOrdering(&Store)")
    assert "memory.volatile-atomic-blocker" in fact_kinds(unknown_atomic)
    assert "memory.atomic-ordering-unknown-blocker" in fact_kinds(unknown_atomic)

    ambiguous = by_source(findings, "isOverwrite(&Store) &&\n        getClobberingMemoryAccess")
    assert "alias.unknown" in fact_kinds(ambiguous)
    assert "alias.noalias" not in fact_kinds(ambiguous)

    partial = by_source(findings, "isPartialOverwrite(&Store, &KillingStore)")
    assert "memory.overwrite.partial" in fact_kinds(partial)
    fixed_partial = by_source(findings, "fixedPartialOverwrite(&Store, &KillingStore)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(fixed_partial)
    high_partial = by_source(findings, "partialOverwriteByteMask(&Store, &KillingStore, 2, 2)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(high_partial)
    sparse_partial = by_source(findings, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0x9)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(sparse_partial)
    single_partial = by_source(findings, "partialOverwriteByteMask(&Store, &KillingStore, 2, 1)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(single_partial)
    triple_partial = by_source(findings, "partialOverwriteByteMask(&Store, &KillingStore, 0, 3)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(triple_partial)
    sparse_triple_partial = by_source(findings, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0xd)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(sparse_triple_partial)
    width_three_partial = by_source(findings, "partialOverwriteByteMask(&Store, &KillingStore, 0, 1, 3)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(width_three_partial)
    width_eight_partial = by_source(findings, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0x2a, 8)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(width_eight_partial)
    full_mask_partial = by_source(findings, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0xf)")
    assert "memory.overwrite.partial.fixed-byte-mask" in fact_kinds(full_mask_partial)
    nonoverlap = by_source(findings, "nonOverlapping(&Store, &KillingStore)")
    assert "memory.overwrite.nonoverlap" in fact_kinds(nonoverlap)
    unknown_size = by_source(findings, "unknownSize(&Store)")
    assert "memory.overwrite.unknown-size" in fact_kinds(unknown_size)
    symbolic_size = by_source(findings, "sameSize(&Store, &KillingStore)")
    assert "memory.overwrite.unknown-size" in fact_kinds(symbolic_size)
    assert "memory.overwrite.size.symbolic-bounded-eight-lane" in fact_kinds(symbolic_size)
    assert "memory.overwrite.size.symbolic-equal" in fact_kinds(symbolic_size)
    assert "memory.overwrite.size.symbolic-upper-bound" in fact_kinds(symbolic_size)
    symbolic_value_size = by_source(findings, "StoreSize.getValue() == KillingSize.getValue()")
    assert "memory.overwrite.unknown-size" in fact_kinds(symbolic_value_size)
    assert "memory.overwrite.size.symbolic-bounded-eight-lane" in fact_kinds(symbolic_value_size)
    assert "memory.overwrite.size.symbolic-equal" in fact_kinds(symbolic_value_size)
    assert "memory.overwrite.size.symbolic-upper-bound" in fact_kinds(symbolic_value_size)
    symbolic_four_size = by_source(findings, "StoreSize.getValue() <= 4")
    assert "memory.overwrite.unknown-size" in fact_kinds(symbolic_four_size)
    assert "memory.overwrite.size.symbolic-bounded-four-lane" in fact_kinds(symbolic_four_size)
    assert "memory.overwrite.size.symbolic-equal" in fact_kinds(symbolic_four_size)
    assert "memory.overwrite.size.symbolic-upper-bound" in fact_kinds(symbolic_four_size)
    symbolic_too_wide = by_source(findings, "StoreSize.getValue() <= 16")
    assert "memory.overwrite.unknown-size" in fact_kinds(symbolic_too_wide)
    assert "memory.overwrite.size.symbolic-bounded-four-lane" not in fact_kinds(symbolic_too_wide)
    assert "memory.overwrite.size.symbolic-bounded-eight-lane" not in fact_kinds(symbolic_too_wide)
    location_size = by_source(findings, "LocationSize::precise(8).getValue() <= 8")
    assert "memory.overwrite.size.known" in fact_kinds(location_size)
    assert "memory.overwrite.size.bounded-eight-lane" in fact_kinds(location_size)

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
        ]
    )
    candidates = load_jsonl(candidates_path)
    assert len(candidates) == 28

    complete = [c for c in candidates if c.get("evidence", {}).get("formal_inference") == "source-derived-analysis-facts"]
    blocked = [c for c in candidates if "formal" not in c.get("intent_candidate", {})]
    assert len(complete) == 14
    assert len(blocked) == 14
    assert all(c.get("evidence", {}).get("formal_inference") != "registry-fallback" for c in complete)
    assert all(c.get("evidence", {}).get("formal_parameters", {}).get("dse.analysis_facts.complete") is True for c in complete)
    assert all(c.get("evidence", {}).get("formal_parameters", {}).get("dse.analysis_facts.complete") is False for c in blocked)
    fixed_partial_candidate = by_source(candidates, "fixedPartialOverwrite(&Store, &KillingStore)")
    fixed_partial_params = fixed_partial_candidate["evidence"]["formal_parameters"]
    assert fixed_partial_params["dse.overwrite_range"] == "partial"
    assert fixed_partial_params["dse.overwrite_byte_mask"] == "lanes-0-1-of-4"
    high_partial_candidate = by_source(candidates, "partialOverwriteByteMask(&Store, &KillingStore, 2, 2)")
    high_partial_params = high_partial_candidate["evidence"]["formal_parameters"]
    assert high_partial_params["dse.overwrite_range"] == "partial"
    assert high_partial_params["dse.overwrite_byte_mask"] == "lanes-2-3-of-4"
    sparse_partial_candidate = by_source(candidates, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0x9)")
    sparse_partial_params = sparse_partial_candidate["evidence"]["formal_parameters"]
    assert sparse_partial_params["dse.overwrite_range"] == "partial"
    assert sparse_partial_params["dse.overwrite_byte_mask"] == "lanes-0-3-of-4"
    single_partial_candidate = by_source(candidates, "partialOverwriteByteMask(&Store, &KillingStore, 2, 1)")
    single_partial_params = single_partial_candidate["evidence"]["formal_parameters"]
    assert single_partial_params["dse.overwrite_range"] == "partial"
    assert single_partial_params["dse.overwrite_byte_mask"] == "lanes-2-of-4"
    triple_partial_candidate = by_source(candidates, "partialOverwriteByteMask(&Store, &KillingStore, 0, 3)")
    triple_partial_params = triple_partial_candidate["evidence"]["formal_parameters"]
    assert triple_partial_params["dse.overwrite_range"] == "partial"
    assert triple_partial_params["dse.overwrite_byte_mask"] == "lanes-0-1-2-of-4"
    sparse_triple_partial_candidate = by_source(candidates, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0xd)")
    sparse_triple_partial_params = sparse_triple_partial_candidate["evidence"]["formal_parameters"]
    assert sparse_triple_partial_params["dse.overwrite_range"] == "partial"
    assert sparse_triple_partial_params["dse.overwrite_byte_mask"] == "lanes-0-2-3-of-4"
    width_three_partial_candidate = by_source(candidates, "partialOverwriteByteMask(&Store, &KillingStore, 0, 1, 3)")
    width_three_partial_params = width_three_partial_candidate["evidence"]["formal_parameters"]
    assert width_three_partial_params["dse.overwrite_range"] == "partial"
    assert width_three_partial_params["dse.overwrite_byte_mask"] == "lanes-0-of-3"
    assert width_three_partial_params["dse.overwrite_width_bytes"] == 3
    width_eight_partial_candidate = by_source(candidates, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0x2a, 8)")
    width_eight_partial_params = width_eight_partial_candidate["evidence"]["formal_parameters"]
    assert width_eight_partial_params["dse.overwrite_range"] == "partial"
    assert width_eight_partial_params["dse.overwrite_byte_mask"] == "lanes-1-3-5-of-8"
    assert width_eight_partial_params["dse.overwrite_width_bytes"] == 8
    symbolic_size_candidate = by_source(candidates, "sameSize(&Store, &KillingStore)")
    symbolic_size_params = symbolic_size_candidate["evidence"]["formal_parameters"]
    assert symbolic_size_params["dse.analysis_facts.complete"] is True
    assert symbolic_size_params["dse.overwrite_range"] == "full"
    assert symbolic_size_params["dse.overwrite_size"] == "symbolic"
    assert symbolic_size_params["dse.overwrite_width_bytes"] == 8
    assert symbolic_size_params["dse.overwrite_size_bound"] == "eight-lane"
    symbolic_value_size_candidate = by_source(candidates, "StoreSize.getValue() == KillingSize.getValue()")
    symbolic_value_size_params = symbolic_value_size_candidate["evidence"]["formal_parameters"]
    assert symbolic_value_size_params["dse.analysis_facts.complete"] is True
    assert symbolic_value_size_params["dse.overwrite_range"] == "full"
    assert symbolic_value_size_params["dse.overwrite_size"] == "symbolic"
    assert symbolic_value_size_params["dse.overwrite_width_bytes"] == 8
    assert symbolic_value_size_params["dse.overwrite_size_bound"] == "eight-lane"
    symbolic_four_size_candidate = by_source(candidates, "StoreSize.getValue() <= 4")
    symbolic_four_size_params = symbolic_four_size_candidate["evidence"]["formal_parameters"]
    assert symbolic_four_size_params["dse.analysis_facts.complete"] is True
    assert symbolic_four_size_params["dse.overwrite_range"] == "full"
    assert symbolic_four_size_params["dse.overwrite_size"] == "symbolic"
    assert symbolic_four_size_params["dse.overwrite_width_bytes"] == 4
    assert symbolic_four_size_params["dse.overwrite_size_bound"] == "four-lane"
    symbolic_too_wide_candidate = by_source(candidates, "StoreSize.getValue() <= 16")
    symbolic_too_wide_params = symbolic_too_wide_candidate["evidence"]["formal_parameters"]
    assert "formal" not in symbolic_too_wide_candidate["intent_candidate"]
    assert symbolic_too_wide_params["dse.analysis_facts.blockers"] == ["memory.overwrite.unknown-size"]
    assert symbolic_too_wide_params["semantic.unsupported_reason"] == "model unknown-size overwrite evidence"
    location_size_candidate = by_source(candidates, "LocationSize::precise(8).getValue() <= 8")
    location_size_params = location_size_candidate["evidence"]["formal_parameters"]
    assert location_size_params["dse.analysis_facts.complete"] is True
    assert location_size_params["dse.overwrite_range"] == "full"
    assert location_size_params["dse.overwrite_size"] == "known"
    assert location_size_params["dse.overwrite_size_bound"] == "eight-lane"
    full_mask_partial_candidate = by_source(candidates, "knownPartialOverwriteByteMask(&Store, &KillingStore, 0xf)")
    full_mask_partial_params = full_mask_partial_candidate["evidence"]["formal_parameters"]
    assert "formal" not in full_mask_partial_candidate["intent_candidate"]
    assert full_mask_partial_params["dse.analysis_facts.blockers"] == [
        "memory.overwrite.partial.fixed-byte-mask.unsupported-mask"
    ]
    assert full_mask_partial_params["semantic.unsupported_reason"] == "model partial-overwrite byte ranges"
    assert {
        c["evidence"]["formal_parameters"]["semantic.unsupported_reason"]
        for c in blocked
    } == {
        "keep volatile memory blocked",
        "keep unordered atomic memory blocked",
        "keep ordered atomic memory blocked",
        "keep unknown-ordering atomic memory blocked",
        "model alias/noalias evidence",
        "model partial-overwrite byte ranges",
        "keep non-overlapping overwrite blocked",
        "model unknown-size overwrite evidence",
        "model no-intervening-read evidence",
        "model intervening memory effects",
        "model known overwrite size evidence",
    }

    write_audit_input(candidates, audit_input_path)
    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-audit-intent-coverage.py"),
            "--validated",
            str(audit_input_path),
            "--out",
            str(audit_path),
        ]
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["analysis_facts"]["records"] == 28
    assert audit["summary"]["recommendation"]["covered by source-derived DSE analysis facts"] == 14
    assert audit["summary"]["recommendation"]["keep volatile memory blocked"] == 2
    assert audit["summary"]["recommendation"]["keep unordered atomic memory blocked"] == 1
    assert audit["summary"]["recommendation"]["keep ordered atomic memory blocked"] == 1
    assert audit["summary"]["recommendation"]["keep unknown-ordering atomic memory blocked"] == 1
    assert audit["summary"]["recommendation"]["model alias/noalias evidence"] == 1
    assert audit["summary"]["recommendation"]["model partial-overwrite byte ranges"] == 2
    assert audit["summary"]["recommendation"]["keep non-overlapping overwrite blocked"] == 1
    assert audit["summary"]["recommendation"]["model unknown-size overwrite evidence"] == 2
    assert audit["summary"]["recommendation"]["model no-intervening-read evidence"] == 1
    assert audit["summary"]["recommendation"]["model intervening memory effects"] == 1
    assert audit["summary"]["recommendation"]["model known overwrite size evidence"] == 1

    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-constraints-to-configs.py"),
            "--input",
            str(candidates_path),
            "--out-dir",
            str(configs_dir),
            "--replay",
            str(args.replay),
            "--reducer",
            str(args.reducer),
            "--summary-json",
            str(summary_path),
            "--unsupported-jsonl",
            str(unsupported_path),
        ]
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = load_jsonl(configs_dir / "manifest.jsonl")
    unsupported = load_jsonl(unsupported_path)
    assert summary["generated"] == 14
    assert summary["blocked"] == 14
    assert summary["skipped"] == 14
    assert summary["status"] == {"blocked": 14, "generated": 14, "unsupported": 0}
    assert {record["marker"] for record in manifest} == {
        "probe.dse.dead-store",
        "probe.dse.overwritten-store",
    }
    assert all(record["status"] == "generated" for record in manifest)
    assert all(record["expected_generation_status"] == "generated" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-1-of-4" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-2-3-of-4" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-3-of-4" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-2-of-4" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-1-2-of-4" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-2-3-of-4" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-of-3" for record in manifest)
    assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-1-3-5-of-8" for record in manifest)
    assert any(record["dse_scenario"] == "symbolic-bounded-overwrite" for record in manifest)
    assert {record["status"] for record in unsupported} == {"blocked"}
    assert {record["reason"] for record in unsupported} == {
        "unsupported-volatile-or-atomic-memory",
        "unsupported-unresolved-memory-alias",
        "unsupported-partial-overwrite",
        "unsupported-non-overlapping-overwrite",
        "unsupported-unknown-size-overwrite",
        "missing-no-intervening-read",
        "unsupported-intervening-memory-effect",
        "missing-known-overwrite-size",
        "unsupported-partial-overwrite-byte-mask",
    }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
