#!/usr/bin/env python3
"""Lock the two analysis-fact provers to one shared encoder (consolidation guard).

A ValueTracking fact is consumed by two provers: the intent-validation pipeline
(`formal_ir.assumption_to_smt`) and the symexec cascade discharge
(`extract_pass_model.predicate_to_guard` -> `cv-symexec-pass`). Both must lower a
fact to the SAME SMT or a fold could be "proved" by one and refuted by the other.

This fixture asserts they share ONE encoder, `o2t.facts.value_tracking`:

  1. `formal_ir` delegates to the shared `scalar_assumption_smt` (identity check),
     so its assumption SMT cannot diverge from the symexec path's.
  2. `predicate_to_guard` lowers facts through the same module.
  3. The recognizer maps each ValueTracking predicate to the canonical assumption
     vocabulary `infer.py` emits, and the encoder produces the exact SMT the
     intent-validation fixtures pin (so a drift here trips this test too).

Pure string/identity checks -- no solver needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t import formal_ir
from o2t.facts import value_tracking as vt
from o2t.intent.extract_pass_model import analysis_fact_clauses


def main() -> int:
    # 1. formal_ir's assumption encoder IS the shared one (delegation, not a copy).
    assert formal_ir.scalar_assumption_smt is vt.scalar_assumption_smt, \
        "formal_ir must delegate to the shared value_tracking encoder"

    # 2. Recognizer -> canonical assumption vocabulary (matches intent/infer.py).
    cases = {
        "isKnownToBeAPowerOfTwo(a)": [{"op": "power-of-two", "name": "a", "nonzero": True}],
        "isKnownNonNegative(a)": [{"op": "cmp", "predicate": "sge", "name": "a", "value": 0}],
        "isKnownPositive(a)": [{"op": "cmp", "predicate": "sgt", "name": "a", "value": 0}],
        "isKnownNegative(a)": [{"op": "cmp", "predicate": "slt", "name": "a", "value": 0}],
        "isKnownNonZero(a)": [{"op": "not-eq", "name": "a", "value": 0}],
        "MaskedValueIsZero(a, 255)": [{"op": "known-bits", "name": "a", "zero_mask": 255}],
    }
    for clause, expected in cases.items():
        got = vt.fact_to_assumptions(clause)
        assert got == expected, (clause, got, expected)

    # OrZero power-of-two admits zero; haveNoCommonBitsSet is a two-operand fact.
    assert vt.fact_to_assumptions("isKnownToBeAPowerOfTwo(a, true)")[0].get("or_zero") is True
    assert vt.fact_to_assumptions("haveNoCommonBitsSet(a, b)") == [{"op": "mask-pair", "left": "a", "right": "b"}]
    assert vt.fact_to_assumptions("hasOneUse()") is None

    # 3. The encoder produces exactly the SMT the intent-validation fixtures pin.
    canonical = {
        "(not (= a #x00000000))": {"op": "not-eq", "name": "a", "value": 0},
        "(bvsge a #x00000000)": {"op": "cmp", "predicate": "sge", "name": "a", "value": 0},
        "(bvsgt a #x00000000)": {"op": "cmp", "predicate": "sgt", "name": "a", "value": 0},
        "(= (bvand a #x000000ff) #x00000000)": {"op": "known-bits", "name": "a", "zero_mask": 255},
        "(and (not (= a #x00000000)) (= (bvand a (bvsub a #x00000001)) #x00000000))":
            {"op": "power-of-two", "name": "a", "nonzero": True},
    }
    for expected_smt, assumption in canonical.items():
        assert vt.scalar_assumption_smt(assumption, "a") == expected_smt, (assumption, expected_smt)

    # 4. The symexec path (predicate_to_guard) emits the SAME fragment as a raw-SMT
    #    guard leaf -- byte-identical to the formal_ir encoding above.
    leaves = analysis_fact_clauses("isKnownToBeAPowerOfTwo(P)")
    assert leaves == [{"op": "smt",
                       "text": "(and (not (= P #x00000000)) (= (bvand P (bvsub P #x00000001)) #x00000000))",
                       "vars": ["P"]}], leaves
    disjoint = analysis_fact_clauses("haveNoCommonBitsSet(X, Y)")
    assert disjoint == [{"op": "smt", "text": "(= (bvand X Y) #x00000000)", "vars": ["X", "Y"]}], disjoint

    # 5. The two-operand disjointness fact `mask-pair` is now lowered by BOTH provers through the same
    #    shared encoder, so it cannot drift either: the symexec guard, the formal-IR assumption SMT,
    #    and the raw symexec leaf are byte-identical.
    assert vt.mask_pair_smt("X", "Y") == "(= (bvand X Y) #x00000000)"
    guard_smt, guard_vars = vt.assumption_guard_smt({"op": "mask-pair", "left": "X", "right": "Y"})
    assert guard_smt == disjoint[0]["text"] and guard_vars == ["X", "Y"], (guard_smt, guard_vars)
    formal_masked = formal_ir.pair_for_formal({
        "domain": "scalar-bv32", "marker": "vt.maskpair", "variables": ["x", "y"], "equivalence": "result",
        "before": {"op": "bvadd", "args": [{"op": "var", "name": "x"}, {"op": "var", "name": "y"}]},
        "after": {"op": "bvor", "args": [{"op": "var", "name": "x"}, {"op": "var", "name": "y"}]},
        "assumptions": [{"op": "mask-pair", "left": "x", "right": "y"}],
    })
    assert "(= (bvand x y) #x00000000)" in formal_masked.assumptions, formal_masked.assumptions

    print("value_tracking consolidation OK: one encoder, both provers locked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
