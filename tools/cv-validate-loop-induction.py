#!/usr/bin/env python3
"""UNBOUNDED loop equivalence: prove a structure-preserving pass keeps a loop's value for all n.

Runs `opt -passes=<P>` (default instcombine) on a loop `.ll`, then proves the original and the
transformed loop return the same value for EVERY trip count -- by induction over the loop-carried
state (equal init, equal guards, equal step under the guard, equal result on exit), with no
unrolling. A transform that changes the body's step or the exit value is refuted with a state
witness. A non-isomorphic / unsupported loop shape is declined. Needs z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_induction, scalar_ir  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "loop_induction_cases.ll"


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--passes", default="instcombine")
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
    after = scalar_ir.run_passes(src, args.passes, opt)
    if after is None:
        print(json.dumps({"status": "error", "reason": f"opt -passes={args.passes} failed"}))
        return 1

    results = [loop_induction.validate_loop_equiv(z3, src, after, fn)
               for fn in scalar_ir.function_names(src)]
    proved = [r for r in results if r["status"] == "proved"]
    refuted = [r for r in results if r["status"] == "refuted"]
    unsupported = [r for r in results if r["status"] == "unsupported"]
    ok = not refuted and bool(proved)
    report = {"results": results, "proved": len(proved), "refuted": len(refuted),
              "unsupported": len(unsupported), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"proved": len(proved), "refuted": len(refuted),
                      "unsupported": len(unsupported), "ok": ok}, sort_keys=True))
    for r in results:
        detail = r.get("reason") or (r.get("parts") and "init/guard/step/result") or ""
        print(f"  [{r['status']:11}] {r['function']} {detail}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
