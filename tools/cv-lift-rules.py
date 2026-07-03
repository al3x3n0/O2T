#!/usr/bin/env python3
"""Declarative intent->formal lift engine (M2).

Instead of hand-written Python that builds a before/after DSL tree per idiom,
each algebraic identity is a JSON rule in constraints/lift_rules.json carrying a
before/after template with typed holes:

    {"binop": "add", "args": [{"var": "a"}, {"const": "zero"}]}  ->  a

Holes resolve through the UNIFIED vocabulary (so M2 rides on ①):
    binop  -> BV_OP_FOR_OPERATION  (llvm_idioms.json operations, incl. shifts)
    const  -> CONSTANT_FOR_IDENTITY (zero/one/allones) or an int literal
    unop   -> bvneg / bvnot(via xor allones)
    ite    -> select

Adding an identity is a JSON edit -- no Python -- and this tool proves EVERY rule
sound with z3, so an unsound rule (e.g. add-one->a) is rejected at the gate.

  --rules FILE   lift_rules.json (default constraints/lift_rules.json)
  --no-z3        structural instantiation only (no proof)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402
from cv_lift_rules import DEFAULT_RULES, RuleError, rule_before_after  # noqa: E402


def rule_to_formal(rule: dict) -> dict:
    before, after, variables = rule_before_after(rule)
    return {
        "domain": "scalar-bv32",
        "equivalence": "result",
        "refinement": "refinement",
        "variables": list(variables),
        "poison_variables": list(variables),
        "before": before,
        "after": after,
    }


def prove(formal: dict, z3_bin: str) -> str:
    try:
        pairs = pair_instances_for_formal(formal)
    except FormalIrError as exc:
        return f"encode-error:{exc}"
    for _, pair in pairs:
        smt = equivalence_smt("lift-rule", "m2", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
        head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
        if head == "unsat":
            continue
        return "refuted" if head == "sat" else "error"
    return "proved"


def check_production() -> int:
    """Confirm cv-infer-optimization-intent's semantic_scalar_formal_for consumes
    the declarative-rule fallback for identities the hand-written doors don't
    cover (sub-self, mul-zero) -- guards the M2 production wiring."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from o2t.intent import infer

    def finding(op, identity, rewrite):
        return {"marker": f"probe.test.{op}",
                "semantic_facts": {"model": "optimization-semantic-v1", "shape": "scalar",
                                   "operation": op, "identity": identity, "rewrite": rewrite,
                                   "result": "scalar"}}

    # (operation, identity, rewrite, expected before-tree op) -- identities that
    # the hand-written door never covered, now lifted via the rule engine.
    expect = [("sub", "same-value", "replace-with-zero", "bvsub"),
              ("mul", "zero", "replace-with-zero", "bvmul")]
    failures = []
    for op, identity, rewrite, before_op in expect:
        result = infer.semantic_scalar_formal_for(finding(op, identity, rewrite))
        if result is None or result[0].get("before", {}).get("op") != before_op:
            failures.append({"facts": (op, identity, rewrite), "got": result and result[0]})
    if failures:
        print(json.dumps({"production_wiring": "FAIL", "failures": failures}, sort_keys=True))
        return 1
    print(json.dumps({"production_wiring": "ok", "checked": len(expect)}, sort_keys=True))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    ap.add_argument("--no-z3", action="store_true")
    ap.add_argument("--production", action="store_true",
                    help="check that production inference consumes the rule fallback")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    if args.production:
        return check_production()

    z3_bin = None if args.no_z3 else shutil.which(args.z3_bin)
    data = json.loads(args.rules.read_text())
    rules = data.get("rules", [])

    results = []
    proved = structural = failed = 0
    seen: set[str] = set()
    for rule in rules:
        name = str(rule.get("name") or "?")
        rec = {"name": name}
        if name in seen:
            rec["status"] = "duplicate-name"
            failed += 1
            results.append(rec)
            continue
        seen.add(name)
        try:
            formal = rule_to_formal(rule)
            rec["before_op"] = formal["before"]["op"]
        except (RuleError, KeyError) as exc:
            rec["status"] = f"instantiate-error:{exc}"
            failed += 1
            results.append(rec)
            continue
        if z3_bin is None:
            rec["status"] = "instantiated"
            structural += 1
            results.append(rec)
            continue
        verdict = prove(formal, z3_bin)
        rec["status"] = verdict
        if verdict == "proved":
            proved += 1
        else:
            failed += 1
        results.append(rec)

    backend = "z3" if z3_bin else "structural"
    summary = {"backend": backend, "rules": len(results), "proved": proved,
               "instantiated": structural, "failed": failed, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    print(f"lift-rules: {proved} proved, {structural} instantiated, {failed} failed "
          f"[{backend}] over {len(results)} rules", file=sys.stderr)
    ok = failed == 0 and (proved + structural) == len(results) and len(results) > 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
