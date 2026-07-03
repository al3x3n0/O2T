#!/usr/bin/env python3
"""Closed-loop translation validation for Mem2Reg/promotion: prove the REAL `opt -passes=mem2reg`.

Mem2Reg deletes an alloca/store/load and builds phi nodes. This proves the promoted (SSA+phi)
function returns the same value as the original (memory) one, for all inputs and branch conditions,
by symbolically executing both over the shared CFG: the promoted cell's value is threaded through
stores and merged by the came-via-predecessor conditions, and each phi is resolved by the same
conditions; the returns are proved equal (QF_BV + booleans). A phi placed with swapped incoming
values is refuted with a witness. Acyclic CFGs; an unmodeled shape (loop, etc.) is declined. Needs
z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import mem2reg_ir as m2r  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "mem2reg_ir_cases.ll"


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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
    opt_text = m2r.run_mem2reg(src, opt)
    if opt_text is None:
        print(json.dumps({"status": "error", "reason": "opt -passes=mem2reg failed"}))
        return 1

    results = [m2r.validate_mem2reg(z3, src, opt_text, fn) for fn in m2r.function_names(src)]
    proved = [r for r in results if r["status"] == "proved"]
    refuted = [r for r in results if r["status"] == "refuted"]
    unsupported = [r for r in results if r["status"] == "unsupported"]
    # a phi must have actually been built somewhere (promotion happened) -- non-vacuous.
    promoted = "phi " in opt_text and "alloca" not in opt_text
    ok = not refuted and bool(proved)
    report = {"results": results, "proved": len(proved), "refuted": len(refuted),
              "unsupported": len(unsupported), "promoted": promoted, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"proved": len(proved), "refuted": len(refuted),
                      "unsupported": len(unsupported), "promoted": promoted, "ok": ok},
                     sort_keys=True))
    for r in results:
        print(f"  [{r['status']:11}] {r['function']} {r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
