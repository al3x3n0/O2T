#!/usr/bin/env python3
"""Validate LLM-proposed instrumentation candidates into mined finding records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "constraints" / "pass_constraints.json"
SUPPORTED_CONSTRAINT_KEYS = {
    "instruction.opcode",
    "rhs.kind",
    "rhs.value",
    "lhs",
    "rhs",
    "instruction.is_dead",
    "block.reachable",
    "cfg.shape",
    "memory.alloca",
    "memory.store_load_forward",
    "memory.store",
    "memory.load",
    "loop.shape",
    "loop.induction",
    "loop.trip_count",
    "loop.invariant",
    "loop.body_instruction",
    "loop.exit",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--allow-unsupported", action="store_true")
    parser.add_argument("--rejected-out", type=Path)
    parser.add_argument("--unsupported-out", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.0)
    return parser.parse_args()


def load_registry(path: Path) -> tuple[set[str], dict[str, dict[str, Any]]]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("constraint registry must be a JSON array")
    by_marker = {
        str(record["marker"]): record
        for record in data
        if isinstance(record, dict) and "marker" in record
    }
    return set(by_marker), by_marker


def load_json_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    stripped = text.lstrip()
    if not stripped:
        return []
    raw_records: list[Any]
    if stripped.startswith("["):
        data = json.loads(text)
        raw_records = data if isinstance(data, list) else [data]
    else:
        raw_records = [json.loads(line) for line in text.splitlines() if line.strip()]

    candidates: list[dict[str, Any]] = []
    for record in raw_records:
        if not isinstance(record, dict):
            continue
        nested = record.get("candidates")
        if isinstance(nested, list):
            candidates.extend(candidate for candidate in nested if isinstance(candidate, dict))
        else:
            candidates.append(record)
    return candidates


def context_lines(path: Path, line: int, radius: int = 2) -> list[str]:
    lines = path.read_text(errors="replace").splitlines()
    index = max(0, line - 1)
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return lines[start:end]


def unsupported_constraints(constraints: dict[str, Any]) -> list[str]:
    return sorted(key for key in constraints if key not in SUPPORTED_CONSTRAINT_KEYS)


def candidate_error_without_constraint_support(
    candidate: dict[str, Any],
    known_markers: set[str],
    min_confidence: float,
) -> str | None:
    marker = candidate.get("marker")
    if not isinstance(marker, str) or marker not in known_markers:
        return f"unknown marker: {marker}"
    file_name = candidate.get("file")
    if not isinstance(file_name, str) or not Path(file_name).is_file():
        return f"missing file: {file_name}"
    line = candidate.get("line")
    if not isinstance(line, int) or line < 1:
        return f"invalid line: {line}"
    line_count = len(Path(file_name).read_text(errors="replace").splitlines())
    if line > line_count:
        return f"line outside file: {line}"
    confidence = candidate.get("confidence")
    if not isinstance(confidence, (int, float)) or float(confidence) < min_confidence:
        return f"confidence below threshold: {confidence}"
    constraints = candidate.get("constraints")
    if not isinstance(constraints, dict):
        return "constraints must be an object"
    predicate = candidate.get("predicate_source")
    if not isinstance(predicate, str) or not predicate.strip():
        return "predicate_source is required"
    return None


def normalize_candidate(
    candidate: dict[str, Any],
    registry_by_marker: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    marker = str(candidate["marker"])
    source = Path(str(candidate["file"]))
    line = int(candidate["line"])
    registry_record = registry_by_marker[marker]
    return {
        "file": str(source),
        "line": line,
        "marker": marker,
        "pass": registry_record.get("pass", "unknown"),
        "predicate_kind": registry_record.get("predicate_kind", "unknown"),
        "matched_pattern": candidate["predicate_source"],
        "source": candidate["predicate_source"],
        "constraints": candidate["constraints"],
        "suggestion": f'Wrap predicate with CV_PASS_PROBE_IF("{marker}", <predicate>)',
        "context": context_lines(source, line),
        "finding_source": "llm",
        "confidence": float(candidate["confidence"]),
        "rationale": candidate.get("rationale", ""),
        "instrumentation_hint": candidate.get("instrumentation_hint", ""),
    }


def write_jsonl(path: Path | None, records: list[dict[str, Any]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    try:
        known_markers, registry_by_marker = load_registry(args.registry)
        candidates = load_json_records(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    records: list[dict[str, Any]] = []
    rejected_records: list[dict[str, Any]] = []
    unsupported_records: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for candidate in candidates:
        error = candidate_error_without_constraint_support(candidate, known_markers, args.min_confidence)
        if error:
            rejected_records.append({"candidate": candidate, "reason": error})
            print(f"rejected candidate: {error}", file=sys.stderr)
            continue
        unsupported = unsupported_constraints(candidate["constraints"])
        if unsupported and not args.allow_unsupported:
            unsupported_records.append(
                {
                    "candidate": candidate,
                    "reason": "unsupported constraints: " + ", ".join(unsupported),
                    "unsupported_constraints": unsupported,
                }
            )
            print(
                "unsupported candidate: " + ", ".join(unsupported),
                file=sys.stderr,
            )
            continue
        key = (str(Path(str(candidate["file"]))), int(candidate["line"]), str(candidate["marker"]))
        if key in seen:
            continue
        seen.add(key)
        records.append(normalize_candidate(candidate, registry_by_marker))

    write_jsonl(args.rejected_out, rejected_records)
    write_jsonl(args.unsupported_out, unsupported_records)

    if not records:
        print("no valid LLM candidates", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as output:
        json.dump(records, output, indent=2, sort_keys=True)
        output.write("\n")

    print(f"imported {len(records)} candidate(s) to {args.out}")
    if rejected_records:
        print(f"rejected {len(rejected_records)} candidate(s)", file=sys.stderr)
    if unsupported_records:
        print(f"unsupported {len(unsupported_records)} candidate(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
