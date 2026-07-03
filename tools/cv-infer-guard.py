#!/usr/bin/env python3
"""CEGIS guard inference: synthesize a sufficient precondition for a refuted formal.

When born-proven (cv-verify-candidates) refutes a lifted transform, the z3
counterexample is a concrete input where before != after. This tool turns that
refutation into a *discovered precondition*: it runs a counterexample-guided loop
that picks, from the assumption catalog, a guard that excludes the counterexample,
re-proves, and repeats -- converging on a conjunction of guards under which the
transform IS sound (or reporting that no catalog precondition suffices).

  catalog (instantiated over the formal's variables + literal constants):
    x != 0            (not-eq-zero)
    x is power-of-two
    a <pred> b        (rel -- the G1 relational vocabulary: ne/ult/ule/slt/sle)
    x <pred> C        (cmp against constants appearing in the formal)

A refutation thus becomes either a modeled side-condition (the inferred guard) or
an honest "genuinely unsound -- no precondition found".
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402

DEF_RE = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\)\s*#x([0-9a-fA-F]+)\)")
MASK = (1 << 32) - 1
MAX_GUARDS = 3  # max conjunction size before giving up


def to_signed(v: int) -> int:
    return v - (1 << 32) if v >> 31 else v


def collect_constants(node, out: set[int]) -> None:
    if isinstance(node, dict):
        if node.get("op") == "bvconst":
            out.add(int(node.get("value", 0)) & MASK)
        for arg in node.get("args", []) or []:
            collect_constants(arg, out)


def candidate_catalog(formal: dict) -> list[dict]:
    variables = list(formal.get("variables") or [])
    consts: set[int] = set()
    collect_constants(formal.get("before"), consts)
    collect_constants(formal.get("after"), consts)
    consts |= {0, 1}
    catalog: list[dict] = []
    # Distinctness guards first -- they are the weakest (least over-constraining)
    # and the most common real preconditions (x != 0, a != b), so CEGIS reports
    # the canonical precondition rather than a stronger sufficient one.
    for a, b in itertools.combinations(variables, 2):
        catalog.append({"op": "rel", "predicate": "ne", "left": a, "right": b})
    for x in variables:
        catalog.append({"op": "not-eq", "name": x, "value": 0})
    # Then ordering relations, constant comparisons, and power-of-two.
    for a, b in itertools.permutations(variables, 2):
        for pred in ("ult", "ule", "slt", "sle"):
            catalog.append({"op": "rel", "predicate": pred, "left": a, "right": b})
    for x in variables:
        for c in sorted(consts):
            for pred in ("ult", "ule", "ugt", "uge", "slt", "sle", "sgt", "sge"):
                catalog.append({"op": "cmp", "predicate": pred, "name": x, "value": c})
    for x in variables:
        catalog.append({"op": "power-of-two", "name": x, "nonzero": True})
    return catalog


def eval_guard(guard: dict, env: dict) -> bool:
    """True iff the guard holds at the counterexample assignment env (var->uint32)."""
    op = guard["op"]
    if op == "not-eq":
        return (env.get(guard["name"], 0) & MASK) != (guard["value"] & MASK)
    if op == "power-of-two":
        v = env.get(guard["name"], 0) & MASK
        return v != 0 and (v & (v - 1)) == 0
    if op in ("cmp", "rel"):
        if op == "cmp":
            a, b = env.get(guard["name"], 0) & MASK, guard["value"] & MASK
        else:
            a, b = env.get(guard["left"], 0) & MASK, env.get(guard["right"], 0) & MASK
        p = guard["predicate"]
        if p == "eq":
            return a == b
        if p == "ne":
            return a != b
        if p == "ult":
            return a < b
        if p == "ule":
            return a <= b
        if p == "ugt":
            return a > b
        if p == "uge":
            return a >= b
        sa, sb = to_signed(a), to_signed(b)
        return {"slt": sa < sb, "sle": sa <= sb, "sgt": sa > sb, "sge": sa >= sb}[p]
    raise ValueError(f"eval_guard: unsupported op {op}")


def prove(formal: dict, assumptions: list[dict], z3_bin: str):
    """Return ('proved', None) | ('refuted', env) | ('error', None)."""
    f = dict(formal)
    f["assumptions"] = assumptions
    try:
        pairs = pair_instances_for_formal(f)
    except FormalIrError:
        return ("error", None)
    for _, pair in pairs:
        smt = equivalence_smt("guard-infer", "cegis", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt + "\n(get-model)",
                             capture_output=True, text=True)
        out = res.stdout.strip()
        head = out.splitlines()[0] if out else "error"
        if head == "unsat":
            continue  # this instance proved; check the rest
        if head == "sat":
            model = {k: int(v, 16) for k, v in DEF_RE.findall(out)}
            env = {n: model.get(n, 0) for n in formal.get("variables", [])}
            return ("refuted", env)
        return ("error", None)
    return ("proved", None)


def assumptions_satisfiable(formal: dict, assumptions: list[dict], z3_bin: str) -> bool:
    """Reject VACUOUS guard sets: a sound precondition must admit some input.

    Trick: prove a deliberately-false equivalence (before=0, after=1) under the
    assumptions. The refinement query reduces to just the assumption conjunction,
    so z3 returns sat iff the assumptions are jointly satisfiable.
    """
    probe = {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
             "variables": list(formal.get("variables") or []), "poison_variables": [],
             "before": {"op": "bvconst", "bits": 32, "value": 0},
             "after": {"op": "bvconst", "bits": 32, "value": 1},
             "assumptions": assumptions}
    try:
        pairs = pair_instances_for_formal(probe)
    except FormalIrError:
        return False
    for _, pair in pairs:
        smt = equivalence_smt("sat-probe", "cegis", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
        head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
        return head == "sat"
    return False


def infer_guard(formal: dict, z3_bin: str, max_size: int = MAX_GUARDS) -> dict:
    """CEGIS: smallest NON-VACUOUS catalog guard set that makes the formal sound.

    Searches guard sets of increasing size; a counterexample set is accumulated to
    prune candidates (a usable set must exclude every counterexample seen). Each
    proving candidate is checked for satisfiability so contradictory assumption
    sets can't 'prove' an unsound transform vacuously.
    """
    bare, env0 = prove(formal, [], z3_bin)
    if bare == "proved":
        return {"status": "already-sound", "guards": [], "iterations": 1}
    if bare == "error":
        return {"status": "error", "guards": [], "iterations": 1}

    catalog = candidate_catalog(formal)
    ces = [env0]
    z3_calls = 1
    for size in range(1, max_size + 1):
        # only guards (resp. sets) that exclude every known counterexample can help
        usable = [g for g in catalog if any(not eval_guard(g, ce) for ce in ces)]
        for combo in itertools.combinations(usable, size):
            if not all(any(not eval_guard(g, ce) for g in combo) for ce in ces):
                continue
            combo = list(combo)
            if not assumptions_satisfiable(formal, combo, z3_bin):
                continue  # vacuous -- would prove anything
            z3_calls += 1
            status, ce = prove(formal, combo, z3_bin)
            z3_calls += 1
            if status == "proved":
                return {"status": "guard-found", "guards": combo,
                        "iterations": z3_calls, "counterexamples": len(ces)}
            if status == "refuted" and ce not in ces:
                ces.append(ce)
    return {"status": "no-precondition", "guards": [], "iterations": z3_calls,
            "counterexamples": len(ces)}


# --------------------------------------------------------------------------- #

def var(n):
    return {"op": "var", "name": n}


def scalar_formal(before, after, variables):
    return {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
            "variables": variables, "poison_variables": [], "before": before, "after": after}


def selftest_cases():
    a, b, x, y = var("a"), var("b"), var("x"), var("y")
    eq_ab = {"op": "eq", "args": [a, b]}
    zero = {"op": "bvconst", "bits": 32, "value": 0}
    eq_x0 = {"op": "eq", "args": [x, zero]}
    return [
        # guardable: select(a==b,x,y)==y  iff a!=b -> expect rel ne(a,b)
        dict(name="select(a==b,x,y)->y", expect="guard-found",
             formal=scalar_formal({"op": "ite", "args": [eq_ab, x, y]}, y, ["a", "b", "x", "y"]),
             want_pred="ne"),
        # guardable: select(x==0,a,b)==b  iff x!=0 -> expect not-eq(x,0)
        dict(name="select(x==0,a,b)->b", expect="guard-found",
             formal=scalar_formal({"op": "ite", "args": [eq_x0, a, b]}, b, ["x", "a", "b"]),
             want_pred="not-eq"),
        # genuinely unsound: add a,1 -> a  (no precondition can save it)
        dict(name="add a,1 -> a (unsound)", expect="no-precondition",
             formal=scalar_formal({"op": "bvadd", "args": [a, {"op": "bvconst", "bits": 32, "value": 1}]},
                                  a, ["a"]),
             want_pred=None),
        # already sound: add a,0 -> a
        dict(name="add a,0 -> a (sound)", expect="already-sound",
             formal=scalar_formal({"op": "bvadd", "args": [a, {"op": "bvconst", "bits": 32, "value": 0}]},
                                  a, ["a"]),
             want_pred=None),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--formal", type=Path, help="a formal JSON to infer a precondition for")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(f"infer-guard: z3 not found: {args.z3_bin}", file=sys.stderr)
        return 2

    if args.formal:
        formal = json.loads(args.formal.read_text())
        result = infer_guard(formal, z3_bin)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"guard-found", "already-sound"} else 1

    if not args.selftest:
        ap.error("provide --formal or --selftest")

    results = []
    failed = 0
    for case in selftest_cases():
        r = infer_guard(case["formal"], z3_bin)
        ok = r["status"] == case["expect"]
        if ok and case["want_pred"]:
            ok = any(case["want_pred"] in (g.get("predicate"), g.get("op")) for g in r["guards"])
        if not ok:
            failed += 1
        results.append({"name": case["name"], "expect": case["expect"],
                        "got": r["status"], "guards": r["guards"], "ok": ok})
    summary = {"cases": len(results), "failed": failed, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": failed}, sort_keys=True))
    for r in results:
        tag = "ok" if r["ok"] else "FAIL"
        guards = ", ".join(f"{g.get('predicate', g['op'])}({g.get('left', g.get('name'))}"
                           f"{','+g['right'] if 'right' in g else ''})" for g in r["guards"]) or "-"
        print(f"  [{tag}] {r['name']}: {r['got']} guards=[{guards}]", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
