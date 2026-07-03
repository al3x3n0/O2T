#!/usr/bin/env python3
"""Repair line-aware instrumentation candidates from audit recommendations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPAIRABLE_KINDS = {"line-mismatch", "predicate-text-mismatch"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--recommendations", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    parser.add_argument("--window", type=int, default=3)
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []
    return [
        record
        for record in (json.loads(line) for line in text.splitlines() if line.strip())
        if isinstance(record, dict)
    ]


def key(record: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(Path(str(record.get("file", ""))).resolve()),
        int(record.get("line") or 0),
        str(record.get("marker", "")),
    )


def line_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    return offsets


def line_for_offset(offsets: list[int], offset: int) -> int:
    result = 1
    for index, line_offset in enumerate(offsets):
        if line_offset > offset:
            break
        result = index + 1
    return result


def find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def extract_if_conditions(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(errors="replace")
    lines = text.splitlines(keepends=True)
    offsets = line_offsets(lines)
    conditions: list[dict[str, Any]] = []
    for match in re.finditer(r"\bif\s*\(", text):
        open_index = text.find("(", match.start())
        close_index = find_matching_paren(text, open_index)
        if close_index < 0:
            continue
        conditions.append(
            {
                "line": line_for_offset(offsets, match.start()),
                "condition": text[open_index + 1 : close_index].strip(),
            }
        )
    return conditions


def nearest_condition(path: Path, line: int, window: int) -> dict[str, Any] | None:
    conditions = extract_if_conditions(path)
    candidates = [
        condition
        for condition in conditions
        if abs(int(condition["line"]) - line) <= window
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda condition: abs(int(condition["line"]) - line))


def finding_for_recommendation(
    findings: list[dict[str, Any]], recommendation: dict[str, Any]
) -> tuple[int, dict[str, Any]] | None:
    exact = key(recommendation)
    for index, finding in enumerate(findings):
        if key(finding) == exact:
            return index, finding
    rec_file = str(Path(str(recommendation.get("file", ""))).resolve())
    rec_marker = str(recommendation.get("marker", ""))
    for index, finding in enumerate(findings):
        if (
            str(Path(str(finding.get("file", ""))).resolve()) == rec_file
            and str(finding.get("marker", "")) == rec_marker
        ):
            return index, finding
    return None


def repair_findings(
    findings: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    window: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired = [dict(finding) for finding in findings]
    report: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()

    for recommendation in recommendations:
        kind = str(recommendation.get("kind", ""))
        match = finding_for_recommendation(repaired, recommendation)
        if kind not in REPAIRABLE_KINDS:
            report.append(
                {
                    "status": "not-repairable",
                    "kind": kind,
                    "file": recommendation.get("file", ""),
                    "line": recommendation.get("line", 0),
                    "marker": recommendation.get("marker", ""),
                    "message": recommendation.get("message", ""),
                }
            )
            continue
        if match is None:
            report.append(
                {
                    "status": "not-found",
                    "kind": kind,
                    "file": recommendation.get("file", ""),
                    "line": recommendation.get("line", 0),
                    "marker": recommendation.get("marker", ""),
                }
            )
            continue

        index, finding = match
        if index in seen_indexes:
            continue
        seen_indexes.add(index)

        source = Path(str(recommendation.get("file", finding.get("file", ""))))
        line = int(recommendation.get("line") or finding.get("line") or 0)
        try:
            condition = nearest_condition(source, line, window)
        except OSError as exc:
            report.append(
                {
                    "status": "error",
                    "kind": kind,
                    "file": str(source),
                    "line": line,
                    "marker": finding.get("marker", ""),
                    "message": str(exc),
                }
            )
            continue
        if condition is None:
            report.append(
                {
                    "status": "unrepaired",
                    "kind": kind,
                    "file": str(source),
                    "line": line,
                    "marker": finding.get("marker", ""),
                    "message": "no nearby if condition found",
                }
            )
            continue

        repaired[index]["line"] = int(condition["line"])
        repaired[index]["predicate_source"] = str(condition["condition"])
        repaired[index]["matched_pattern"] = str(condition["condition"])
        repaired[index]["source"] = str(condition["condition"])
        report.append(
            {
                "status": "repaired",
                "kind": kind,
                "file": str(source),
                "old_line": line,
                "new_line": int(condition["line"]),
                "marker": finding.get("marker", ""),
                "predicate_source": str(condition["condition"]),
            }
        )

    return repaired, report


def write_report(path: Path, records: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    lines = ["O2T Instrumentation Repair", "statuses:"]
    for status, count in sorted(counts.items()):
        lines.append(f"  {status}: {count}")
    for record in records:
        marker = record.get("marker", "")
        location = f"{record.get('file', '')}:{record.get('new_line', record.get('line', record.get('old_line', 0)))}"
        detail = record.get("predicate_source", record.get("message", ""))
        lines.append(f"{record.get('status', 'unknown')} {marker} {location} {detail}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        findings = load_records(args.findings)
        recommendations = load_records(args.recommendations)
        repaired, report = repair_findings(findings, recommendations, args.window)
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(repaired, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(args.report_out, report)
    print(f"repaired_findings: {args.out}")
    print(f"repair_report: {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
