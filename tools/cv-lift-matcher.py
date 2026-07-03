#!/usr/bin/env python3
"""Lift a PatternMatch matcher expression directly into the before-tree (M1).

The lossy path goes source -> {operation, identity, rewrite} -> formal, which can
only express a single binop + an identity constant. But an LLVM matcher expression
*is* the before-tree structurally:

    m_Add(m_Mul(m_Value(X), m_Value(Y)), m_Zero())   ==   (X*Y) + 0

This parses that nested m_*(...) expression and lifts it to the cv_formal_ir DSL,
driven entirely by the UNIFIED vocabulary (rides on ①):

    m_Add/m_c_Add/.../m_Shl  -> bvadd/.../bvshl   (llvm_idioms.json op matchers)
    m_Zero/m_One/m_AllOnes   -> bvconst            (llvm_idioms.json const matchers)
    m_Value(X)               -> var X (binds)
    m_Specific(X)/m_Deferred(X) -> var X (reference, for self-patterns)

It handles arbitrarily nested patterns the operation/identity triple cannot, and
proves the resulting identity with z3.
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
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402
from cv_lift_matcher import (  # noqa: E402
    MASK, MatcherError, lift_matcher, lift_transform,
)


# --------------------------------------------------------------------------- #

def prove(before, after, variables, z3_bin):
    formal = {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
              "variables": variables, "poison_variables": variables,
              "before": before, "after": after}
    try:
        pairs = pair_instances_for_formal(formal)
    except FormalIrError as exc:
        return f"encode-error:{exc}"
    for _, pair in pairs:
        smt = equivalence_smt("matcher", "m1", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
        head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
        if head == "unsat":
            continue
        return "refuted" if head == "sat" else "error"
    return "proved"


def v(name):
    return {"op": "var", "name": name}


def bvc(value):
    return {"op": "bvconst", "bits": 32, "value": value & MASK}


def selftest_cases():
    mul_xy = {"op": "bvmul", "args": [v("X"), v("Y")]}
    add_xy = {"op": "bvadd", "args": [v("X"), v("Y")]}
    return [
        # simple -- also cross-checked against the expected before-tree
        dict(name="m_Add(m_Value(X), m_Zero())", expect="proved",
             matcher="m_Add(m_Value(X), m_Zero())", after=v("X"),
             before_expect={"op": "bvadd", "args": [v("X"), bvc(0)]}),
        dict(name="m_Xor(m_Value(X), m_Specific(X))", expect="proved",
             matcher="m_Xor(m_Value(X), m_Specific(X))", after=bvc(0),
             before_expect={"op": "bvxor", "args": [v("X"), v("X")]}),
        dict(name="m_And(m_Value(X), m_AllOnes())", expect="proved",
             matcher="m_And(m_Value(X), m_AllOnes())", after=v("X"),
             before_expect={"op": "bvand", "args": [v("X"), bvc(MASK)]}),
        # NESTED -- impossible for the operation/identity/rewrite triple
        dict(name="m_Add(m_Mul(X,Y), m_Zero())", expect="proved",
             matcher="m_Add(m_Mul(m_Value(X), m_Value(Y)), m_Zero())", after=mul_xy,
             before_expect=None),
        dict(name="m_Mul(m_Add(X,Y), m_One())", expect="proved",
             matcher="m_Mul(m_Add(m_Value(X), m_Value(Y)), m_One())", after=add_xy,
             before_expect=None),
        # teeth: a WRONG simplification must be refuted
        dict(name="m_Sub(m_Value(X), m_Value(Y)) -> X (BAD)", expect="refuted",
             matcher="m_Sub(m_Value(X), m_Value(Y))", after=v("X"),
             before_expect=None),
    ]


def transform_cases():
    """Whole transforms: before from the matcher, AFTER lifted from the rewrite."""
    return [
        # replaceInstUsesWith(I, X): (X+0) == X
        dict(name="add-zero via replaceInstUsesWith", expect="proved",
             matcher="m_Add(m_Value(X), m_Zero())",
             builder="replaceInstUsesWith(I, X)"),
        # nested before + Builder.CreateMul after: X*(Y+0) == X*Y
        dict(name="mul-(add-zero) via Builder.CreateMul", expect="proved",
             matcher="m_Mul(m_Value(X), m_Add(m_Value(Y), m_Zero()))",
             builder="Builder.CreateMul(X, Y)"),
        # commutative builder (static call, swapped operands): (X&Y) == (Y&X)
        dict(name="and-commute via BinaryOperator::CreateAnd", expect="proved",
             matcher="m_And(m_Value(X), m_Value(Y))",
             builder="BinaryOperator::CreateAnd(Y, X)"),
        # const builder: (X | allones) == allones
        dict(name="or-allones via Constant::getAllOnesValue", expect="proved",
             matcher="m_Or(m_Value(X), m_AllOnes())",
             builder="Constant::getAllOnesValue(Ty)"),
        # teeth: (X - Y) == X is unsound
        dict(name="sub -> X via replaceInstUsesWith (BAD)", expect="refuted",
             matcher="m_Sub(m_Value(X), m_Value(Y))",
             builder="replaceInstUsesWith(I, X)"),
        # teeth: (X + Y) == 0 is unsound
        dict(name="add -> 0 via ConstantInt::get (BAD)", expect="refuted",
             matcher="m_Add(m_Value(X), m_Value(Y))",
             builder="ConstantInt::get(Ty, 0)"),
        # NON-LOCAL: select with identical arms folds away: select(C,X,X) == X
        dict(name="select-identical-arms via replaceInstUsesWith", expect="proved",
             matcher="m_Select(m_Value(C), m_Value(X), m_Deferred(X))",
             builder="replaceInstUsesWith(I, X)"),
        # NON-LOCAL if-conversion roundtrip: a matched select rebuilt via CreateSelect
        dict(name="if-conversion via Builder.CreateSelect", expect="proved",
             matcher="m_Select(m_Value(C), m_Value(X), m_Value(Y))",
             builder="Builder.CreateSelect(C, X, Y)"),
        # teeth: dropping a select to one arm is unsound (select(C,X,Y) != X)
        dict(name="select -> true-arm via replaceInstUsesWith (BAD)", expect="refuted",
             matcher="m_Select(m_Value(C), m_Value(X), m_Value(Y))",
             builder="replaceInstUsesWith(I, X)"),
        # NON-LOCAL icmp-guarded select: select(A==0, 0, A) == A
        dict(name="select-icmp-eq-zero -> A", expect="proved",
             matcher="m_Select(m_SpecificICmp(ICmpInst::ICMP_EQ, m_Value(A), m_Zero()), m_Zero(), m_Value(A))",
             builder="replaceInstUsesWith(I, A)"),
        # teeth: select(A==0, A, 0) is 0, not A
        dict(name="select-icmp-eq-zero wrong arm -> A (BAD)", expect="refuted",
             matcher="m_Select(m_SpecificICmp(ICmpInst::ICMP_EQ, m_Value(A), m_Zero()), m_Value(A), m_Zero())",
             builder="replaceInstUsesWith(I, A)"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matcher", help="a single matcher expression to lift (prints before-tree)")
    ap.add_argument("--transform", nargs=2, metavar=("MATCHER", "BUILDER"),
                    help="lift a whole transform (before from matcher, after from rewrite)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-z3", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    if args.matcher:
        before, variables = lift_matcher(args.matcher)
        print(json.dumps({"before": before, "variables": variables}, sort_keys=True))
        return 0

    if args.transform:
        before, after, variables = lift_transform(args.transform[0], args.transform[1])
        print(json.dumps({"before": before, "after": after, "variables": variables}, sort_keys=True))
        return 0

    if not args.selftest:
        ap.error("provide --matcher, --transform, or --selftest")

    z3_bin = None if args.no_z3 else shutil.which(args.z3_bin)
    results = []
    proved = refuted = structural = failed = 0
    for case in selftest_cases():
        rec = {"name": case["name"]}
        try:
            before, variables = lift_matcher(case["matcher"])
        except MatcherError as exc:
            rec["status"] = f"parse-error:{exc}"
            failed += 1
            results.append(rec)
            continue
        if case["before_expect"] is not None and before != case["before_expect"]:
            rec["status"] = "before-mismatch"
            rec["before"] = before
            failed += 1
            results.append(rec)
            continue
        if z3_bin is None:
            rec["status"] = "lifted"
            structural += 1
            results.append(rec)
            continue
        verdict = prove(before, case["after"], variables, z3_bin)
        rec["status"] = verdict
        if verdict == case["expect"]:
            proved += verdict == "proved"
            refuted += verdict == "refuted"
        else:
            rec["status"] = f"unexpected:{verdict} (wanted {case['expect']})"
            failed += 1
        results.append(rec)

    # Whole-transform cases: before from matcher, AFTER lifted from the rewrite.
    for case in transform_cases():
        rec = {"name": case["name"], "kind": "transform"}
        try:
            before, after, variables = lift_transform(case["matcher"], case["builder"])
        except MatcherError as exc:
            rec["status"] = f"parse-error:{exc}"
            failed += 1
            results.append(rec)
            continue
        if z3_bin is None:
            rec["status"] = "lifted"
            structural += 1
            results.append(rec)
            continue
        verdict = prove(before, after, variables, z3_bin)
        if verdict == case["expect"]:
            rec["status"] = verdict
            proved += verdict == "proved"
            refuted += verdict == "refuted"
        else:
            rec["status"] = f"unexpected:{verdict} (wanted {case['expect']})"
            failed += 1
        results.append(rec)

    backend = "z3" if z3_bin else "structural"
    summary = {"backend": backend, "cases": len(results), "proved": proved,
               "refuted": refuted, "lifted": structural, "failed": failed, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: val for k, val in summary.items() if k != "results"}, sort_keys=True))
    print(f"lift-matcher: {proved} proved, {refuted} refuted, {structural} lifted, {failed} failed "
          f"[{backend}]", file=sys.stderr)
    ok = failed == 0 and (proved + refuted + structural) == len(results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
