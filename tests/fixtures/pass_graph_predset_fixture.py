#!/usr/bin/env python3
"""Phase 39: predicate-set case splits + domain-affirming guard drops + the inverted-guard hole.

A predicate-SET guard (`ICmpInst::isEquality(Pred)`) constrains a bound predicate to a member set;
the obligation is proved once PER MEMBER, instantiated consistently through both the matcher and a
generic `Builder.CreateICmp(Pred, ...)` rewrite -- ALL cases must prove, so a rewrite that
hardcodes one member is REFUTED on the others (predicate overreach caught by the split). Guards
that only affirm the modeled domain or an IR-structural ordering (`!isa<VectorType>`,
`isIntOrIntVectorTy()`, `!isa<Constant>`, `!shouldChangeType`) drop -- including through POSITIVE
bails, whose path contribution is now the textually negated atom.

Also pins the SIXTH latent hole found by this ladder: the fact vocabulary matches by substring, so
a NEGATED fact (`!isKnownNonNegative(X)` -- from a positive guard or a fact-bail) used to bind the
POSITIVE premise -- an inverted guard and a false-proof vector predating this phase. Negated
non-domain conjuncts now decline. Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

REBUILD = """static Value *foldCmpRebuild(ICmpInst &I) {
  CmpInst::Predicate Pred;
  Value *A, *B;
  if (!match(&I, m_ICmp(Pred, m_Value(A), m_Value(B))))
    return nullptr;
  if (!ICmpInst::isEquality(Pred))
    return nullptr;
  return Builder.CreateICmp(Pred, A, B);
}"""


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("pass_graph_predset_fixture: z3 not found, skipped")
        return 0

    # 1. CASE SPLIT: the same-predicate rebuild proves in BOTH the eq and ne worlds; each case is
    #    a distinct obligation, tagged, both instantiated through matcher AND rewrite.
    arms = pg.recover_folds_from_function(REBUILD)
    assert [(a["arm"], a["case"]["pred"]) for a in arms] == [(0, "eq"), (0, "ne")], arms
    assert all(ma.prove(a, z3)[0] == "proved" for a in arms)

    # 2. OVERREACH TEETH: a rewrite that hardcodes EQ under the isEquality guard proves the eq
    #    case and REFUTES the ne case with a witness -- the split catches what a single
    #    representative member would bless.
    over = pg.recover_folds_from_function(
        REBUILD.replace("Builder.CreateICmp(Pred, A, B)", "Builder.CreateICmpEQ(A, B)"))
    verdicts = {a["case"]["pred"]: ma.prove(a, z3) for a in over}
    assert verdicts["eq"][0] == "proved" and verdicts["ne"][0] == "refuted" and verdicts["ne"][1]

    # 3. SUBJECT form (`I.isEquality()`) resolves through the matcher's UNIQUE predicate binder;
    #    two binders would be ambiguous -- consistency with the explicit form pinned here.
    subj = pg.recover_folds_from_function(
        REBUILD.replace("!ICmpInst::isEquality(Pred)", "!I.isEquality()"))
    assert [(a["case"]["pred"], ma.prove(a, z3)[0]) for a in subj] == \
        [("eq", "proved"), ("ne", "proved")]

    # 4. isUnsigned splits 4 ways; the same-predicate rebuild proves in every world.
    uns = pg.recover_folds_from_function(
        REBUILD.replace("isEquality", "isUnsigned"))
    assert sorted(a["case"]["pred"] for a in uns) == ["bvuge", "bvugt", "bvule", "bvult"]
    assert all(ma.prove(a, z3)[0] == "proved" for a in uns)

    # 5. DOMAIN-AFFIRMING drops, including through POSITIVE bails (`if (isa<VectorType>(...))
    #    return nullptr;` contributes the negated, droppable atom): the fold proves.
    drops = ("static Value *foldWithDomainGuards(BinaryOperator &I) {\n"
             "  Value *X;\n"
             "  if (!match(&I, m_Add(m_Value(X), m_Zero())))\n"
             "    return nullptr;\n"
             "  if (isa<VectorType>(I.getType()))\n"
             "    return nullptr;\n"
             "  if (isa<Constant>(X))\n"
             "    return nullptr;\n"
             "  if (shouldChangeType(SrcTy, DestTy))\n"
             "    return nullptr;\n"
             "  return X;\n}")
    darms = pg.recover_folds_from_function(drops)
    assert len(darms) == 1 and ma.prove(darms[0], z3)[0] == "proved", darms

    # 6. POLARITY bounds: a POSITIVE `isa<Constant>` conjunct is value-relevant for refinement
    #    (constants are never poison) and declines; `isIntOrIntVectorTy(1)` is an i1 width
    #    constraint and declines (only the empty-paren domain form drops).
    pos = ("Value *foldPos(BinaryOperator &I){ Value *X;\n"
           "  if (match(&I, m_Add(m_Value(X), m_Zero())) && isa<Constant>(X))\n"
           "    return replaceInstUsesWith(I, X);\n  return nullptr; }")
    assert pg.recover_folds_from_function(pos) == []
    i1 = drops.replace("isa<VectorType>(I.getType())", "!I.getType()->isIntOrIntVectorTy(1)")
    assert pg.recover_folds_from_function(i1) == []

    # 7. THE INVERTED-GUARD HOLE (specimen six, predating this phase): the fact vocabulary matches
    #    by substring, so `!isKnownNonNegative(X)` used to bind the POSITIVE premise. Both routes
    #    -- a negated fact in a positive guard, and a fact-bail contributing a negated atom --
    #    now decline instead of mis-binding.
    inverted = ("Value *foldInv(BinaryOperator &I){ Value *X;\n"
                "  if (match(&I, m_Add(m_Value(X), m_Zero())) && !isKnownNonNegative(X))\n"
                "    return replaceInstUsesWith(I, X);\n  return nullptr; }")
    assert pg.recover_from_function(inverted) is None, "negated fact must decline, never mis-bind"
    factbail = drops.replace("isa<Constant>(X)", "isKnownNonNegative(X)")
    assert pg.recover_folds_from_function(factbail) == []

    # 8. The SINGULAR API refuses a multi-case arm: no caller can silently prove one member.
    assert pg.recover_from_function(REBUILD) is None

    print("pass_graph_predset_fixture OK: isEquality/isUnsigned guards split into per-member "
          "cases instantiated through matcher and generic CreateICmp rewrite alike -- all prove "
          "for the faithful rebuild, and a hardcoded-EQ rewrite is refuted on the ne case "
          "(overreach caught); the subject form resolves via the unique binder; domain-affirming "
          "guards drop (through positive bails too) while positive isa<Constant> and i1-width "
          "forms decline; and the inverted-guard hole -- negated facts binding their POSITIVE "
          "premise -- is closed on both routes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
