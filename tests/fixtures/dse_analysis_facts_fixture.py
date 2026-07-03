#!/usr/bin/env python3
"""Regression fixture for DSE source-mined analysis dependency facts."""

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
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--reducer", type=Path)
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
        if text in str(record.get("predicate_source") or record.get("source") or ""):
            return record
    raise AssertionError(f"missing source containing {text}")


def by_line(records: list[dict[str, Any]], line: int) -> dict[str, Any]:
    for record in records:
        if int(record.get("line") or 0) == line:
            return record
    raise AssertionError(f"missing record at line {line}")


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source = args.repo / "tests" / "fixtures" / "dse_analysis_facts_snippet.cpp"
    findings_path = args.work_dir / "findings.json"
    candidates_path = args.work_dir / "candidates.jsonl"
    audit_input_path = args.work_dir / "audit-input.jsonl"
    audit_path = args.work_dir / "audit.json"
    dse_ir_dir = args.work_dir / "dse-ir"
    dse_ir_summary = args.work_dir / "dse-ir-summary.json"
    dse_ir_unsupported = args.work_dir / "dse-ir-unsupported.jsonl"

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
    assert len(findings) == 24

    dead = by_source(findings, "isRemovable(&S) && getMemoryAccess")
    assert dead["marker"] == "probe.dse.dead-store"
    assert {"memoryssa.dead-store", "alias.noalias"} <= fact_kinds(dead)
    assert dead["source_intent_graph"]["analysis_facts"]

    overwritten = by_source(findings, "isOverwrite(&S) && getLocForWrite")
    assert overwritten["marker"] == "probe.dse.overwritten-store"
    assert {
        "memoryssa.clobber",
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
        "memory.overwrite.full",
        "alias.noalias",
    } <= fact_kinds(overwritten)

    blocked = by_source(findings, "S.isVolatile()")
    assert "memory.volatile-atomic-blocker" in fact_kinds(blocked)
    assert "memory.volatile-blocker" in fact_kinds(blocked)
    blocker = next(fact for fact in blocked["analysis_facts"] if fact["kind"] == "memory.volatile-atomic-blocker")
    assert blocker["status"] == "unsupported"
    volatile_blocker = next(fact for fact in blocked["analysis_facts"] if fact["kind"] == "memory.volatile-blocker")
    assert volatile_blocker["status"] == "unsupported"

    unordered_atomic = by_source(findings, "AtomicOrdering::Unordered")
    assert "memory.volatile-atomic-blocker" in fact_kinds(unordered_atomic)
    assert "memory.atomic-unordered-blocker" in fact_kinds(unordered_atomic)

    ordered_atomic = by_source(findings, "AtomicOrdering::SequentiallyConsistent")
    assert "memory.volatile-atomic-blocker" in fact_kinds(ordered_atomic)
    assert "memory.atomic-ordered-blocker" in fact_kinds(ordered_atomic)

    unknown_atomic = by_source(findings, "unknownAtomicOrdering(&S)")
    assert "memory.volatile-atomic-blocker" in fact_kinds(unknown_atomic)
    assert "memory.atomic-ordering-unknown-blocker" in fact_kinds(unknown_atomic)

    unknown = by_source(findings, "isOverwrite(&S) && getClobberingMemoryAccess")
    assert "alias.unknown" in fact_kinds(unknown)
    assert "alias.noalias" not in fact_kinds(unknown)

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
    assert len(candidates) == 24
    candidate_dead = by_source(candidates, "isRemovable(&S) && getMemoryAccess")
    params = candidate_dead["evidence"]["formal_parameters"]
    assert "analysis_facts" in params
    assert "memoryssa.dead-store" in params["analysis_facts.kinds"]
    assert candidate_dead["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert params["dse.analysis_facts.complete"] is True

    candidate_overwritten = by_source(candidates, "isOverwrite(&S) && getLocForWrite")
    overwritten_params = candidate_overwritten["evidence"]["formal_parameters"]
    assert candidate_overwritten["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert overwritten_params["dse.analysis_facts.complete"] is True
    assert overwritten_params["dse.overwrite_range"] == "full"
    assert overwritten_params["dse.overwrite_size"] == "known"
    assert overwritten_params["dse.overwrite_size_bound"] == "four-lane"

    candidate_partial = by_source(candidates, "fixedPartialOverwrite(&S, &Killing)")
    partial_params = candidate_partial["evidence"]["formal_parameters"]
    assert candidate_partial["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert partial_params["dse.analysis_facts.complete"] is True
    assert partial_params["dse.overwrite_range"] == "partial"
    assert partial_params["dse.overwrite_byte_mask"] == "lanes-0-1-of-4"
    assert partial_params["dse.overwrite_size"] == "known"
    assert partial_params["dse.overwrite_size_bound"] == "four-lane"
    assert "memory.overwrite.partial.fixed-byte-mask" in partial_params["analysis_facts.kinds"]

    candidate_high_partial = by_source(candidates, "partialOverwriteByteMask(&S, &Killing, 2, 2)")
    high_partial_params = candidate_high_partial["evidence"]["formal_parameters"]
    assert high_partial_params["dse.analysis_facts.complete"] is True
    assert high_partial_params["dse.overwrite_range"] == "partial"
    assert high_partial_params["dse.overwrite_byte_mask"] == "lanes-2-3-of-4"

    candidate_sparse_partial = by_source(candidates, "knownPartialOverwriteByteMask(&S, &Killing, 0x9)")
    sparse_partial_params = candidate_sparse_partial["evidence"]["formal_parameters"]
    assert sparse_partial_params["dse.analysis_facts.complete"] is True
    assert sparse_partial_params["dse.overwrite_range"] == "partial"
    assert sparse_partial_params["dse.overwrite_byte_mask"] == "lanes-0-3-of-4"

    candidate_single_partial = by_source(candidates, "partialOverwriteByteMask(&S, &Killing, 2, 1)")
    single_partial_params = candidate_single_partial["evidence"]["formal_parameters"]
    assert single_partial_params["dse.analysis_facts.complete"] is True
    assert single_partial_params["dse.overwrite_range"] == "partial"
    assert single_partial_params["dse.overwrite_byte_mask"] == "lanes-2-of-4"

    candidate_triple_partial = by_source(candidates, "partialOverwriteByteMask(&S, &Killing, 0, 3)")
    triple_partial_params = candidate_triple_partial["evidence"]["formal_parameters"]
    assert triple_partial_params["dse.analysis_facts.complete"] is True
    assert triple_partial_params["dse.overwrite_range"] == "partial"
    assert triple_partial_params["dse.overwrite_byte_mask"] == "lanes-0-1-2-of-4"

    candidate_sparse_triple_partial = by_source(candidates, "knownPartialOverwriteByteMask(&S, &Killing, 0xd)")
    sparse_triple_partial_params = candidate_sparse_triple_partial["evidence"]["formal_parameters"]
    assert sparse_triple_partial_params["dse.analysis_facts.complete"] is True
    assert sparse_triple_partial_params["dse.overwrite_range"] == "partial"
    assert sparse_triple_partial_params["dse.overwrite_byte_mask"] == "lanes-0-2-3-of-4"

    candidate_width_three_partial = by_source(candidates, "partialOverwriteByteMask(&S, &Killing, 0, 1, 3)")
    width_three_partial_params = candidate_width_three_partial["evidence"]["formal_parameters"]
    assert width_three_partial_params["dse.analysis_facts.complete"] is True
    assert width_three_partial_params["dse.overwrite_range"] == "partial"
    assert width_three_partial_params["dse.overwrite_byte_mask"] == "lanes-0-of-3"
    assert width_three_partial_params["dse.overwrite_width_bytes"] == 3
    assert width_three_partial_params["dse.overwrite_size_bound"] == "eight-lane"

    candidate_width_eight_partial = by_source(candidates, "knownPartialOverwriteByteMask(&S, &Killing, 0x2a, 8)")
    width_eight_partial_params = candidate_width_eight_partial["evidence"]["formal_parameters"]
    assert width_eight_partial_params["dse.analysis_facts.complete"] is True
    assert width_eight_partial_params["dse.overwrite_range"] == "partial"
    assert width_eight_partial_params["dse.overwrite_byte_mask"] == "lanes-1-3-5-of-8"
    assert width_eight_partial_params["dse.overwrite_width_bytes"] == 8
    assert width_eight_partial_params["dse.overwrite_size_bound"] == "eight-lane"

    candidate_symbolic_size = by_source(candidates, "unknownSize(&S)")
    symbolic_size_params = candidate_symbolic_size["evidence"]["formal_parameters"]
    assert candidate_symbolic_size["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert symbolic_size_params["dse.analysis_facts.complete"] is True
    assert symbolic_size_params["dse.overwrite_range"] == "full"
    assert symbolic_size_params["dse.overwrite_size"] == "symbolic"
    assert symbolic_size_params["dse.overwrite_width_bytes"] == 8
    assert symbolic_size_params["dse.overwrite_size_bound"] == "eight-lane"
    assert "memory.overwrite.size.symbolic-bounded-eight-lane" in symbolic_size_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.symbolic-equal" in symbolic_size_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.symbolic-upper-bound" in symbolic_size_params["analysis_facts.kinds"]
    assert "memory.overwrite.unknown-size" in symbolic_size_params["analysis_facts.kinds"]

    candidate_symbolic_value_size = by_source(candidates, "StoreSize.getValue() == KillingSize.getValue()")
    symbolic_value_params = candidate_symbolic_value_size["evidence"]["formal_parameters"]
    assert candidate_symbolic_value_size["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert symbolic_value_params["dse.analysis_facts.complete"] is True
    assert symbolic_value_params["dse.overwrite_range"] == "full"
    assert symbolic_value_params["dse.overwrite_size"] == "symbolic"
    assert symbolic_value_params["dse.overwrite_width_bytes"] == 8
    assert symbolic_value_params["dse.overwrite_size_bound"] == "eight-lane"
    assert "memory.overwrite.size.symbolic-bounded-eight-lane" in symbolic_value_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.symbolic-equal" in symbolic_value_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.symbolic-upper-bound" in symbolic_value_params["analysis_facts.kinds"]
    assert "memory.overwrite.unknown-size" in symbolic_value_params["analysis_facts.kinds"]

    candidate_symbolic_four_size = by_source(candidates, "StoreSize.getValue() <= 4")
    symbolic_four_params = candidate_symbolic_four_size["evidence"]["formal_parameters"]
    assert candidate_symbolic_four_size["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert symbolic_four_params["dse.analysis_facts.complete"] is True
    assert symbolic_four_params["dse.overwrite_range"] == "full"
    assert symbolic_four_params["dse.overwrite_size"] == "symbolic"
    assert symbolic_four_params["dse.overwrite_width_bytes"] == 4
    assert symbolic_four_params["dse.overwrite_size_bound"] == "four-lane"
    assert "memory.overwrite.size.symbolic-bounded-four-lane" in symbolic_four_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.symbolic-equal" in symbolic_four_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.symbolic-upper-bound" in symbolic_four_params["analysis_facts.kinds"]
    assert "memory.overwrite.unknown-size" in symbolic_four_params["analysis_facts.kinds"]

    candidate_symbolic_too_wide = by_source(candidates, "StoreSize.getValue() <= 16")
    symbolic_too_wide_params = candidate_symbolic_too_wide["evidence"]["formal_parameters"]
    assert "formal" not in candidate_symbolic_too_wide["intent_candidate"]
    assert symbolic_too_wide_params["dse.analysis_facts.complete"] is False
    assert symbolic_too_wide_params["dse.analysis_facts.blockers"] == ["memory.overwrite.unknown-size"]
    assert symbolic_too_wide_params["semantic.unsupported_reason"] == "model unknown-size overwrite evidence"

    candidate_location_size = by_source(candidates, "LocationSize::precise(8).getValue() <= 8")
    location_size_params = candidate_location_size["evidence"]["formal_parameters"]
    assert candidate_location_size["evidence"]["formal_inference"] == "source-derived-analysis-facts"
    assert location_size_params["dse.analysis_facts.complete"] is True
    assert location_size_params["dse.overwrite_range"] == "full"
    assert location_size_params["dse.overwrite_size"] == "known"
    assert location_size_params["dse.overwrite_size_bound"] == "eight-lane"
    assert "memory.overwrite.size.known" in location_size_params["analysis_facts.kinds"]
    assert "memory.overwrite.size.bounded-eight-lane" in location_size_params["analysis_facts.kinds"]

    candidate_full_mask_partial = by_source(candidates, "knownPartialOverwriteByteMask(&S, &Killing, 0xf)")
    full_mask_partial_params = candidate_full_mask_partial["evidence"]["formal_parameters"]
    assert "formal" not in candidate_full_mask_partial["intent_candidate"]
    assert full_mask_partial_params["dse.analysis_facts.complete"] is False
    assert full_mask_partial_params["dse.analysis_facts.blockers"] == [
        "memory.overwrite.partial.fixed-byte-mask.unsupported-mask"
    ]
    assert full_mask_partial_params["semantic.unsupported_reason"] == "model partial-overwrite byte ranges"

    candidate_blocked = by_source(candidates, "S.isVolatile()")
    blocked_params = candidate_blocked["evidence"]["formal_parameters"]
    assert "formal" not in candidate_blocked["intent_candidate"]
    assert blocked_params["dse.analysis_facts.complete"] is False
    assert blocked_params["dse.analysis_facts.blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.volatile-blocker",
    ]
    assert blocked_params["semantic.unsupported_reason"] == "keep volatile memory blocked"

    candidate_unordered_atomic = by_source(candidates, "AtomicOrdering::Unordered")
    unordered_atomic_params = candidate_unordered_atomic["evidence"]["formal_parameters"]
    assert "formal" not in candidate_unordered_atomic["intent_candidate"]
    assert unordered_atomic_params["dse.analysis_facts.complete"] is False
    assert unordered_atomic_params["dse.analysis_facts.blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.atomic-unordered-blocker",
    ]
    assert unordered_atomic_params["semantic.unsupported_reason"] == "keep unordered atomic memory blocked"

    candidate_ordered_atomic = by_source(candidates, "AtomicOrdering::SequentiallyConsistent")
    ordered_atomic_params = candidate_ordered_atomic["evidence"]["formal_parameters"]
    assert "formal" not in candidate_ordered_atomic["intent_candidate"]
    assert ordered_atomic_params["dse.analysis_facts.complete"] is False
    assert ordered_atomic_params["dse.analysis_facts.blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.atomic-ordered-blocker",
    ]
    assert ordered_atomic_params["semantic.unsupported_reason"] == "keep ordered atomic memory blocked"

    candidate_unknown_atomic = by_source(candidates, "unknownAtomicOrdering(&S)")
    unknown_atomic_params = candidate_unknown_atomic["evidence"]["formal_parameters"]
    assert "formal" not in candidate_unknown_atomic["intent_candidate"]
    assert unknown_atomic_params["dse.analysis_facts.complete"] is False
    assert unknown_atomic_params["dse.analysis_facts.blockers"] == [
        "memory.volatile-atomic-blocker",
        "memory.atomic-ordering-unknown-blocker",
    ]
    assert unknown_atomic_params["semantic.unsupported_reason"] == "keep unknown-ordering atomic memory blocked"

    candidate_unknown = by_source(candidates, "isOverwrite(&S) && getClobberingMemoryAccess")
    unknown_params = candidate_unknown["evidence"]["formal_parameters"]
    assert "formal" not in candidate_unknown["intent_candidate"]
    assert unknown_params["dse.analysis_facts.complete"] is False
    assert unknown_params["dse.analysis_facts.missing"] == [
        "memory.no-intervening-store",
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
        "memory.overwrite.full",
    ]
    assert unknown_params["dse.analysis_facts.blockers"] == ["alias.unknown"]
    assert unknown_params["semantic.unsupported_reason"] == "model alias/noalias evidence"

    candidate_intervening_read = by_source(candidates, "noInterveningStore(&S, &Killing) && fullyOverwrites")
    intervening_read_params = candidate_intervening_read["evidence"]["formal_parameters"]
    assert "formal" not in candidate_intervening_read["intent_candidate"]
    assert intervening_read_params["dse.analysis_facts.complete"] is False
    assert intervening_read_params["dse.analysis_facts.missing"] == [
        "memory.no-intervening-read",
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
    ]
    assert intervening_read_params["semantic.unsupported_reason"] == "model no-intervening-read evidence"

    candidate_unknown_effect = by_source(candidates, "mayReadOrWriteMemory(&Call)")
    unknown_effect_params = candidate_unknown_effect["evidence"]["formal_parameters"]
    assert "formal" not in candidate_unknown_effect["intent_candidate"]
    assert unknown_effect_params["dse.analysis_facts.complete"] is False
    assert unknown_effect_params["dse.analysis_facts.missing"] == [
        "memory.no-intervening-memory-effect",
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
    ]
    assert unknown_effect_params["dse.analysis_facts.blockers"] == ["memory.unknown-intervening-effect"]
    assert unknown_effect_params["semantic.unsupported_reason"] == "model intervening memory effects"

    candidate_missing_size = by_source(candidates, "noInterveningMemoryAccess(&S, &Killing) &&\n      fullyOverwrites")
    missing_size_params = candidate_missing_size["evidence"]["formal_parameters"]
    assert "formal" not in candidate_missing_size["intent_candidate"]
    assert missing_size_params["dse.analysis_facts.complete"] is False
    assert missing_size_params["dse.analysis_facts.missing"] == [
        "memory.overwrite.size.known",
        "memory.overwrite.size.bounded-four-lane",
    ]
    assert missing_size_params["semantic.unsupported_reason"] == "model known overwrite size evidence"

    no_analysis_candidate_path = args.work_dir / "no-analysis-candidates.jsonl"
    no_analysis_finding = dict(dead)
    no_analysis_finding.pop("analysis_facts", None)
    graph = dict(no_analysis_finding.get("source_intent_graph") or {})
    graph.pop("analysis_facts", None)
    no_analysis_finding["source_intent_graph"] = graph
    no_analysis_findings_path = args.work_dir / "no-analysis-findings.json"
    no_analysis_findings_path.write_text(json.dumps([no_analysis_finding]) + "\n", encoding="utf-8")
    run(
        [
            sys.executable,
            str(args.repo / "tools" / "cv-infer-optimization-intent.py"),
            "--findings",
            str(no_analysis_findings_path),
            "--format",
            "jsonl",
            "--out",
            str(no_analysis_candidate_path),
        ]
    )
    no_analysis_candidate = load_jsonl(no_analysis_candidate_path)[0]
    assert no_analysis_candidate["evidence"]["formal_inference"] == "registry-fallback"
    assert no_analysis_candidate["evidence"]["formal_parameters"]["semantic.unsupported_reason"] == "missing-dse-analysis-facts"

    audit_input_records: list[dict[str, Any]] = []
    for candidate in candidates:
        record = dict(candidate)
        if record["line"] in {
            candidate_dead["line"],
            candidate_overwritten["line"],
            candidate_partial["line"],
            candidate_high_partial["line"],
            candidate_sparse_partial["line"],
            candidate_single_partial["line"],
            candidate_triple_partial["line"],
            candidate_sparse_triple_partial["line"],
            candidate_width_three_partial["line"],
            candidate_width_eight_partial["line"],
            candidate_symbolic_size["line"],
            candidate_symbolic_four_size["line"],
            candidate_location_size["line"],
        }:
            record["proof_status"] = "proved"
            record["proof_result"] = "unsat"
            record["promotion_status"] = "ready"
        else:
            record["proof_status"] = "unsupported"
            record["proof_result"] = "unsupported-formal-ir"
            record["promotion_status"] = "blocked"
        audit_input_records.append(record)
    audit_input_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in audit_input_records),
        encoding="utf-8",
    )

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
    records = audit["records"]
    assert audit["summary"]["analysis_facts"]["records"] == 24
    assert audit["summary"]["analysis_facts"]["kinds"]["memoryssa.dead-store"] == 5
    assert audit["summary"]["analysis_facts"]["kinds"]["memoryssa.clobber"] == 19
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.no-intervening-read"] == 17
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.no-intervening-memory-effect"] == 16
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.known"] == 15
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.bounded-four-lane"] == 9
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.bounded-eight-lane"] == 5
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.symbolic-bounded-eight-lane"] == 2
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.symbolic-bounded-four-lane"] == 1
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.symbolic-equal"] == 4
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.size.symbolic-upper-bound"] == 4
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.unknown-size"] == 4
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.full"] == 9
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.overwrite.partial.fixed-byte-mask"] == 9
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.volatile-atomic-blocker"] == 4
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.volatile-blocker"] == 1
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.atomic-unordered-blocker"] == 1
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.atomic-ordered-blocker"] == 1
    assert audit["summary"]["analysis_facts"]["kinds"]["memory.atomic-ordering-unknown-blocker"] == 1
    assert audit["summary"]["analysis_facts"]["blockers"] == 13
    assert audit["summary"]["recommendation"]["covered by source-derived DSE analysis facts"] == 13
    assert audit["summary"]["recommendation"]["extend formal IR"] == 1
    unknown_audit = by_line(records, int(unknown["line"]))
    assert unknown_audit["recommendation"] == "model alias/noalias evidence"
    blocked_audit = by_line(records, int(blocked["line"]))
    assert blocked_audit["recommendation"] == "keep volatile memory blocked"
    assert by_line(records, int(unordered_atomic["line"]))["recommendation"] == "keep unordered atomic memory blocked"
    assert by_line(records, int(ordered_atomic["line"]))["recommendation"] == "keep ordered atomic memory blocked"
    assert by_line(records, int(unknown_atomic["line"]))["recommendation"] == "keep unknown-ordering atomic memory blocked"

    replay = args.replay or args.repo / "build-clang-tools" / "cv-replay"
    reducer = args.reducer or args.repo / "build-clang-tools" / "cv-reduce-config"
    if replay.exists() and reducer.exists():
        run(
            [
                sys.executable,
                str(args.repo / "tools" / "cv-constraints-to-configs.py"),
                "--input",
                str(candidates_path),
                "--out-dir",
                str(dse_ir_dir),
                "--replay",
                str(replay),
                "--reducer",
                str(reducer),
                "--summary-json",
                str(dse_ir_summary),
                "--unsupported-jsonl",
                str(dse_ir_unsupported),
            ]
        )
        summary = json.loads(dse_ir_summary.read_text(encoding="utf-8"))
        manifest = load_jsonl(dse_ir_dir / "manifest.jsonl")
        blocked_records = load_jsonl(dse_ir_unsupported)
        assert summary["generated"] == 14
        assert summary["blocked"] == 10
        assert summary["skipped"] == 10
        assert summary["status"] == {"blocked": 10, "generated": 14, "unsupported": 0}
        assert {record["marker"] for record in manifest} == {
            "probe.dse.dead-store",
            "probe.dse.overwritten-store",
        }
        assert all(record["status"] == "generated" for record in manifest)
        assert any(record["dse_scenario"] == "complete-dead-store" for record in manifest)
        assert any(record["dse_scenario"] == "full-overwrite" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-1-of-4" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-2-3-of-4" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-3-of-4" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-2-of-4" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-1-2-of-4" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-2-3-of-4" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-of-3" for record in manifest)
        assert any(record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-1-3-5-of-8" for record in manifest)
        assert any(record["dse_scenario"] == "symbolic-bounded-overwrite" for record in manifest)
        assert any("memoryssa.dead-store" in record["analysis_fact_kinds"] for record in manifest)
        assert any("memoryssa.clobber" in record["analysis_fact_kinds"] for record in manifest)
        assert any("memory.no-intervening-read" in record["analysis_fact_kinds"] for record in manifest)
        assert any("memory.no-intervening-memory-effect" in record["analysis_fact_kinds"] for record in manifest)
        assert any("memory.overwrite.size.known" in record["analysis_fact_kinds"] for record in manifest)
        assert any("memory.overwrite.size.bounded-four-lane" in record["analysis_fact_kinds"] for record in manifest)
        assert any("memory.overwrite.full" in record["analysis_fact_kinds"] for record in manifest)
        dead_record = next(record for record in manifest if record["marker"] == "probe.dse.dead-store")
        full_record = next(record for record in manifest if record["dse_scenario"] == "full-overwrite")
        high_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-2-3-of-4"
        )
        sparse_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-3-of-4"
        )
        single_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-2-of-4"
        )
        triple_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-1-2-of-4"
        )
        sparse_triple_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-2-3-of-4"
        )
        width_three_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-0-of-3"
        )
        width_eight_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "partial-overwrite-fixed-byte-mask:lanes-1-3-5-of-8"
        )
        symbolic_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "symbolic-bounded-overwrite"
            and record["line"] == candidate_symbolic_size["line"]
        )
        symbolic_four_record = next(
            record
            for record in manifest
            if record["dse_scenario"] == "symbolic-bounded-overwrite"
            and record["line"] == candidate_symbolic_four_size["line"]
        )
        dead_cfg = dse_ir_dir / dead_record["config"]
        overwritten_cfg = dse_ir_dir / full_record["config"]
        high_cfg = dse_ir_dir / high_record["config"]
        sparse_cfg = dse_ir_dir / sparse_record["config"]
        single_cfg = dse_ir_dir / single_record["config"]
        triple_cfg = dse_ir_dir / triple_record["config"]
        sparse_triple_cfg = dse_ir_dir / sparse_triple_record["config"]
        width_three_cfg = dse_ir_dir / width_three_record["config"]
        width_eight_cfg = dse_ir_dir / width_eight_record["config"]
        symbolic_cfg = dse_ir_dir / symbolic_record["config"]
        symbolic_four_cfg = dse_ir_dir / symbolic_four_record["config"]
        assert "memory_shape=3" in dead_cfg.read_text(encoding="utf-8")
        assert "memory_shape=4" in overwritten_cfg.read_text(encoding="utf-8")
        assert "const_a=12" in high_cfg.read_text(encoding="utf-8")
        assert "const_a=9" in sparse_cfg.read_text(encoding="utf-8")
        assert "const_a=4" in single_cfg.read_text(encoding="utf-8")
        assert "const_a=7" in triple_cfg.read_text(encoding="utf-8")
        assert "const_a=13" in sparse_triple_cfg.read_text(encoding="utf-8")
        assert "const_a=1" in width_three_cfg.read_text(encoding="utf-8")
        assert "const_b=3" in width_three_cfg.read_text(encoding="utf-8")
        assert "const_a=42" in width_eight_cfg.read_text(encoding="utf-8")
        assert "const_b=8" in width_eight_cfg.read_text(encoding="utf-8")
        assert "feature_bits=2" in symbolic_cfg.read_text(encoding="utf-8")
        assert "const_b=8" in symbolic_cfg.read_text(encoding="utf-8")
        assert "feature_bits=2" in symbolic_four_cfg.read_text(encoding="utf-8")
        assert "const_b=4" in symbolic_four_cfg.read_text(encoding="utf-8")
        assert (dse_ir_dir / dead_record["ir"]).is_file()
        assert (dse_ir_dir / full_record["ir"]).is_file()
        assert {record["status"] for record in blocked_records} == {"blocked"}
        assert {record["reason"] for record in blocked_records} == {
            "unsupported-volatile-or-atomic-memory",
            "unsupported-unresolved-memory-alias",
            "missing-no-intervening-read",
            "unsupported-intervening-memory-effect",
            "missing-known-overwrite-size",
            "unsupported-partial-overwrite-byte-mask",
            "unsupported-unknown-size-overwrite",
        }
        assert any("memory.volatile-atomic-blocker" in record["analysis_fact_kinds"] for record in blocked_records)
        assert any("memory.volatile-blocker" in record["analysis_fact_kinds"] for record in blocked_records)
        assert any("memory.atomic-unordered-blocker" in record["analysis_fact_kinds"] for record in blocked_records)
        assert any("memory.atomic-ordered-blocker" in record["analysis_fact_kinds"] for record in blocked_records)
        assert any("memory.atomic-ordering-unknown-blocker" in record["analysis_fact_kinds"] for record in blocked_records)
        assert any("alias.unknown" in record["analysis_fact_kinds"] for record in blocked_records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
