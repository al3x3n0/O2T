#!/usr/bin/env python3
"""4th oracle for recovered folds: execute the emitted LLVM IR through an external interpreter.

O2T's three existing checks (z3, concrete bv8, poison-aware evaluator) all reason over its OWN SMT/DSL
encoding. This adds an independent oracle at the LLVM-IR level: `pass_graph.to_llvm_ir` lowers the
recovered `before`/`after` to REAL textual LLVM IR (native `freeze`/`add nsw`/`icmp`/`select`/casts),
and `reconcile_vellvm` runs both sides through an external interpreter over a value sweep, requiring
agreement with z3.

The intended production backend is Vellvm's interpreter, EXTRACTED FROM a Coq/Rocq-mechanized LLVM
semantics -- the only oracle backed by a machine-checked spec, and one that models the poison/undef the
value engines cannot see. Vellvm is not vendored here, so this fixture exercises two runnable stand-ins:
  * clang/CPU (real, present) for the value fragment -- validates the emitted IR actually compiles and
    executes to the right values;
  * a poison-aware fake interpreter (fake-llvm-interp.py, parsing the emitted IR TEXT) for the
    refinement fragment (nsw/nuw/freeze), which clang at -O0 cannot observe.

Needs z3; the clang leg self-skips without a compiler.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg

FAKE = str(Path(__file__).resolve().parent / "fake-llvm-interp.py")


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_ir_fixture: z3 not found, skipped")
        return 0

    def rp(pred, rw):
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("expected a recovered fold", pred, rw)
        return pair

    # 1. to_llvm_ir emits REAL LLVM IR with the native, poison-relevant ops a machine-checked
    #    interpreter evaluates -- not O2T's SMT.
    nsw = pg.to_llvm_ir(rp("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",
                           "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));"), "before")
    assert "add nsw i32" in nsw and nsw.startswith("define i32 @f(i32 %x, i32 %y)"), nsw
    frz = pg.to_llvm_ir(rp("match(&I, m_Value(X))", "return replaceInstUsesWith(I, Builder.CreateFreeze(X));"), "after")
    assert "freeze i32" in frz, frz
    sel = pg.to_llvm_ir(rp("match(&I, m_SpecificICmp(ICmpInst::ICMP_SLT, m_Value(X), m_Value(Y)))",
                           "return replaceInstUsesWith(I, getTrue());"), "before")
    assert "icmp slt i32" in sel and "select i1" in sel, sel
    cast = pg.to_llvm_ir(rp("match(&I, m_Trunc(m_ZExt(m_Value(X)))) && X->getType() == I.getType()",
                            "return replaceInstUsesWith(I, X);"), "before")
    assert "zext i8" in cast and "trunc i32" in cast, cast

    # 2. clang/CPU oracle (runs here): the emitted IR compiles and executes to the right values --
    #    a sound value fold AGREES with z3, a wrong one is a concrete differential-mismatch.
    clang = shutil.which("clang")
    if clang is not None:
        rc = pg.reconcile_vellvm(rp("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"), z3)
        assert rc["interp"] == "proved" and rc["agree"], ("clang: add X,0->X", rc)
        rc = pg.reconcile_vellvm(rp("match(&I, m_Sub(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X);"), z3)
        assert rc["interp"] == "refuted" and rc["agree"] and rc["witness"], ("clang: sub X,Y->X wrong", rc)
        # xor-self through real IR (icmp/select-free path): sound.
        rc = pg.reconcile_vellvm(rp("match(&I, m_Xor(m_Value(X), m_Deferred(X)))",
                                    "return replaceInstUsesWith(I, getNullValue());"), z3)
        assert rc["interp"] == "proved" and rc["agree"], ("clang: xor X,X->0", rc)
        # clang cannot observe poison, so it ABSTAINS on a refinement fold rather than (dis)agree.
        rc = pg.reconcile_vellvm(rp("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",
                                    "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));"), z3)
        assert rc["interp"] == "skipped", ("clang value-oracle must abstain on a refinement fold", rc)

    # 3. poison-aware interpreter (Vellvm stand-in): handles the refinement fragment the value oracle
    #    cannot -- flag-drop and freeze folds prove, adding a flag refutes, all AGREEING with z3.
    refinement = [
        ("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",                        # flag-drop
         "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));", "proved"),
        ("match(&I, m_Add(m_Value(X), m_Value(Y)))",                           # add a flag -> unsound
         "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));", "refuted"),
        ("match(&I, m_Value(X))",                                              # introduce freeze
         "return replaceInstUsesWith(I, Builder.CreateFreeze(X));", "proved"),
        ("match(&I, m_Freeze(m_Value(X))) && isGuaranteedNotToBeUndefOrPoison(X)",  # guarded freeze-drop
         "return replaceInstUsesWith(I, X);", "proved"),
    ]
    for pred, rw, expect in refinement:
        rc = pg.reconcile_vellvm(rp(pred, rw), z3, interp_bin=FAKE)
        assert rc["z3"] == expect and rc["interp"] == expect and rc["agree"], (pred, rc)

    # 4. the oracle abstains cleanly when no interpreter/compiler is available (no false agreement).
    rc = pg.reconcile_vellvm(rp("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
                             z3, interp_bin="/nonexistent/vellvm")
    assert rc["interp"] == "skipped", ("absent interpreter must skip", rc)

    print("pass_graph_ir_fixture OK: to_llvm_ir emits real LLVM IR (native freeze/nsw/icmp/select/casts); "
          "clang/CPU executes the value fragment in agreement with z3 and yields a concrete witness on a "
          "wrong fold; a poison-aware interpreter (Vellvm stand-in) discharges the refinement fragment -- "
          "flag-drop/freeze prove, adding a flag refutes -- all agreeing with z3; absent runners skip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
