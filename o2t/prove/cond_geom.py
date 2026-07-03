#!/usr/bin/env python3
"""Conditional and geometric loop recurrences -- beyond the polynomial template.

Two recurrence shapes the polynomial synth can't handle, each with a tractable angle,
both discharged over Z (sound for every bitvector width):

CONDITIONAL (invariant condition) -- a CLOSED FORM with an ite-valued stride:
    for i: acc += (cond ? a : b)   ==>   acc == i * (cond ? a : b)
  proved by 1-induction (base + step); the stride is loop-invariant so it factors out.

GEOMETRIC (acc *= c) -- exponential, NO polynomial closed form, but the TRANSFORM is
provable RELATIONALLY by a lockstep invariant acc_A == acc_B (the per-iteration
multiply preserves equality):
    acc = acc*c   ==   acc = c*acc          (commute);
    acc = acc*(c*d)  ==  acc = (acc*c)*d     (reassociate);
    acc *= c   vs   acc *= d (d != c)        -> NOT equivalent, refuted.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ARITH = {"bvadd": "+", "bvsub": "-", "bvmul": "*"}


def v(name):
    return {"op": "var", "name": name}


def c(value):
    return {"op": "bvconst", "value": value}


def op(o, *a):
    return {"op": o, "args": list(a)}


def ite(cond, t, e):
    return {"op": "ite", "args": [cond, t, e]}


def lower(e):
    o = e["op"]
    if o == "var":
        return e["name"]
    if o == "bvconst":
        return str(e["value"])
    if o in ARITH:
        return "(" + ARITH[o] + " " + " ".join(lower(a) for a in e["args"]) + ")"
    if o == "ite":
        return "(ite " + " ".join(lower(a) for a in e["args"]) + ")"
    if o == "eq":
        return "(= " + lower(e["args"][0]) + " " + lower(e["args"][1]) + ")"
    if o == "ne":
        return "(not (= " + lower(e["args"][0]) + " " + lower(e["args"][1]) + "))"
    if o in ("and", "or"):
        return "(" + o + " " + " ".join(lower(a) for a in e["args"]) + ")"
    if o == "not":
        return "(not " + lower(e["args"][0]) + ")"
    raise ValueError(o)


def subst(node, name, repl):
    if isinstance(node, dict):
        if node.get("op") == "var" and node.get("name") == name:
            return repl
        if "args" in node:
            return {**node, "args": [subst(a, name, repl) for a in node["args"]]}
    return node


def valid(z3_bin, variables, assumptions, before, after):
    decls = [f"(declare-const {n} Int)" for n in variables]
    asserts = [f"(assert {lower(a)})" for a in assumptions]
    smt = "\n".join(["(set-logic ALL)", *decls, *asserts,
                     f"(assert (not (= {lower(before)} {lower(after)})))",
                     "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return bool(out) and out.splitlines()[0].strip() == "unsat"


ACC, I = v("ACC"), v("i")
COND = ite(op("ne", v("c"), c(0)), v("a"), v("b"))  # (c != 0) ? a : b

# Each case: kind + a closure producing (base_ok, step_ok).
def closed_form(z3, variables, init, nxt, rhs):
    base = valid(z3, variables, [], init, subst(rhs, "i", c(0)))
    step = valid(z3, variables, [op("eq", ACC, rhs)], nxt, subst(rhs, "i", op("bvadd", I, c(1))))
    return base and step


def relational(z3, variables, init_a, init_b, next_a, next_b):
    base = valid(z3, variables, [], init_a, init_b)   # equal initial accumulators
    step = valid(z3, variables, [], next_a, next_b)   # acc_A==acc_B unified as ACC
    return base and step


def run_cases(z3_bin):
    out = []

    def rec(name, expect, proved):
        out.append({"case": name, "expected_proved": expect, "proved": proved,
                    "ok": proved == expect})

    # CONDITIONAL closed form: acc += (c?a:b)  ->  acc == i*(c?a:b)
    rec("cond-invariant-closed-form", True,
        closed_form(z3_bin, ["ACC", "i", "c", "a", "b"], c(0),
                    op("bvadd", ACC, COND), op("bvmul", I, COND)))
    # GEOMETRIC relational transforms (no closed form needed)
    rec("geom-commute-sound", True,
        relational(z3_bin, ["ACC", "i", "s", "c"], v("s"), v("s"),
                   op("bvmul", ACC, v("c")), op("bvmul", v("c"), ACC)))
    rec("geom-reassoc-sound", True,
        relational(z3_bin, ["ACC", "i", "s", "c", "d"], v("s"), v("s"),
                   op("bvmul", ACC, op("bvmul", v("c"), v("d"))),
                   op("bvmul", op("bvmul", ACC, v("c")), v("d"))))
    rec("geom-wrong-factor-unsound", False,
        relational(z3_bin, ["ACC", "i", "s", "c", "d"], v("s"), v("s"),
                   op("bvmul", ACC, v("c")), op("bvmul", ACC, v("d"))))
    return out


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

    results = run_cases(z3_bin)
    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['case']}: proved={r['proved']} (expect {r['expected_proved']})",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
