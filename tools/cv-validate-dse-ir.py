#!/usr/bin/env python3
"""Closed-loop translation validation for DSE: prove the REAL `opt -passes=dse` output.

Runs the actual `opt -passes=dse` on a `.ll`, parses the LITERAL store/load instructions of the
before and after IR, and proves each function's final memory preserved over a theory of arrays --
so the proof is about the instructions the compiler really emitted, not an abstract model. Only
ESCAPING memory (parameter/global pointers) is validated; functions with local allocas are
declined (they need escape analysis). Needs z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import dse_ir  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "dse_ir_cases.ll"


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
    opt_text = dse_ir.run_dse(src, opt)
    if opt_text is None:
        print(json.dumps({"status": "error", "reason": "opt -passes=dse failed"}))
        return 1

    results = [dse_ir.validate_dse(z3, src, opt_text, fn) for fn in dse_ir.function_names(src)]
    validated = [r for r in results if r["status"] in ("proved", "refuted")]
    refuted = [r for r in validated if r["status"] == "refuted"]
    ok = bool(validated) and not refuted
    report = {"results": results, "validated": len(validated),
              "proved": len(validated) - len(refuted), "refuted": len(refuted), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"validated": len(validated), "proved": report["proved"],
                      "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']:11}] {r.get('function', '?')} "
              f"{r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
