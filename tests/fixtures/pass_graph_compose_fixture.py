#!/usr/bin/env python3
"""Phase 38: multi-match conjunct composition -- one before-tree from several match() calls.

Real fold arms match the instruction AND its operands' shapes in separate conjuncts
(`match(&I, ...) && match(I.getOperand(0), ...)`); E6 measured 69 upstream candidates in this
class. Composition splices each operand conjunct into its slot of the primary tree (on the
STRUCTURED trees, never by string surgery), with sound bounds: the slot must be a bound
`m_Value(NAME)` that the splice retires (NAME referenced anywhere else declines -- the alias is
unresolvable in tree form), and any conjunct whose subject is not the instruction or its own
operand declines (the misattribution guard, conjunct-wise).

Also pins a GATE HOLE found while wiring composition: the phase-36 subject gate's regex captured
`I` from `match(I.getOperand(0), ...)`, letting a single operand-subject match impersonate an
instruction-subject one -- the simplifyOrLogic misattribution class hidden behind the `I.` prefix.
The comma-anchored subject regex closes it. Plus the comma-declarator let fix
(`Value *Op0 = I.getOperand(0), *Op1 = ...;`) that normalizes upstream's dominant operand-local
idiom into composable form. Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

COMPOSED = """static Value *foldXorOfAnd(BinaryOperator &I) {
  Value *A, *B, *X, *Y;
  if (!match(&I, m_Xor(m_Value(A), m_Value(B))) ||
      !match(I.getOperand(0), m_And(m_Value(X), m_Value(Y))))
    return nullptr;
  return Builder.CreateXor(Builder.CreateAnd(X, Y), B);
}"""


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("pass_graph_compose_fixture: z3 not found, skipped")
        return 0

    # 1. Two conjuncts -- the instruction tree and an operand shape -- compose into ONE before
    #    tree `xor(and(X, Y), B)`; the retired slot name A is gone from the variables; proved and
    #    reconciled.
    pair = pg.recover_from_function(COMPOSED)
    assert pair is not None, "composed fold must be recovered"
    assert pair["variables"] == ["b", "x", "y"], pair["variables"]
    assert ma.prove(pair, z3)[0] == "proved"
    assert pg.reconcile(pair, z3)["agree"]

    # 2. TEETH through the composed tree: a wrong rewrite (Or where the operand matched And)
    #    refutes with a witness -- composition does not rubber-stamp.
    st, cex = ma.prove(pg.recover_from_function(
        COMPOSED.replace("CreateAnd(X, Y)", "CreateOr(X, Y)")), z3)
    assert st == "refuted" and cex, ("wrong composed rewrite must refute", st)

    # 3. The comma-declarator let fix normalizes upstream's dominant operand-local idiom
    #    (`Value *Op0 = I.getOperand(0), *Op1 = ...;` then `match(Op1, ...)`) into composable
    #    getOperand form -- and the composed A - (-X) -> A + X proves.
    lets = ("static Value *foldSubOfNeg(BinaryOperator &I) {\n"
            "  Value *Op0 = I.getOperand(0), *Op1 = I.getOperand(1);\n"
            "  Value *A, *B, *X;\n"
            "  if (!match(&I, m_Sub(m_Value(A), m_Value(B))))\n"
            "    return nullptr;\n"
            "  if (!match(Op1, m_Neg(m_Value(X))))\n"
            "    return nullptr;\n"
            "  return Builder.CreateAdd(A, X);\n}")
    pair = pg.recover_from_function(lets)
    assert pair is not None and ma.prove(pair, z3)[0] == "proved", "A - (-X) -> A + X"

    # 4. SOUND BOUNDS, each a decline:
    #    (a) the rewrite still references the retired slot name (alias unresolvable);
    assert pg.recover_from_function(COMPOSED.replace(
        "Builder.CreateXor(Builder.CreateAnd(X, Y), B)", "Builder.CreateXor(A, B)")) is None
    #    (b) a conjunct on a FOREIGN subject (neither the instruction nor its operand);
    assert pg.recover_from_function(COMPOSED.replace("I.getOperand(0)", "SomeOtherVal")) is None
    #    (c) an out-of-range operand index;
    assert pg.recover_from_function(COMPOSED.replace("I.getOperand(0)", "I.getOperand(7)")) is None
    #    (d) the slot is a m_Specific (not a splice-able bound m_Value).
    spec = COMPOSED.replace("m_Xor(m_Value(A), m_Value(B))", "m_Xor(m_Specific(A), m_Value(B))")
    assert pg.recover_from_function(spec) is None

    # 5. GATE HOLE closed: a SINGLE operand-subject match (`match(I.getOperand(0), ...)`) no
    #    longer impersonates an instruction-subject match -- it declines instead of recovering a
    #    misattributed before (the simplifyOrLogic class behind the `I.` prefix).
    hole = ("static Value *foldOperandOnly(BinaryOperator &I) {\n"
            "  Value *X;\n"
            "  if (!match(I.getOperand(0), m_Not(m_Value(X))))\n"
            "    return nullptr;\n"
            "  return ConstantInt::getAllOnesValue(Ty);\n}")
    assert pg.recover_from_function(hole) is None, "operand-only match must decline, never misattribute"

    # 6. NO REGRESSION on the single-match anchors: the verbatim phase-36 fold still recovers.
    upstream = ("static Instruction *combineAddSubWithShlAddSub(InstCombiner::BuilderTy &Builder,\n"
                "                                               const BinaryOperator &I) {\n"
                "  Value *A, *B, *Cnt;\n"
                "  if (match(&I,\n"
                "            m_c_Add(m_OneUse(m_Shl(m_OneUse(m_Neg(m_Value(B))), m_Value(Cnt))),\n"
                "                    m_Value(A)))) {\n"
                "    Value *NewShl = Builder.CreateShl(B, Cnt);\n"
                "    return BinaryOperator::CreateSub(A, NewShl);\n"
                "  }\n"
                "  return nullptr;\n}")
    assert ma.prove(pg.recover_from_function(upstream), z3)[0] == "proved"

    print("pass_graph_compose_fixture OK: instruction + operand match conjuncts compose into one "
          "before-tree (slot spliced on the structured trees, retired name gone), proved and "
          "reconciled with teeth; comma-declarator lets normalize the upstream operand-local "
          "idiom; alias/foreign-subject/out-of-range/m_Specific slots decline; and the "
          "operand-subject gate hole (match(I.getOperand(0),...) impersonating the instruction "
          "subject) is closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
