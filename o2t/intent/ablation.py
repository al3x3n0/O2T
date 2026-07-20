#!/usr/bin/env python3
"""E7: the recovery-soundness ablation -- seed misrecoveries, measure which layer catches each.

The C6 recovery is a C++-reading program; a silent mis-reading changes the obligation and can
false-prove. The C7 stack defends it in layers. This module SIMULATES misrecoveries -- corrupting
the RECOVERED obligation the way a recovery bug would (a dropped operator, a mislowered builder, a
weakened guard, a swapped operand, a width-specific constant, a skipped predicate case, a
contradictory premise) -- and records which layer catches each: the prover's teeth (refuted), the
symbolic/concrete reconciliation, the second solver, the width corroboration, or the premise-SAT
anti-vacuity gate. The zero-escape invariant the fixture pins: EVERY seeded class is caught by at
least one layer. This is the mutation study applied to the recovery itself, not the target
program -- the paper's E7.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from o2t import mini_alive as ma
from o2t.intent import pass_graph as pg

# A representative known-good fold pool: recovered from source so the seeds corrupt REAL
# obligations, spanning plain identities, guarded folds, builders, and a non-commutative shape.
SEED_FOLDS = [
    ("nested-identity", "match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))",
     "return replaceInstUsesWith(I, X);"),
    ("guarded-sdiv", "match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) && "
     "isKnownNonNegative(Y)", "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"),
    ("builder-dfg", "match(&I, m_Sub(m_Value(A), m_Value(B)))",
     "return replaceInstUsesWith(I, Builder.CreateSub(A, B));"),
    ("disjoint-or", "match(&I, m_Add(m_Value(X), m_Value(Y))) && haveNoCommonBitsSet(X, Y)",
     "return replaceInstUsesWith(I, Builder.CreateOr(X, Y));"),
]

MISRECOVERY_CLASSES = (
    "dropped-operator",       # after loses its root op (and(X,Y) -> X): an elided rewrite step
    "mislowered-builder",     # after's root op swapped (bvsub -> bvadd): a wrong vocabulary entry
    "weakened-guard",         # one recovered assumption silently dropped
    "swapped-operands",       # before's root args reversed: an operand-order misreading
    "width-specific-const",   # a const replaced by a 32-bit-specific mask: a width coincidence
    "skipped-pred-case",      # a predicate-set fold consumed as ONE member (no split)
    "contradictory-premise",  # an impossible guard conjunction: the vacuous-proof trap
)


def _first_binop(node: dict) -> dict | None:
    if node.get("op", "").startswith("bv") and len(node.get("args", [])) == 2:
        return node
    for a in node.get("args", []):
        found = _first_binop(a)
        if found is not None:
            return found
    return None


def seed_misrecovery(pair: dict, kind: str) -> dict | None:
    """Corrupt a recovered obligation the way the named misrecovery class would. Returns the
    corrupted pair, or None when the class does not apply to this fold's shape (reported, never
    silently skipped)."""
    bad = copy.deepcopy(pair)
    if kind == "dropped-operator":
        root = _first_binop(bad["after"])
        if root is None:
            return None
        bad["after"] = root["args"][0]
        return bad
    if kind == "mislowered-builder":
        root = _first_binop(bad["after"])
        if root is None:
            return None
        root["op"] = "bvadd" if root["op"] != "bvadd" else "bvor"
        return bad
    if kind == "weakened-guard":
        if not bad.get("assumptions"):
            return None
        bad["assumptions"] = bad["assumptions"][:-1]
        return bad
    if kind == "swapped-operands":
        root = _first_binop(bad["before"])
        if root is None or root["op"] in ("bvadd", "bvmul", "bvand", "bvor", "bvxor"):
            return None                                  # commutative root: the swap is invisible
        root["args"] = [root["args"][1], root["args"][0]]
        return bad
    if kind == "width-specific-const":
        # replace a zero const in `before` with the 32-bit sign-bit mask: value-equal nowhere but
        # a fold PROVABLE at bv32 that cannot generalize is the more insidious sibling -- model it
        # by making `after` the 32-bit byte-mask identity (and X 0xFFFFFFFF), provable only at 32.
        bad["before"] = {"op": "bvand", "args": [bad["before"],
                                                 {"op": "bvconst", "bits": 32, "value": 0xFFFFFFFF}]}
        return bad
    if kind == "contradictory-premise":
        vs = bad.get("variables") or []
        if not vs:
            return None
        v = vs[0]
        bad["assumptions"] = list(bad.get("assumptions", [])) + [
            {"op": "cmp", "predicate": "sgt", "name": v, "value": 5},
            {"op": "cmp", "predicate": "slt", "name": v, "value": -5},
        ]
        return bad
    return None


def catch_layers(pair: dict, z3: str) -> list[str]:
    """Run the corrupted obligation through the C7 layers and report every catcher."""
    catchers = []
    status, _ = ma.prove(pair, z3)
    if status == "refuted":
        catchers.append("prove-teeth")
    if status == "unsupported":
        catchers.append("premise-sat-gate")              # vacuous premises never yield `proved`
    rec = pg.reconcile(pair, z3)
    if not rec.get("agree", True) or rec.get("concrete") == "refuted":
        catchers.append("reconcile-concrete")
    sol = pg.reconcile_solver(pair, z3)
    if not sol.get("agree", True) or sol.get("solver") == "refuted":
        catchers.append("second-solver")
    corr = pg.corroborate_widths(pair, z3)
    if corr.get("applicable") and (not corr.get("agree", True)
                                   or corr.get("status") not in ("proved", None)):
        catchers.append("width-corroboration")
    return catchers


def run_ablation(z3: str) -> dict:
    """The E7 matrix: for every (seed fold x misrecovery class), which layers catch the corruption.
    `skipped-pred-case` runs on its own predicate-set fold (the classes need their shapes)."""
    matrix: dict[str, dict] = {}
    escapes: list = []
    for fold_name, pred, rw in SEED_FOLDS:
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, fold_name
        assert ma.prove(pair, z3)[0] == "proved", (fold_name, "seed must start sound")
        for kind in MISRECOVERY_CLASSES:
            if kind == "skipped-pred-case":
                continue                                 # shape-specific, handled below
            bad = seed_misrecovery(pair, kind)
            if bad is None:
                matrix.setdefault(kind, {})[fold_name] = "not-applicable"
                continue
            caught = catch_layers(bad, z3)
            matrix.setdefault(kind, {})[fold_name] = caught or "ESCAPED"
            if not caught:
                escapes.append((kind, fold_name))
    # skipped-pred-case: an isEquality fold whose rewrite hardcodes EQ. Consuming ONE case
    # (the eq member) proves -- the misrecovery is skipping the OTHER member, whose obligation
    # refutes; the catcher is the all-cases discipline itself.
    cases = pg.recover_pair_cases(
        "match(&I, m_ICmp(Pred, m_Value(A), m_Value(B))) && ICmpInst::isEquality(Pred)",
        "return replaceInstUsesWith(I, Builder.CreateICmpEQ(A, B));")
    assert len(cases) == 2
    one_member = {c["case"]["pred"]: ma.prove(c, z3)[0] for c in cases}
    caught = ["all-cases-discipline"] if "refuted" in one_member.values() else []
    matrix["skipped-pred-case"] = {"icmp-eq-hardcode": caught or "ESCAPED",
                                   "member_verdicts": one_member}
    if not caught:
        escapes.append(("skipped-pred-case", "icmp-eq-hardcode"))
    return {"matrix": matrix, "escapes": escapes,
            "invariant": "every seeded misrecovery class must be caught by at least one layer"}


def render(report: dict) -> str:
    lines = ["== E7: recovery-soundness ablation (misrecovery class x catching layer) =="]
    for kind, row in report["matrix"].items():
        lines.append(f"  {kind}:")
        for fold, caught in row.items():
            if fold == "member_verdicts":
                continue
            lines.append(f"    {fold:18s} -> {caught}")
    lines.append(f"escapes: {report['escapes'] or 'NONE (zero-escape invariant holds)'}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    import shutil
    ap = argparse.ArgumentParser(description="E7: seed misrecoveries, measure the catching layers")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print("cv-passir-ablation: z3 required", file=sys.stderr)
        return 2
    report = run_ablation(z3)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(render(report), end="")
    return 1 if report["escapes"] else 0


if __name__ == "__main__":
    sys.exit(main())
