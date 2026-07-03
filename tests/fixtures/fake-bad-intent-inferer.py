#!/usr/bin/env python3
"""Emit a deliberately invalid high-confidence intent candidate."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--findings")
    parser.add_argument("--out", required=True)
    parser.add_argument("--format", choices=["json", "jsonl"], default="jsonl")
    parser.add_argument("--min-confidence", default="low")
    args = parser.parse_args()

    record = {
        "file": "synthetic.cpp",
        "line": 1,
        "marker": "probe.instcombine.add-zero",
        "confidence": "high",
        "predicate_source": "if (match(Op1, m_Zero()))",
        "rewrite_source": "return replaceInstUsesWith(I, Op0);",
        "side_conditions": [],
        "intent_candidate": {
            "marker": "probe.instcombine.add-zero",
            "precondition": "instruction.opcode == add && rhs == 0",
            "rewrite": "bad rewrite",
            "intent": "result-equivalence",
            "smt_before": "(bvadd a #x00000000)",
            "smt_after": "b",
        },
    }

    with open(args.out, "w", encoding="utf-8") as output:
        if args.format == "json":
            json.dump([record], output, indent=2, sort_keys=True)
            output.write("\n")
        else:
            output.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"wrote 1 intent candidate(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
