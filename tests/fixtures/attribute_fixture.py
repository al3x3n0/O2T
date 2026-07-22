#!/usr/bin/env python3
"""Attribution: the A<->B seam -- explain a proved whole-function transform by recovered folds.

Track B proves opt's whole-function transform sound; Track A recovers folds from source. This welds
them (o2t/validate/attribute.py): for each function opt rewrites, find a recovered fold whose
`(before, after)` matches the transform under a variable mapping (checked by SMT, so an equivalent
form still matches). A hit is EXPLAINED -- sound AND accounted for by a source-recovered fold; a miss
is honest RESIDUE (a composed transform, or a fold O2T has not recovered -- the enrichment work-list).

The check is exact (f == before AND f' == after under the mapping), so it CANNOT mis-attribute: a
wrong fold in the corpus is never credited. Over the vendored real-test corpus, a set of recovered
folds explains most single-fold transforms; the rest are residue. Needs z3 AND opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate.attribute import attribute_file  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "vendor_folds" / "instcombine_scalar_tests.ll"

# A recovered-fold corpus (Track A: each recovered from a source pattern), tagged by marker.
SPECS = [
    ("and(X,0)->0", "match(&I, m_And(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, ConstantInt::get(Ty, 0));"),
    ("and(X,-1)->X", "match(&I, m_And(m_Value(X), m_AllOnes()))", "return replaceInstUsesWith(I, X);"),
    ("and(X,X)->X", "match(&I, m_And(m_Value(X), m_Specific(X)))", "return replaceInstUsesWith(I, X);"),
    ("or(X,0)->X", "match(&I, m_Or(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
    ("or(X,-1)->-1", "match(&I, m_Or(m_Value(X), m_AllOnes()))", "return replaceInstUsesWith(I, ConstantInt::get(Ty, -1));"),
    ("or(X,X)->X", "match(&I, m_Or(m_Value(X), m_Specific(X)))", "return replaceInstUsesWith(I, X);"),
    ("xor(X,X)->0", "match(&I, m_Xor(m_Value(X), m_Specific(X)))", "return replaceInstUsesWith(I, ConstantInt::get(Ty, 0));"),
    ("add(X,0)->X", "match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
    ("sub(X,X)->0", "match(&I, m_Sub(m_Value(X), m_Specific(X)))", "return replaceInstUsesWith(I, ConstantInt::get(Ty, 0));"),
]
# TEETH: a WRONG fold (and X,0 -> X, which is unsound -- it is 0). Attribution must NEVER credit it.
WRONG = ("and(X,0)->X[WRONG]", "match(&I, m_And(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("attribute_fixture: z3 or opt(18) not found, skipped")
        return 0

    folds = []
    for name, pred, rw in SPECS + [WRONG]:
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, name
        pair["marker"] = name
        folds.append(pair)

    res = attribute_file(z3, CORPUS.read_text(), folds, opt)
    counts = res["counts"]
    by_fn = {r["function"]: r for r in res["functions"]}

    # 1. Attribution explains most single-fold transforms; each hit names the responsible fold.
    attributed = counts.get("attributed", 0)
    assert attributed >= 8, ("attribution should explain most of the corpus", counts)
    assert counts.get("error", 0) == 0, res["functions"]

    # 2. Known-good attributions land on the RIGHT fold (spot-check).
    for fn, expect in [("and_test1", "and(X,0)->0"), ("and_test2", "and(X,-1)->X"),
                       ("and_test4", "and(X,X)->X")]:
        assert by_fn[fn]["status"] == "attributed" and by_fn[fn]["fold"] == expect, (fn, by_fn[fn])

    # 3. TEETH -- no function is ever attributed to the WRONG fold (the exact f==before && f'==after
    #    check makes mis-attribution impossible; the unsound after never matches opt's real output).
    assert all(r.get("fold") != WRONG[0] for r in res["functions"]), \
        "the unsound fold must never be credited"

    # 4. Residue is honest, not error: everything is attributed or residue (or unsupported), never a
    #    false attribution. The residue is the enrichment work-list.
    assert attributed + counts.get("residue", 0) + counts.get("unsupported", 0) == len(res["functions"])

    print(f"attribute_fixture OK: {attributed}/{len(res['functions'])} whole-function transforms "
          "EXPLAINED by a specific recovered fold (Track A <-> Track B seam) -- each hit names the "
          "responsible fold and mapping; known cases land on the right fold; an unsound fold in the "
          "corpus is NEVER credited (exact before/after match -> no mis-attribution); the rest are "
          "honest residue -- the enrichment work-list, not a false claim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
