#!/usr/bin/env python3
"""Closed-loop translation validation for SLP: prove the REAL `opt -passes=slp-vectorizer` output.

Runs the actual SLP vectorizer on a `.ll`, then -- modeling memory as compile-time cells -- proves
that every output cell receives the same value in the scalar (before) and vectorized (after)
function, for all input values (QF_BV). So the proof is about the vector load/op/shuffle/store the
compiler really emitted; a wrong vectorization (bad op or lane permutation) is refuted with a
witness. A function using an unmodeled shape is soundly declined (`unsupported`). Needs z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import slp_ir  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "slp_ir_cases.ll"


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--threshold", default="-1", help="-slp-threshold (lower forces vectorization)")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = _resolve(args.z3_bin, "/opt/homebrew/bin/z3")
    opt = _resolve(args.opt_bin, "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print(json.dumps({"status": "skipped", "reason": "z3 or opt not found"}))
        return 0

    src = args.source.read_text()
    opt_text = slp_ir.run_slp(src, opt, threshold=args.threshold)
    if opt_text is None:
        print(json.dumps({"status": "error", "reason": "opt -passes=slp-vectorizer failed"}))
        return 1

    results = [slp_ir.validate_slp(z3, src, opt_text, fn) for fn in slp_ir.function_names(src)]
    proved = [r for r in results if r["status"] == "proved"]
    refuted = [r for r in results if r["status"] == "refuted"]
    unsupported = [r for r in results if r["status"] == "unsupported"]
    # at least one function must have actually vectorized (output differs from input).
    vectorized = "x i32>" in opt_text
    ok = not refuted and bool(proved) and vectorized
    report = {"results": results, "proved": len(proved), "refuted": len(refuted),
              "unsupported": len(unsupported), "vectorized": vectorized, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"proved": len(proved), "refuted": len(refuted),
                      "unsupported": len(unsupported), "vectorized": vectorized, "ok": ok},
                     sort_keys=True))
    for r in results:
        print(f"  [{r['status']:11}] {r['function']} {r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
