#!/usr/bin/env python3
"""Create provider-agnostic LLM prompt bundles for instrumentation discovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "constraints" / "pass_constraints.json"
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc"}
ANCHOR_TOKENS = [
    "match(",
    "m_",
    "isa<",
    "dyn_cast<",
    "cast<",
    "isInstructionTriviallyDead",
    "isAllocaPromotable",
    "rewriteSingleStoreAlloca",
    "isRemovable",
    "isOverwrite",
    "FindAvailableLoadedValue",
    "getHeader",
    "PHINode",
    "getSmallConstantTripCount",
    "isLoopInvariant",
    "makeLoopInvariant",
    "isDeadLoopInstruction",
    "getExitBlock",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--context", type=int, default=12)
    parser.add_argument("--max-bundles-per-file", type=int, default=20)
    return parser.parse_args()


def source_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix in SOURCE_SUFFIXES
            )
            continue
        raise FileNotFoundError(path)
    return sorted(files)


def load_registry(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("constraint registry must be a JSON array")
    return [record for record in data if isinstance(record, dict)]


def anchor_lines(lines: list[str], registry: list[dict[str, Any]]) -> list[int]:
    tokens = set(ANCHOR_TOKENS)
    for entry in registry:
        for pattern in entry.get("source_patterns", []):
            if isinstance(pattern, str):
                tokens.add(pattern)

    anchors: list[int] = []
    for index, line in enumerate(lines):
        if any(token in line for token in tokens):
            anchors.append(index)
    return anchors


def merge_windows(anchors: list[int], line_count: int, context: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for anchor in anchors:
        start = max(0, anchor - context)
        end = min(line_count, anchor + context + 1)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def response_schema() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "file": "string",
                "line": "integer, 1-based source line",
                "marker": "known probe.* marker",
                "predicate_source": "source expression or statement to wrap",
                "constraints": "object using O2T constraint vocabulary",
                "confidence": "number from 0.0 to 1.0",
                "rationale": "short reason this predicate should be instrumented",
                "instrumentation_hint": "optional wrapping hint",
            }
        ]
    }


def make_bundle(
    source: Path,
    lines: list[str],
    start: int,
    end: int,
    registry: list[dict[str, Any]],
) -> dict[str, Any]:
    numbered = [
        f"{line_no}: {lines[line_no - 1]}"
        for line_no in range(start + 1, end + 1)
    ]
    return {
        "task": (
            "Identify LLVM pass predicates worth wrapping with "
            "CV_PASS_PROBE_IF. Return only JSON matching response_schema. "
            "Use only known marker names."
        ),
        "source_file": str(source),
        "source_start_line": start + 1,
        "source_end_line": end,
        "source_excerpt": "\n".join(numbered),
        "known_markers": registry,
        "response_schema": response_schema(),
    }


def main() -> int:
    args = parse_args()
    try:
        registry = load_registry(args.registry)
        files = source_files(args.paths)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.out.open("w", encoding="utf-8") as output:
        for source in files:
            lines = source.read_text(errors="replace").splitlines()
            anchors = anchor_lines(lines, registry)
            windows = merge_windows(anchors, len(lines), args.context)
            for start, end in windows[: args.max_bundles_per_file]:
                output.write(json.dumps(make_bundle(source, lines, start, end, registry), sort_keys=True) + "\n")
                count += 1

    print(f"wrote {count} prompt bundle(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
