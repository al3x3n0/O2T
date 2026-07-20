#!/usr/bin/env python3
"""Phase 40: the two-icmp caller contract -- and/or combinations of icmp pairs.

InstCombine's logical-combination folds take TWO icmp instructions and a combiner selector:
`foldX(ICmpInst *Cmp0, ICmpInst *Cmp1, bool IsAnd, ...)` -- the caller (visitAnd/visitOr)
guarantees the replaced value is the IsAnd-selected combination. Per case, an `IsAnd` guard
conjunct is satisfied (true case) or makes the arm unreachable (false case); the reachable
obligation combines both matched icmp trees under the case's combiner, and rewrite-side operand
PROJECTIONS (`Cmp0->getOperand(0)`) lower to the matched subtree's node.

The centerpiece is VERBATIM upstream: foldIsPowerOf2OrZero (LLVM 18 InstCombineAndOrXor.cpp) --
both arms prove real theorems through the phase-26 ctpop model:
    ctpop(X) != 1  &&  X != 0   <->   ctpop(X) >  1        (the IsAnd arm)
    ctpop(X) == 1  ||  X == 0   <->   ctpop(X) <  2        (the !IsAnd arm)
with teeth both ways: a UGE-for-UGT rewrite refutes, and claiming the AND arm under !IsAnd
(combiner swap) refutes in the or-world. Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

# VERBATIM from llvm-project release/18.x, llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp.
UPSTREAM = """static Value *foldIsPowerOf2OrZero(ICmpInst *Cmp0, ICmpInst *Cmp1, bool IsAnd,
                                   InstCombiner::BuilderTy &Builder) {
  CmpInst::Predicate Pred0, Pred1;
  Value *X;
  if (!match(Cmp0, m_ICmp(Pred0, m_Intrinsic<Intrinsic::ctpop>(m_Value(X)),
                          m_SpecificInt(1))) ||
      !match(Cmp1, m_ICmp(Pred1, m_Specific(X), m_ZeroInt())))
    return nullptr;

  Value *CtPop = Cmp0->getOperand(0);
  if (IsAnd && Pred0 == ICmpInst::ICMP_NE && Pred1 == ICmpInst::ICMP_NE)
    return Builder.CreateICmpUGT(CtPop, ConstantInt::get(CtPop->getType(), 1));
  if (!IsAnd && Pred0 == ICmpInst::ICMP_EQ && Pred1 == ICmpInst::ICMP_EQ)
    return Builder.CreateICmpULT(CtPop, ConstantInt::get(CtPop->getType(), 2));

  return nullptr;
}"""


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("pass_graph_twoicmp_fixture: z3 not found, skipped")
        return 0

    # 1. THE MILESTONE: both arms of the verbatim upstream fold recovered and PROVED -- each in
    #    exactly its reachable IsAnd case, each a real ctpop theorem, each reconcile-checked
    #    (the symbolic side; the concrete engine abstains beyond its variable budget).
    arms = pg.recover_folds_from_function(UPSTREAM)
    assert [(a["arm"], a["case"]["IsAnd"]) for a in arms] == [(0, True), (1, False)], arms
    assert all(ma.prove(a, z3)[0] == "proved" for a in arms), \
        [ma.prove(a, z3)[0] for a in arms]
    assert all(pg.reconcile(a, z3)["agree"] for a in arms)

    # 2. TEETH, rewrite side: UGE-for-UGT (admits ctpop == 1) refutes with a witness.
    mut = pg.recover_folds_from_function(UPSTREAM.replace("CreateICmpUGT", "CreateICmpUGE"))
    st, cex = ma.prove(mut[0], z3)
    assert st == "refuted" and cex, ("UGE mutation must refute", st)

    # 3. TEETH, combiner side: claiming the AND arm under !IsAnd (combiner swap) puts the same
    #    rewrite in the OR world -- refuted. The contract's case selection is load-bearing.
    swap = pg.recover_folds_from_function(UPSTREAM.replace("if (IsAnd &&", "if (!IsAnd &&"))
    st, cex = ma.prove(swap[0], z3)
    assert st == "refuted" and cex and swap[0]["case"]["IsAnd"] is False, (st, swap[0].get("case"))

    # 4. CONTRACT GATES, each a decline: a single matched cmp (the other never inspected); a
    #    signature without the IsAnd selector falls through to (and declines in) the other paths.
    one = UPSTREAM.replace(" ||\n      !match(Cmp1, m_ICmp(Pred1, m_Specific(X), m_ZeroInt()))", "")
    assert pg.recover_folds_from_function(one) == [], "one matched cmp must decline"
    noband = UPSTREAM.replace("bool IsAnd,", "unsigned Depth,").replace("IsAnd &&", "Depth &&") \
                     .replace("!IsAnd &&", "!Depth &&")
    assert pg.recover_folds_from_function(noband) == [], "no IsAnd selector must decline"

    # 5. The SINGULAR API refuses the multi-case fold (no silent single-case proof).
    assert pg.recover_from_function(UPSTREAM) is None

    print("pass_graph_twoicmp_fixture OK: the verbatim upstream foldIsPowerOf2OrZero proves BOTH "
          "arms -- ctpop(X)!=1 && X!=0 <-> ctpop(X)>1 in the IsAnd world and its dual in the or "
          "world -- through two-primary composition, IsAnd case selection, predicate guards, and "
          "operand projection; a UGE-for-UGT rewrite refutes, a combiner swap refutes in the or "
          "case, and single-cmp / selector-less shapes decline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
