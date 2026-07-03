#!/usr/bin/env python3
"""Symbolically execute the REAL compiled C++ of pass folds and discharge soundness per path.

Compiles a fold written against the symbolic-LLVM shim, enumerates the pass's actual control-flow
paths (the analysis queries are choice points), and for every rewriting path proves the rewrite
refines the input under the facts the taken branches established. A fold that rewrites on a path
with insufficient established facts (an under-guarded pass) is refuted with a concrete witness --
caught by executing the genuine branches, not a pattern match. Needs clang++ and z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.symexec import real_pass as R  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "symexec_folds.cpp"
DEFAULT_FOLDS = ("urem_guarded", "sdiv_guarded")


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--fold", action="append", default=[], help="fold name(s) to verify")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--clang", default="clang++")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    clang = _resolve(args.clang, "/usr/bin/clang++")
    if z3 is None or clang is None:
        print(json.dumps({"status": "skipped", "reason": "z3 or clang++ not found"}))
        return 0

    exe = R.compile_harness(str(args.source), clang=clang)
    if exe is None:
        print(json.dumps({"status": "error", "reason": "harness failed to compile"}))
        return 1

    folds = args.fold or list(DEFAULT_FOLDS)
    results = [R.verify_fold(z3, exe, f) for f in folds]
    ok = all(r["ok"] for r in results)
    report = {"results": results, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"folds": len(results),
                      "proved": sum(r["proved"] for r in results),
                      "refuted": sum(r["refuted"] for r in results), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{'ok ' if r['ok'] else 'BAD'}] {r['fold']}: {r['paths']} paths, "
              f"{r['proved']} proved / {r['refuted']} refuted", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
