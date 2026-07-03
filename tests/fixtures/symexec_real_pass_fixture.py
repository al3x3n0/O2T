#!/usr/bin/env python3
"""Cover real-pass symbolic execution (o2t/symexec/real_pass.py).

Asserts that O2T compiles and symbolically executes the GENUINE C++ of pass folds (built against
the symbolic-LLVM shim), enumerates their real control-flow paths, and on each rewriting path
proves the rewrite refines the input under the facts the taken branches established -- so a
correctly-guarded fold proves and an under-guarded one (rewrites without checking its precondition)
is REFUTED with a witness, caught by executing the actual branch rather than a pattern match.
Needs clang++ and z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.symexec import real_pass as R


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = shutil.which("z3")
    clang = _resolve("clang++", "/usr/bin/clang++")
    if z3 is None or clang is None:
        print("symexec_real_pass_fixture: z3 or clang++ not found, skipped")
        return 0

    exe = R.compile_harness(str(ROOT / "tests" / "fixtures" / "symexec_folds.cpp"), clang=clang)
    assert exe is not None, "harness failed to compile"

    # 1) the GUARDED urem fold: two real paths (fold / no-fold); the rewriting path checked
    #    power-of-two, so the rewrite is proved sound under exactly that fact.
    g = R.verify_fold(z3, exe, "urem_guarded")
    assert g["ok"] and g["paths"] == 2 and g["rewriting_paths"] == 1 and g["proved"] == 1, g
    rw = next(r for r in g["rows"] if r["rewrote"])
    assert rw["status"] == "proved" and rw["facts"] == 1 and rw["decisions"] == ["power-of-two"], rw

    # 2) the UNDER-GUARDED urem fold: it rewrites on a path with NO established facts -> the
    #    rewrite cannot refine the input for all P -> REFUTED with a witness. Caught from the
    #    genuine branch (there is no guard to pattern-match; the path simply has no facts).
    b = R.verify_fold(z3, exe, "urem_unguarded")
    assert not b["ok"] and b["refuted"] == 1, b
    bad = next(r for r in b["rows"] if r["rewrote"])
    assert bad["status"] == "refuted" and bad["facts"] == 0 and bad.get("witness"), bad

    # 3) a multi-query fold (sdiv -> udiv): the rewriting path established BOTH operands
    #    non-negative -> proved under those two facts.
    s = R.verify_fold(z3, exe, "sdiv_guarded")
    assert s["ok"] and s["proved"] == 1, s
    srw = next(r for r in s["rows"] if r["rewrote"])
    assert srw["status"] == "proved" and srw["facts"] == 2, srw

    # 3b) folds written the way REAL 3rd-party code is -- PatternMatch (incl. NESTED patterns,
    #     m_Specific, constant matchers, dyn_cast), operand capture, ConstantInt::get, and APInt
    #     reasoning on a captured constant -- compile against the shim and verify sound.
    for fold in ("urem_pattern", "sub_self", "add_sub_cancel", "and_allones", "mul_pow2",
                 "add_zero", "add_zero_comm"):
        pf = R.verify_fold(z3, exe, fold)
        assert pf["ok"] and pf["proved"] == 1, (fold, pf)
        assert all(r["status"] == "proved" for r in pf["rows"] if r["rewrote"]), (fold, pf)
    # the guarded pattern fold relied on exactly the power-of-two fact.
    pg = next(r for r in R.verify_fold(z3, exe, "urem_pattern")["rows"] if r["rewrote"])
    assert pg["decisions"] == ["power-of-two"], pg

    # 3c) CAPTURED-CONSTANT reasoning is load-bearing: `mul X,C -> shl X,log2(C)` is sound only when
    #     C is a power of two. The faithful floor-log2 model means the UNGUARDED variant is REFUTED
    #     with a non-power-of-two witness -- the APInt-derived rewrite is not vacuously proved.
    mb = R.verify_fold(z3, exe, "mul_pow2_unguarded")
    assert not mb["ok"] and mb["refuted"] == 1, mb
    mbrw = next(r for r in mb["rows"] if r["rewrote"])
    assert mbrw["status"] == "refuted" and mbrw["witness"], mbrw

    # 3d) POISON/UB-AWARE REFINEMENT (not value-equality): `add X,Y -> add nsw X,Y` keeps the same
    #     value but sets `nsw`, which is poison on signed overflow. The guarded variant proves the
    #     add cannot overflow before setting the flag -> the introduced poison is excluded -> proved.
    #     The unguarded variant sets `nsw` unconditionally -> on overflow the output is poison while
    #     the source was defined -> REFUTED with a genuine two-negatives-sum-positive witness. This is
    #     a bug class pure value-equality cannot see (the values are identical; only definedness differs).
    #     The same refinement machinery covers every poison-producing flag the same way: nsw
    #     (signed overflow), nuw (unsigned overflow), and `or disjoint` (set when X&Y!=0 -- here the
    #     fold also CHANGES the value to `or`, so one no-common-bits fact must discharge BOTH the
    #     value-equality and the flag; refinement handles them together).
    #     `udiv exact` extends this past the overflow flags: its poison `(X urem Y) != 0` depends on
    #     the operand VALUES, not just their signs -- the guard is a divides-evenly query.
    flag_folds = ("add_nsw", "add_nuw", "add_or_disjoint", "udiv_exact")
    for flag in flag_folds:
        ng = R.verify_fold(z3, exe, f"{flag}_guarded")
        assert ng["ok"] and ng["paths"] == 2 and ng["rewriting_paths"] == 1 and ng["proved"] == 1, (flag, ng)
        nb = R.verify_fold(z3, exe, f"{flag}_unguarded")
        assert not nb["ok"] and nb["refuted"] == 1, (flag, nb)
        nbrw = next(r for r in nb["rows"] if r["rewrote"])
        assert nbrw["status"] == "refuted" and nbrw["witness"], (flag, nbrw)

    # 3e) MULTI-INSTRUCTION rewrite where the SOURCE is itself flagged: `add nsw (add nsw X,C1),C2`
    #     -> `add nsw X, (C1+C2)`. The value is X+C1+C2 either way (equal mod 2^32), so pure
    #     value-equality proves it "sound". But the combined `add nsw X,(C1+C2)` is poison whenever
    #     C1+C2 itself signed-overflows -- on inputs where the source's two in-range nsw adds were
    #     defined. Refinement (with the source's own poison modeled) refutes the unguarded combine
    #     with a witness (C1,C2 both negative, summing positive); the C1+C2-no-overflow guard fixes it.
    cg = R.verify_fold(z3, exe, "nested_nsw_addconst_guarded")
    assert cg["ok"] and cg["paths"] == 2 and cg["proved"] == 1, cg
    cb = R.verify_fold(z3, exe, "nested_nsw_addconst_unguarded")
    assert not cb["ok"] and cb["refuted"] == 1, cb
    cbrw = next(r for r in cb["rows"] if r["rewrote"])
    assert cbrw["status"] == "refuted" and cbrw["witness"], cbrw

    # 3f) POISON CONTAGION (not introduction): `select C, true, Y -> or C, Y` is value-identical on
    #     i1 (C?1:Y == C|Y) -- a value-only checker passes BOTH this and its freeze-fixed sibling.
    #     But `or C, Y` is poison when the OPERAND Y is poison, while the source select returns 1
    #     (defined) when C is true. With symbolic operand-poison flags, refinement REFUTES the raw
    #     rewrite and PROVES the `or C, freeze Y` version -- the canonical reason `freeze` exists.
    so = R.verify_fold(z3, exe, "select_to_or_raw")
    assert not so["ok"] and so["refuted"] == 1 and so["proved"] == 0, so
    sorw = next(r for r in so["rows"] if r["rewrote"])
    assert sorw["status"] == "refuted" and sorw["witness"], sorw
    sf = R.verify_fold(z3, exe, "select_to_or_freeze")
    assert sf["ok"] and sf["proved"] == 1 and sf["refuted"] == 0, sf

    # 3g) FLOATING-POINT / fast-math: the refinement check is not bitvector-only. `fadd X,Y ->
    #     fadd nnan X,Y` is the FP analogue of nsw -- the nnan flag is poison when the sum is NaN
    #     (e.g. +inf + -inf, a DEFINED value in the source). Discharged in the FP theory (QF_FPBV):
    #     guarded by a no-NaN query -> proved; unguarded -> refuted with an FP witness.
    fg = R.verify_fold(z3, exe, "fadd_nnan_guarded")
    assert fg["ok"] and fg["paths"] == 2 and fg["proved"] == 1, fg
    fb = R.verify_fold(z3, exe, "fadd_nnan_unguarded")
    assert not fb["ok"] and fb["refuted"] == 1, fb
    fbrw = next(r for r in fb["rows"] if r["rewrote"])
    assert fbrw["status"] == "refuted" and fbrw["witness"], fbrw

    # 3h) MEMORY / aliasing: store-to-load forwarding `store V,P; load Q -> V` modeled in the array
    #     theory (QF_ABV): the loaded value is select(store(MEM,P,V),Q). Sound only under must-alias
    #     (P==Q); a forward justified by anything weaker reads the wrong cell. Guarded by a must-alias
    #     query -> proved; unguarded -> refuted (a P!=Q witness where the load sees the old memory).
    lg = R.verify_fold(z3, exe, "load_forward_guarded")
    assert lg["ok"] and lg["paths"] == 2 and lg["proved"] == 1, lg
    lb = R.verify_fold(z3, exe, "load_forward_unguarded")
    assert not lb["ok"] and lb["refuted"] == 1, lb
    lbrw = next(r for r in lb["rows"] if r["rewrote"])
    assert lbrw["status"] == "refuted" and lbrw["witness"], lbrw

    # 3i) The SAME `store V,P; load Q` pattern has two opposite sound resolutions, distinguished only
    #     by the aliasing fact: load-forwarding (-> V) needs MUST-alias (P==Q, block 3h); dead-store
    #     elimination (remove the store so the load reads original memory) needs NO-alias (P!=Q).
    #     A pass that picks either resolution under the wrong (or no) aliasing guard is refuted.
    dg = R.verify_fold(z3, exe, "dead_store_guarded")
    assert dg["ok"] and dg["paths"] == 2 and dg["proved"] == 1, dg
    db = R.verify_fold(z3, exe, "dead_store_unguarded")
    assert not db["ok"] and db["refuted"] == 1, db
    dbrw = next(r for r in db["rows"] if r["rewrote"])
    assert dbrw["status"] == "refuted" and dbrw["witness"], dbrw

    # 3j) MULTI-INSTRUCTION + KNOWN-BITS: `mul (lshr X,1), 2 -> X` matches on the operand's PRODUCER
    #     (the lshr feeding the mul -- two instructions). It looks like a shift round-trip, but
    #     (X>>1)<<1 drops X's low bit, so it equals X only when X is even. The guard is a known-bits
    #     query (low bit zero); unguarded is refuted with the minimal odd witness X=1.
    rg = R.verify_fold(z3, exe, "shr_shl_roundtrip_guarded")
    assert rg["ok"] and rg["paths"] == 2 and rg["proved"] == 1, rg
    rb = R.verify_fold(z3, exe, "shr_shl_roundtrip_unguarded")
    assert not rb["ok"] and rb["refuted"] == 1, rb
    rbrw = next(r for r in rb["rows"] if r["rewrote"])
    assert rbrw["status"] == "refuted" and rbrw["witness"], rbrw

    # 3k) A WHOLE multi-instruction PASS RUN, not a single fold: a worklist simplifier iterates a
    #     straight-line block (`%1=add X,0; %2=mul %1,1; %3=sub %2,%2`) to FIXPOINT, threading each
    #     simplification into its users. O2T discharges that the COMPOSED final value refines the
    #     original block's semantics. The sound rule set collapses the block to 0 (proved); a planted
    #     unsound `sub v,v -> v` rule yields X, refuted against the spec 0 -- the bug is caught on the
    #     composition, across iterations, not on any one fold in isolation.
    ws = R.verify_fold(z3, exe, "worklist_sound")
    assert ws["ok"] and ws["proved"] == 1, ws
    assert next(r for r in ws["rows"] if r["rewrote"])["status"] == "proved", ws
    wb = R.verify_fold(z3, exe, "worklist_buggy")
    assert not wb["ok"] and wb["refuted"] == 1, wb
    wbrw = next(r for r in wb["rows"] if r["rewrote"])
    assert wbrw["status"] == "refuted" and wbrw["witness"], wbrw

    # 3l) MEMORY PROVENANCE / out-of-bounds UB: hoisting a guarded load `if (i<n) load a[i] else 0`
    #     into an unconditional `load a[i]` is UB when i>=n -- the speculated access is out of bounds,
    #     UB the guarded source never had. Modeled in QF_ABV with the OOB load carrying poison
    #     (bvuge i n); guarded by an in-bounds query -> proved, unguarded -> refuted. Combines the
    #     array theory (memory) with the poison machinery (UB), a step into provenance reasoning.
    pg = R.verify_fold(z3, exe, "speculate_load_guarded")
    assert pg["ok"] and pg["paths"] == 2 and pg["proved"] == 1, pg
    pb = R.verify_fold(z3, exe, "speculate_load_unguarded")
    assert not pb["ok"] and pb["refuted"] == 1, pb
    pbrw = next(r for r in pb["rows"] if r["rewrote"])
    assert pbrw["status"] == "refuted" and pbrw["witness"], pbrw

    # 4) the CLI agrees and exits 0 on the sound folds.
    tool = ROOT / "tools" / "cv-symexec-real-pass.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout

    print("symexec_real_pass_fixture OK: the GENUINE C++ of pass folds compiled and symbolically "
          "executed over its real control-flow paths; each rewrite proved to refine the input under "
          "the facts its branches established; an under-guarded fold refuted with a witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
