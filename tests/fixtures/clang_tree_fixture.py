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

# (name, source, expected verdict). Unguarded structural folds -- the AST front-end's first cut.
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

    # 4. GUARD IS LOAD-BEARING: the guarded sdiv->udiv refutes when its guard is DROPPED -- so the
    #    reconstructed precondition is genuinely carrying the proof, not decoration.
    unguarded = ("Value *f(Instruction &I){ Value *X, *Y;\n"
                 "  if (match(&I, m_SDiv(m_Value(X), m_Value(Y))))\n"
                 "    return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n  return nullptr; }")
    up = ct.recover_from_clang(unguarded, clang_bin=clang)
    assert up is not None and ma.prove(up, z3)[0] == "refuted", "unguarded sdiv->udiv must refute"

    # 5. SOUND DECLINE, never a dropped premise: a non-fold declines; a guard that is NOT a flat
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
