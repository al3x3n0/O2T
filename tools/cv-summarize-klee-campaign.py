#!/usr/bin/env python3
"""Summarize generated and replayed coverage from a KLEE campaign."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

from cv_targeted_ir_configs import MARKER_CONFIGS


KNOWN_MARKERS = list(MARKER_CONFIGS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-manifest", type=Path, required=True)
    parser.add_argument("--opt-manifest", type=Path)
    parser.add_argument("--runner-summary", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if isinstance(record, dict) else {}


def split_markers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def sorted_known(markers: set[str]) -> list[str]:
    known_order = {marker: index for index, marker in enumerate(KNOWN_MARKERS)}
    return sorted(markers, key=lambda marker: (known_order.get(marker, len(KNOWN_MARKERS)), marker))


def infer_category(markers: list[str]) -> str:
    if any(marker.startswith("probe.globalopt.") for marker in markers):
        return "global"
    if any(marker.startswith("probe.vector.") for marker in markers):
        return "vector"
    if any(
        marker.startswith("probe.mem2reg.")
        or marker.startswith("probe.dse.")
        or marker == "probe.instcombine.redundant-load"
        or marker == "probe.cleanup.unused-alloca"
        for marker in markers
    ):
        return "memory"
    if any(
        marker.startswith("probe.loop.")
        or marker == "probe.licm.invariant-op"
        or marker == "probe.dce.dead-loop-instruction"
        or marker == "probe.simplifycfg.loop-exit"
        for marker in markers
    ):
        return "loop"
    if any(marker.startswith("probe.simplifycfg.") for marker in markers):
        return "cfg"
    return "scalar"


def summarize(
    cases_records: list[dict[str, Any]],
    opt_records: list[dict[str, Any]],
    runner_summary: dict[str, Any],
) -> dict[str, Any]:
    generated_markers: set[str] = set()
    observed_markers: set[str] = set()
    missing_markers: set[str] = set()
    unexpected_markers: set[str] = set()
    generated_category_counts: collections.Counter[str] = collections.Counter()
    status_counts: collections.Counter[str] = collections.Counter()
    semantic_counts: collections.Counter[str] = collections.Counter()
    alive2_counts: collections.Counter[str] = collections.Counter()
    oracle_counts: collections.Counter[str] = collections.Counter()
    failures: list[dict[str, str]] = []

    for record in cases_records:
        markers = split_markers(record.get("coverage"))
        generated_markers.update(markers)
        generated_category_counts[infer_category(markers)] += 1

    checked_category_counts: collections.Counter[str] = collections.Counter()
    for record in opt_records:
        expected = split_markers(record.get("expected_markers"))
        observed = split_markers(record.get("observed_markers"))
        missing = split_markers(record.get("missing_markers"))
        unexpected = split_markers(record.get("unexpected_markers"))
        generated_markers.update(expected)
        observed_markers.update(observed)
        missing_markers.update(missing)
        unexpected_markers.update(unexpected)
        checked_category_counts[str(record.get("category") or infer_category(expected))] += 1
        status = str(record.get("status") or "unset")
        semantic = str(record.get("semantic_status") or "unset")
        alive2 = str(record.get("alive2_status") or "unset")
        oracle = str(record.get("oracle_status") or "unset")
        status_counts[status] += 1
        semantic_counts[semantic] += 1
        alive2_counts[alive2] += 1
        oracle_counts[oracle] += 1
        if status != "passed":
            failures.append(
                {
                    "case": str(record.get("case") or ""),
                    "status": status,
                    "semantic_status": semantic,
                    "alive2_status": alive2,
                    "oracle_status": oracle,
                    "message": str(record.get("message") or ""),
                    "missing_markers": ",".join(missing),
                }
            )

    known = set(KNOWN_MARKERS)
    never_generated = known - generated_markers
    generated_not_observed = generated_markers - observed_markers if opt_records else set()

    return {
        "run_id": runner_summary.get("run_id", ""),
        "cases": {
            "generated": len(cases_records),
            "reduced": sum(1 for record in cases_records if record.get("reduced")),
            "checked": len(opt_records),
        },
        "generated_categories": dict(sorted(generated_category_counts.items())),
        "checked_categories": dict(sorted(checked_category_counts.items())),
        "status": dict(sorted(status_counts.items())),
        "semantic_status": dict(sorted(semantic_counts.items())),
        "alive2_status": dict(sorted(alive2_counts.items())),
        "oracle_status": dict(sorted(oracle_counts.items())),
        "markers": {
            "known": KNOWN_MARKERS,
            "generated": sorted_known(generated_markers),
            "observed": sorted_known(observed_markers),
            "missing_observed": sorted_known(missing_markers),
            "unexpected_observed": sorted_known(unexpected_markers),
            "never_generated": sorted_known(never_generated),
            "generated_not_observed": sorted_known(generated_not_observed),
        },
        "failures": failures,
    }


def format_counter(title: str, values: dict[str, int]) -> list[str]:
    lines = [title]
    if not values:
        return [title, "  none"]
    for key, value in sorted(values.items()):
        lines.append(f"  {key}: {value}")
    return lines


def format_marker_list(title: str, markers: list[str]) -> list[str]:
    lines = [title]
    if not markers:
        return [title, "  none"]
    lines.extend(f"  {marker}" for marker in markers)
    return lines


def format_text(summary: dict[str, Any]) -> str:
    cases = summary["cases"]
    lines = [
        "O2T KLEE Coverage Summary",
        f"run_id: {summary.get('run_id', '')}",
        f"generated_cases: {cases['generated']}",
        f"reduced_cases: {cases['reduced']}",
        f"checked_cases: {cases['checked']}",
        "",
    ]
    lines.extend(format_counter("Generated categories", summary["generated_categories"]))
    lines.append("")
    lines.extend(format_counter("Checked categories", summary["checked_categories"]))
    lines.append("")
    lines.extend(format_counter("Replay status", summary["status"]))
    lines.append("")
    lines.extend(format_counter("Semantic status", summary["semantic_status"]))
    lines.append("")
    lines.extend(format_counter("Alive2 status", summary["alive2_status"]))
    lines.append("")
    lines.extend(format_counter("Probe oracle status", summary["oracle_status"]))
    lines.append("")
    lines.extend(format_marker_list("Generated markers", summary["markers"]["generated"]))
    lines.append("")
    lines.extend(format_marker_list("Observed markers", summary["markers"]["observed"]))
    lines.append("")
    lines.extend(format_marker_list("Missing observed markers", summary["markers"]["missing_observed"]))
    lines.append("")
    lines.extend(format_marker_list("Never generated markers", summary["markers"]["never_generated"]))
    lines.append("")
    lines.extend(format_marker_list("Generated but not observed markers", summary["markers"]["generated_not_observed"]))
    lines.append("")
    lines.append("Failures")
    if summary["failures"]:
        for failure in summary["failures"]:
            lines.append(
                "  "
                + " ".join(
                    [
                        f"case={failure['case']}",
                        f"status={failure['status']}",
                        f"semantic={failure['semantic_status']}",
                        f"alive2={failure['alive2_status']}",
                        f"oracle={failure['oracle_status']}",
                        f"missing={failure['missing_markers']}",
                    ]
                )
            )
            if failure["message"]:
                lines.append(f"    message={failure['message']}")
    else:
        lines.append("  none")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    cases_records = load_jsonl(args.cases_manifest)
    opt_records = load_jsonl(args.opt_manifest)
    runner_summary = load_json(args.runner_summary)
    summary = summarize(cases_records, opt_records, runner_summary)
    text = format_text(summary)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
