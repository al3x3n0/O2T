#!/usr/bin/env python3
"""Vocabulary stratum A: inline mask-test guards -> known-bits facts.

A fold's legality often rests on a mask test the pass writes INLINE rather than through a
ValueTracking helper: `(X & C) == 0` (the C bits of X are known zero) or `(X & C) == C` (the C bits
are known one). O2T's SMT model already discharges both directions of a `known-bits` assumption
(o2t/facts/value_tracking.py, scalar_assumption_smt); the gap was the RECONSTRUCTOR -- it only ever
emitted a zero-mask known-bits from the `MaskedValueIsZero(X, C)` helper, never from the inline forms
and never a one-mask. This fixture pins the widened reconstructor and proves it end-to-end.

The soundness story is the usual one: the recovered fact is EXACT (the guard states it literally), so
a fold proves under it and REFUTES without it (load-bearing); a contradictory inline-mask guard (a bit
known both zero and one) is rejected at formal-IR construction, never proved vacuously; and a non-clean
RHS (`(X & C) == D`, D neither 0 nor C) declines. See docs/roadmap-vocabulary-strata.md. Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402
from o2t.facts.value_tracking import fact_to_assumptions  # noqa: E402
from o2t.formal_ir import pair_for_formal, FormalIrError  # noqa: E402


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_inline_mask_fixture: z3 not found, skipped")
        return 0

    # 1. RECONSTRUCTOR: each inline form lowers to the right fact; a non-clean RHS declines.
    assert fact_to_assumptions("(X & 8) == 0") == [{"op": "known-bits", "name": "X", "zero_mask": 8}]
    assert fact_to_assumptions("(X & 8) == 8") == [{"op": "known-bits", "name": "X", "one_mask": 8}]
    assert fact_to_assumptions("(X & Y) == 0") == [{"op": "mask-pair", "left": "X", "right": "Y"}]
    assert fact_to_assumptions("(X & 8) == 4") is None, "(X&C)==D with D!=0,D!=C is not a clean fact"
    assert fact_to_assumptions("(X & 12) == 12") == [{"op": "known-bits", "name": "X", "one_mask": 12}]

    # 2. ZERO-MASK, load-bearing: or(X, 8) -> xor(X, 8) is valid ONLY when X and 8 are disjoint. It
    #    PROVES under `(X & 8) == 0` and REFUTES with a witness once the guard is dropped.
    rw = "return replaceInstUsesWith(I, Builder.CreateXor(X, ConstantInt::get(Ty, 8)));"
    guarded = pg.recover_pair("match(&I, m_Or(m_Value(X), m_SpecificInt(8))) && (X & 8) == 0", rw)
    assert guarded is not None and guarded["assumptions"] == [{"op": "known-bits", "name": "x", "zero_mask": 8}]
    assert ma.prove(guarded, z3)[0] == "proved", "or(X,8)->xor(X,8) must prove under (X&8)==0"
    assert pg.reconcile(guarded, z3)["agree"]
    unguarded = pg.recover_pair("match(&I, m_Or(m_Value(X), m_SpecificInt(8)))", rw)
    st, cex = ma.prove(unguarded, z3)
    assert st == "refuted" and cex, ("unguarded or->xor must refute (the mask fact is load-bearing)", st)

    # 3. ONE-MASK direction (new): and(X, 7) -> 7 is valid ONLY when the low 3 bits of X are all set.
    #    Proves under `(X & 7) == 7`; refutes unguarded.
    one = pg.recover_pair("match(&I, m_And(m_Value(X), m_SpecificInt(7))) && (X & 7) == 7",
                          "return replaceInstUsesWith(I, ConstantInt::get(Ty, 7));")
    assert one is not None and one["assumptions"] == [{"op": "known-bits", "name": "x", "one_mask": 7}]
    assert ma.prove(one, z3)[0] == "proved", "and(X,7)->7 must prove under (X&7)==7"
    one_ung = pg.recover_pair("match(&I, m_And(m_Value(X), m_SpecificInt(7)))",
                              "return replaceInstUsesWith(I, ConstantInt::get(Ty, 7));")
    assert ma.prove(one_ung, z3)[0] == "refuted", "and(X,7)->7 unguarded must refute"

    # 4. ANTI-VACUITY: a contradictory inline-mask guard (bit 3 known both zero AND one) is rejected
    #    at formal-IR construction -- it DECLINES, never proves vacuously.
    contra = pg.recover_pair(
        "match(&I, m_Or(m_Value(X), m_SpecificInt(8))) && (X & 8) == 0 && (X & 8) == 8", rw)
    assert contra is None, "contradictory known-bits guard must decline"
    try:
        pair_for_formal({"domain": "scalar-bv32", "marker": "m", "variables": ["x"],
                         "before": {"op": "var", "name": "x"}, "after": {"op": "var", "name": "x"},
                         "equivalence": "result",
                         "assumptions": [{"op": "known-bits", "name": "x", "zero_mask": 8},
                                         {"op": "known-bits", "name": "x", "one_mask": 8}]})
        raise AssertionError("conflicting known-bits masks must be rejected by formal-IR")
    except FormalIrError:
        pass

    print("pass_graph_inline_mask_fixture OK: inline mask-test guards (X&C)==0 / (X&C)==C reconstruct "
          "to zero-/one-mask known-bits facts (a non-clean RHS declines); or(X,8)->xor(X,8) proves "
          "under (X&8)==0 and refutes unguarded, and(X,7)->7 proves under (X&7)==7 (the one-mask "
          "direction the SMT already supported but the reconstructor never emitted) -- each fact "
          "load-bearing, a contradictory known-both-ways guard rejected at formal-IR construction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
