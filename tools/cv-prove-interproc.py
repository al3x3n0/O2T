#!/usr/bin/env python3
"""Prove interprocedural transforms: inlining (+ simplify/fold), dead-arg removal.

Inlining replaces a call with the callee body, formal parameters substituted by the
actual arguments -- then the surrounding optimizer simplifies. The end-to-end
transform is sound iff the inlined-and-simplified code computes the call's value.
Modeled by substituting the actuals into the body and proving the result equals the
optimized form (with Z3):

  * inline + simplify: f(p)=(p+0)*1 ; f(a) -> a;
  * inline + constant fold: f(p,q)=p+q ; f(3,4) -> 7;
  * inline + distribute: f(p,q,r)=p*(q+r) ; f(a,b,c) -> a*b + a*c;
  * arg-swap inlining is UNSOUND (a-b vs b-a);
  * dropping a USED argument (dead-arg elim applied wrongly) is UNSOUND.

UNSAT == sound; a broken interprocedural transform expects SAT.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import equivalence_smt, pair_for_formal  # noqa: E402


def v(name):
    return {"op": "var", "name": name}


def c(value):
    return {"op": "bvconst", "bits": 32, "value": value}


def op(o, *args):
    return {"op": o, "args": list(args)}


A, B, C = v("a"), v("b"), v("c")

CASES = [
    # f(p)=(p+0)*1 ; inline f(a) then simplify -> a
    ("inline-then-simplify-sound",
     op("bvmul", op("bvadd", A, c(0)), c(1)), A, "unsat"),
    # f(p,q)=p+q ; inline f(3,4) then constant-fold -> 7
    ("inline-const-fold-sound",
     op("bvadd", c(3), c(4)), c(7), "unsat"),
    # f(p,q,r)=p*(q+r) ; inline f(a,b,c) then distribute -> a*b + a*c
    ("inline-distribute-sound",
     op("bvmul", A, op("bvadd", B, C)),
     op("bvadd", op("bvmul", A, B), op("bvmul", A, C)), "unsat"),
    # arg-swap inlining: f(p,q)=p-q called f(a,b) must be a-b, not b-a
    ("inline-arg-swap-unsound",
     op("bvsub", A, B), op("bvsub", B, A), "sat"),
    # dead-arg elim is only sound for UNUSED args; here q is used (a+b)
    ("dead-arg-drop-used-unsound",
     op("bvadd", A, B), A, "sat"),
]

VARS = ["a", "b", "c"]


def formal_for(before, after):
    return {"domain": "scalar-bv32", "equivalence": "result", "variables": VARS,
            "poison_variables": [], "refinement": "refinement", "before": before, "after": after}


def run_z3(z3_bin, smt):
    proc = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
    return proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = []
    for label, before, after, expected in CASES:
        pair = pair_for_formal(formal_for(before, after))
        verdict = run_z3(z3_bin, equivalence_smt(label, "interproc", pair))
        results.append({"case": label, "expected": expected, "verdict": verdict,
                        "ok": verdict == expected})

    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['case']}: expected {r['expected']}, got {r['verdict']}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
