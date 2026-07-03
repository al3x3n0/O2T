#!/usr/bin/env python3
"""Prove bounded loop transforms: unrolling, LICM, loop unswitching.

A loop with a bounded trip count is a finite fold over its iterations, so the
optimization reduces to an expression equivalence Z3 can settle (bounded model
checking of the transform). Iteration values are modeled as independent variables
(i0,i1,... / x0,x1,...):

  * unroll-by-2: a left-assoc accumulation == the 2-at-a-time grouping;
  * LICM: hoisting a loop-INVARIANT factor C=(a+b) out of `sum C*i_k` -> C*sum(i_k)
    is sound; hoisting a per-iteration VARIANT (a+i_k) is UNSOUND;
  * loop unswitching: `sum (c ? t_k : e_k)` with c INVARIANT == `c ? sum t_k : sum e_k`;
    with a per-iteration condition it is UNSOUND.

UNSAT == sound; a transform that wrongly assumes invariance expects SAT.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from o2t.formal_ir import equivalence_smt, pair_for_formal  # noqa: E402

ZERO = {"op": "bvconst", "bits": 32, "value": 0}


def v(name):
    return {"op": "var", "name": name}


def op(o, *args):
    return {"op": o, "args": list(args)}


def add(*xs):
    acc = xs[0]
    for x in xs[1:]:
        acc = op("bvadd", acc, x)
    return acc


def mul(x, y):
    return op("bvmul", x, y)


def cond(name):
    return {"op": "ne", "args": [v(name), ZERO]}


def ite(c, t, e):
    return {"op": "ite", "args": [c, t, e]}


A, B = v("a"), v("b")
I0, I1, I2 = v("i0"), v("i1"), v("i2")
X0, X1, X2, X3 = v("x0"), v("x1"), v("x2"), v("x3")
T0, T1, E0, E1 = v("t0"), v("t1"), v("e0"), v("e1")
INV = op("bvadd", A, B)  # loop-invariant value

CASES = [
    # unroll-by-2: left-assoc fold == 2-at-a-time grouping (associativity)
    ("loop-unroll-by-2-sound",
     add(add(add(X0, X1), X2), X3),
     op("bvadd", op("bvadd", X0, X1), op("bvadd", X2, X3)), "unsat"),
    # LICM: hoist invariant C out of sum(C*i_k) -> C*sum(i_k)
    ("licm-invariant-hoist-sound",
     add(mul(INV, I0), mul(INV, I1), mul(INV, I2)),
     mul(INV, add(I0, I1, I2)), "unsat"),
    # hoisting a per-iteration VARIANT (a+i_k) as if invariant is UNSOUND
    ("licm-variant-hoist-unsound",
     add(mul(op("bvadd", A, I0), X0), mul(op("bvadd", A, I1), X1)),
     mul(op("bvadd", A, I0), add(X0, X1)), "sat"),
    # unswitch with an INVARIANT condition c
    ("loop-unswitch-invariant-sound",
     add(ite(cond("c"), T0, E0), ite(cond("c"), T1, E1)),
     ite(cond("c"), add(T0, T1), add(E0, E1)), "unsat"),
    # unswitch with a per-iteration condition (c0 != c1) is UNSOUND
    ("loop-unswitch-variant-unsound",
     add(ite(cond("c0"), T0, E0), ite(cond("c1"), T1, E1)),
     ite(cond("c0"), add(T0, T1), add(E0, E1)), "sat"),
]

VARS = ["a", "b", "c", "c0", "c1", "i0", "i1", "i2",
        "x0", "x1", "x2", "x3", "t0", "t1", "e0", "e1"]


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
        verdict = run_z3(z3_bin, equivalence_smt(label, "loop", pair))
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
