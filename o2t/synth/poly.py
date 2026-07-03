#!/usr/bin/env python3
"""Synthesize POLYNOMIAL loop invariants (affine/quadratic/cubic), proved over the integers.

Affine synthesis declines `acc += i` (closed form is the triangular sum i*(i-1)/2)
because /2 is not exact in fixed-width bitvectors. This searches a polynomial template
with an integer MULTIPLIER that clears the division:

    M * acc  ==  c0 + c1*i + c2*i*i        (M in {1, 2}; c0,c1,c2 over {0,1,consts})

so `acc += i` is proved as `2*acc == i*i - i` (multiply through by 2 -- no division).

PROVED OVER THE INTEGERS, not bv32: 32-bit nonlinear multiplication is intractable
for Z3's bit-blasting, but the obligations are POLYNOMIAL IDENTITIES, which Z3 settles
over Int instantly. Soundness for the real (modular) accumulator follows from the ring
homomorphism Z -> Z/2^n: +, -, * commute with `mod 2^n`, so an identity that holds over
Z holds in every bitvector width. The template subsumes affine (c2=0, M=1); a recurrence
whose closed form exceeds degree 2 (e.g. `acc += i*i`, cubic) is honestly declined.
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path


ACC, I = {"op": "var", "name": "ACC"}, {"op": "var", "name": "i"}
INT_OP = {"bvadd": "+", "bvsub": "-", "bvmul": "*", "eq": "="}


def v(name):
    return {"op": "var", "name": name}


def c(value):
    return {"op": "bvconst", "bits": 32, "value": value}


def op(o, *a):
    return {"op": o, "args": list(a)}


def subst(node, name, repl):
    if isinstance(node, dict):
        if node.get("op") == "var" and node.get("name") == name:
            return repl
        if "args" in node:
            return {**node, "args": [subst(a, name, repl) for a in node["args"]]}
    return node


def lower_int(expr):
    """Lower the small DSL (var / bvconst / bvadd|sub|mul) to SMT-LIB integer terms."""
    o = expr.get("op")
    if o == "var":
        return expr["name"]
    if o == "bvconst":
        return str(expr["value"])
    return "(" + INT_OP[o] + " " + " ".join(lower_int(a) for a in expr["args"]) + ")"


def valid(z3_bin, variables, assumptions, before, after):
    """`assumptions => before == after` valid over the INTEGERS (Z3 unsat of negation)."""
    decls = [f"(declare-const {name} Int)" for name in variables]
    asserts = [f"(assert {lower_int(a)})" for a in assumptions]
    smt = "\n".join(["(set-logic ALL)", *decls, *asserts,
                     f"(assert (not (= {lower_int(before)} {lower_int(after)})))",
                     "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return bool(out) and out.splitlines()[0].strip() == "unsat"


def batch_check(z3_bin, variables, queries):
    """Discharge many `(assumptions, before, after)` obligations in ONE z3 process via
    push/pop -- avoids per-candidate spawn overhead. Returns a list of booleans (valid)."""
    lines = ["(set-logic ALL)"] + [f"(declare-const {n} Int)" for n in variables]
    for assumptions, before, after in queries:
        lines.append("(push 1)")
        lines += [f"(assert {lower_int(a)})" for a in assumptions]
        lines.append(f"(assert (not (= {lower_int(before)} {lower_int(after)})))")
        lines += ["(check-sat)", "(pop 1)"]
    out = subprocess.run([z3_bin, "-in"], input="\n".join(lines) + "\n",
                         capture_output=True, text=True).stdout.split()
    verdicts = [t for t in out if t in ("sat", "unsat", "unknown")]
    return [v == "unsat" for v in verdicts]


def signed(atoms):
    out = []
    for a in atoms:
        out.append(a)
        out.append(op("bvsub", c(0), a))  # -a (two's complement)
    return out


def simplify(expr):
    if not isinstance(expr, dict) or "args" not in expr:
        return expr
    o = expr["op"]
    a = [simplify(x) for x in expr["args"]]

    def zero(x):
        return x.get("op") == "bvconst" and x.get("value") == 0

    def one(x):
        return x.get("op") == "bvconst" and x.get("value") == 1
    if o == "bvadd":
        if zero(a[0]):
            return a[1]
        if zero(a[1]):
            return a[0]
    if o == "bvsub" and zero(a[1]):
        return a[0]
    if o == "bvmul":
        if zero(a[0]) or zero(a[1]):
            return c(0)
        if one(a[0]):
            return a[1]
        if one(a[1]):
            return a[0]
    return {"op": o, "args": a}


def render(expr):
    o = expr.get("op")
    if o == "var":
        return expr["name"]
    if o == "bvconst":
        val = expr["value"]
        return "-1" if val == (1 << 32) - 1 else str(val)
    sym = {"bvadd": "+", "bvmul": "*", "bvsub": "-"}.get(o, o)
    return "(" + f" {sym} ".join(render(a) for a in expr["args"]) + ")"


def obligations(m, init, delta, poly):
    """(BASE, STEP) each as (assumptions, before, after) for a candidate poly."""
    base = ([], op("bvmul", c(m), init), subst(poly, "i", c(0)))
    step = ([op("eq", op("bvmul", c(m), ACC), poly)],
            op("bvmul", c(m), op("bvadd", ACC, delta)),
            subst(poly, "i", op("bvadd", I, c(1))))
    return base, step


def free_vars(node, out):
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["name"])
        for a in node.get("args", []) or []:
            free_vars(a, out)


def degree_i(node):
    """Degree of `node` in the loop index i (0 for constants/params)."""
    if not isinstance(node, dict):
        return 0
    o = node.get("op")
    if o == "var":
        return 1 if node.get("name") == "i" else 0
    if o == "bvconst":
        return 0
    if o == "bvmul":
        return sum(degree_i(a) for a in node["args"])
    if o in ("bvadd", "bvsub"):
        return max(degree_i(a) for a in node["args"])
    return 0


def ipow(j):
    p = I
    for _ in range(j - 1):
        p = op("bvmul", p, I)
    return p


# Faulhaber: summing a degree-d delta gives a degree-(d+1) closed form whose
# denominator divides (d+1)!. The MULTIPLIER M clears it: 1!,2!,3! for delta degree 0,1,2.
FACTORIAL_M = {0: [1], 1: [1, 2], 2: [1, 2, 6]}


def synthesize(z3_bin, consts, init, delta):
    """Degree-AWARE template `M*acc == c0 + c1*i + ... + c_{d+1}*i^{d+1}`, discharged
    over Z. Handles affine (acc+=a), quadratic (acc+=i, acc+=c*i) and CUBIC closed
    forms (acc+=i*i -> 6*acc == 2i^3-3i^2+i). Delta degree > 2 exceeds the template
    and is declined."""
    variables = ["ACC", "i"] + consts
    d = degree_i(delta)
    if d > 2:
        return None
    deg = d + 1
    # Only consts that actually appear in the recurrence can appear in its closed form,
    # so restrict the coefficient basis to those (excludes e.g. the loop bound n) --
    # keeps the cubic search small.
    fv = set()
    free_vars(init, fv)
    free_vars(delta, fv)
    relevant = [x for x in consts if x in fv]
    # coefficient atoms: small integers + relevant consts + (small int)*const products.
    atoms = [c(k) for k in range(4)] + [v(x) for x in relevant] \
        + [op("bvmul", c(k), v(x)) for k in (2, 3) for x in relevant]
    coeffs = signed(atoms)
    candidates, queries = [], []
    for m in FACTORIAL_M[d]:
        for c0 in [c(0)] + [v(x) for x in relevant]:
            for cs in itertools.product(coeffs, repeat=deg):  # (c1, ..., c_{deg})
                poly = c0
                for j, cj in enumerate(cs, start=1):
                    poly = op("bvadd", poly, op("bvmul", cj, ipow(j)))
                base, step = obligations(m, init, delta, poly)
                candidates.append((m, poly))
                queries += [base, step]
    verdicts = batch_check(z3_bin, variables, queries)
    for idx, (m, poly) in enumerate(candidates):
        if verdicts[2 * idx] and verdicts[2 * idx + 1]:
            return {"multiplier": m, "invariant": ("" if m == 1 else f"{m}*")
                    + "acc == " + render(simplify(poly))}
    return None


# init = acc at iteration 0; delta = per-iteration increment (may reference i).
MODELS = [
    dict(name="acc += a (affine, subsumed)", consts=["a"], init=c(0), delta=v("a"), expect=True),
    dict(name="acc += i (triangular, quadratic)", consts=[], init=c(0), delta=I, expect=True),
    dict(name="acc += c*i (quadratic)", consts=["c"], init=c(0), delta=op("bvmul", v("c"), I), expect=True),
    dict(name="acc += i*i (sum of squares, CUBIC)", consts=[], init=c(0),
         delta=op("bvmul", I, I), expect=True),
    dict(name="acc += i*i*i (quartic -- exceeds degree 3, declined)", consts=[], init=c(0),
         delta=op("bvmul", I, op("bvmul", I, I)), expect=False),
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
        found = synthesize(z3_bin, model["consts"], model["init"], model["delta"])
        ok = (found is not None) == model["expect"]
        results.append({"loop": model["name"], "expect_found": model["expect"],
                        "synthesized": found["invariant"] if found else None, "ok": ok})

    failed = [r for r in results if not r["ok"]]
    report = {"models": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"models": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['loop']}: {r['synthesized'] or 'no quadratic invariant'}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
