#!/usr/bin/env python3
"""Deterministic O2T LLM-command fixture."""

from __future__ import annotations

import json
import re
import sys


def line_for(excerpt: str, needle: str, fallback: int) -> int:
    for line in excerpt.splitlines():
        match = re.match(r"^\s*(\d+):\s*(.*)$", line)
        if match and needle in match.group(2):
            return int(match.group(1))
    return fallback


def main() -> int:
    prompt = json.loads(sys.stdin.read())
    source = str(prompt["source_file"])
    start = int(prompt.get("source_start_line", 1))
    excerpt = str(prompt.get("source_excerpt", ""))
    line = line_for(excerpt, "m_Zero", start)
    response = {
        "candidates": [
            {
                "file": source,
                "line": line,
                "marker": "probe.instcombine.add-zero",
                "predicate_source": "match(Op1, m_Zero())",
                "constraints": {
                    "instruction.opcode": "add",
                    "rhs.value": 0,
                },
                "confidence": 0.93,
                "rationale": "zero operand matcher",
                "instrumentation_hint": "Wrap the zero-matcher predicate.",
            }
        ]
    }
    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
