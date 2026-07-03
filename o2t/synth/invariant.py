#!/usr/bin/env python3
"""Synthesize the inductive loop invariant automatically (template-based search).

cv-prove-loop-induction needs the invariant R hand-supplied. This INFERS it: given
a loop's accumulator recurrence (init + per-iteration step), it searches an affine
template `acc == offset + i*stride` over the loop's symbolic constants and checks
each candidate with Z3:

  BASE:  init == RHS[i := 0]
  STEP:  (acc == RHS)  =>  step(acc, i) == RHS[i := i+1]

The first candidate that discharges BOTH is a sound inductive invariant for ALL
trip counts (e.g. for `acc += c` it finds `acc == i*c`). Synthesis is SOUND but
basis-limited: a loop needing a non-affine invariant (e.g. `acc += i`, whose closed
form is quadratic) honestly reports "no invariant found" rather than fabricating one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from o2t.formal_ir import FormalContext, typed_expr_to_smt  # noqa: E402

ACC, I = {"op": "var", "name": "ACC"}, {"op": "var", "name": "i"}


def v(name):
    return {"op": "var", "name": name}


def c(value):
    return {"op": "bvconst", "bits": 32, "value": value}


def op(o, *args):
    return {"op": o, "args": list(args)}


def subst(node, name, repl):
    if isinstance(node, dict):
        if node.get("op") == "var" and node.get("name") == name:
            return repl
        if "args" in node:
            return {**node, "args": [subst(a, name, repl) for a in node["args"]]}
    return node


def lower(expr, variables):
    ctx = FormalContext(set(), variable_bits={name: 32 for name in variables})
    return typed_expr_to_smt(expr, set(variables), ctx).smt


def valid(z3_bin, variables, assumptions, before, after):
    """True iff `assumptions => before == after` is valid (Z3 unsat of the negation)."""
    decls = [f"(declare-const {name} (_ BitVec 32))" for name in variables]
    asserts = [f"(assert {lower(a, variables)})" for a in assumptions]
    smt = "\n".join(["(set-logic QF_BV)", *decls, *asserts,
                     f"(assert (not (= {lower(before, variables)} {lower(after, variables)})))",
                     "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return out.splitlines()[0].strip() == "unsat" if out else False


def candidate_invariants(consts):
    """affine template: offset + i*stride, over {0,1,consts} and a doubled const."""
    offsets = [c(0)] + [v(x) for x in consts]
    strides = [c(0), c(1)] + [v(x) for x in consts]
    if consts:
        strides.append(op("bvadd", v(consts[0]), v(consts[0])))  # 2*c0
    seen, out = set(), []
    for off in offsets:
        for st in strides:
            rhs = op("bvadd", off, op("bvmul", I, st))
            key = json.dumps(rhs, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(rhs)
    return out


def render(expr):
    o = expr.get("op")
    if o == "var":
        return expr["name"]
    if o == "bvconst":
        return str(expr["value"])
    sym = {"bvadd": "+", "bvmul": "*", "bvsub": "-"}.get(o, o)
    return "(" + f" {sym} ".join(render(a) for a in expr["args"]) + ")"


def synthesize(z3_bin, model):
    consts = model["consts"]
    variables = ["ACC", "i"] + consts
    init, step = model["init"], model["step"]
    for rhs in candidate_invariants(consts):
        base_ok = valid(z3_bin, variables, [], init, subst(rhs, "i", c(0)))
        if not base_ok:
            continue
        step_ok = valid(z3_bin, variables, [op("eq", ACC, rhs)],
                        step, subst(rhs, "i", op("bvadd", I, c(1))))
        if step_ok:
            return {"invariant": "acc == " + render(rhs), "rhs": rhs}
    return None


# init = acc at iteration 0; step = acc' as a function of ACC and i (and consts).
MODELS = [
    dict(name="strength-reduction (acc += c)", consts=["c"], init=c(0),
         step=op("bvadd", ACC, v("c")), expect_found=True),
    dict(name="affine with offset (acc=b; acc += c)", consts=["c", "b"], init=v("b"),
         step=op("bvadd", ACC, v("c")), expect_found=True),
    dict(name="doubled stride (acc += c+c)", consts=["c"], init=c(0),
         step=op("bvadd", ACC, op("bvadd", v("c"), v("c"))), expect_found=True),
    # closed form is quadratic (n(n-1)/2) -> no affine invariant; must report None.
    dict(name="quadratic (acc += i) -- no affine invariant", consts=["c"], init=c(0),
         step=op("bvadd", ACC, I), expect_found=False),
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
    for model in MODELS:
        found = synthesize(z3_bin, model)
        ok = (found is not None) == model["expect_found"]
        results.append({"loop": model["name"], "expect_found": model["expect_found"],
                        "synthesized": found["invariant"] if found else None, "ok": ok})

    failed = [r for r in results if not r["ok"]]
    report = {"models": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"models": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['loop']}: {r['synthesized'] or 'no affine invariant'}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
