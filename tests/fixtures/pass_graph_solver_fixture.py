#!/usr/bin/env python3
"""Second-solver cross-check: discharge the obligation with an INDEPENDENT SMT solver.

Every other O2T oracle either re-runs z3 or reasons over O2T's own evaluators. This is the one check
that guards against a z3 SOUNDNESS bug (or a malformed encoding z3 mishandles consistently): hand the
IDENTICAL SMT-LIB QF_BV query to a second solver -- bitwuzla, a completely different codebase -- and
require the same sat/unsat.

Pins that z3 and bitwuzla AGREE on proved, refuted, and refinement folds; that an absent second solver
skips cleanly (no false agreement); and that a solver which times out on a query (bitwuzla is much
slower than z3 on div-heavy obligations) abstains rather than disagreeing -- z3 stays authoritative.

Needs z3; the second-solver legs self-skip without bitwuzla.
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
        print("pass_graph_solver_fixture: z3 not found, skipped")
        return 0

    def rp(pred, rw):
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("expected a recovered fold", pred, rw)
        return pair

    # An absent second solver always skips cleanly -- never a false agreement.
    r = pg.reconcile_solver(rp("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
                            z3, solver_bin="/nonexistent/cvc5")
    assert r["solver"] == "skipped" and r["agree"], r

    have_bitwuzla = shutil.which("bitwuzla") is not None
    if not have_bitwuzla:
        print("pass_graph_solver_fixture OK: bitwuzla absent, second-solver legs skipped (plumbing verified)")
        return 0

    # z3 and bitwuzla must AGREE on the same SMT for proved / refuted / refinement folds.
    agree_cases = [
        ("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);", "proved"),
        ("match(&I, m_Xor(m_Value(X), m_Deferred(X)))", "return replaceInstUsesWith(I, getNullValue());", "proved"),
        ("match(&I, m_Sub(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X);", "refuted"),
        ("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",                       # refinement: flag drop
         "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));", "proved"),
        ("match(&I, m_Add(m_Value(X), m_Value(Y)))",                          # refinement: adding a flag
         "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));", "refuted"),
        ("match(&I, m_And(m_Value(X), m_Value(Y)))",                          # disjointness precondition
         "return replaceInstUsesWith(I, Builder.CreateAnd(X, Y));", "proved"),
    ]
    for pred, rw, expect in agree_cases:
        r = pg.reconcile_solver(rp(pred, rw), z3)
        assert r["z3"] == expect and r["solver"] == expect and r["agree"], (pred, r)

    # A solver that cannot finish in time abstains (skips) rather than (dis)agreeing: bitwuzla is far
    # slower than z3 on bvsdiv/bvudiv, so a short timeout on the guarded sdiv->udiv yields a clean skip.
    r = pg.reconcile_solver(
        rp("match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) && isKnownNonNegative(Y)",
           "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"), z3, timeout=2)
    assert r["solver"] == "skipped" and r["agree"] and r["reason"] == "timeout", r

    print("pass_graph_solver_fixture OK: an independent SMT solver (bitwuzla) agrees with z3 on the same "
          "SMT for proved/refuted/refinement folds -- a cross-check no re-run of z3 can give; an absent "
          "solver or a query it cannot finish in time abstains cleanly, leaving z3 authoritative")
    return 0


if __name__ == "__main__":
    sys.exit(main())
