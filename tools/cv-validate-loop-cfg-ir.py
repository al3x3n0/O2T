#!/usr/bin/env python3
"""BOUNDED closed-loop translation validation for loop-CFG transforms (loop-rotate, unswitch).

For a constant-trip-count loop, fully unrolls the original and the `opt -passes=<transform>` output
to acyclic IR and proves them equivalent for all inputs -- so the loop transform is shown to
preserve the computation for that trip count, with two-sided teeth. A non-constant trip count is
not unrolled and is soundly declined (`unsupported`). Needs z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_cfg_ir  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "loop_cfg_ir_cases.ll"
DEFAULT_TRANSFORMS = ("loop-rotate", "simple-loop-unswitch")


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--transform", action="append", default=[],
                    help="loop transform(s) to validate (default: loop-rotate + unswitch)")
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
    transforms = args.transform or list(DEFAULT_TRANSFORMS)
    results = []
    for fn in loop_cfg_ir.function_names(src):
        for t in transforms:
            results.append(loop_cfg_ir.validate_loop_transform(z3, src, t, fn, opt))
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
        print(f"  [{r['status']:11}] {r['function']} via {r['transform']} "
              f"{r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
