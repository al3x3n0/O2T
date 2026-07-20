#!/usr/bin/env python3
"""Reduction-rebuild loops: recovering a multi-instruction rewrite whose loop ACCUMULATES a fold.

Phase 34 handled an operand loop whose non-independent iterations are a quantified GUARD (phi collapse).
This is the dual: a loop whose non-independent iterations ACCUMULATE a reduction, rebuilding an n-ary
instruction from its operand list (reassociate / SimplifyAssociative style):

    if (I.getOpcode() != Instruction::Or) return nullptr;   // I is an Or  (OP_before)
    Value *Acc = I.getOperand(0);
    for (unsigned i = 1; i < I.getNumOperands(); ++i)
      Acc = Builder.CreateOr(Acc, I.getOperand(i));         // left-fold reducer (OP_after)
    return replaceInstUsesWith(I, Acc);

The rewrite emits a LEFT-associated fold `((op0 . op1) . op2)...`; the instruction it replaces is the
SAME operands under I's own nesting. They are equal IFF the operator is ASSOCIATIVE and OP_after ==
OP_before -- so the obligation is `right-fold(OP_before) == left-fold(OP_after)`. Crucially,
associativity is INVISIBLE at arity 2 (`a.b == a.b` for ANY operator) and only bites at arity 3+: so a
single arity-2 proof would wrongly bless a non-associative reducer, and `corroborate_arity` is exactly
what catches it -- the same "bug hidden at the representative bound" story as phase 34.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg
from o2t import mini_alive as ma


def _reduction_src(opcode: str, creator: str) -> str:
    return (f"Value *foldReassoc(BinaryOperator &I){{\n"
            f"  if (I.getOpcode() != Instruction::{opcode}) return nullptr;\n"
            f"  Value *Acc = I.getOperand(0);\n"
            f"  for (unsigned i = 1; i < I.getNumOperands(); ++i)\n"
            f"    Acc = Builder.Create{creator}(Acc, I.getOperand(i));\n"
            f"  return replaceInstUsesWith(I, Acc);\n}}")


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_reduction_loop_fixture: z3 not found, skipped")
        return 0

    # 1. An ASSOCIATIVE reducer (or/and/add/mul/xor) rebuilds the instruction faithfully: the recovered
    #    obligation proves, at every bounded arity, with just the k operands as variables (no selectors).
    for opcode in ("Or", "And", "Add", "Mul", "Xor"):
        pair = pg.recover_from_function(_reduction_src(opcode, opcode))
        assert pair is not None, ("reduction loop must be recovered", opcode)
        assert pair["variables"] == ["op0", "op1", "op2"], pair["variables"]
        assert ma.prove(pair, z3)[0] == "proved", ("associative reduction must prove", opcode)
        corr = pg.corroborate_arity(lambda k, o=opcode: pg._reduction_obligation(k, pg.OPCODE_BINOP[o], pg.OPCODE_BINOP[o]), z3)
        assert corr["agree"] and corr["status"] == "proved", (opcode, corr)

    # 2. TEETH via arity corroboration: a NON-associative operator (sub) rebuilt by a left fold is
    #    value-equal at arity 2 -- `a-b == a-b` -- but DIVERGES at arity 3 (`a-(b-c) != (a-b)-c`). A
    #    single arity-2 proof would have blessed it; the corroboration flags it `arity-specific`.
    sub = pg.corroborate_arity(lambda k: pg._reduction_obligation(k, "bvsub", "bvsub"), z3)
    assert not sub["agree"] and sub["status"] == "arity-specific", sub
    assert sub["verdicts"] == {2: "proved", 3: "refuted", 4: "refuted"}, sub["verdicts"]
    st, cex = ma.prove(pg._reduction_obligation(3, "bvsub", "bvsub"), z3)
    assert st == "refuted" and cex, ("non-associative sub must refute at arity 3", st)

    # 3. TEETH via op mismatch: a reducer that does NOT match the instruction's op (I is Or, loop folds
    #    with And) is unsound at EVERY arity -- refuted already at arity 2 with a witness.
    st, cex = ma.prove(pg._reduction_obligation(2, "bvor", "bvand"), z3)
    assert st == "refuted" and cex, ("mismatched reducer must refute", st)
    # ...and it is recovered from source as such: an Or instruction folded with CreateAnd refutes.
    mism = pg.recover_from_function(_reduction_src("Or", "And"))
    assert mism is not None and ma.prove(mism, z3)[0] == "refuted", "Or-instr / And-reducer must refute"

    # 4. SOUND DECLINE (never mis-model): a bare reduction loop that does NOT state I's opcode cannot
    #    form `before` and declines; a loop that replaces I with something OTHER than the accumulator
    #    is not this fold and declines.
    no_cue = ("Value *f(BinaryOperator &I){ Value *Acc = I.getOperand(0);\n"
              "  for (unsigned i=1;i<N;++i) Acc = Builder.CreateOr(Acc, I.getOperand(i));\n"
              "  return replaceInstUsesWith(I, Acc); }")
    replace_other = ("Value *f(BinaryOperator &I){ if (I.getOpcode() != Instruction::Or) return nullptr;\n"
                     "  Value *Acc = I.getOperand(0);\n"
                     "  for (unsigned i=1;i<N;++i) Acc = Builder.CreateOr(Acc, I.getOperand(i));\n"
                     "  return replaceInstUsesWith(I, Other); }")
    assert pg.recover_reduction_loop(no_cue) is None, "missing opcode cue must decline"
    assert pg.recover_reduction_loop(replace_other) is None, "replace-with-non-accumulator must decline"

    # 5. NO CROSS-TALK with phase 34: the reduction loop is not a phi-collapse, and vice versa; each
    #    recognizer declines the other's shape, so `recover_from_function` routes deterministically.
    assert pg.recover_operand_loop(_reduction_src("Or", "Or")) is None
    phi = ("Value *f(PHINode *PN){ Value *First = PN->getIncomingValue(0);\n"
           "  for (Value *In : PN->incoming_values()) if (In != First) return nullptr;\n"
           "  return replaceInstUsesWith(*PN, First); }")
    assert pg.recover_reduction_loop(phi) is None

    print("pass_graph_reduction_loop_fixture OK: a loop that left-folds an n-ary instruction's operands "
          "(reassociate-style) is recovered as the associativity obligation right-fold==left-fold and "
          "proved for associative reducers {or,and,add,mul,xor} at bounded arity {2,3,4}; a "
          "non-associative reducer (sub) is value-equal at arity 2 but flagged arity-specific by the "
          "corroboration (refutes at 3+), a mismatched reducer refutes at every arity, and "
          "missing-opcode / replace-other loops decline -- with no cross-talk with the phase-34 collapse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
