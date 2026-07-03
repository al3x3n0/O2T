#!/usr/bin/env python3
"""Prove loop transforms for UNBOUNDED trip counts via 1-induction (base + step).

Bounded unrolling (cv-prove-loop) only settles a fixed iteration count. This proves
a loop transform sound for EVERY trip count n with a constant-size proof, by an
inductive invariant R over the loop state:

  BASE:  R holds at the loop pre-header (iteration 0);
  STEP:  R holds before an iteration  =>  R holds after it.

From BASE + STEP, R holds at every iteration, hence at the (symbolic, unbounded)
exit -- where R yields equality of the original and transformed results. Two
invariant shapes are exercised:

  * lockstep equality (LICM / unswitching): acc_orig == acc_xform every iteration;
  * relational invariant (strength reduction): the incremental accumulator tracks
    i*c, so `acc += c` replaces `i*c` for ALL i -- the inductive heart, which
    bounded unrolling cannot generalize.

Each obligation `assumptions => before == after` is discharged with Z3 (UNSAT of
`assumptions /\\ before != after` == valid). A broken transform fails the STEP
(SAT) -- caught.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from o2t.formal_ir import FormalContext, typed_expr_to_smt  # noqa: E402


def v(name):
    return {"op": "var", "name": name}


def c(value):
    return {"op": "bvconst", "bits": 32, "value": value}


def op(o, *args):
    return {"op": o, "args": list(args)}


def lower(expr, variables):
    ctx = FormalContext(set(), variable_bits={name: 32 for name in variables})
    return typed_expr_to_smt(expr, set(variables), ctx).smt


def discharge(z3_bin, variables, assumptions, before, after):
    """Return the Z3 verdict for `assumptions /\\ before != after` (unsat == valid)."""
    decls = [f"(declare-const {name} (_ BitVec 32))" for name in variables]
    asserts = [f"(assert {lower(a, variables)})" for a in assumptions]
    smt = "\n".join(["(set-logic QF_BV)", *decls, *asserts,
                     f"(assert (not (= {lower(before, variables)} {lower(after, variables)})))",
                     "(check-sat)", ""])
    proc = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
    return proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else "error"


ACC, I, A, B, T, Cc, D = v("ACC"), v("i"), v("a"), v("b"), v("t"), v("c"), v("d")

# Each case: base and step obligations, with the expected Z3 verdict (unsat==valid).
CASES = [
    # LICM: invariant t == a+b hoisted; lockstep acc_orig == acc_xform.
    dict(name="licm-lockstep-unbounded-sound",
         variables=["ACC", "a", "b", "t", "z"],
         base=(([], v("z"), v("z")), "unsat"),                # both accumulators init to z
         step=(([op("eq", T, op("bvadd", A, B))],             # assume t == a+b
                op("bvadd", ACC, op("bvadd", A, B)),          # orig body: acc + (a+b)
                op("bvadd", ACC, T)), "unsat")),              # xform body: acc + t
    # Strength reduction: replace i*c by a running accumulator acc==i*c.
    dict(name="strength-reduction-unbounded-sound",
         variables=["ACC", "i", "c"],
         base=(([], c(0), op("bvmul", c(0), Cc)), "unsat"),   # acc0 == 0*c
         step=(([op("eq", ACC, op("bvmul", I, Cc))],          # assume acc == i*c
                op("bvadd", ACC, Cc),                         # acc += c
                op("bvmul", op("bvadd", I, c(1)), Cc)), "unsat")),  # == (i+1)*c
    # teeth: claiming `acc += c` realizes multiply-by-d (d != c) fails the STEP.
    dict(name="strength-reduction-wrong-stride-caught",
         variables=["ACC", "i", "c", "d"],
         base=(([], c(0), op("bvmul", c(0), D)), "unsat"),    # base still holds
         step=(([op("eq", ACC, op("bvmul", I, D))],
                op("bvadd", ACC, Cc),
                op("bvmul", op("bvadd", I, c(1)), D)), "sat")),  # STEP must fail
]


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
    for case in CASES:
        entry = {"case": case["name"]}
        ok = True
        for kind in ("base", "step"):
            (assumptions, before, after), expected = case[kind]
            verdict = discharge(z3_bin, case["variables"], assumptions, before, after)
            entry[kind] = verdict
            entry[kind + "_expected"] = expected
            ok = ok and verdict == expected
        entry["ok"] = ok
        results.append(entry)

    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['case']}: base={r['base']} step={r['step']}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
