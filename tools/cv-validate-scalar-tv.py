#!/usr/bin/env python3
"""Closed-loop translation validation for ANY value-preserving scalar pass.

Generalizes the InstCombine validator: runs `opt -passes=<PASS>` on a `.ll`, translates the before
and after of each single-BB integer function to an SMT term for its returned value, and proves
them equal for all inputs (QF_BV). Works for instcombine, reassociate, early-cse, gvn, instsimplify
and any other pass that preserves scalar function semantics. A function using an unmodeled
instruction is soundly declined (`unsupported`). A wrong transform is refuted with a witness.
Needs z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "scalar_tv_cases.ll"


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--passes", default="reassociate", help="opt pass pipeline to validate")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = _resolve(args.z3_bin, "/opt/homebrew/bin/z3")
    opt = _resolve(args.opt_bin, "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print(json.dumps({"status": "skipped", "reason": "z3 or opt not found"}))
        return 0

    src = args.source.read_text()
    opt_text = scalar_ir.run_passes(src, args.passes, opt)
    if opt_text is None:
        print(json.dumps({"status": "error", "reason": f"opt -passes={args.passes} failed"}))
        return 1

    results = [scalar_ir.validate_transform(z3, src, opt_text, fn)
               for fn in scalar_ir.function_names(src)]
    proved = [r for r in results if r["status"] == "proved"]
    refuted = [r for r in results if r["status"] == "refuted"]
    unsupported = [r for r in results if r["status"] == "unsupported"]
    ok = not refuted and bool(proved)
    report = {"passes": args.passes, "results": results, "proved": len(proved),
              "refuted": len(refuted), "unsupported": len(unsupported), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passes": args.passes, "proved": len(proved), "refuted": len(refuted),
                      "unsupported": len(unsupported), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']:11}] {r['function']} {r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
