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

        # 2. CROSS-FRONT-END AGREEMENT: the same obligation as the regex path, byte for byte.
        #    Two independent readings of the source agree -- the structured-tree soundness idea,
        #    realized end-to-end from real C++ rather than hand-authored trees.
        regex_pair = pg.recover_from_function(src)
        assert regex_pair is not None, name
        assert clang_pair["before"] == regex_pair["before"], (name, "before diverged")
        assert clang_pair["after"] == regex_pair["after"], (name, "after diverged")
        assert clang_pair["variables"] == regex_pair["variables"], name

        # 3. The verdict is identical and correct (teeth: the wrong fold refutes via the AST path).
        status, cex = ma.prove(clang_pair, z3)
        assert status == expect, (name, status, expect)
        if status == "refuted":
            assert cex, (name, "refutation needs a witness")
            refuted += 1
        else:
            proved += 1

    # 4. SOUND DECLINE: a non-fold function (no match / no rewrite) yields nothing -- never a
    #    mis-mapping; and a guarded fold is out of this cut and declines here (guards route through
    #    the string path), rather than silently dropping the premise.
    assert ct.recover_from_clang("Value *f(Instruction &I){ return nullptr; }", clang_bin=clang) is None
    guarded = ("Value *f(Instruction &I){ Value *X, *Y;\n"
               "  if (match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X))\n"
               "    return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n  return nullptr; }")
    assert ct.recover_from_clang(guarded, clang_bin=clang) is None, "guarded fold: out of this cut"

    print(f"clang_tree_fixture OK: the Clang-AST front-end recovers {proved} folds proved and "
          f"{refuted} refuted from real C++ WITHOUT the regex parser -- each obligation "
          "byte-identical to the regex path (two independent readings agree) with the wrong fold "
          "refuted via the AST path; non-fold and (this-cut) guarded shapes decline, never "
          "mis-map. The structured-tree TCB-shrink is realized end-to-end from source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
