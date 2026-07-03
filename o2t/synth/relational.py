#!/usr/bin/env python3
"""Two-loop relational (simulation) synthesis: prove a loop TRANSFORM sound for all n.

Given an original loop A and a transformed loop B stepping in lockstep over the same
index i, this synthesizes a simulation relation R(state_A, state_B) and proves the
loops produce the same output -- for EVERY trip count, not a bounded one. R is:

    {  auxiliary invariants of B's extra induction variables  }  /\\  outputA == outputB

The auxiliary invariants (e.g. the running IV k == c*i of a strength-reduced loop) are
synthesized; the output equality is then shown inductive UNDER them. The relation is
typically far simpler than either loop's closed form -- that is the power of relating
the two executions directly instead of computing each.

  * strength reduction: A does `accA += i*c` (a multiply each iteration); B does
    `accB += k; k += c`. R = {k == c*i, accA == accB} -- proved, no quadratic needed.
  * LICM: A recomputes `a+b`; B uses a hoisted `t == a+b`. R = {accA == accB}.
  * a wrong stride (B increments k by d != c) admits NO relation -- caught.

Discharged over the integers (sound for every bitvector width). Reuses the coupled
synthesizer's per-variable machinery.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path





from o2t.synth import coupled
poly = coupled.poly
c, v, op, subst = poly.c, poly.v, poly.op, poly.subst
I = poly.I


def find(accs, name):
    return next(a for a in accs if a["name"] == name)


def render_rel(eq):
    return poly.render(poly.simplify(eq["args"][0])) + " == " + poly.render(poly.simplify(eq["args"][1]))


def synthesize_relational(z3_bin, model):
    accs, consts = model["accumulators"], model["consts"]
    all_vars = [a["name"] for a in accs] + ["i"] + consts
    given = list(model.get("given", []))
    relation = list(given)

    # 1. synthesize the auxiliary invariants (B's running IVs), in order.
    for auxname in model.get("aux", []):
        a = find(accs, auxname)
        found = coupled.synth_one(z3_bin, auxname, a["init"], a["delta"], consts, all_vars, relation)
        if found is None:
            return {"status": "no-aux-invariant", "var": auxname}
        relation.append(found["inv"])

    # 2. the output equality must be inductive under the relation.
    oa, ob = model["output"]
    A, B = find(accs, oa), find(accs, ob)
    out_eq = op("eq", v(oa), v(ob))
    base = poly.valid(z3_bin, all_vars, relation, A["init"], B["init"])
    step = poly.valid(z3_bin, all_vars, relation + [out_eq],
                      op("bvadd", v(oa), A["delta"]), op("bvadd", v(ob), B["delta"]))
    if base and step:
        relation.append(out_eq)
        return {"status": "proved",
                "relation": [render_rel(r) for r in relation if r not in given],
                "given": [render_rel(g) for g in given]}
    return {"status": "output-not-preserved", "base": base, "step": step}


MODELS = [
    dict(name="strength reduction (accA+=i*c  vs  accB+=k;k+=c)", consts=["c"], expect=True,
         accumulators=[dict(name="accA", init=c(0), delta=op("bvmul", I, v("c"))),
                       dict(name="accB", init=c(0), delta=v("k")),
                       dict(name="k", init=c(0), delta=v("c"))],
         aux=["k"], output=("accA", "accB")),
    dict(name="LICM (accA+=a+b  vs  accB+=t, t==a+b hoisted)", consts=["a", "b"], expect=True,
         accumulators=[dict(name="accA", init=c(0), delta=op("bvadd", v("a"), v("b"))),
                       dict(name="accB", init=c(0), delta=v("t")),
                       dict(name="t", init=c(0), delta=c(0))],
         aux=[], given=[op("eq", v("t"), op("bvadd", v("a"), v("b")))], output=("accA", "accB")),
    dict(name="wrong stride (k+=d, d!=c) -- no relation", consts=["c", "d"], expect=False,
         accumulators=[dict(name="accA", init=c(0), delta=op("bvmul", I, v("c"))),
                       dict(name="accB", init=c(0), delta=v("k")),
                       dict(name="k", init=c(0), delta=v("d"))],
         aux=["k"], output=("accA", "accB")),
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
        res = synthesize_relational(z3_bin, model)
        proved = res["status"] == "proved"
        ok = proved == model["expect"]
        results.append({"transform": model["name"], "expect_proved": model["expect"],
                        "status": res["status"], "relation": res.get("relation"), "ok": ok})

    failed = [r for r in results if not r["ok"]]
    report = {"transforms": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"transforms": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        rel = " /\\ ".join(r["relation"]) if r["relation"] else r["status"]
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['transform']}: {rel}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
