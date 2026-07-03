#!/usr/bin/env python3
"""Bounded model-check the real C++ of LLVM-style folds with CBMC or ESBMC.

The harness is compiled by the model checker against a tiny LLVM-like shim. Analysis queries are
nondet choices constrained by the facts they establish, and every rewriting path asserts the same
poison/UB-aware refinement property used by O2T's Z3 symbolic-execution backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.symexec import modelcheck as M  # noqa: E402


def _write_report(path: Path | None, report: dict) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=M.DEFAULT_SOURCE)
    ap.add_argument("--fold", action="append", default=[], help="fold name(s) to verify")
    ap.add_argument("--engine", choices=("auto", "cbmc", "esbmc"), default="auto")
    ap.add_argument("--unwind", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    report = M.run_modelcheck(args.source, args.fold or M.DEFAULT_SOUND_FOLDS,
                              engine=args.engine, unwind=args.unwind,
                              timeout_s=args.timeout)
    _write_report(args.report, report)
    print(json.dumps({k: report.get(k) for k in
                      ("status", "engine", "folds", "proved", "refuted", "errors", "ok")},
                     sort_keys=True))
    for r in report.get("results", []):
        suffix = ""
        if r.get("reason"):
            suffix = f" ({r['reason'].splitlines()[0]})"
        print(f"  [{r['status']:7}] {r['fold']}{suffix}", file=sys.stderr)
    if report.get("status") == "skipped":
        return 0
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
