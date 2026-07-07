#!/usr/bin/env python3
"""Width-parametric corroboration: is a fold's bv32 verdict width-UNIFORM, or a 32-bit coincidence?

O2T proves at 32 bits, but a bv32 proof is not a proof for all widths. `corroborate_widths` re-proves
a fold at several widths ({8,16,32,64}): a genuinely width-uniform identity/refinement holds at EVERY
width (verdicts agree), while a fold tuned to 32 bits -- a byte mask that is all-ones only at i8, or a
closed form like bswap/ctpop that hardcodes 32-bit constants -- diverges and is flagged `width-specific`,
telling the caller the verdict does not generalize. This generalizes phase 16's cast cross-width check
to every non-cast fold, and gives each verdict an explicit width bound.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_width_fixture: z3 not found, skipped")
        return 0

    def rp(pred, rw):
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("expected a recovered fold", pred, rw)
        return pair

    def corr(pred, rw):
        return pg.corroborate_widths(rp(pred, rw), z3)

    # 1. Width-UNIFORM identities: proved at every width (only width-scaling constants 0/1, or none).
    for pred, rw in [
        ("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
        ("match(&I, m_Xor(m_Value(X), m_Deferred(X)))", "return replaceInstUsesWith(I, getNullValue());"),
        ("match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))", "return replaceInstUsesWith(I, X);"),
    ]:
        r = corr(pred, rw)
        assert r["applicable"] and r["agree"] and r["status"] == "proved", (pred, r)
        assert set(r["verdicts"]) == {8, 16, 32, 64}, r["verdicts"]

    # 2. A REFINEMENT fold (no-wrap flag) is uniform too: dropping nsw refines at every width.
    r = corr("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));")
    assert r["agree"] and r["status"] == "proved", r

    # 3. WIDTH-SPECIFIC (teeth): `and X, 0xFF -> X` holds ONLY at i8 (0xFF is all-ones there); every
    #    wider width refutes. A single-width bv32 check would have called this a plain (refuted) fold;
    #    the corroboration additionally reveals it is width-specific.
    r = corr("match(&I, m_And(m_Value(X), m_SpecificInt(255)))", "return replaceInstUsesWith(I, X);")
    assert not r["agree"] and r["status"] == "width-specific", r
    assert r["verdicts"][8] == "proved" and r["verdicts"][32] == "refuted", r["verdicts"]

    # 4. A 32-bit closed form (the bswap involution's mask/shift model) proves ONLY at 32 bits, so it is
    #    correctly flagged as not generalizing -- an honest label on a genuinely width-specific model.
    bs = "m_Intrinsic<Intrinsic::bswap>"
    r = corr(f"match(&I, {bs}({bs}(m_Value(X))))", "return replaceInstUsesWith(I, X);")
    assert r["status"] == "width-specific" and r["verdicts"][32] == "proved", r

    # 5. Even a fold that is abstractly uniform is flagged if its MODEL uses a 32-bit-specific constant:
    #    m_AllOnes lowers to 0xFFFFFFFF, which is all-ones only at i32, so `and X, allones` does not scale
    #    to i64. Honest about the model, never a false "uniform".
    r = corr("match(&I, m_And(m_Value(X), m_AllOnes()))", "return replaceInstUsesWith(I, X);")
    assert r["status"] == "width-specific" and r["verdicts"][32] == "proved" and r["verdicts"][64] == "refuted", r

    # 6. A width-changing cast is NOT applicable here -- it has distinct narrow/wide widths and is
    #    corroborated by the pair-based reconcile_widths (phase 16) instead.
    cast = rp("match(&I, m_Trunc(m_ZExt(m_Value(X)))) && X->getType() == I.getType()",
              "return replaceInstUsesWith(I, X);")
    assert pg.corroborate_widths(cast, z3)["applicable"] is False
    assert pg.reconcile_widths(cast, z3)["agree"], "the cast is corroborated cross-width instead"

    print("pass_graph_width_fixture OK: uniform identities/refinements are corroborated at {8,16,32,64}; "
          "a byte-mask fold (all-ones only at i8), the 32-bit bswap closed form, and a 32-bit-specific "
          "AllOnes model are each flagged width-specific -- the bv32 verdict is labeled with its width "
          "bound; width-changing casts defer to reconcile_widths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
