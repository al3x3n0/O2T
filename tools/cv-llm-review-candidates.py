#!/usr/bin/env python3
"""Review accepted, duplicate, rejected, and unsupported LLM candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-findings", type=Path, required=True)
    parser.add_argument("--llm-findings", type=Path, required=True)
    parser.add_argument("--rejected", type=Path)
    parser.add_argument("--unsupported", type=Path)
    parser.add_argument("--cases-manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def load_records(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
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
        str(record.get("file", "")),
        int(record.get("line") or 0),
        str(record.get("marker", "")),
    )


def candidate_key(record: dict[str, Any]) -> tuple[str, int, str]:
    candidate = record.get("candidate", record)
    return key(candidate) if isinstance(candidate, dict) else ("", 0, "")


def marker_set_from_cases(records: list[dict[str, Any]]) -> set[str]:
    markers: set[str] = set()
    for record in records:
        for field in ("expected_markers", "observed_markers"):
            value = str(record.get(field, ""))
            for marker in value.split(","):
                marker = marker.strip()
                if marker:
                    markers.add(marker)
    return markers


def describe(record: dict[str, Any]) -> str:
    candidate = record.get("candidate", record)
    if not isinstance(candidate, dict):
        candidate = record
    return (
        f"{candidate.get('marker', '')} "
        f"{candidate.get('file', '')}:{candidate.get('line', '')} "
        f"{candidate.get('predicate_source', candidate.get('source', ''))}"
    ).strip()


def main() -> int:
    args = parse_args()
    static_findings = load_records(args.static_findings)
    llm_findings = load_records(args.llm_findings)
    rejected = load_records(args.rejected)
    unsupported = load_records(args.unsupported)
    cases = load_records(args.cases_manifest)

    static_keys = {key(record) for record in static_findings}
    generated_markers = marker_set_from_cases(cases)
    accepted = [record for record in llm_findings if key(record) not in static_keys]
    duplicates = [record for record in llm_findings if key(record) in static_keys]
    generated = [
        record for record in llm_findings if str(record.get("marker", "")) in generated_markers
    ]

    lines = [
        "# LLM Candidate Review",
        "",
        f"accepted: {len(accepted)}",
        f"duplicate: {len(duplicates)}",
        f"unsupported_constraints: {len(unsupported)}",
        f"invalid: {len(rejected)}",
        f"generated_case: {len(generated)}",
        "",
        "## Accepted",
    ]
    lines.extend([f"- {describe(record)}" for record in accepted] or ["- none"])
    lines.append("")
    lines.append("## Duplicate")
    lines.extend([f"- {describe(record)}" for record in duplicates] or ["- none"])
    lines.append("")
    lines.append("## Unsupported Constraints")
    lines.extend(
        [
            f"- {describe(record)} :: {record.get('reason', '')}"
            for record in unsupported
        ]
        or ["- none"]
    )
    lines.append("")
    lines.append("## Invalid")
    lines.extend(
        [f"- {describe(record)} :: {record.get('reason', '')}" for record in rejected]
        or ["- none"]
    )
    lines.append("")
    lines.append("## Generated Cases")
    lines.extend([f"- {describe(record)}" for record in generated] or ["- none"])
    lines.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote LLM review to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
