#!/usr/bin/env python3
"""UNBOUNDED validation of the real `opt -passes=loop-rotate` (guard-motion).

Runs the actual loop-rotate, reconstructs a canonical guard-on-current loop model from the rotated
do-while IR (pre-guard + bottom guard + lcssa), SELF-VERIFIES that model against the emitted
instructions, and proves it equivalent to the original loop for ALL trip counts via simulation with
automatic relation inference. A miscompiled rotation fails a self-check or the equivalence proof.
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
from o2t.validate import loop_rotate  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "loop_rotate_cases.ll"


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
    rot = loop_rotate.run_rotate(src, opt)
    if rot is None:
        print(json.dumps({"status": "error", "reason": "opt -passes=loop-rotate failed"}))
        return 1

    results = [loop_rotate.validate_rotate(z3, src, rot, fn)
               for fn in loop_rotate.function_names(src)]
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
        print(f"  [{r['status']:11}] {r['function']} {r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
