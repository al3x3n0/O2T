#!/usr/bin/env python3
"""Summarize packaged campaign replay against instrumented LLVM."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if isinstance(record, dict) else {}


def split_markers(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def count(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(record.get(key) or "unset") for record in records).items()))


def origin_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        case = str(record.get("case") or Path(str(record.get("config") or "")).stem)
        if case:
            result[case] = record
    return result


def summarize(campaign: Path) -> dict[str, Any]:
    cases_dir = campaign / "cases"
    source_records = load_jsonl(cases_dir / "source-manifest.jsonl")
    replay_records = load_jsonl(cases_dir / "opt" / "manifest.jsonl")
    package_summary = load_json(campaign / "package-summary.json")
    sources = origin_map(source_records)

    joined: list[dict[str, Any]] = []
    observed_markers: set[str] = set()
    missing_markers: set[str] = set()
    for replay in replay_records:
        case = str(replay.get("case") or "")
        source = sources.get(case, {})
        observed = split_markers(replay.get("observed_markers"))
        missing = split_markers(replay.get("missing_markers"))
        observed_markers.update(observed)
        missing_markers.update(missing)
        joined.append(
            {
                "case": case,
                "origin": str(source.get("origin") or "unknown"),
                "marker": str(source.get("marker") or ""),
                "gap_type": str(source.get("gap_type") or ""),
                "status": str(replay.get("status") or "unset"),
                "semantic_status": str(replay.get("semantic_status") or "unset"),
                "alive2_status": str(replay.get("alive2_status") or "unset"),
                "oracle_status": str(replay.get("oracle_status") or "unset"),
                "expected_markers": split_markers(replay.get("expected_markers")),
                "observed_markers": observed,
                "missing_markers": missing,
                "unexpected_markers": split_markers(replay.get("unexpected_markers")),
                "message": str(replay.get("message") or ""),
                "before": str(replay.get("before") or ""),
                "after": str(replay.get("after") or ""),
                "probe_log": str(replay.get("probe_log") or ""),
                "alive2_output": str(replay.get("alive2_output") or ""),
            }
        )

    failures = [record for record in joined if record["status"] != "passed"]
    return {
        "campaign": str(campaign),
        "package_summary": package_summary,
        "cases": {
            "packaged": len(source_records),
            "replayed": len(replay_records),
            "failures": len(failures),
        },
        "origin": count(joined, "origin"),
        "gap_type": count(joined, "gap_type"),
        "status": count(joined, "status"),
        "semantic_status": count(joined, "semantic_status"),
        "alive2_status": count(joined, "alive2_status"),
        "oracle_status": count(joined, "oracle_status"),
        "markers": {
            "observed": sorted(observed_markers),
            "missing": sorted(missing_markers),
        },
        "failures": failures,
        "cases_detail": joined,
    }


def format_counter(title: str, values: dict[str, int]) -> list[str]:
    lines = [title]
    if not values:
        return [title, "  none"]
    for key, value in values.items():
        lines.append(f"  {key}: {value}")
    return lines


def format_markers(title: str, markers: list[str]) -> list[str]:
    lines = [title]
    if not markers:
        return [title, "  none"]
    lines.extend(f"  {marker}" for marker in markers)
    return lines


def format_text(summary: dict[str, Any]) -> str:
    cases = summary["cases"]
    lines = [
        "O2T Verification Summary",
        f"campaign: {summary['campaign']}",
        f"packaged_cases: {cases['packaged']}",
        f"replayed_cases: {cases['replayed']}",
        f"failures: {cases['failures']}",
        "",
    ]
    lines.extend(format_counter("Origin", summary["origin"]))
    lines.append("")
    lines.extend(format_counter("Gap type", summary["gap_type"]))
    lines.append("")
    lines.extend(format_counter("Status", summary["status"]))
    lines.append("")
    lines.extend(format_counter("Semantic status", summary["semantic_status"]))
    lines.append("")
    lines.extend(format_counter("Alive2 status", summary["alive2_status"]))
    lines.append("")
    lines.extend(format_counter("Probe oracle status", summary["oracle_status"]))
    lines.append("")
    lines.extend(format_markers("Observed markers", summary["markers"]["observed"]))
    lines.append("")
    lines.extend(format_markers("Missing markers", summary["markers"]["missing"]))
    lines.append("")
    lines.append("Failures")
    if summary["failures"]:
        for failure in summary["failures"]:
            lines.append(
                "  "
                + " ".join(
                    [
                        f"case={failure['case']}",
                        f"origin={failure['origin']}",
                        f"marker={failure['marker']}",
                        f"gap={failure['gap_type']}",
                        f"status={failure['status']}",
                        f"semantic={failure['semantic_status']}",
                        f"alive2={failure['alive2_status']}",
                        f"oracle={failure['oracle_status']}",
                    ]
                )
            )
            if failure["message"]:
                lines.append(f"    message={failure['message']}")
            if failure["probe_log"]:
                lines.append(f"    probe_log={failure['probe_log']}")
            if failure["alive2_output"]:
                lines.append(f"    alive2_output={failure['alive2_output']}")
            if failure["before"] or failure["after"]:
                lines.append(f"    before={failure['before']}")
                lines.append(f"    after={failure['after']}")
    else:
        lines.append("  none")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    summary = summarize(args.campaign)
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
