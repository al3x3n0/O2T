#!/usr/bin/env python3
"""Phase 37: the simplifyXInst caller contract -- the operand orientation IS the API's name.

InstructionSimplify's entry points carry their instruction in the NAME: `simplifySubInst(Value
*Op0, Value *Op1, ...)` is documented as "simplify `sub Op0, Op1`". That contract licenses
synthesizing the PHANTOM instruction the name declares -- `match(&__P, m_Sub(m_Value(Op0),
m_Value(Op1)))` -- normalizing the arm's `match(Op0/Op1, ...)` conjuncts to operand form, and
handing everything to the phase-38 composer (all splice/retire/alias discipline inherited).

The soundness centerpiece is ORIENTATION: unlike foldX helper arg order (which callers commute --
out of scope, stated), the name declares which operand is which, and on a NON-commutative op a
swapped reading would falsely prove. Pinned here: `X - 0 -> X` proves while `0 - X -> X` refutes
with a witness. Also pins the let-inliner refinements real cascades forced (nullptr-sentinel
inits never substituted; a reassigned local skips per-name instead of declining the cascade) and
the rewrite-only `getType()` normalization (guard atoms keep it verbatim -- the type-equality
guard for cast folds is load-bearing). Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

# A faithful miniature of upstream simplifySubInst: guard-heavy arms decline, the X - 0 -> X arm
# proves, and the local-reuse idiom (nullptr sentinels, later reassignment) must not sink the
# cascade.
SUB = """static Value *simplifySubInst(Value *Op0, Value *Op1, bool IsNSW, bool IsNUW,
                              const SimplifyQuery &Q, unsigned MaxRecurse) {
  if (isa<PoisonValue>(Op0) || isa<PoisonValue>(Op1))
    return PoisonValue::get(Op0->getType());
  // X - 0 -> X
  if (match(Op1, m_Zero()))
    return Op0;
  if (match(Op0, m_Zero())) {
    if (IsNUW)
      return Constant::getNullValue(Op0->getType());
  }
  Value *X = nullptr, *Y = nullptr, *Z = Op1;
  X = Op0;
  return nullptr;
}"""


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("pass_graph_contract_fixture: z3 not found, skipped")
        return 0

    # 1. The X - 0 -> X arm is recovered THROUGH the name contract (no instruction parameter
    #    exists) and proved; the poison/IsNUW arms decline; sentinels and reassignment are inert.
    arms = pg.recover_folds_from_function(SUB)
    assert len(arms) == 1, [a["arm"] for a in arms]
    assert ma.prove(arms[0], z3)[0] == "proved", "X - 0 -> X must prove via the contract"
    assert pg.reconcile(arms[0], z3)["agree"]

    # 2. ORIENTATION (the soundness centerpiece): on non-commutative sub, the name declares the
    #    slots. `0 - X -> X` -- sound-looking under a SWAPPED reading -- must REFUTE.
    wrong = ("static Value *simplifySubInst(Value *Op0, Value *Op1, const SimplifyQuery &Q) {\n"
             "  if (match(Op0, m_Zero()))\n"
             "    return Op1;\n"
             "  return nullptr;\n}")
    warms = pg.recover_folds_from_function(wrong)
    st, cex = ma.prove(warms[0], z3)
    assert st == "refuted" and cex, ("0 - X -> X must refute (orientation honored)", st)
    # ...and the correctly-oriented mirror proves.
    mirror = wrong.replace("match(Op0", "match(Op1").replace("return Op1", "return Op0")
    assert ma.prove(pg.recover_folds_from_function(mirror)[0], z3)[0] == "proved"

    # 3. A constant rewrite whose type argument is a `getType()` chain parses (rewrite-only `Ty`
    #    normalization): and(X, 0) -> 0 proves. Guard atoms keep getType() verbatim -- the cast
    #    folds' type-equality guard still recovers (regression-pinned via recover_pair).
    andz = ("static Value *simplifyAndInst(Value *Op0, Value *Op1, const SimplifyQuery &Q) {\n"
            "  if (match(Op1, m_Zero()))\n"
            "    return Constant::getNullValue(Op0->getType());\n"
            "  return nullptr;\n}")
    assert ma.prove(pg.recover_folds_from_function(andz)[0], z3)[0] == "proved"
    cast = pg.recover_pair("match(&I, m_Trunc(m_ZExt(m_Value(X)))) && X->getType() == I.getType()",
                           "return replaceInstUsesWith(I, X);")
    assert cast is not None, "type-equality guard must stay recognizable (rewrite-only Ty)"

    # 4. Contract GATES, each a decline: a non-canonical name; fewer than two Value* params;
    #    an arm whose conjunct subject is neither operand param.
    assert pg.recover_folds_from_function(andz.replace("AndInst", "FooInst")) == []
    one_param = ("static Value *simplifyAndInst(Value *Op0, const SimplifyQuery &Q) {\n"
                 "  if (match(Op0, m_Zero()))\n"
                 "    return Op0;\n"
                 "  return nullptr;\n}")
    assert pg.recover_folds_from_function(one_param) == []
    foreign = andz.replace("match(Op1", "match(Other")
    assert pg.recover_folds_from_function(foreign) == []

    # 5. Multi-arm contract cascades slice: two provable identities from one simplifyXorInst,
    #    the second standalone.
    xor2 = ("static Value *simplifyXorInst(Value *Op0, Value *Op1, const SimplifyQuery &Q) {\n"
            "  if (match(Op1, m_Zero()))\n"
            "    return Op0;\n"
            "  if (match(Op1, m_Specific(Op0)))\n"
            "    return Constant::getNullValue(Op0->getType());\n"
            "  return nullptr;\n}")
    xarms = pg.recover_folds_from_function(xor2)
    assert [(a["arm"], a["standalone"]) for a in xarms] == [(0, False), (1, True)], xarms
    assert [ma.prove(a, z3)[0] for a in xarms] == ["proved", "proved"]

    print("pass_graph_contract_fixture OK: simplifyXInst arms are recovered through the "
          "name-declared phantom instruction and proved (X-0->X, and(X,0)->0, xor pair with "
          "slicing); ORIENTATION is honored on non-commutative sub (0-X->X refutes with a "
          "witness); nullptr sentinels and local reassignment stay inert; getType() normalizes "
          "in rewrites only (the cast type-equality guard survives); non-canonical names, "
          "missing params, and foreign subjects decline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
