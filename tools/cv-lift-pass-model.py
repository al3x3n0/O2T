#!/usr/bin/env python3
"""Lift the optimization CODE (not just intent) into a formal pass model (code-lift A).

Intent-lifting proves one idealized before->after pattern. This models the whole
fold function as an ordered list of guarded rewrites and proves each branch under
THE GUARD THE CODE ACTUALLY USES -- closing the "idealized vs. actual" gap and
catching the real instcombine bug class: insufficient guards.

    PassModel(fn) = [ Branch(guard, before, after), ... ]   ; else unchanged

For each branch we prove (before == after) twice -- WITH the extracted guard
assumptions and WITHOUT -- and classify:

    unconditionally-sound  sound even without the guard (guard is belt-and-braces)
    guard-sufficient       sound WITH the code's guard, unsound without (correct &
                           necessary guard)
    insufficient-guard     UNSOUND even with the code's guard, and every guard
                           clause was modeled -> a real implementation bug (the
                           code performs an unsound rewrite) + counterexample
    guard-unmodeled        unsound, but some guard clause could not be modeled ->
                           inconclusive (not reported as a bug)

A pass model's branch coverage = branches modeled / branches in the function.
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
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402

DEF_RE = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\)\s*#x([0-9a-fA-F]+)\)")


def prove(before, after, variables, assumptions, z3_bin):
    """Return ('proved', None) | ('refuted', cex_env) | ('error', None)."""
    formal = {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
              "variables": variables, "poison_variables": [],
              "before": before, "after": after, "assumptions": assumptions}
    try:
        pairs = pair_instances_for_formal(formal)
    except FormalIrError:
        return ("error", None)
    for _, pair in pairs:
        smt = equivalence_smt("pass-model", "code-lift", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt + "\n(get-model)",
                             capture_output=True, text=True)
        out = res.stdout.strip()
        head = out.splitlines()[0] if out else "error"
        if head == "unsat":
            continue
        if head == "sat":
            model = {k: int(v, 16) for k, v in DEF_RE.findall(out)}
            return ("refuted", {n: model.get(n, 0) for n in variables})
        return ("error", None)
    return ("proved", None)


def classify_branch(branch, z3_bin):
    before, after = branch["before"], branch["after"]
    variables = branch["variables"]
    assumptions = branch.get("assumptions", [])
    unmodeled = branch.get("unmodeled_guards", [])
    with_status, cex = prove(before, after, variables, assumptions, z3_bin)
    if with_status == "error":
        return {"status": "error"}
    if with_status == "proved":
        without_status, _ = prove(before, after, variables, [], z3_bin)
        if without_status == "proved":
            return {"status": "unconditionally-sound"}
        return {"status": "guard-sufficient", "guard": assumptions}
    # refuted even with the code's guard
    if unmodeled:
        return {"status": "guard-unmodeled", "unmodeled": unmodeled}
    return {"status": "insufficient-guard", "guard": assumptions, "counterexample": cex}


# --------------------------------------------------------------------------- #
# guard recognizers: predicate clause -> assumption (a small G1-style map)
# --------------------------------------------------------------------------- #

GUARD_PATTERNS = [
    (re.compile(r"isKnownNonZero\s*\(\s*&?(\w+)"), lambda m: {"op": "not-eq", "name": m.group(1), "value": 0}),
    (re.compile(r"isKnownNonEqual\s*\(\s*&?(\w+)\s*,\s*&?(\w+)"),
     lambda m: {"op": "rel", "predicate": "ne", "left": m.group(1), "right": m.group(2)}),
    (re.compile(r"isKnownPositive\s*\(\s*&?(\w+)"),
     lambda m: {"op": "cmp", "predicate": "sgt", "name": m.group(1), "value": 0}),
    (re.compile(r"isKnownNonNegative\s*\(\s*&?(\w+)"),
     lambda m: {"op": "cmp", "predicate": "sge", "name": m.group(1), "value": 0}),
]
# guards with no value effect (poison/structural) -- modeled, add nothing
NO_EFFECT = re.compile(r"hasPoisonGeneratingFlags|hasOneUse|hasNUses")


def extract_guard(clause: str):
    """Return (assumption|None, modeled: bool). A clause we recognize is modeled."""
    for pat, build in GUARD_PATTERNS:
        m = pat.search(clause)
        if m:
            return build(m), True
    if NO_EFFECT.search(clause):
        return None, True
    return None, False


# --------------------------------------------------------------------------- #

def selftest_model():
    def vv(n):
        return {"op": "var", "name": n}

    def bvc(x):
        return {"op": "bvconst", "bits": 32, "value": x}
    return {"function": "demoFold", "branches": [
        # unconditionally sound: add x, 0 -> x
        dict(name="add-zero", before={"op": "bvadd", "args": [vv("a"), bvc(0)]},
             after=vv("a"), variables=["a"], assumptions=[]),
        # guard-sufficient: select(a==b,x,y) -> y, guarded by isKnownNonEqual(a,b)
        dict(name="select-distinct",
             before={"op": "ite", "args": [{"op": "eq", "args": [vv("a"), vv("b")]}, vv("x"), vv("y")]},
             after=vv("y"), variables=["a", "b", "x", "y"],
             assumptions=[{"op": "rel", "predicate": "ne", "left": "a", "right": "b"}]),
        # INSUFFICIENT guard (real bug): add a, b -> a guarded only by b != 0
        # (it actually needs b == 0; the guard is wrong) -> refutes under the guard
        dict(name="add-drops-y-badguard", before={"op": "bvadd", "args": [vv("a"), vv("b")]},
             after=vv("a"), variables=["a", "b"],
             assumptions=[{"op": "not-eq", "name": "b", "value": 0}]),
    ]}


def run_model(model, z3_bin):
    branches = []
    counts: dict[str, int] = {}
    for br in model["branches"]:
        result = classify_branch(br, z3_bin)
        result["name"] = br.get("name", "?")
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        branches.append(result)
    modeled = sum(1 for b in branches if b["status"] != "guard-unmodeled")
    total = len(branches)
    return {"function": model.get("function", "?"), "branches": branches,
            "counts": counts, "branch_coverage_pct": round(100.0 * modeled / total, 1) if total else 0.0,
            "insufficient_guard_bugs": counts.get("insufficient-guard", 0)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--model", type=Path, help="a pass-model json (function + branches)")
    src.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    model = selftest_model() if args.selftest else json.loads(args.model.read_text())
    report = run_model(model, z3_bin)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "branches"}, sort_keys=True))
    for b in report["branches"]:
        extra = f"  cex={b['counterexample']}" if b.get("counterexample") else ""
        print(f"  [{b['status']:20}] {b['name']}{extra}", file=sys.stderr)
    return 1 if report["insufficient_guard_bugs"] and not args.selftest else 0


if __name__ == "__main__":
    sys.exit(main())
