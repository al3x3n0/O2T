#!/usr/bin/env python3
"""Coupled / relational invariant synthesis for multi-accumulator loops.

A loop can carry several state variables that evolve TOGETHER, where one feeds
another. The classic case is the sum-of-sums:

    for i: { t = t + s; s = s + a; }

here `s` is linear (s == a*i) and `t` is quadratic BECAUSE it accumulates the linear
`s`. The invariants are coupled: t's proof needs s's invariant.

This synthesizes a CONJUNCTION of per-variable invariants `M*X == c0 + c1*i + c2*i^2`
in dependency order. When synthesizing X's invariant, the already-solved invariants
of the variables X depends on are asserted as STEP assumptions -- that is the
coupling. Each obligation is discharged over the integers (sound for every bitvector
width via the ring homomorphism Z -> Z/2^n, same as cv-synth-invariant-poly).

Independent accumulators -> two affine invariants; sum-of-sums -> {s==a*i, 2t==a*i^2-a*i};
a variable that accumulates a quadratic (-> cubic closed form) is honestly declined.
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
from pathlib import Path





from o2t.synth import poly
c, v, op, subst = poly.c, poly.v, poly.op, poly.subst
I = poly.I


def acc_var(name):
    return {"op": "var", "name": name}


_K = (1, 2, -1, -2)


def _dedup(nodes):
    seen, out = set(), []
    for n in nodes:
        key = poly.render(n)
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _atoms(relevant):
    """Simple coefficient atoms: signed small ints, +/- params, and `k*param`. Always includes the
    small ints (an integer-delta loop's closed form has integer coefficients, e.g. acc += i*2 ->
    i^2 - i)."""
    forms = [c(0), c(1), c(-1), c(2), c(-2)]
    for x in relevant:
        forms += [v(x), op("bvsub", c(0), v(x))]
    for x in relevant:
        forms += [op("bvmul", c(k), v(x)) for k in _K]
    return _dedup(forms)


def _linear_forms(relevant):
    """Atoms plus pairwise `k1*x + k2*y` combinations -- captures coefficients like `2b - a` (the
    closed form of `acc += a*i + b`) that single atoms cannot express. Used only for c1 (the linear
    coefficient); c2 (the quadratic coefficient) is a single Faulhaber term and stays simple."""
    forms = list(_atoms(relevant))
    for x, y in itertools.combinations(relevant, 2):
        for k1 in _K:
            for k2 in _K:
                forms.append(op("bvadd", op("bvmul", c(k1), v(x)), op("bvmul", c(k2), v(y))))
    return _dedup(forms)


def synth_one(z3_bin, name, init, delta, consts, all_vars, prior):
    """Synthesize `M*name == c0 + c1*i + c2*i^2`; `prior` = already-solved invariant eqs (asserted
    during STEP -- the coupling). The c1 basis is integer-linear in the params, so e.g.
    `acc += a*i + b` synthesizes `2*acc == a*i^2 + (2b-a)*i`. All candidates are discharged in ONE
    z3 process via batch_check; the basis is pruned to params reachable from this accumulator
    (its delta/init AND the coupled prior invariants)."""
    fv = set()
    poly.free_vars(init, fv)
    poly.free_vars(delta, fv)
    for inv in prior:                # a coupled accumulator's closed form can involve params that
        poly.free_vars(inv, fv)      # only appear through the prior invariants (e.g. t via s==a*i)
    relevant = [x for x in consts if x in fv]
    isq = op("bvmul", I, I)
    X = acc_var(name)
    c0_choices = [c(0)] + [v(x) for x in relevant]
    c1_choices = _linear_forms(relevant)
    c2_choices = _atoms(relevant)
    candidates, queries = [], []
    for m in (1, 2):
        for c0 in c0_choices:
            for c1 in c1_choices:
                for c2 in c2_choices:
                    p = op("bvadd", op("bvadd", c0, op("bvmul", c1, I)), op("bvmul", c2, isq))
                    inv = op("eq", op("bvmul", c(m), X), p)
                    base = ([], op("bvmul", c(m), init), subst(p, "i", c(0)))
                    step = (prior + [inv], op("bvmul", c(m), op("bvadd", X, delta)),
                            subst(p, "i", op("bvadd", I, c(1))))
                    candidates.append((m, p, inv))
                    queries += [base, step]
    verdicts = poly.batch_check(z3_bin, all_vars, queries)
    for idx, (m, p, inv) in enumerate(candidates):
        if verdicts[2 * idx] and verdicts[2 * idx + 1]:
            return {"m": m, "poly": p, "inv": inv,
                    "invariant": ("" if m == 1 else f"{m}*") + name
                    + " == " + poly.render(poly.simplify(p))}
    return None


def synthesize(z3_bin, accumulators, consts):
    all_vars = [a["name"] for a in accumulators] + ["i"] + consts
    solved, prior = [], []
    for acc in accumulators:
        found = synth_one(z3_bin, acc["name"], acc["init"], acc["delta"], consts, all_vars, prior)
        solved.append({"name": acc["name"],
                       "invariant": found["invariant"] if found else None})
        if found is None:
            return solved, False
        prior.append(found["inv"])
    return solved, True


MODELS = [
    dict(name="independent (s+=a, u+=b)", consts=["a", "b"], expect=True,
         accumulators=[dict(name="s", init=c(0), delta=v("a")),
                       dict(name="u", init=c(0), delta=v("b"))]),
    dict(name="sum-of-sums (t+=s; s+=a) -- coupled quadratic", consts=["a"], expect=True,
         accumulators=[dict(name="s", init=c(0), delta=v("a")),
                       dict(name="t", init=c(0), delta=v("s"))]),
    dict(name="triple sum (u+=t; t+=s; s+=a) -- u is cubic, declined", consts=["a"], expect=False,
         accumulators=[dict(name="s", init=c(0), delta=v("a")),
                       dict(name="t", init=c(0), delta=v("s")),
                       dict(name="u", init=c(0), delta=v("t"))]),
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
        solved, all_found = synthesize(z3_bin, model["accumulators"], model["consts"])
        ok = all_found == model["expect"]
        results.append({"loop": model["name"], "expect_all": model["expect"],
                        "all_found": all_found, "invariants": solved, "ok": ok})

    failed = [r for r in results if not r["ok"]]
    report = {"loops": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"loops": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        inv = "; ".join(i["invariant"] or f"{i['name']}=?" for i in r["invariants"])
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['loop']}: {inv}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
