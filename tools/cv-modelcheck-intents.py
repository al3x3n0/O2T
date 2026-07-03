#!/usr/bin/env python3
"""Generate and run CBMC/ESBMC harnesses from validated scalar/CFG intent records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.symexec.modelcheck_intents import run_intent_modelcheck, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--engine", choices=("auto", "cbmc", "esbmc"), default="auto")
    parser.add_argument("--unwind", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--widths", default="native",
                        help="native, or comma-separated scalar widths such as 8,16,32,64")
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--report", type=Path, help="Alias for --summary-json")
    args = parser.parse_args()

    summary = run_intent_modelcheck(args.input, args.out_dir, args.engine, args.unwind, args.timeout, args.widths)
    summary_path = args.summary_json or args.report
    if summary_path:
        write_json(summary_path, summary)
    print(json.dumps({k: summary.get(k) for k in
                      ("records", "generated", "proved", "refuted", "unsupported", "skipped", "error", "ok")},
                     sort_keys=True))
    for result in summary.get("results", []):
        marker = result.get("marker") or f"record-{result.get('record_index')}"
        width = f" @{result.get('width')}b" if result.get("width") else ""
        domain = f" {result.get('domain')}" if result.get("domain") else ""
        suffix = f" ({result.get('reason')})" if result.get("reason") else ""
        print(f"  [{result.get('status'):11}] {marker}{width}{domain}{suffix}", file=sys.stderr)
    return 1 if summary.get("refuted") or summary.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
