#!/usr/bin/env python3
"""Phase 36: the RETURN-form rewrite anchor -- recovering upstream's dominant fold idiom.

E6 measured that 48% of upstream InstCombine/InstSimplify candidates decline at the rewrite
ANCHOR: real fold helpers honor the contract "return the replacement value (nullptr for no fold)"
instead of calling replaceInstUsesWith. This phase treats a non-bail `return <expr>;` in a
FOLD-NAMED (fold|simplify|visit|combine|canonicalize) Value*/Instruction* helper as the rewrite,
with single-assignment `Value *T = ...;` locals inlined as pure lets, plus the vocabulary the real
corpus needs (m_OneUse transparency, m_Neg/m_Not sugar, CreateNeg/CreateNot, static creators).

The centerpiece is a VERBATIM upstream fold (combineAddSubWithShlAddSub, LLVM 18
InstCombineAddSub.cpp) recovered from its unmodified source, proved, and confirmed by exhaustive
concrete enumeration -- with teeth (a mutated reducer refutes), the name gate (a query helper's
return is an ANSWER, not a rewrite -- declines), and the let-mutation guard (a local mutated after
binding declines). Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

# VERBATIM from llvm-project release/18.x, llvm/lib/Transforms/InstCombine/InstCombineAddSub.cpp.
UPSTREAM = """static Instruction *combineAddSubWithShlAddSub(InstCombiner::BuilderTy &Builder,
                                               const BinaryOperator &I) {
  Value *A, *B, *Cnt;
  if (match(&I,
            m_c_Add(m_OneUse(m_Shl(m_OneUse(m_Neg(m_Value(B))), m_Value(Cnt))),
                    m_Value(A)))) {
    Value *NewShl = Builder.CreateShl(B, Cnt);
    return BinaryOperator::CreateSub(A, NewShl);
  }
  return nullptr;
}"""


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("pass_graph_return_form_fixture: z3 not found, skipped")
        return 0

    # 1. THE MILESTONE: a verbatim upstream InstCombine fold -- ((-B << Cnt) + A -> A - (B << Cnt),
    #    sound because shl distributes over negation mod 2^n -- is recovered from its UNMODIFIED
    #    source through the return-form anchor + let-inlining + m_OneUse/m_Neg vocabulary, PROVED,
    #    and confirmed by the exhaustive concrete engine.
    pair = pg.recover_from_function(UPSTREAM)
    assert pair is not None, "verbatim upstream fold must be recovered"
    assert pair["variables"] == ["a", "b", "cnt"], pair["variables"]
    assert ma.prove(pair, z3)[0] == "proved", "upstream fold must prove"
    rec = pg.reconcile(pair, z3)
    assert rec["agree"] and rec["concrete"] == "proved" and rec["checked"] > 0, rec

    # 2. TEETH: mutating the rebuilt reducer (Sub -> Add) makes the fold unsound; it is recovered
    #    the same way and REFUTED with a witness -- the anchor does not rubber-stamp.
    st, cex = ma.prove(pg.recover_from_function(
        UPSTREAM.replace("CreateSub(A, NewShl)", "CreateAdd(A, NewShl)")), z3)
    assert st == "refuted" and cex, ("mutated upstream fold must refute", st)

    # 3. NAME GATE: the identical body under a query-helper name is NOT a rewrite contract --
    #    its return is an answer. Sound decline, so query helpers can never produce false claims.
    assert pg.recover_from_function(
        UPSTREAM.replace("combineAddSubWithShlAddSub", "dyn_castShlAddSub")) is None

    # 4. LET-MUTATION GUARD: a local mutated after binding (the canonicalizeLowbitMask idiom --
    #    setHasNoSignedWrap on the built value) would make the substitution unfaithful; declines.
    mutated_local = UPSTREAM.replace(
        "    return BinaryOperator::CreateSub(A, NewShl);",
        "    NewShl->setHasNoSignedWrap();\n    return BinaryOperator::CreateSub(A, NewShl);")
    assert pg.recover_from_function(mutated_local) is None, "let-mutation must decline"

    # 5. The RIUW anchor is UNTOUCHED: the return-form retry runs only when the explicit anchor
    #    finds nothing, so an explicit replaceInstUsesWith fold recovers exactly as before.
    riuw = ("Value *f(BinaryOperator &I){ Value *X,*Y;\n"
            "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
            "  if (!isKnownNonNegative(X) || !isKnownNonNegative(Y)) return nullptr;\n"
            "  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y)); }")
    assert ma.prove(pg.recover_from_function(riuw), z3)[0] == "proved"

    # 6. Return-form + BAILOUT path conditions compose: guards accumulate exactly as in the RIUW
    #    path, and the recovered precondition stays load-bearing (drop it -> refuted).
    guarded = ("static Value *simplifyDivByNonNeg(BinaryOperator &I) {\n"
               "  Value *X, *Y;\n"
               "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y))))\n"
               "    return nullptr;\n"
               "  if (!isKnownNonNegative(X) || !isKnownNonNegative(Y))\n"
               "    return nullptr;\n"
               "  return Builder.CreateUDiv(X, Y);\n}")
    pair = pg.recover_from_function(guarded)
    assert pair is not None and ma.prove(pair, z3)[0] == "proved"
    assert {a["name"] for a in pair["assumptions"]} == {"x", "y"}
    st, cex = ma.prove(pg.recover_from_function(guarded.replace(
        "  if (!isKnownNonNegative(X) || !isKnownNonNegative(Y))\n    return nullptr;\n", "")), z3)
    assert st == "refuted" and cex, "unguarded return-form sdiv->udiv must refute"

    # 7. Vocabulary round-trips: m_Not/CreateNot and m_Neg/CreateNeg are inverses-of-themselves,
    #    proved end to end through the new sugar (and a WRONG pairing refutes).
    notnot = ("static Value *foldNotNot(BinaryOperator &I) {\n"
              "  Value *X;\n"
              "  if (!match(&I, m_Not(m_Not(m_Value(X)))))\n"
              "    return nullptr;\n"
              "  return X;\n}")
    assert ma.prove(pg.recover_from_function(notnot), z3)[0] == "proved", "~~X -> X"
    negneg = notnot.replace("m_Not(m_Not", "m_Neg(m_Neg").replace("foldNotNot", "foldNegNeg")
    assert ma.prove(pg.recover_from_function(negneg), z3)[0] == "proved", "-(-X) -> X"
    cross = notnot.replace("return X;", "return Builder.CreateNeg(X);")
    st, cex = ma.prove(pg.recover_from_function(cross), z3)
    assert st == "refuted" and cex, "~~X -> -X must refute"

    # 8. SUBJECT GATE (found by the first upstream E6 run with this anchor): a helper that matches
    #    an OPERAND parameter simplifies an IMPLICIT outer op -- upstream's simplifyOrLogic(X, Y)
    #    matches on Y but the returned value replaces `X | Y`. Taking the operand pattern as
    #    `before` produced a FALSE REFUTATION (`~X ≡ -1`); the gate declines it instead.
    operand_subject = ("static Value *simplifyOrLogic(Value *X, Value *Y) {\n"
                       "  if (match(Y, m_Not(m_Specific(X))))\n"
                       "    return ConstantInt::getAllOnesValue(Ty);\n"
                       "  return nullptr;\n}")
    assert pg.recover_from_function(operand_subject) is None, \
        "operand-subject match must decline, never falsely refute"

    # 9. `return &I;` (changed-in-place, revisit) and a return of an UNBOUND name are not rewrites
    #    the fragment can model -- both decline rather than misattribute.
    inplace = ("static Instruction *visitTouch(BinaryOperator &I) {\n"
               "  Value *X;\n"
               "  if (!match(&I, m_Add(m_Value(X), m_Zero())))\n"
               "    return nullptr;\n"
               "  return &I;\n}")
    assert pg.recover_from_function(inplace) is None, "return &I must decline"
    unbound = inplace.replace("return &I;", "return SomethingElse;")
    assert pg.recover_from_function(unbound) is None, "unbound return value must decline"

    print("pass_graph_return_form_fixture OK: a VERBATIM upstream InstCombine fold "
          "(combineAddSubWithShlAddSub) is recovered from unmodified LLVM 18 source via the "
          "return-form anchor with let-inlining and m_OneUse/m_Neg vocabulary, proved, and "
          "confirmed by exhaustive enumeration; a mutated reducer refutes with a witness; query "
          "helpers are name-gated out; mutated locals, in-place returns, and unbound returns "
          "decline; the RIUW anchor and its load-bearing preconditions are unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
