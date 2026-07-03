#!/usr/bin/env python3
"""Symbolic execution of a fold function -> faithful per-path model (code-lift B).

Approach A proves each branch in isolation. A real fold is an else-if CASCADE: a
branch fires only when every EARLIER guard was false. Symbolically executing the
fold over symbolic IR captures that -- each path's condition is

    (not g_0) and (not g_1) and ... and (not g_{i-1}) and g_i

This is what KLEE on the real bitcode would produce (path condition + output per
path); here it is computed directly over the lifted fold model, which is feasible
without a symbolic LLVM-IR model / a KLEE install (KLEE is unavailable locally).
The faithful path semantics buys two things A cannot see:

  * soundness UNDER REACHABILITY -- a branch proved sound only on inputs that
    actually reach it (earlier branches didn't fire)
  * DEAD branches -- a guard whose path condition is unsatisfiable (shadowed by an
    earlier branch / a redundant check) -> dead code
  * exhaustiveness -- whether the fall-through (no branch fires) is reachable

Per path we report: reachable|dead, and sound|UNSOUND (+counterexample). An
unsound REACHABLE path is a real miscompile that accounts for the cascade.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import expr_to_smt, smt_and, smt_or  # noqa: E402

DEF_RE = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\)\s*#x([0-9a-fA-F]+)\)")
CMP = {"eq", "ne", "slt", "sle", "sgt", "sge", "ult", "ule", "ugt", "uge"}


def guard_to_smt(g, variables):
    """Lower a boolean guard node (eq/ne/cmp over values, and/or/not) to SMT."""
    op = g["op"]
    if op == "smt":
        # A pre-lowered fact fragment from the shared value_tracking encoder; its
        # text references variables advertised in g["vars"] (declared elsewhere).
        return g["text"]
    if op in CMP:
        a = expr_to_smt(g["args"][0], variables)
        b = expr_to_smt(g["args"][1], variables)
        if op == "eq":
            return f"(= {a} {b})"
        if op == "ne":
            return f"(not (= {a} {b}))"
        return f"(bv{op} {a} {b})"
    if op == "not":
        return f"(not {guard_to_smt(g['args'][0], variables)})"
    if op == "and":
        return smt_and([guard_to_smt(a, variables) for a in g["args"]])
    if op == "or":
        return smt_or([guard_to_smt(a, variables) for a in g["args"]])
    raise ValueError(f"unsupported guard op {op}")


def collect_vars(node, out):
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["name"])
        if node.get("op") == "smt":
            out.update(node.get("vars", []) or [])
        for a in node.get("args", []) or []:
            collect_vars(a, out)


def run_z3(z3_bin, decls, asserts, want_model=False):
    body = ["(set-logic QF_BV)", *decls, *[f"(assert {a})" for a in asserts], "(check-sat)"]
    if want_model:
        body.append("(get-model)")
    res = subprocess.run([z3_bin, "-in"], input="\n".join(body), capture_output=True, text=True)
    out = res.stdout.strip()
    head = out.splitlines()[0] if out else "error"
    model = {k: int(v, 16) for k, v in DEF_RE.findall(out)} if (want_model and head == "sat") else {}
    return head, model


def symexec(model, z3_bin):
    operands = model.get("operands", ["a", "b"])
    opcode = model["opcode"]
    before = {"op": opcode, "args": [{"op": "var", "name": o} for o in operands]}
    branches = model["branches"]

    variables = set(operands)
    for br in branches:
        collect_vars(br.get("guard", {}), variables)
        collect_vars(br.get("output", {}), variables)
    decls = [f"(declare-const {v} (_ BitVec 32))" for v in sorted(variables)]
    before_smt = expr_to_smt(before, variables)

    paths = []
    prior_neg = []  # negations of earlier guards
    counts = {"sound": 0, "unsound": 0, "dead": 0}
    for br in branches:
        gsmt = guard_to_smt(br["guard"], variables)
        pc = smt_and(prior_neg + [gsmt]) if (prior_neg or True) else gsmt
        prior_neg.append(f"(not {gsmt})")
        reach, _ = run_z3(z3_bin, decls, [pc])
        if reach != "sat":
            counts["dead"] += 1
            paths.append({"name": br.get("name", "?"), "reachable": False, "status": "dead-branch"})
            continue
        out_smt = expr_to_smt(br["output"], variables)
        # unsound iff exists input on this path where before != output
        refute, m = run_z3(z3_bin, decls, [pc, f"(not (= {before_smt} {out_smt}))"], want_model=True)
        if refute == "unsat":
            counts["sound"] += 1
            paths.append({"name": br.get("name", "?"), "reachable": True, "status": "sound"})
        elif refute == "sat":
            counts["unsound"] += 1
            cex = {o: m.get(o, 0) for o in sorted(variables)}
            paths.append({"name": br.get("name", "?"), "reachable": True,
                          "status": "UNSOUND", "counterexample": cex})
        else:
            paths.append({"name": br.get("name", "?"), "reachable": True, "status": "error"})

    fall, _ = run_z3(z3_bin, decls, prior_neg) if prior_neg else ("unsat", {})
    exhaustive = fall != "sat"
    return {"function": model.get("function", "?"), "paths": paths, "counts": counts,
            "fallthrough_reachable": not exhaustive, "exhaustive": exhaustive,
            "miscompiles": counts["unsound"], "dead_branches": counts["dead"]}


def selftest_model():
    def vv(n):
        return {"op": "var", "name": n}

    def bvc(x):
        return {"op": "bvconst", "bits": 32, "value": x}

    def eq(a, b):
        return {"op": "eq", "args": [a, b]}
    return {"function": "foldAddDemo", "opcode": "bvadd", "operands": ["a", "b"], "branches": [
        # sound: add a,0 -> a
        dict(name="add-zero", guard=eq(vv("b"), bvc(0)), output=vv("a")),
        # sound: add a,a -> a<<1
        dict(name="add-self->shl", guard=eq(vv("a"), vv("b")),
             output={"op": "bvshl", "args": [vv("a"), bvc(1)]}),
        # DEAD: b==0 again -- shadowed by branch 1 (path forces b!=0)
        dict(name="add-zero-dup", guard=eq(vv("b"), bvc(0)), output=vv("a")),
        # UNSOUND reachable: add a,5 -> a is false
        dict(name="add-five-drops", guard=eq(vv("b"), bvc(5)), output=vv("a")),
    ]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--model", type=Path)
    src.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    model = selftest_model() if args.selftest else json.loads(args.model.read_text())
    report = symexec(model, z3_bin)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "paths"}, sort_keys=True))
    for p in report["paths"]:
        cex = f"  cex={p['counterexample']}" if p.get("counterexample") else ""
        print(f"  [{p['status']:12}] {p['name']}{cex}", file=sys.stderr)
    return 1 if report["miscompiles"] and not args.selftest else 0


if __name__ == "__main__":
    sys.exit(main())
