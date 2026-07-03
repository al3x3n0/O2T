#!/usr/bin/env python3
"""Summarize O2T opt-check manifest files."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def count(records: list[dict[str, Any]], key: str) -> collections.Counter[str]:
    return collections.Counter(str(record.get(key, "") or "unset") for record in records)


def format_counter(title: str, values: collections.Counter[str]) -> list[str]:
    lines = [title]
    for key, value in sorted(values.items()):
        lines.append(f"  {key}: {value}")
    return lines


def failure_lines(records: list[dict[str, Any]]) -> list[str]:
    failures = [record for record in records if record.get("status") != "passed"]
    if not failures:
        return ["Failures", "  none"]

    lines = ["Failures"]
    for record in failures:
        details = [
            f"case={record.get('case', '')}",
            f"status={record.get('status', '')}",
            f"semantic={record.get('semantic_status', '')}",
            f"alive2={record.get('alive2_status', '')}",
            f"oracle={record.get('oracle_status', '')}",
            f"passes={record.get('passes', '')}",
        ]
        if record.get("semantic_mismatch_input"):
            details.append(f"input={record['semantic_mismatch_input']}")
        lines.append("  " + " ".join(details))
        if record.get("message"):
            lines.append(f"    message={record['message']}")
        if record.get("before") or record.get("after"):
            lines.append(f"    before={record.get('before', '')}")
            lines.append(f"    after={record.get('after', '')}")
    return lines


def summarize(records: list[dict[str, Any]], manifest: Path) -> str:
    lines = [
        "O2T Campaign Summary",
        f"manifest: {manifest}",
        f"cases: {len(records)}",
        "",
    ]
    lines.extend(format_counter("Status", count(records, "status")))
    lines.append("")
    lines.extend(format_counter("Semantic status", count(records, "semantic_status")))
    lines.append("")
    lines.extend(format_counter("Alive2 status", count(records, "alive2_status")))
    lines.append("")
    lines.extend(format_counter("Probe oracle status", count(records, "oracle_status")))
    lines.append("")
    lines.extend(format_counter("Category", count(records, "category")))
    lines.append("")
    lines.extend(failure_lines(records))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    records = load_records(args.manifest)
    text = summarize(records, args.manifest)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
