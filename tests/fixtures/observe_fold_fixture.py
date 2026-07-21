#!/usr/bin/env python3
"""Observational validation: close the source-intent <-> actual-behavior loop for peephole folds.

Recovering `before == after` from pass source and proving it verifies the pass's INTENT, not the
compiled pass's ACTUAL behavior. This fixture pins the missing observational link (o2t/validate/
observe.py, the peephole analogue of E1's loop TV): a fold recovered FROM SOURCE is emitted as LLVM
IR, the REAL `opt -passes=instcombine` is run on it, and the optimizer's actual output is checked
against the recovered `after`:

  * unguarded identities (`or(X,X)->X`, `add(X,X)->shl X,1`, `(X+0)*1->X`, `sub(X,X)->0`) are
    CONFIRMED -- the real pass performs exactly the transform O2T recovered from source (loop closed),
    even when opt picks an equivalent form (mul X,2 -> shl X,1 vs the recovered add X,X: value-equal);
  * a GUARDED fold (`or(X,C)->xor(X,C)` under `(X&C)==0`) is NOT-FIRED -- opt legitimately does not
    apply it on unconstrained inputs, and checking `before==after` UNDER the recovered precondition
    prevents a false "divergent" (the loop respects the guard, exactly as the pass does);
  * TEETH -- a corrupted `after` (a simulated mis-recovery) is caught DIVERGENT, because the real opt
    output does not match it. The proof says "if the pass does this, it is sound"; the observation
    says "the pass actually does this."

Needs z3 AND opt 18.
"""

from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate.observe import observe_fold  # noqa: E402

UNGUARDED = {
    "or(X,X)->X": ("match(&I, m_Or(m_Value(X), m_Specific(X)))",
                   "return replaceInstUsesWith(I, X);"),
    "add(X,X)->shl X,1": ("match(&I, m_Add(m_Value(X), m_Specific(X)))",
                          "return replaceInstUsesWith(I, Builder.CreateShl(X, ConstantInt::get(Ty, 1)));"),
    "(X+0)*1->X": ("match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))",
                   "return replaceInstUsesWith(I, X);"),
    "sub(X,X)->0": ("match(&I, m_Sub(m_Value(X), m_Specific(X)))",
                    "return replaceInstUsesWith(I, ConstantInt::get(Ty, 0));"),
    "mul(X,2)->X+X": ("match(&I, m_Mul(m_Value(X), m_SpecificInt(2)))",
                      "return replaceInstUsesWith(I, Builder.CreateAdd(X, X));"),
}
GUARDED = ("match(&I, m_Or(m_Value(X), m_SpecificInt(8))) && (X & 8) == 0",
           "return replaceInstUsesWith(I, Builder.CreateXor(X, ConstantInt::get(Ty, 8)));")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("observe_fold_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. LOOP CLOSED: each unguarded fold recovered from source is CONFIRMED against the real opt --
    #    the compiled pass actually performs the transform O2T recovered (equivalent forms allowed).
    confirmed = 0
    for name, (pred, rw) in UNGUARDED.items():
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("recover", name)
        res = observe_fold(pair, z3, opt)
        assert res["status"] == "confirmed", (name, res["status"], "opt must perform the recovered fold")
        confirmed += 1

    # 2. GUARDED: opt cannot assume `(X&8)==0` on unconstrained IR, so it does NOT fire -- and because
    #    the observational equality is checked UNDER the recovered precondition, this is a clean
    #    NOT-FIRED, not a false divergent. The loop honors the guard exactly as the pass does.
    gpair = pg.recover_pair(*GUARDED)
    assert gpair is not None
    gres = observe_fold(gpair, z3, opt)
    assert gres["status"] == "not-fired", ("guarded fold must be not-fired, got", gres["status"])

    # 3. TEETH: a corrupted `after` (as a mis-recovery would produce) is caught DIVERGENT -- the real
    #    opt output (X) does not match the corrupted claim (X & 0 = 0). Observation refutes it
    #    independently of the symbolic proof.
    good = pg.recover_pair("match(&I, m_Or(m_Value(X), m_Specific(X)))", "return replaceInstUsesWith(I, X);")
    bad = copy.deepcopy(good)
    bad["after"] = {"op": "bvand", "args": [{"op": "var", "name": "x"},
                                            {"op": "bvconst", "bits": 32, "value": 0}]}
    assert observe_fold(bad, z3, opt)["status"] == "divergent", "corrupted after must be caught divergent"

    print(f"observe_fold_fixture OK: {confirmed} unguarded folds recovered FROM SOURCE are CONFIRMED "
          "against the real `opt -passes=instcombine` (the pass actually performs the transform O2T "
          "recovered, equivalent forms accepted); a guarded fold is NOT-FIRED under its recovered "
          "precondition (no false divergent -- the loop honors the guard as the pass does); and a "
          "corrupted after is caught DIVERGENT by the real optimizer. Source-intent <-> actual-behavior "
          "loop closed for peephole folds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
