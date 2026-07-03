#!/usr/bin/env python3
"""Validate relational guard lifting (the `rel` assumption, G1).

A relational guard between two SSA values (e.g. isKnownNonEqual(A, B), or a
dominating icmp) is lifted to a `rel` assumption over both symbols and encoded
into the refinement query. This exercises the path end to end:

  * structural (always): lower_guard_effects turns a relation-assumption guard
    into a {op: rel, predicate, left, right} assumption on the formal.
  * semantic (with z3): a transform that is sound ONLY under the relation proves
    WITH the guard and is refuted WITHOUT it -- a teeth-test that the assumption
    is actually doing the work (not vacuously true).

Example: select(icmp eq a, b, x, y) == y holds iff a != b.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import equivalence_smt, pair_instances_for_formal  # noqa: E402


def load_infer():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from o2t.intent import infer
    return infer


def var(n):
    return {"op": "var", "name": n}


def cases():
    """Each: name, predicate, before-tree, after-tree, subjects (sound under the relation)."""
    a, b, x, y = var("a"), var("b"), var("x"), var("y")
    eq = {"op": "eq", "args": [a, b]}
    ne = {"op": "ne", "args": [a, b]}
    return [
        # select(a==b, x, y) == y  iff  a != b
        dict(name="select(a==b,x,y)->y under a!=b", predicate="ne",
             before={"op": "ite", "args": [eq, x, y]}, after=y),
        # select(a!=b, x, y) == x  iff  a != b
        dict(name="select(a!=b,x,y)->x under a!=b", predicate="ne",
             before={"op": "ite", "args": [ne, x, y]}, after=x),
    ]


def build_formal(before, after):
    return {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
            "variables": ["a", "b", "x", "y"], "poison_variables": [],
            "before": before, "after": after}


def z3_decide(z3_bin, formal):
    for _, pair in pair_instances_for_formal(formal):
        smt = equivalence_smt("guard", "guard-lift", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
        return res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
    return "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-z3", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    infer = load_infer()
    z3_bin = None if args.no_z3 else shutil.which(args.z3_bin)

    results = []
    structural_ok = proved = refuted = failed = 0
    for case in cases():
        rec = {"name": case["name"]}
        formal = build_formal(case["before"], case["after"])
        guard = {"kind": "known-nonequal", "role": "modeled-side-condition",
                 "formal_effect": "relation-assumption",
                 "formal_effect_args": {"assumption": {"op": "rel", "predicate": case["predicate"]}},
                 "left_subject": "A", "right_subject": "B"}
        ok = infer.lower_guard_effects(formal, {}, [guard], lambda s: {"A": "a", "B": "b"}.get(s), "g")
        assumptions = formal.get("assumptions") or []
        expected = {"op": "rel", "predicate": case["predicate"], "left": "a", "right": "b"}
        if not ok or expected not in assumptions:
            rec["status"] = "structural-fail"
            failed += 1
            results.append(rec)
            continue
        structural_ok += 1
        if z3_bin is None:
            rec["status"] = "structural-ok"
            results.append(rec)
            continue
        with_guard = z3_decide(z3_bin, formal)
        bare = build_formal(case["before"], case["after"])  # no assumptions
        without_guard = z3_decide(z3_bin, bare)
        if with_guard == "unsat" and without_guard == "sat":
            rec["status"] = "proved-with-refuted-without"
            proved += 1
            refuted += 1
        else:
            rec["status"] = f"unexpected: with={with_guard} without={without_guard}"
            failed += 1
        results.append(rec)

    backend = "z3" if z3_bin else "structural"
    summary = {"backend": backend, "cases": len(results), "structural_ok": structural_ok,
               "proved": proved, "refuted_without": refuted, "failed": failed, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    print(f"guard-lift self-test: {structural_ok} structural-ok, {proved} proved-with/refuted-without, "
          f"{failed} failed [{backend}]", file=sys.stderr)
    return 0 if failed == 0 and structural_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
