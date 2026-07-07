#!/usr/bin/env python3
"""Precondition SYNTHESIS: turn a refuted fold into an actionable diagnosis.

O2T checks a fold under the guard it RECOVERS from source; if that guard is too weak, the fold simply
refutes. This adds abduction on top: for an unsound (or under-guarded) fold, search O2T's ValueTracking
vocabulary for the WEAKEST additional precondition that would make it sound -- and report it as the
missing `isKnownNonNegative(..)` / `haveNoCommonBitsSet(..)` etc., or conclude that no modeled guard
can rescue it. This catches the INSUFFICIENT-GUARD miscompile class proactively and suggests the fix.

Pins that: (1) an unguarded fold's missing guard is synthesized; (2) building on a PARTIAL guard, only
the still-missing atom is reported; (3) a genuinely broken fold yields no guard; (4) an already-sound
fold needs none; and crucially (5) the synthesized guard, added back, really makes the fold prove.

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


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_synthesis_fixture: z3 not found, skipped")
        return 0

    def rp(pred, rw):
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("expected a recovered fold", pred, rw)
        return pair

    udiv = "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"

    # 1. An unguarded sdiv->udiv is unsound; abduction reports the exact missing preconditions.
    d = pg.diagnose(rp("match(&I, m_SDiv(m_Value(X), m_Value(Y)))", udiv), z3)
    assert d["status"] == "insufficient-guard", d
    assert set(d["missing"]) == {"isKnownNonNegative(x)", "isKnownNonNegative(y)"}, d["missing"]

    # 2. add->or's missing precondition is operand disjointness.
    d = pg.diagnose(rp("match(&I, m_Add(m_Value(X), m_Value(Y)))",
                       "return replaceInstUsesWith(I, Builder.CreateOr(X, Y));"), z3)
    assert d["status"] == "insufficient-guard" and d["missing"] == ["haveNoCommonBitsSet(x, y)"], d

    # 3. THE KEY CASE: with a PARTIAL guard already recovered (X non-negative), abduction builds on it
    #    and reports ONLY the still-missing atom (Y non-negative) -- a precise "you forgot this" fix.
    partial = rp("match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X)", udiv)
    d = pg.diagnose(partial, z3)
    assert d["status"] == "insufficient-guard" and d["missing"] == ["isKnownNonNegative(y)"], d
    # ROUND-TRIP: the synthesized atom, added to the existing guard, actually makes the fold prove.
    fixed = {**partial, "assumptions": list(partial["assumptions"]) + d["atoms"]}
    assert ma.prove(fixed, z3)[0] == "proved", "the synthesized guard must genuinely discharge the fold"

    # 4. A genuinely broken fold: no modeled precondition can rescue it.
    d = pg.diagnose(rp("match(&I, m_Sub(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X);"), z3)
    assert d["status"] == "unsound", d

    # 5. An already-sound fold needs no guard.
    assert pg.diagnose(rp("match(&I, m_Add(m_Value(X), m_Zero()))",
                          "return replaceInstUsesWith(I, X);"), z3)["status"] == "sound"

    # 6. synthesize_precondition returns the minimal set directly, and [] when already sound.
    assert pg.synthesize_precondition(rp("match(&I, m_Add(m_Value(X), m_Zero()))",
                                         "return replaceInstUsesWith(I, X);"), z3) == []

    print("pass_graph_synthesis_fixture OK: abduction over the ValueTracking vocabulary turns a refuted "
          "fold into an actionable fix -- sdiv->udiv needs both operands non-negative, add->or needs "
          "disjoint operands; given a partial guard it reports only the still-missing precondition, and "
          "the synthesized guard really discharges the fold; a broken fold yields no guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
