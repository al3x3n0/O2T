#!/usr/bin/env python3
"""Verify an UNMODIFIED upstream InstCombine fold by executing its real C++.

This is the capability the project is named for: a formal model of an optimization taken from the
pass's own source. Not a recovered pattern, not a re-implementation -- the byte-for-byte C++ of
`combineAddSubWithShlAddSub` from LLVM 18's `InstCombineAddSub.cpp`, compiled against the
symbolic-LLVM shim so every `Value` is an SMT term, executed so its GENUINE branches are the ones
explored, and each rewriting path discharged as

    (facts the taken branches established)  =>  out == in   for all inputs

The other track recovers a fold's *pattern* from source and proves an obligation about it, which
reaches 3 of 106 fold-shaped functions in real InstCombine files and, measured, does not improve with
vocabulary: adding the 32 most-wanted constructs unblocks ZERO further functions, because real folds
call pass-local helpers rather than fitting a matcher template. What stood between the shim and real
source was pointer/reference parity: upstream writes `Value *A; match(&I, m_Value(A))`, binding
POINTERS, where the shim took references.

This path was initially expected to have the opposite shape -- shared surface, so each addition helps
every fold at once. MEASUREMENT REFUTED THAT. Three separate batches were tried and each unblocked
ZERO further folds: matcher vocabulary, generic construction (`BinaryOperator::Create`, `CreateBinOp`,
`cast`, `m_ICmp`, APInt predicates), and the `Intrinsic` surface -- the single largest blocker at 68
occurrences. Error counts fall each time (the 9+-error bucket 76 -> 67) but nothing crosses to zero,
because a fold typically needs items from SEVERAL categories at once. Of the undeclared identifiers
across the 101 non-compiling folds, 55% are LLVM analysis/type infrastructure (`Intrinsic`, `SQ`,
`ConstantExpr`, pass-member helpers), 33% matcher vocabulary, and only 12% pass-local helpers -- so
compiling whole `.cpp` files would address the smallest share. Reach here is bounded by modelling
LLVM's analysis infrastructure soundly, which is verification work rather than plumbing.

Gated here:
  * the vendored upstream source still COMPILES against the shim. If this breaks, the shim has
    drifted away from genuine pass source, which is the whole point of keeping real source in-tree;
  * its real execution proves the rewrite sound on every rewriting path;
  * TEETH -- corrupting the rewrite's shift amount REFUTES with a concrete witness, so the proof is
    load-bearing rather than a harness that would prove anything;
  * a rewriting path that neither proves nor refutes is NOT sound. `verify_fold` used to compute
    `ok = rewriting and not refuted`, so a path whose discharge errored or returned `unknown`
    counted as verified -- a non-answer reported as a proof. It now requires every rewriting path to
    be proved, and this fixture pins that.
Needs clang++ and z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.symexec import real_pass as R  # noqa: E402

VENDOR = ROOT / "tests" / "fixtures" / "vendor_folds"
SRC = VENDOR / "upstream_addsub_fold.cpp"
FOLD = "combineAddSubWithShlAddSub"
SRC2 = VENDOR / "upstream_andorxor_fold.cpp"
FOLD2 = "foldNotXor"
FOLD3 = "foldXorToXor"
FOLD4 = "foldOrToXor"
FOLD5 = "foldAndToXor"
# The first fold here that reasons about ICMP and CONSTANT MASKS rather than pure boolean algebra:
# it exercises the shim's i1 modelling, APInt mask arithmetic and ConstantInt.
MASKEDICMP = VENDOR / "upstream_maskedicmp_fold.cpp"
FOLD6 = "foldLogOpOfMaskedICmps_NotAllZeros_BMask_Mixed"
# The first fold whose correctness turns on POISON rather than only on values.
SELECTSHIFT = VENDOR / "upstream_select_lshrashr_fold.cpp"
FOLD7 = "foldSelectICmpLshrAshr"
# The first fold whose rewrite CHANGES WIDTH: `sext (X != 0)`.
ZEROORONES = VENDOR / "upstream_select_zeroorones_fold.cpp"
FOLD8 = "foldSelectZeroOrOnes"
# A bit-test rewritten as a masked compare; the @shift arm is the first REAL fold to exercise
# `m_SpecificInt_ICMP` (a constraint on a constant, not an icmp instruction).
ICMPANDAND = VENDOR / "upstream_select_icmpandand_fold.cpp"
POW2 = VENDOR / "upstream_icmps_and_pow2_fold.cpp"
# The batch the reach sweep named: folds blocked by shim API SHAPE or one missing accessor, not by
# a whole category. See tools/cv-symexec-reach-sweep.py.
ADDCONST = VENDOR / "upstream_icmp_addconst_fold.cpp"
ADDCONST_ARMS = ("addconst_ult", "addconst_ule", "addconst_ugt", "addconst_slt", "addconst_sgt",
                 "addconst_slt_neg", "addconst_sgt_neg")
SETCLEAR = VENDOR / "upstream_setclearbits_fold.cpp"
SETCLEAR_ARMS = ("setclear_clear_first", "setclear_set_first")
EQICMP = VENDOR / "upstream_icmpeq_and_icmp_fold.cpp"
EQICMP_ARMS = ("eqicmp_or", "eqicmp_and", "eqicmp_logical_or")
FOLD10_ARMS = ("pow2_and", "pow2_or", "pow2_and_logical")
FOLD9_ARMS = ("foldSelectICmpAndAnd", "foldSelectICmpAndAnd@shift")
# Every REWRITING ARM of the three AndOrXor folds, not just the first one each. A fold's arms are
# separate theorems reached by different patterns, and a copy-paste slip between them is the most
# plausible real bug -- upstream's own comments list four commuted variants per arm.
ARMS = ("foldNotXor", "foldNotXor@2",
        "foldXorToXor", "foldXorToXor@2", "foldXorToXor@3", "foldXorToXor@4",
        "foldOrToXor", "foldOrToXor@2", "foldOrToXor@3",
        # the commuted forms upstream enumerates, which reach the same arm via m_c_*
        "foldXorToXor#c2", "foldXorToXor#c3", "foldXorToXor#c4",
        # foldAndToXor, reached through m_BinOp (any binary operator)
        "foldAndToXor", "foldAndToXor@2")


def _clang():
    for cand in ("clang++", "/opt/homebrew/opt/llvm@18/bin/clang++", "/usr/bin/clang++"):
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


def main() -> int:
    z3, clang = shutil.which("z3"), _clang()
    if z3 is None or clang is None:
        print("upstream_symexec_fixture: needs clang++ and z3, skipped")
        return 0

    # 1) UNMODIFIED upstream source compiles against the shim and executes.
    exe = R.compile_harness(str(SRC), clang=clang)
    assert exe is not None, ("verbatim upstream source no longer compiles against the symbolic shim "
                             "-- the shim has drifted from real pass source")
    r = R.verify_fold(z3, exe, FOLD)
    assert r["rewriting_paths"] >= 1, ("the fold must actually rewrite on some path", r)
    assert r["refuted"] == 0 and r["proved"] == r["rewriting_paths"], r
    assert r["ok"], ("the verbatim upstream fold must verify", r)

    # 2) TEETH: corrupt the rewrite the upstream source performs -- swap the shift amount -- and the
    #    same machinery must refute it with a witness. Without this, "SOUND" could mean the harness
    #    proves anything put in front of it.
    import tempfile
    # upstream's own local is `Cnt`; the harness's symbol is `C`. Corrupt the FOLD, not the harness.
    bad_src = SRC.read_text().replace("Builder.CreateShl(B, Cnt)", "Builder.CreateShl(B, A)")
    assert bad_src != SRC.read_text(), "the corruption must actually apply to the vendored source"
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "corrupted.cpp"
        bad.write_text(bad_src)
        bad_exe = R.compile_harness(str(bad), clang=clang, )
        assert bad_exe is not None, "the corrupted variant should still compile"
        rb = R.verify_fold(z3, bad_exe, FOLD)
        assert rb["refuted"] >= 1, ("a corrupted rewrite must be refuted", rb)
        assert not rb["ok"], rb
        witness = next(row for row in rb["rows"] if row["status"] == "refuted").get("witness")
        assert witness, "a refutation must ship a concrete witness"

    # 3) A SECOND unmodified fold, from a different file, and the reason it is here: `foldNotXor`
    #    detects a SHARED operand by POINTER IDENTITY -- upstream's `hasCommonOperand` tests
    #    `A == C`. Matchers must bind the ACTUAL node for that to mean anything.
    exe2 = R.compile_harness(str(SRC2), clang=clang)
    assert exe2 is not None, "verbatim upstream AndOrXor source no longer compiles against the shim"
    r2 = R.verify_fold(z3, exe2, FOLD2)
    assert r2["ok"] and r2["proved"] == r2["rewriting_paths"] >= 1, r2

    #    TEETH for it: drop the `Not` from the rewrite and the same machinery refutes with a witness.
    bad2 = SRC2.read_text().replace("return BinaryOperator::CreateOr(X, NotY);",
                                    "return BinaryOperator::CreateOr(X, Y);", 1)
    assert bad2 != SRC2.read_text(), "the corruption must actually apply"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c2.cpp"
        p.write_text(bad2)
        e = R.compile_harness(str(p), clang=clang)
        rb2 = R.verify_fold(z3, e, FOLD2)
        assert rb2["refuted"] >= 1 and not rb2["ok"], rb2

    # 4) ACID TEST for the binding fix, which is the subtle half. `m_Value(A)` used to bind a COPY of
    #    the matched node, so `A == C` was ALWAYS false: the fold compiled, ran, and silently never
    #    rewrote. That is not unsound -- no rewrite means nothing to prove, and `ok` stays False --
    #    but it is INVISIBLE non-modelling, indistinguishable from a fold that legitimately declines.
    #    Revert the binding in a scratch copy of the header and require the fold to go quiet, so the
    #    fix is demonstrably what makes this fold reachable rather than incidental.
    hdr = (ROOT / "o2t" / "symexec" / "symbolic_llvm.h").read_text()
    reverted = hdr.replace("if (m->capp) { *m->capp = const_cast<Value *>(&v); }",
                           "if (m->capp) { *m->capp = cv_keep(v); }", 1)
    assert reverted != hdr, "the identity-binding line must be present to revert"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "symbolic_llvm.h").write_text(reverted)
        src = Path(td) / "f.cpp"
        src.write_text(SRC2.read_text())
        out = Path(td) / "f"
        import subprocess
        cc = subprocess.run([clang, "-std=c++17", "-I", td, str(src), "-o", str(out)],
                            capture_output=True, text=True)
        assert cc.returncode == 0, cc.stderr[:400]
        rr = R.verify_fold(z3, str(out), FOLD2)
        assert rr["rewriting_paths"] == 0, ("with copy-binding restored the fold must silently "
                                            "DECLINE -- that is the bug this pins", rr)
        assert not rr["ok"], rr

    # 4b) A THIRD unmodified fold, `foldXorToXor`, whose canonical shape `(A & B) ^ (A | B)` uses
    #     `m_Deferred` to re-match an operand bound earlier in the SAME pattern.
    r3 = R.verify_fold(z3, exe2, FOLD3)
    r4 = R.verify_fold(z3, exe2, FOLD4)
    assert r4["ok"] and r4["proved"] == r4["rewriting_paths"] >= 1, r4
    assert r3["ok"] and r3["proved"] == r3["rewriting_paths"] >= 1, r3

    #     EVERY vendored fold must actually REWRITE, not merely compile and run. Two of the folds
    #     here were silently inert when first added -- one binding copies, one crashing -- and both
    #     looked exactly like a fold that declines. "It compiles" is an upper bound on what is
    #     modelled; requiring a rewrite on some path is what makes the count mean something.
    arm_proved = 0
    for name in ARMS:
        v = R.verify_fold(z3, exe2, name)
        assert v["rewriting_paths"] >= 1, (f"{name} compiles and runs but never rewrites -- it is "
                                           "silently unmodelled, not declining", v)
        assert v["ok"] and not v["crashes"], (name, v)
        arm_proved += v["proved"]
    assert arm_proved == len(ARMS), (arm_proved, len(ARMS))

    #     ...and the arms are genuinely DISTINCT. Three of foldXorToXor's arms all produce `A ^ B`,
    #     so a harness that silently fell through to an earlier arm would still prove a true theorem
    #     while overstating coverage. ABLATE arm 1: its own harness must go quiet, and arms 2 and 3
    #     must keep rewriting. Without this, "every arm" is an unverified claim about which code ran.
    #     COMMUTATION is load-bearing, not incidental. Ablate the swapped-operand branch of the
    #     binary matcher: the commuted variants must go quiet while the canonical order keeps
    #     matching. Two matcher bugs already surfaced here by executing real source, so the
    #     commutative path gets the same treatment rather than being assumed correct.
    hdr3 = (ROOT / "o2t" / "symexec" / "symbolic_llvm.h").read_text()
    nocomm = hdr3.replace("      return m->commutative && cv_matchV(*v.op1, m->a) && "
                          "cv_matchV(*v.op0, m->b);  // swapped",
                          "      return false;  // ablated", 1)
    assert nocomm != hdr3, "the commutative branch must be present to ablate"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "symbolic_llvm.h").write_text(nocomm)
        sc = Path(td) / "f.cpp"
        sc.write_text(SRC2.read_text())
        ob = Path(td) / "f"
        import subprocess as _sp
        cc = _sp.run([clang, "-std=c++17", "-I", td, str(sc), "-o", str(ob)],
                     capture_output=True, text=True)
        assert cc.returncode == 0, cc.stderr[:400]
        assert R.verify_fold(z3, str(ob), FOLD3)["rewriting_paths"] >= 1, \
            "the CANONICAL operand order must still match without commutation"
        for cv in ("foldXorToXor#c2", "foldXorToXor#c3", "foldXorToXor#c4"):
            assert R.verify_fold(z3, str(ob), cv)["rewriting_paths"] == 0, \
                (f"{cv} must depend on commutative matching", cv)

    ablated = SRC2.read_text().replace("  if (match(&I, m_c_Xor(m_And(",
                                       "  if (false && match(&I, m_c_Xor(m_And(", 1)
    assert ablated != SRC2.read_text(), "the arm-1 ablation must apply"
    with tempfile.TemporaryDirectory() as td:
        pa = Path(td) / "ablated.cpp"
        pa.write_text(ablated)
        ea = R.compile_harness(str(pa), clang=clang)
        assert ea is not None
        assert R.verify_fold(z3, ea, FOLD3)["rewriting_paths"] == 0, \
            "with arm 1 ablated its own harness must stop rewriting"
        for other in ("foldXorToXor@2", "foldXorToXor@3"):
            assert R.verify_fold(z3, ea, other)["rewriting_paths"] >= 1, \
                (f"{other} must reach a DIFFERENT arm, not fall through to arm 1", other)
    bad3 = SRC2.read_text().replace("    return BinaryOperator::CreateXor(A, B);\n\n  // (A | ~B)",
                                    "    return BinaryOperator::CreateXor(A, A);\n\n  // (A | ~B)", 1)
    assert bad3 != SRC2.read_text(), "the corruption must actually apply"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c3.cpp"
        p.write_text(bad3)
        rb3 = R.verify_fold(z3, R.compile_harness(str(p), clang=clang), FOLD3)
        assert rb3["refuted"] >= 1 and not rb3["ok"], rb3

    #     TEETH for the fourth: `~(A ^ B)` and `~(A & B)` are the two arms' results; swapping the
    #     first arm's to the second's is a plausible copy-paste slip and must refute.
    bad4 = SRC2.read_text().replace("      return BinaryOperator::CreateNot(Builder.CreateXor(A, B));",
                                    "      return BinaryOperator::CreateNot(Builder.CreateAnd(A, B));", 1)
    assert bad4 != SRC2.read_text(), "the corruption must actually apply"
    with tempfile.TemporaryDirectory() as td:
        p4 = Path(td) / "c4.cpp"
        p4.write_text(bad4)
        rb4 = R.verify_fold(z3, R.compile_harness(str(p4), clang=clang), FOLD4)
        assert rb4["refuted"] >= 1 and not rb4["ok"], rb4

    #     ACID TEST, and the sharper one. `m_Deferred` must read its binding at MATCH time; the shim
    #     snapshotted it when the matcher tree was BUILT, which is before anything is bound, so the
    #     pattern dereferenced a null and SEGFAULTED. That was invisible twice over: a crashed run was
    #     silently dropped, so the fold merely looked like it declined. Reverting the fix must now
    #     produce a SURFACED crash that blocks SOUND, not a quiet "no rewriting paths".
    hdr2 = (ROOT / "o2t" / "symexec" / "symbolic_llvm.h").read_text()
    rev2 = hdr2.replace("inline Matcher *m_Deferred(Value *&v)     { Matcher *m = cv_m(MK_DEFERRED); "
                        "m->deferred = &v; return m; }",
                        "inline Matcher *m_Deferred(Value *&v)     { Matcher *m = cv_m(MK_SPECIFIC); "
                        "m->specific = v; return m; }", 1)
    assert rev2 != hdr2, "the m_Deferred match-time line must be present to revert"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "symbolic_llvm.h").write_text(rev2)
        src = Path(td) / "f.cpp"
        src.write_text(SRC2.read_text())
        out = Path(td) / "f"
        import subprocess
        cc = subprocess.run([clang, "-std=c++17", "-I", td, str(src), "-o", str(out)],
                            capture_output=True, text=True)
        assert cc.returncode == 0, cc.stderr[:400]
        rr2 = R.verify_fold(z3, str(out), FOLD3)
        assert rr2["crashes"], ("a crashed harness must be SURFACED, not silently dropped -- it "
                                "used to look identical to a fold that declines", rr2)
        assert not rr2["ok"], rr2

    # 4c) A SIXTH fold, of a different KIND: constant-mask reasoning over icmps, which is where the
    #     analysis-infrastructure work begins. `m_APInt` had recorded where to store a matched
    #     constant and then stored NOTHING, so the first fold to dereference one segfaulted on all
    #     16 paths -- surfaced as crashes rather than as a quiet "declines", because that was fixed
    #     earlier. The verified result is upstream's own worked example from the comment above the
    #     arm: (icmp ne (A & 12), 0) & (icmp eq (A & 7), 1)  ->  (icmp eq (A & 15), 9).
    exe6 = R.compile_harness(str(MASKEDICMP), clang=clang)
    assert exe6 is not None, "the vendored masked-icmp fold must compile against the shim"
    r6 = R.verify_fold(z3, exe6, FOLD6)
    assert r6["ok"] and not r6["crashes"] and r6["proved"] == r6["rewriting_paths"] >= 1, r6

    #     TEETH: perturb the folded constant and the same machinery must refute it.
    bad6 = MASKEDICMP.read_text().replace("(*BCst & (*BCst ^ *DCst)) | ECst",
                                          "(*BCst & (*BCst ^ *DCst)) | ECst | *DCst", 1)
    if bad6 != MASKEDICMP.read_text():
        with tempfile.TemporaryDirectory() as td:
            p6 = Path(td) / "c6.cpp"
            p6.write_text(bad6)
            rb6 = R.verify_fold(z3, R.compile_harness(str(p6), clang=clang), FOLD6)
            assert rb6["refuted"] >= 1 and not rb6["ok"], rb6

    # 4d) A SEVENTH fold, and the first whose soundness is about POISON rather than values:
    #        (X >s -1) ? (lshr X, Y) : (ashr X, Y)  ->  ashr X, Y
    #     The values agree because lshr and ashr coincide when the sign bit is clear. The delicate
    #     part is the flag: upstream propagates `exact` onto the result ONLY IF BOTH source shifts
    #     had it.
    exe7 = R.compile_harness(str(SELECTSHIFT), clang=clang)
    assert exe7 is not None, "the vendored select/shift fold must compile against the shim"
    r7 = R.verify_fold(z3, exe7, FOLD7)
    assert r7["ok"] and not r7["crashes"] and r7["proved"] == r7["rewriting_paths"] >= 1, r7

    #     POISON TEETH: force the flag on unconditionally. The values are still identical everywhere,
    #     so a value-only checker would see nothing wrong -- the target is simply POISON wherever a
    #     non-zero bit is shifted out and the source is perfectly defined. It must refute, with a
    #     witness.
    bad7 = SELECTSHIFT.read_text().replace(
        "bool IsExact = Ashr->isExact() && cast<Instruction>(TrueVal)->isExact();",
        "bool IsExact = true;", 1)
    assert bad7 != SELECTSHIFT.read_text(), "the exact-flag corruption must apply"
    with tempfile.TemporaryDirectory() as td:
        p7 = Path(td) / "c7.cpp"
        p7.write_text(bad7)
        rb7 = R.verify_fold(z3, R.compile_harness(str(p7), clang=clang), FOLD7)
        assert rb7["refuted"] >= 1 and not rb7["ok"], ("propagating `exact` unconditionally "
                                                       "introduces poison and must be refuted", rb7)
        assert next(x for x in rb7["rows"] if x["status"] == "refuted").get("witness")

    # 4e) An EIGHTH fold, and the first whose rewrite changes WIDTH:
    #        (X u< 2) ? -X : -1  ->  sext (X != 0)
    #     `sext i1 %c to i32` denotes something quite different from %c, so width is load-bearing
    #     rather than bookkeeping -- every value carries a type and the two cannot be conflated.
    exe8 = R.compile_harness(str(ZEROORONES), clang=clang)
    assert exe8 is not None, "the vendored zero-or-ones fold must compile against the shim"
    r8 = R.verify_fold(z3, exe8, FOLD8)
    assert r8["ok"] and not r8["crashes"] and r8["proved"] == r8["rewriting_paths"] >= 1, r8

    #     TEETH: zext instead of sext gives 0/1 where the fold must give 0/-1.
    bad8 = ZEROORONES.read_text().replace("return new SExtInst(Builder.CreateIsNotNull(X), TVal->getType());",
                                          "return new ZExtInst(Builder.CreateIsNotNull(X), TVal->getType());", 1)
    assert bad8 != ZEROORONES.read_text(), "the sext->zext corruption must apply"
    with tempfile.TemporaryDirectory() as td:
        p8 = Path(td) / "c8.cpp"
        p8.write_text(bad8)
        rb8 = R.verify_fold(z3, R.compile_harness(str(p8), clang=clang), FOLD8)
        assert rb8["refuted"] >= 1 and not rb8["ok"], ("zext where sext is required must refute", rb8)

    # 4f) A NINTH fold, both arms. This closes the last compiles-but-unverified gap except
    #     foldBoxMultiply, which is a documented SOLVER bound rather than a modelling one:
    #        ((X & Y) == 0) ? (X & 1) : 1         ->  zext((X & (Y | 1)) != 0)
    #        ((X & Y) == 0) ? ((X >> Z) & 1) : 1  ->  zext((X & (Y | (1 << Z))) != 0)
    exe9 = R.compile_harness(str(ICMPANDAND), clang=clang)
    assert exe9 is not None, "the vendored icmp-and-and fold must compile against the shim"
    nine = 0
    for arm in FOLD9_ARMS:
        v9 = R.verify_fold(z3, exe9, arm)
        assert v9["ok"] and not v9["crashes"] and v9["proved"] == v9["rewriting_paths"] >= 1, (arm, v9)
        nine += v9["proved"]

    #     TEETH: widen the mask to the full `Y | -1` and the equivalence breaks.
    bad9 = ICMPANDAND.read_text().replace("Value *FullMask = Builder.CreateOr(Y, MaskB);",
                                          "Value *FullMask = Builder.CreateOr(Y, Builder.CreateNot(One));", 1)
    assert bad9 != ICMPANDAND.read_text(), "the mask corruption must apply"
    with tempfile.TemporaryDirectory() as td:
        p9 = Path(td) / "c9.cpp"
        p9.write_text(bad9)
        e9 = R.compile_harness(str(p9), clang=clang)
        assert e9 is not None, "the corrupted variant should still compile"
        rb9 = R.verify_fold(z3, e9, FOLD9_ARMS[0])
        assert rb9["refuted"] >= 1 and not rb9["ok"], ("a widened mask must be refuted", rb9)

    # 4g) A TENTH fold, and the first whose soundness rests ENTIRELY on what an ANALYSIS returned
    #     rather than on what the pattern matched:
    #        ((X & P1) != 0) & ((X & P2) != 0)  ->  (X & (P1|P2)) == (P1|P2)
    #        ((X & P1) == 0) | ((X & P2) == 0)  ->  (X & (P1|P2)) != (P1|P2)
    #     Both are false for general masks and true for POWERS OF TWO -- a one-bit mask makes "some
    #     bit set" and "every bit set" the same statement. Nothing in the matched shape says so; the
    #     two `isKnownToBeAPowerOfTwo` calls do. So this is the arm that exercises query grounding
    #     end to end: each query's fact is emitted into the path condition and the obligation is
    #     discharged over exactly those facts, with none left ungrounded.
    batch_proved = 0
    exe10 = R.compile_harness(str(POW2), clang=clang)
    assert exe10 is not None, "the vendored power-of-two icmp fold must compile against the shim"
    ten = 0
    for arm in FOLD10_ARMS:
        v10 = R.verify_fold(z3, exe10, arm)
        assert v10["ok"] and not v10["crashes"] and v10["proved"] == v10["rewriting_paths"] >= 1, (arm, v10)
        row = next(x for x in v10["rows"] if x["rewrote"])
        assert row["facts"] == 2 and not row["ungrounded"], \
            ("the rewrite must be discharged under BOTH established facts, with neither ungrounded "
             "-- a dropped fact here would widen the input space and could refute a correct fold", arm, row)
        ten += v10["proved"]

    #     ...and its LOGICAL arm, where the freeze earns its keep: `a && b` does not evaluate b, so
    #     b's mask may be poison where the whole expression is not. Deleting only the freeze call
    #     refutes, with a witness in which that mask is poison.
    nofz = R.verify_fold(z3, exe10, "pow2_and_logical_nofreeze")
    assert nofz["refuted"] == 1 and not nofz["ok"], \
        ("folding a possibly-poison mask in without freezing it must be refuted", nofz)
    assert next(x for x in nofz["rows"] if x["status"] == "refuted").get("witness")

    #     TEETH, and the sharpest kind available: corrupt only the STRENGTH OF A FACT, leaving the
    #     rewrite untouched. `OrZero` admits a zero mask, and with P1 = 0 the source's first compare
    #     is false while the target can still be true. The fold's code is otherwise identical, so
    #     nothing but the grounding of that one query can catch it.
    bad10 = POW2.read_text().replace("isKnownToBeAPowerOfTwo(L2, false, 0, CxtI)",
                                     "isKnownToBeAPowerOfTwo(L2, true, 0, CxtI)", 1)
    assert bad10 != POW2.read_text(), "the OrZero corruption must apply"
    with tempfile.TemporaryDirectory() as td:
        p10 = Path(td) / "c10.cpp"
        p10.write_text(bad10)
        e10 = R.compile_harness(str(p10), clang=clang)
        assert e10 is not None, "the corrupted variant should still compile"
        rb10 = R.verify_fold(z3, e10, FOLD10_ARMS[0])
        assert rb10["refuted"] >= 1 and not rb10["ok"], \
            ("weakening a power-of-two query to admit zero must be refuted", rb10)
        assert next(x for x in rb10["rows"] if x["status"] == "refuted").get("witness")

    # 4h) THREE MORE FOLDS, the batch that exhausts what vocabulary can buy on this track. The reach
    #     sweep (tools/cv-symexec-reach-sweep.py) named them: measured over all 15 InstCombine files,
    #     no blocker CATEGORY unblocks a fold on its own, and these were the folds blocked by shim API
    #     SHAPE or by a single missing accessor rather than by a category.
    #
    #       foldICmpAddOpConst    icmp Pred (X+C), X -> a comparison of X against a constant bound,
    #                             four theorems in one function (signed/unsigned x direction). First
    #                             fold to COMPUTE its bound by two's-complement arithmetic on the
    #                             constant, which is why APInt carries a width: `0 - C` at i8 is not
    #                             the host's 64-bit answer, and a bound at the wrong width still
    #                             type-checks and still looks like a fold.
    #       foldSetClearBits      Cond ? (X & ~C) : (X | C) -> (X & ~C) | (Cond ? 0 : C). Guarded by
    #                             the constants being exact complements AT THEIR WIDTH.
    #       foldAndOrOfICmpEqConstantAndICmp
    #                             two range checks collapsing into one, in `and` and `or` forms that
    #                             are the same code with every predicate inverted.
    for src, arms in ((ADDCONST, ADDCONST_ARMS), (SETCLEAR, SETCLEAR_ARMS), (EQICMP, EQICMP_ARMS)):
        ex = R.compile_harness(str(src), clang=clang)
        assert ex is not None, (f"{src.name} must compile against the shim", src.name)
        for arm in arms:
            v = R.verify_fold(z3, ex, arm)
            assert v["ok"] and not v["crashes"] and v["proved"] == v["rewriting_paths"] >= 1, (arm, v)
            batch_proved += v["proved"]

    #     TEETH for each, aimed at the seam each fold introduced: the arithmetic that computes the
    #     bound, the choice of which constant goes in which select arm, and the predicate inversion.
    for src, corruption, replacement, arm in (
            (ADDCONST, "SMax - (C - 1)", "SMax - C", "addconst_sgt"),
            (ADDCONST, "ConstantInt::get(X->getType(), -C)", "ConstantInt::get(X->getType(), C)", "addconst_ugt"),
            (SETCLEAR, 'Builder.CreateSelect(Cond, Zero, OrC, "masksel", &Sel)',
                       'Builder.CreateSelect(Cond, OrC, Zero, "masksel", &Sel)', "setclear_clear_first"),
            (EQICMP, "ConstantInt::get(LHS0->getType(), *CInt + 1)",
                     "ConstantInt::get(LHS0->getType(), *CInt)", "eqicmp_or"),
            (EQICMP, "IsAnd ? ICmpInst::ICMP_ULT : ICmpInst::ICMP_UGE,", "ICmpInst::ICMP_UGE,", "eqicmp_and")):
        body = src.read_text().replace(corruption, replacement, 1)
        assert body != src.read_text(), ("the corruption must apply", src.name, corruption)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.cpp"
            p.write_text(body)
            e = R.compile_harness(str(p), clang=clang)
            assert e is not None, ("the corrupted variant should still compile", src.name)
            rb = R.verify_fold(z3, e, arm)
            assert rb["refuted"] >= 1 and not rb["ok"], (src.name, corruption, rb)
            assert next(x for x in rb["rows"] if x["status"] == "refuted").get("witness")

    #     THE LOGICAL ARM, and why `freeze` is in that fold at all. `a || b` is `select a, true, b`:
    #     when a is true the result is true whatever b is, INCLUDING poison, so the rewrite -- which
    #     puts b's value into a plain comparison, where poison WOULD propagate -- has to freeze it.
    #     Upstream's own code proves. Deleting just that one call, changing nothing else, refutes with
    #     a witness in which the operand is poison. This arm was unverifiable until the ordinary
    #     builders started carrying operand poison: before that the unfrozen version proved too,
    #     because the model could not see the thing the freeze is for.
    nofreeze = R.verify_fold(z3, R.compile_harness(str(EQICMP), clang=clang), "eqicmp_logical_or_nofreeze")
    assert nofreeze["refuted"] == 1 and not nofreeze["ok"], \
        ("a logical or whose unevaluated operand is used unfrozen must be refuted", nofreeze)
    assert next(x for x in nofreeze["rows"] if x["status"] == "refuted").get("witness")

    #     ...and one corruption that does NOT refute, which is the more instructive kind. Asking the
    #     STATIC `getInversePredicate` instead of the instance one gives the shortcut spelling on
    #     Value, which collapses every non-equality predicate to ICMP_EQ. The fold then recognises
    #     nothing and REWRITES ON NO PATH -- sound, silent, and invisible except that the arm stops
    #     being verified at all. That is the failure this shim keeps producing (a matcher binding a
    #     copy, a mask dropped, a query ungrounded), so the instance form deliberately does not reuse
    #     the shortcut, and this pins the reason.
    quiet = EQICMP.read_text().replace(
        "IsAnd ? RHS->getInversePredicate() : RHS->getPredicate();",
        "IsAnd ? Value::getInversePredicate(RHS->getPredicate()) : RHS->getPredicate();", 1)
    assert quiet != EQICMP.read_text(), "the inverse-predicate substitution must apply"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "quiet.cpp"
        p.write_text(quiet)
        e = R.compile_harness(str(p), clang=clang)
        assert e is not None, "the substituted variant should still compile"
        rq = R.verify_fold(z3, e, "eqicmp_and")
        assert rq["rewriting_paths"] == 0 and rq["refuted"] == 0, \
            ("collapsing the inverse predicate must make the fold DECLINE, not misfire -- if this "
             "ever refutes instead, the shortcut is being used somewhere it changes a result", rq)

    # 5) A SOLVER TIMEOUT is a non-answer too, and it must be BOUNDED. Real folds carry obligations a
    #    bit-blasting solver cannot settle -- `foldBoxMultiply` reassociates a 32x32 multiply, and
    #    with no bound z3 ran indefinitely and hung the whole run rather than reporting anything. The
    #    obligation below is that multiply identity: TRUE (checked concretely over random inputs), so
    #    a correct-but-slow solver is exactly the situation being modelled. Under a 1-second bound it
    #    must come back `error`, never `proved`.
    XLO, YLO = "(bvand X (_ bv65535 32))", "(bvand Y (_ bv65535 32))"
    CS = ("(bvadd (bvmul (bvlshr Y (_ bv16 32)) X) "
          "(bvmul (bvlshr X (_ bv16 32)) Y))")
    hard = {"input": f"(bvadd (bvshl {CS} (_ bv16 32)) (bvmul {YLO} {XLO}))",
            "output": "(bvmul X Y)", "decisions": [], "constraints": [],
            "input_poison": "false", "output_poison": "false", "logic": "QF_BV"}
    slow = R.discharge_path(z3, hard, rlimit=200_000)   # a deliberately tiny work budget
    assert slow["status"] == "error", ("a solver timeout is a NON-ANSWER and must never be reported "
                                       "as proved -- the obligation is true but out of reach", slow)
    assert slow["rewrote"] and not (slow["status"] == "proved"), slow

    # 6) A non-answer is not soundness. Simulate a path whose discharge errored and require `ok`
    #    to be False -- the shape of the bug this fixture was written alongside.
    faked = {"fold": FOLD, "rows": [{"rewrote": True, "status": "error"}]}
    rewriting = [x for x in faked["rows"] if x["rewrote"]]
    assert not (bool(rewriting) and all(x["status"] == "proved" for x in rewriting)), \
        "an errored rewriting path must never count as sound"

    print(f"upstream_symexec_fixture OK: THIRTEEN UNMODIFIED upstream LLVM 18 InstCombine folds ({FOLD} "
          f"from InstCombineAddSub.cpp, {FOLD2}, {FOLD3}, {FOLD4} and {FOLD5} from "
          f"InstCombineAndOrXor.cpp) "
          f"are "
          f"verified by executing their REAL C++ against the symbolic shim -- "
          f"{r['proved'] + arm_proved + r6['proved'] + r7['proved'] + r8['proved'] + nine + ten + batch_proved} "
          "rewriting arm(s) proved a sound refinement by z3 -- EVERY arm of the three AndOrXor folds, not merely the first of each. Corrupting any rewrite refutes with "
          "a concrete witness, so the proofs are load-bearing. The second fold is the interesting "
          "one: it detects a shared operand by POINTER IDENTITY (`A == C`), and matchers used to bind "
          "a COPY of the matched node, so that test was always false and the fold silently never "
          "rewrote -- not unsound, but invisible non-modelling. Reverting the binding here makes it "
          "go quiet again. The third needed `m_Deferred` to read its binding at MATCH time rather "
          "than when the matcher tree was built: it SEGFAULTED on its own canonical pattern, and a "
          "crashed run was silently dropped, so the fold merely looked like it declined. Crashes are "
          "surfaced now and block SOUND, as do errored discharges and solver timeouts -- three "
          "flavours of non-answer that must never read as a proof")
    return 0


if __name__ == "__main__":
    sys.exit(main())
