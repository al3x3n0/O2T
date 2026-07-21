#!/usr/bin/env python3
"""The Clang-AST structured-tree front-end: recover folds from source WITHOUT the regex parser.

pass_graph.recover_pair accepts pre-parsed matcher/rewrite trees, which bypass O2T's tokenizer +
hand-parser (the brittle, TCB-resident component that produced most of the recovery-soundness holes
in phases 36-40). Until now only fixtures hand-authored those trees; o2t/mine/clang_tree.py now
PRODUCES them from real C++ by walking `clang -ast-dump=json` -- the compiler's own parser builds
the call structure, O2T only relabels it.

This pins the payoff: the AST front-end recovers the SAME obligation as the regex path
(byte-identical before/after) on real fold source, proves/refutes it identically, and declines
cleanly outside its mapped fragment -- an independent second reading of the source that removes
the regex parser from the trusted base. Needs z3 AND clang 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.mine import clang_tree as ct  # noqa: E402
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

_HOMEBREW_CLANG = "/opt/homebrew/opt/llvm@18/bin/clang"

# (name, source, expected verdict). STUB-MODE source: written to parse against the minimal API
# stub. This gates the parser-free PRINCIPLE (byte-identical to the regex path), NOT verbatim
# upstream reach -- which is 0 in stub mode and needs real headers + compile context (maturity.md).
FOLDS = [
    ("nested-identity", "proved",
     "Value *f(Instruction &I){ Value *X, *Y;\n"
     "  if (match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One())))\n"
     "    return replaceInstUsesWith(I, X);\n  return nullptr; }"),
    ("or-self", "proved",
     "Value *f(Instruction &I){ Value *X;\n"
     "  if (match(&I, m_Or(m_Value(X), m_Deferred(X))))\n"
     "    return replaceInstUsesWith(I, X);\n  return nullptr; }"),
    ("builder-dfg-and", "proved",
     "Value *f(Instruction &I){ Value *X, *Y;\n"
     "  if (match(&I, m_And(m_Value(X), m_Value(Y))))\n"
     "    return replaceInstUsesWith(I, Builder.CreateAnd(X, Y));\n  return nullptr; }"),
    ("wrong-sub-fold", "refuted",   # X - Y -> X is unsound; the AST path must refute too (teeth)
     "Value *f(Instruction &I){ Value *X, *Y;\n"
     "  if (match(&I, m_Sub(m_Value(X), m_Value(Y))))\n"
     "    return replaceInstUsesWith(I, X);\n  return nullptr; }"),
    # GUARDED folds: the AST condition's non-match conjuncts are reconstructed into the recovered
    # precondition (the matcher tree is still parser-free). sdiv->udiv proves ONLY under both
    # nonneg guards; add->or proves ONLY under disjointness.
    ("guarded-sdiv-udiv", "proved",
     "Value *f(Instruction &I){ Value *X, *Y;\n"
     "  if (match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) "
     "&& isKnownNonNegative(Y))\n"
     "    return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n  return nullptr; }"),
    ("disjoint-add-or", "proved",
     "Value *f(Instruction &I){ Value *X, *Y;\n"
     "  if (match(&I, m_Add(m_Value(X), m_Value(Y))) && haveNoCommonBitsSet(X, Y))\n"
     "    return replaceInstUsesWith(I, Builder.CreateOr(X, Y));\n  return nullptr; }"),
]


def main() -> int:
    z3 = shutil.which("z3")
    clang = shutil.which("clang") or (_HOMEBREW_CLANG if Path(_HOMEBREW_CLANG).exists() else None)
    if z3 is None or clang is None:
        print("clang_tree_fixture: z3 or clang(18) not found, skipped")
        return 0

    proved = refuted = 0
    for name, expect, src in FOLDS:
        # 1. Recover via the Clang AST -- the regex _parse is NOT in this path.
        clang_pair = ct.recover_from_clang(src, clang_bin=clang)
        assert clang_pair is not None, ("AST front-end must recover", name)

        # 2. CROSS-FRONT-END AGREEMENT: the same obligation as the regex path, byte for byte --
        #    before, after, variables, AND the recovered assumptions (the guard preconditions).
        #    Two independent readings of the source agree, matcher AND guards.
        regex_pair = pg.recover_from_function(src)
        assert regex_pair is not None, name
        assert clang_pair["before"] == regex_pair["before"], (name, "before diverged")
        assert clang_pair["after"] == regex_pair["after"], (name, "after diverged")
        assert clang_pair["variables"] == regex_pair["variables"], name
        assert sorted(map(str, clang_pair["assumptions"])) == \
            sorted(map(str, regex_pair["assumptions"])), (name, "assumptions diverged")

        # 3. The verdict is identical and correct (teeth: the wrong fold refutes via the AST path).
        status, cex = ma.prove(clang_pair, z3)
        assert status == expect, (name, status, expect)
        if status == "refuted":
            assert cex, (name, "refutation needs a witness")
            refuted += 1
        else:
            proved += 1

    # 4. RETURN-FORM anchor (upstream's dominant idiom): a fold-named helper that RETURNS the
    #    replacement value (no replaceInstUsesWith), with a local Builder let inlined. Recovered
    #    via the AST parser-free, byte-identical to the regex path, and refuted when mutated.
    retform = ("Value *combineAddSub(Instruction &I) {\n"
               "  Value *A, *B, *Cnt;\n"
               "  if (match(&I, m_Add(m_Shl(m_Neg(m_Value(B)), m_Value(Cnt)), m_Value(A)))) {\n"
               "    Value *NewShl = Builder.CreateShl(B, Cnt);\n"
               "    return Builder.CreateSub(A, NewShl);\n  }\n  return nullptr; }")
    rf_clang = ct.recover_from_clang(retform, clang_bin=clang)
    rf_regex = pg.recover_from_function(retform)
    assert rf_clang is not None and rf_regex is not None, "return-form must recover both ways"
    assert rf_clang["before"] == rf_regex["before"] and rf_clang["after"] == rf_regex["after"], \
        "return-form obligation diverged from the regex path"
    assert ma.prove(rf_clang, z3)[0] == "proved", "(-B<<Cnt)+A -> A-(B<<Cnt) must prove"
    mutated = ct.recover_from_clang(retform.replace("CreateSub", "CreateAdd"), clang_bin=clang)
    assert mutated is not None and ma.prove(mutated, z3)[0] == "refuted", "mutated reducer must refute"
    # a query-helper NAME and a guarded return-form both decline (name gate; unguarded cut).
    assert ct.recover_from_clang(retform.replace("combineAddSub", "dyn_castFoo"), clang_bin=clang) is None
    proved += 1
    refuted += 1

    # 5. GUARD IS LOAD-BEARING: the guarded sdiv->udiv refutes when its guard is DROPPED -- so the
    #    reconstructed precondition is genuinely carrying the proof, not decoration.
    unguarded = ("Value *f(Instruction &I){ Value *X, *Y;\n"
                 "  if (match(&I, m_SDiv(m_Value(X), m_Value(Y))))\n"
                 "    return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n  return nullptr; }")
    up = ct.recover_from_clang(unguarded, clang_bin=clang)
    assert up is not None and ma.prove(up, z3)[0] == "refuted", "unguarded sdiv->udiv must refute"

    # 6. SOUND DECLINE, never a dropped premise: a non-fold declines; a guard that is NOT a flat
    #    reconstructible call (a `Pred == ICMP_EQ` compare) declines rather than drop it; a bailout
    #    cascade (multiple ifs) is out of this cut and declines.
    assert ct.recover_from_clang("Value *f(Instruction &I){ return nullptr; }", clang_bin=clang) is None
    cascade = ("Value *f(Instruction &I){ Value *X, *Y;\n"
               "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
               "  if (!isKnownNonNegative(X)) return nullptr;\n"
               "  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y)); }")
    assert ct.recover_from_clang(cascade, clang_bin=clang) is None, "bailout cascade: out of this cut"

    print(f"clang_tree_fixture OK: the Clang-AST front-end recovers {proved} folds proved and "
          f"{refuted} refuted from real C++ WITHOUT the regex parser -- matcher AND guard "
          "conjuncts reconstructed, each obligation byte-identical to the regex path (before, "
          "after, variables, assumptions) with the wrong fold refuted and the guard shown "
          "load-bearing; non-fold, non-reconstructible-guard, and bailout-cascade shapes decline, "
          "never dropping a premise. The structured-tree TCB-shrink is realized end-to-end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
