"""Ground the RECOVERY against the compiler's own reading of the source.

Hardening the parser (structured trees) removes MISparsing; it does not confirm that O2T's recovered
model is a FAITHFUL reading of the source's semantics -- the mapping `Builder.CreateUDiv` -> bvudiv is a
hand-written interpretation. ground_recovery closes that gap the CompCert-translation-validation way:
it compiles the VERBATIM source rewrite against the symbolic shim (which implements each Builder.Create*
INDEPENDENTLY of O2T's lowering), and checks the SMT the compiler+shim compute equals O2T's recovered
`after`. A misrecovery -- a dropped operator, a wrong builder, a mislowered op -- makes them diverge.

This is stronger than reconcile_compiled, which reconstructs the harness from O2T's OWN recovered node
and so cannot catch a recovery that is internally self-consistent but unfaithful to the source.

Pins: (1) a faithful recovery grounds (source SMT == recovered SMT); (2) a mislowered `after` is caught
as a divergence against the source; (3) absent compiler / out-of-fragment rewrite skips cleanly.

Needs z3 and clang++.
"""

from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_grounding_fixture: z3 not found, skipped")
        return 0

    # Absent compiler / out-of-shim-fragment rewrites skip cleanly -- never a false grounding.
    rw = "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"
    pair = pg.recover_pair("match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) "
                           "&& isKnownNonNegative(Y)", rw)
    assert pg.ground_recovery(pair, rw, z3, clang="/nonexistent/clang++")["grounded"] == "skipped"

    if shutil.which("clang++") is None:
        print("pass_graph_grounding_fixture OK: clang++ absent, grounding skipped (plumbing verified)")
        return 0

    # 1. FAITHFUL recovery: the shim's independent reading of the source rewrite equals O2T's recovered
    #    `after` -- source_smt == recovered_smt.
    g = pg.ground_recovery(pair, rw, z3)
    assert g["grounded"] is True and g["source_smt"] == g["recovered_smt"], g

    # a multi-op DFG rewrite grounds too (the shim composes Create* the same way O2T lowers the tree).
    rw_or = "return replaceInstUsesWith(I, Builder.CreateOr(X, Y));"
    pair_or = pg.recover_pair("match(&I, m_Add(m_Value(X), m_Value(Y))) && haveNoCommonBitsSet(X, Y)", rw_or)
    assert pg.ground_recovery(pair_or, rw_or, z3)["grounded"] is True, "add->or must ground"

    # 2. TEETH: a MISRECOVERY of the rewrite is caught. Simulate O2T recovering `after` as an add while
    #    the source clearly writes CreateUDiv -- the shim reads the source as bvudiv and the divergence
    #    is flagged. reconcile_compiled could not catch this (it builds the harness from O2T's node).
    mislowered = copy.deepcopy(pair)
    mislowered["after"] = {"op": "bvadd", "args": [{"op": "var", "name": "x"}, {"op": "var", "name": "y"}]}
    g = pg.ground_recovery(mislowered, rw, z3)
    assert g["divergence"] is True and g["grounded"] is False, ("mislowered after must diverge", g)
    assert g["source_smt"] == "(bvudiv X Y)" and g["recovered_smt"] == "(bvadd X Y)", g

    print("pass_graph_grounding_fixture OK: the recovered `after` is grounded against the VERBATIM source "
          "rewrite compiled through the symbolic shim (an independent reading of the source); a faithful "
          "recovery matches, a mislowered rewrite is caught as a source-vs-recovery divergence that "
          "reconcile_compiled cannot see; absent compiler / out-of-fragment rewrites skip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
