#!/usr/bin/env python3
"""Validate the widened optimization-intent -> formal lifter.

Exercises every grammar shape the lifter learned beyond the original
add/sub/mul/and/or/xor binary form: shifts (shl/lshr/ashr), unary neg/not, and
select with an icmp condition. For each shape it constructs a synthetic
``source-intent-v1`` finding, lifts it with
``cv-infer-optimization-intent.source_intent_formal_for``, and checks:

  * structural (always): the lifted DSL ``before`` contains the expected op, and
    the record carries variables/poison_variables.
  * semantic (with Z3): sound rewrites prove (z3 unsat under refinement) and the
    matching unsound mutants are correctly refuted (z3 sat) -- a teeth-test that
    the lifter+prover are not vacuous.

Casts (trunc/zext/sext) are intentionally out of scope: they change bit width,
which the single-width scalar-bv32 domain cannot model.
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


SYM = lambda n: {"symbol": n}            # noqa: E731
CON = lambda v: {"constant": v}          # noqa: E731
ICMP = lambda p, a, b: {"operation": "icmp", "predicate": p, "operands": [a, b]}  # noqa: E731


def finding(before_op: dict, after_value: dict) -> dict:
    return {
        "marker": "probe.lift-selftest",
        "source_intent": {
            "model": "source-intent-v1",
            "before": dict(before_op, shape="scalar"),
            "after": {"result": after_value},
            "rewrite": {"action": "replace-result"},
        },
    }


def cases() -> list[dict]:
    """Each case: name, finding, expected op substring, and soundness."""
    x, a, b = SYM("x"), SYM("a"), SYM("b")
    out: list[dict] = []

    # ---- shifts (binary, registry-driven) ----
    out.append(dict(name="shl x,0 -> x", shape="shift", op="bvshl", sound=True,
                    finding=finding({"operation": "shl", "operands": [x, CON(0)]}, x)))
    out.append(dict(name="lshr x,0 -> x", shape="shift", op="bvlshr", sound=True,
                    finding=finding({"operation": "lshr", "operands": [x, CON(0)]}, x)))
    out.append(dict(name="ashr x,0 -> x", shape="shift", op="bvashr", sound=True,
                    finding=finding({"operation": "ashr", "operands": [x, CON(0)]}, x)))
    # unsound: shl x,1 -> x
    out.append(dict(name="shl x,1 -> x (BAD)", shape="shift", op="bvshl", sound=False,
                    finding=finding({"operation": "shl", "operands": [x, CON(1)]}, x)))

    # ---- unary neg ----
    out.append(dict(name="neg(neg x) -> x", shape="neg", op="bvneg", sound=True,
                    finding=finding({"operation": "neg",
                                     "operands": [{"operation": "neg", "operands": [x]}]}, x)))
    # unsound: neg x -> x
    out.append(dict(name="neg x -> x (BAD)", shape="neg", op="bvneg", sound=False,
                    finding=finding({"operation": "neg", "operands": [x]}, x)))

    # ---- unary not ----
    out.append(dict(name="not(not x) -> x", shape="not", op="bvxor", sound=True,
                    finding=finding({"operation": "not",
                                     "operands": [{"operation": "not", "operands": [x]}]}, x)))
    # unsound: not x -> x
    out.append(dict(name="not x -> x (BAD)", shape="not", op="bvxor", sound=False,
                    finding=finding({"operation": "not", "operands": [x]}, x)))

    # ---- select + icmp ----
    out.append(dict(name="select(x==x,a,b) -> a", shape="select", op="ite", sound=True,
                    finding=finding({"operation": "select",
                                     "operands": [ICMP("eq", x, x), a, b]}, a)))
    out.append(dict(name="select(x!=x,a,b) -> b", shape="select", op="ite", sound=True,
                    finding=finding({"operation": "select",
                                     "operands": [ICMP("ne", x, x), a, b]}, b)))
    # unsound: select(x==x,a,b) -> b  (cond is always true, so result is a, not b)
    out.append(dict(name="select(x==x,a,b) -> b (BAD)", shape="select", op="ite", sound=False,
                    finding=finding({"operation": "select",
                                     "operands": [ICMP("eq", x, x), a, b]}, b)))
    return out


def formal_contains_op(node, op: str) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("op") == op:
        return True
    return any(formal_contains_op(arg, op) for arg in node.get("args", []) or [])


def z3_decide(z3_bin: str, formal: dict) -> str:
    """Return 'unsat' (proved) / 'sat' (refuted) / 'error'."""
    for _, pair in pair_instances_for_formal(formal):
        smt = equivalence_smt(formal.get("marker", "lift"), "lift-selftest", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
        head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
        return head
    return "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-z3", action="store_true", help="structural checks only")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    infer = load_infer()
    z3_bin = None if args.no_z3 else shutil.which(args.z3_bin)

    results = []
    proved = refuted = structural_ok = failed = 0
    for case in cases():
        rec = {"name": case["name"], "shape": case["shape"]}
        lifted = infer.source_intent_formal_for(case["finding"])
        if lifted is None:
            rec["status"] = "lift-failed"
            failed += 1
            results.append(rec)
            continue
        formal, _params = lifted
        if not formal_contains_op(formal["before"], case["op"]):
            rec["status"] = "structure-mismatch"
            rec["expected_op"] = case["op"]
            failed += 1
            results.append(rec)
            continue
        if not formal.get("variables") or "poison_variables" not in formal:
            rec["status"] = "missing-variables"
            failed += 1
            results.append(rec)
            continue
        structural_ok += 1
        if z3_bin is None:
            rec["status"] = "structural-ok"
            results.append(rec)
            continue
        head = z3_decide(z3_bin, formal)
        want = "unsat" if case["sound"] else "sat"
        if head == want:
            rec["status"] = "proved" if case["sound"] else "refuted"
            proved += case["sound"]
            refuted += (not case["sound"])
        else:
            rec["status"] = f"z3-unexpected:{head} (wanted {want})"
            failed += 1
        results.append(rec)

    # Semantic-facts door (#2): shifts must lift via semantic_scalar_formal_for too,
    # not only via the structured source-intent door (#3). Guards the cv_semantic_facts
    # ALLOWED_VALUES shift entries (golden output can't catch this -- the miner emits
    # no shift facts, so this capability has no corpus trigger).
    facts_shifts = {"shl": "bvshl", "lshr": "bvlshr", "ashr": "bvashr"}
    for operation, expected_op in facts_shifts.items():
        rec = {"name": f"facts:{operation}-zero -> lhs", "shape": "facts-shift"}
        finding = {
            "marker": f"probe.instcombine.{operation}-zero",
            "semantic_facts": {"model": "optimization-semantic-v1", "shape": "scalar",
                               "operation": operation, "identity": "zero",
                               "rewrite": "replace-with-lhs", "result": "scalar"},
        }
        lifted = infer.semantic_scalar_formal_for(finding)
        if lifted is None or not formal_contains_op(lifted[0]["before"], expected_op):
            rec["status"] = "lift-failed" if lifted is None else "structure-mismatch"
            failed += 1
        else:
            structural_ok += 1
            if z3_bin is None:
                rec["status"] = "structural-ok"
            elif z3_decide(z3_bin, lifted[0]) == "unsat":
                rec["status"] = "proved"
                proved += 1
            else:
                rec["status"] = "z3-unexpected"
                failed += 1
        results.append(rec)

    backend = "z3" if z3_bin else "structural"
    summary = {
        "backend": backend,
        "cases": len(results),
        "structural_ok": structural_ok,
        "proved": proved,
        "refuted": refuted,
        "failed": failed,
        "results": results,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    if z3_bin:
        print(f"lift-grammar self-test: {proved} sound proved, {refuted} unsound refuted, "
              f"{failed} failed [z3]", file=sys.stderr)
    else:
        print(f"lift-grammar self-test: {structural_ok} structural-ok, {failed} failed "
              f"[no-z3]", file=sys.stderr)
    return 0 if failed == 0 and structural_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
