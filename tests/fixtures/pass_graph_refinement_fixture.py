#!/usr/bin/env python3
"""Proper refinement via the existential (2QBF) encoding.

O2T's default prover discharges a refinement with a single UNSAT query, which is universal-only. That
cannot handle the NONDETERMINISM of `freeze`: since each freeze is a fresh value, the single-quantifier
check forces two freezes to be equal for all values and so DECLINES folds like freeze idempotence
`freeze(freeze(X)) -> freeze(X)` (documented limitation since the refinement phase).

prove_refinement gives freeze the correct quantifier structure: a freeze in the SOURCE is universally
quantified (the environment picks the worst value) and a freeze in the TARGET is existential (the
target picks to match), so the refinement counterexample check becomes an exists-forall query z3 can
solve. It PROVES freeze idempotence, and AGREES with the default prover on every freeze-free fold (no
source freeze -> no quantifier -> the ordinary check), so it is a strict generalization.

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


def V(name):
    return {"op": "var", "name": name}


def fr(n):
    return {"op": "freeze", "args": [n]}


def refpair(before, after, assumptions=()):
    return {"domain": "scalar-bv32", "marker": "refine", "variables": ["x"], "poison_variables": ["x"],
            "before": before, "after": after, "equivalence": "result", "refinement": "refinement",
            "assumptions": list(assumptions)}


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_refinement_fixture: z3 not found, skipped")
        return 0

    X = V("x")

    # 1. THE upgrade: freeze idempotence. The default (single-quantifier) prover DECLINES it (refutes,
    #    because it treats the two freezes' fresh values as independent universals); the existential
    #    encoding PROVES it (the source freeze is universal, the target freeze picks to match).
    idem = refpair(fr(fr(X)), fr(X))
    assert ma.prove(idem, z3)[0] == "refuted", "the single-quantifier check declines freeze idempotence"
    assert pg.prove_refinement(idem, z3) == "proved", "the 2QBF encoding proves freeze idempotence"

    # 2. STRICT GENERALIZATION: it AGREES with the default prover on every fold that prover handles.
    agree = [
        refpair(X, fr(X)),                                          # introduce freeze -> proved
        refpair(fr(X), X),                                          # drop freeze, unguarded -> refuted
        refpair(fr(X), X, [{"op": "not-poison", "name": "x"}]),     # drop freeze, guarded -> proved
    ]
    for pair in agree:
        assert pg.prove_refinement(pair, z3) == ma.prove(pair, z3)[0], ("must agree with default", pair)

    # 3. Flag refinements (recovered from source, no freeze) also agree: no source freeze means no
    #    quantifier, so the exists-forall query degenerates to the ordinary refinement check.
    nsw_drop = pg.recover_pair("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",
                               "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));")
    flag_add = pg.recover_pair("match(&I, m_Add(m_Value(X), m_Value(Y)))",
                               "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));")
    assert pg.prove_refinement(nsw_drop, z3) == "proved" == ma.prove(nsw_drop, z3)[0], "flag drop"
    assert pg.prove_refinement(flag_add, z3) == "refuted" == ma.prove(flag_add, z3)[0], "flag add"

    # 4. Honest bound: an op outside the refinement fragment (a select/ite) declines rather than
    #    silently mis-encoding.
    assert pg.prove_refinement(refpair({"op": "ite", "args": [{"op": "eq", "args": [X, X]}, X, X]}, X), z3) \
        == "unsupported"

    print("pass_graph_refinement_fixture OK: the existential (2QBF) encoding PROVES freeze idempotence "
          "freeze(freeze(X)) -> freeze(X) -- which the single-quantifier check can only decline -- while "
          "agreeing with it on introduce/drop-freeze and no-wrap-flag refinements; an op outside the "
          "fragment declines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
