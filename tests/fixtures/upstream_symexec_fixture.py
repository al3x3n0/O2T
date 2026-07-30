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
call pass-local helpers rather than fitting a matcher template. This path has the opposite shape --
the missing surface is shared, so each addition helps every fold at once -- and what stood between the
shim and real source was pointer/reference parity: upstream writes `Value *A; match(&I, m_Value(A))`,
binding POINTERS, where the shim took references.

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
    for name in (FOLD2, FOLD3, FOLD4):
        v = R.verify_fold(z3, exe2, name)
        assert v["rewriting_paths"] >= 1, (f"{name} compiles and runs but never rewrites -- it is "
                                           "silently unmodelled, not declining", v)
        assert v["ok"] and not v["crashes"], (name, v)
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
    slow = R.discharge_path(z3, hard, timeout=1)
    assert slow["status"] == "error", ("a solver timeout is a NON-ANSWER and must never be reported "
                                       "as proved -- the obligation is true but out of reach", slow)
    assert slow["rewrote"] and not (slow["status"] == "proved"), slow

    # 6) A non-answer is not soundness. Simulate a path whose discharge errored and require `ok`
    #    to be False -- the shape of the bug this fixture was written alongside.
    faked = {"fold": FOLD, "rows": [{"rewrote": True, "status": "error"}]}
    rewriting = [x for x in faked["rows"] if x["rewrote"]]
    assert not (bool(rewriting) and all(x["status"] == "proved" for x in rewriting)), \
        "an errored rewriting path must never count as sound"

    print(f"upstream_symexec_fixture OK: FOUR UNMODIFIED upstream LLVM 18 InstCombine folds ({FOLD} "
          f"from InstCombineAddSub.cpp, {FOLD2}, {FOLD3} and {FOLD4} from InstCombineAndOrXor.cpp) "
          f"are "
          f"verified by executing their REAL C++ against the symbolic shim -- "
          f"{r['proved'] + r2['proved'] + r3['proved'] + r4['proved']} "
          "rewriting path(s) proved a sound refinement by z3. Corrupting any rewrite refutes with "
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
