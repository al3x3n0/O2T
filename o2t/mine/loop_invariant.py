#!/usr/bin/env python3
"""End-to-end: mine a loop from real source, synthesize its invariant, prove all n.

Connects the shape miner (cv-mine-shapes' control-flow parser) to the invariant
synthesizer (cv-synth-invariant). For each fold function with a counted loop:

  parse  ->  acc = INIT ; for (...) acc = acc + DELTA ; return acc
  extract the recurrence (INIT, DELTA) -- DELTA must be loop-invariant (no acc, no
           loop index);
  propose  acc == INIT + i*DELTA  and discharge BASE + STEP with Z3;
  on success report the closed form at the (symbolic, unbounded) exit:
           acc == INIT + n*DELTA.

So `acc=a; for i<3 acc+=a` is proved to compute (n+1)*a for EVERY n -- not just the
literal 3 the source happens to write. A delta that uses the index (acc += i, whose
closed form is quadratic) is honestly rejected.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




from o2t.mine import shapes
from o2t.synth import invariant as synth
from o2t.formal_ir import FormalContext, typed_expr_to_smt  # noqa: E402

FOR_IDX_RE = re.compile(r"for\s*\(\s*int\s+(\w+)")


def v(name):
    return {"op": "var", "name": name}


def c(value):
    return {"op": "bvconst", "bits": 32, "value": value}


def op(o, *a):
    return {"op": o, "args": list(a)}


def subst_env(node, env):
    if isinstance(node, dict):
        if node.get("op") == "var" and node["name"] in env:
            return env[node["name"]]
        if "args" in node:
            return {**node, "args": [subst_env(a, env) for a in node["args"]]}
    return node


def free_vars(node, out):
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["name"])
        for a in node.get("args", []) or []:
            free_vars(a, out)


def is_var(node, name):
    return isinstance(node, dict) and node.get("op") == "var" and node.get("name") == name


def extract_recurrence(stmts):
    """-> (acc_name, init_expr, delta_expr) or None for `acc=INIT; for{acc=acc+DELTA}`."""
    env = {}
    for s in stmts:
        if s[0] == "assign":
            env[s[1]] = subst_env(s[2], env)
        elif s[0] == "for":
            body = [x for x in s[2] if x[0] == "assign"]
            if len(body) != 1:
                return None
            acc, step = body[0][1], body[0][2]
            init = env.get(acc)
            if init is None or not isinstance(step, dict) or step.get("op") != "bvadd":
                return None
            x, y = step["args"]
            delta = y if is_var(x, acc) else (x if is_var(y, acc) else None)
            if delta is None:
                return None
            return acc, init, delta
    return None


def lower(expr, variables):
    ctx = FormalContext(set(), variable_bits={name: 32 for name in variables})
    return typed_expr_to_smt(expr, set(variables), ctx).smt


def valid(z3_bin, variables, assumptions, before, after):
    decls = [f"(declare-const {name} (_ BitVec 32))" for name in variables]
    asserts = [f"(assert {lower(a, variables)})" for a in assumptions]
    smt = "\n".join(["(set-logic QF_BV)", *decls, *asserts,
                     f"(assert (not (= {lower(before, variables)} {lower(after, variables)})))",
                     "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return bool(out) and out.splitlines()[0].strip() == "unsat"


def analyze_function(name, body_text, z3_bin):
    try:
        stmts = shapes.Parser(shapes.tokenize(body_text)).program()
    except shapes.ShapeError:
        return None
    rec = extract_recurrence(stmts)
    if rec is None:
        return None
    acc, init, delta = rec
    idx_match = FOR_IDX_RE.search(body_text)
    index = idx_match.group(1) if idx_match else "i"
    dvars = set()
    free_vars(delta, dvars)
    # the proposed invariant uses "i" as the counter; reject deltas that touch the
    # accumulator or the loop index (those are not loop-invariant).
    if acc in dvars or index in dvars:
        return {"function": name, "status": "non-affine",
                "reason": f"delta references {'accumulator' if acc in dvars else 'loop index'}"}
    consts = sorted(dvars | {n for n in _vars(init) if n != acc})
    variables = ["ACC", "i"] + consts
    rhs = op("bvadd", init, op("bvmul", v("i"), delta))
    base = valid(z3_bin, variables, [], init, synth.subst(rhs, "i", c(0)))
    step = valid(z3_bin, variables, [op("eq", v("ACC"), rhs)],
                 op("bvadd", v("ACC"), delta), synth.subst(rhs, "i", op("bvadd", v("i"), c(1))))
    if base and step:
        closed = op("bvadd", init, op("bvmul", v("n"), delta))
        return {"function": name, "status": "proved",
                "invariant": "acc == " + synth.render(rhs),
                "closed_form": "acc(n) == " + synth.render(closed)}
    return {"function": name, "status": "unproved", "base": base, "step": step}


def _vars(node):
    out = set()
    free_vars(node, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path, default=ROOT / "tests" / "fixtures" / "branch_shapes.cpp")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    source = args.source.read_text()
    results = []
    for m in shapes.FUNC_RE.finditer(source):
        name = m.group(1)
        depth, j = 1, m.end()
        while j < len(source) and depth:
            depth += {"{": 1, "}": -1}.get(source[j], 0)
            j += 1
        analysis = analyze_function(name, source[m.end():j - 1], z3_bin)
        if analysis is not None:
            results.append(analysis)

    proved = [r for r in results if r["status"] == "proved"]
    declined = [r for r in results if r["status"] == "non-affine"]
    ok = bool(proved) and all(r["status"] in ("proved", "non-affine") for r in results)
    report = {"loops_analyzed": len(results), "proved": len(proved),
              "declined_non_affine": len(declined), "results": results, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"loops_analyzed": len(results), "proved": len(proved),
                      "declined_non_affine": len(declined), "ok": ok}, sort_keys=True))
    for r in results:
        extra = r.get("closed_form") or r.get("reason") or r.get("status")
        print(f"  [{r['status']}] {r['function']}: {extra}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
