#!/usr/bin/env python3
"""Auto-discover the output pairing for a two-loop transform (multi-output).

The relational synthesizer needs to know which output of loop A corresponds to which
of loop B. When B renames or permutes its accumulators (or has several candidates),
this DISCOVERS the bijection by proof: for each A-output it searches B's accumulators
for the one whose value provably stays equal -- under the synthesized auxiliary
invariants -- for all n.

  * A `acc += a`  vs B `{p += a, q += b}`  ->  discovers acc <-> p (not q);
  * A `{s += a, t += b}` vs B `{p += b, q += a}` (permuted) -> discovers s<->q, t<->p;
  * A `acc += a` vs B `{p += b, q += c}` -> NO partner (honestly reported).

Reuses cv-synth-relational (aux-invariant synthesis + integer discharge).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path





from o2t.synth import relational as rel
coupled = rel.coupled
poly = rel.poly
c, v, op = poly.c, poly.v, poly.op
find = rel.find


def discover(z3_bin, model):
    accs, consts = model["accumulators"], model["consts"]
    all_vars = [a["name"] for a in accs] + ["i"] + consts
    relation = list(model.get("given", []))

    for auxname in model.get("aux", []):
        a = find(accs, auxname)
        found = coupled.synth_one(z3_bin, auxname, a["init"], a["delta"], consts, all_vars, relation)
        if found is None:
            return {"status": "no-aux-invariant", "var": auxname}
        relation.append(found["inv"])

    pairing, used = {}, set()
    for oa in model["a_outputs"]:
        A = find(accs, oa)
        partner = None
        for ob in model["b_candidates"]:
            if ob in used:
                continue
            B = find(accs, ob)
            out_eq = op("eq", v(oa), v(ob))
            base = poly.valid(z3_bin, all_vars, relation, A["init"], B["init"])
            step = poly.valid(z3_bin, all_vars, relation + [out_eq],
                              op("bvadd", v(oa), A["delta"]), op("bvadd", v(ob), B["delta"]))
            if base and step:
                partner = ob
                break
        if partner is None:
            return {"status": "no-partner", "output": oa, "pairing": pairing}
        used.add(partner)
        pairing[oa] = partner
        relation.append(op("eq", v(oa), v(partner)))
    return {"status": "proved", "pairing": pairing}


MODELS = [
    dict(name="rename (A acc+=a ; B {p+=a, q+=b})", consts=["a", "b"], expect=True,
         expect_pairing={"acc": "p"},
         accumulators=[dict(name="acc", init=c(0), delta=v("a")),
                       dict(name="p", init=c(0), delta=v("a")),
                       dict(name="q", init=c(0), delta=v("b"))],
         a_outputs=["acc"], b_candidates=["p", "q"]),
    dict(name="permuted (A {s+=a,t+=b} ; B {p+=b,q+=a})", consts=["a", "b"], expect=True,
         expect_pairing={"s": "q", "t": "p"},
         accumulators=[dict(name="s", init=c(0), delta=v("a")),
                       dict(name="t", init=c(0), delta=v("b")),
                       dict(name="p", init=c(0), delta=v("b")),
                       dict(name="q", init=c(0), delta=v("a"))],
         a_outputs=["s", "t"], b_candidates=["p", "q"]),
    dict(name="no partner (A acc+=a ; B {p+=b,q+=c})", consts=["a", "b", "cc"], expect=False,
         accumulators=[dict(name="acc", init=c(0), delta=v("a")),
                       dict(name="p", init=c(0), delta=v("b")),
                       dict(name="q", init=c(0), delta=v("cc"))],
         a_outputs=["acc"], b_candidates=["p", "q"]),
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
        res = discover(z3_bin, model)
        proved = res["status"] == "proved"
        ok = (proved == model["expect"]) and (not proved or res["pairing"] == model.get("expect_pairing"))
        results.append({"loop": model["name"], "expect": model["expect"],
                        "status": res["status"], "pairing": res.get("pairing"), "ok": ok})

    failed = [r for r in results if not r["ok"]]
    report = {"models": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"models": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        p = ", ".join(f"{k}<->{w}" for k, w in (r["pairing"] or {}).items()) or r["status"]
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['loop']}: {p}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
