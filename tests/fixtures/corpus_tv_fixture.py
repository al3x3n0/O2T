#!/usr/bin/env python3
"""Whole-function translation validation over a real InstCombine corpus (Track B, end-to-end).

The per-fold observational check grounds ONE recovered fold against opt on minimal IR. This goes
end-to-end on REAL code: for every function in a corpus it runs the ACTUAL `opt -passes=instcombine`
and proves the WHOLE function's transformation sound (o2t/validate/corpus_tv.py -> scalar_ir's
Alive2-style refinement TV). It verifies the COMPOSITION of whatever folds fired, not an isolated
obligation -- directly attacking the "obligations, not passes" gap.

The gated corpus is 14 verbatim single-BB scalar functions from LLVM 18's own InstCombine tests
(and/or/xor/add.ll); each is transformed by real opt and each transform is proved sound. Teeth: a
hand-built WRONG optimization (`and X, 0 -> X`) is REFUTED with a witness, so a real miscompile would
not slip through. Anything scalar_ir cannot model would decline (unsupported), never mis-prove.
(Measured reach on the full files, not gated here: 93/207 whole-function transforms proved on
InstCombine/and.ll alone, 0 false refutations.) Needs z3 AND opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.corpus_tv import validate_file  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "vendor_folds" / "instcombine_scalar_tests.ll"


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("corpus_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. Every function in the real-test corpus: opt's WHOLE-function transform is proved sound.
    result = validate_file(z3, CORPUS.read_text(), opt)
    assert result["opt_ok"], "opt must run on the corpus"
    counts = result["counts"]
    assert counts.get("refuted", 0) == 0, ("no real transform may refute", result["functions"])
    assert counts.get("error", 0) == 0, ("no function may error", result["functions"])
    proved = counts.get("proved", 0)
    assert proved == len(result["functions"]) >= 14, ("all corpus transforms must prove", counts)

    # 2. TEETH: a hand-built WRONG "optimization" (and X, 0 -> X, which is unsound -- it is 0) is
    #    REFUTED with a witness by whole-function TV, so a miscompiling pass would be caught.
    src = "define i32 @t(i32 %A) {\n  %r = and i32 %A, 0\n  ret i32 %r\n}\n"
    bad_opt = "define i32 @t(i32 %A) {\n  ret i32 %A\n}\n"
    v = si.validate_transform(z3, src, bad_opt, "t")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong optimization must refute", v)

    # 3. ...and the CORRECT optimization of the same function proves (the teeth are not vacuous).
    good_opt = "define i32 @t(i32 %A) {\n  ret i32 0\n}\n"
    assert si.validate_transform(z3, src, good_opt, "t")["status"] == "proved"

    # 4. A FILE `opt` CANNOT PROCESS MUST NOT VANISH INTO THE DENOMINATOR. InstCombine does not
    #    always reach a fixpoint in one iteration, and plain `opt -passes=instcombine` then ABORTS
    #    the whole file with "did not reach a fixpoint" -- so every function in it produced no
    #    output and the sweep reported the file as empty, indistinguishable from a file with no
    #    work in it. LLVM's own `shift.ll` is exactly this case and answers it in its RUN line;
    #    `run_instcombine` now falls back the same way. This function is a minimal reproduction:
    #    This is the actual trigger, `ashr_out_of_range` (OSS-Fuzz #26135), copied from that file:
    #    ONE function out of 171 aborted `opt`, and with it the whole file left the corpus.
    nofix = """define void @ashr_out_of_range(ptr %A) {
  %L = load i177, ptr %A
  %B5 = udiv i177 %L, -1
  %B4 = add i177 %B5, -1
  %B2 = add i177 %B4, -1
  %G11 = getelementptr i177, ptr %A, i177 %B2
  %L7 = load i177, ptr %G11
  %B6 = mul i177 %B5, %B2
  %B24 = ashr i177 %L7, %B6
  %B36 = and i177 %L7, %B4
  %C17 = icmp sgt i177 %B36, %B24
  %G62 = getelementptr i177, ptr %G11, i1 %C17
  %B28 = urem i177 %B24, %B6
  store i177 %B28, ptr %G62
  ret void
}
"""
    assert si.run_passes(nofix, "instcombine", opt) is None, \
        ("this fixture's premise is that PLAIN instcombine aborts on this function; if that stops "
         "being true the assertion below proves nothing and needs a new trigger")
    assert si.run_instcombine(nofix, opt) is not None, \
        "run_instcombine must return IR, falling back to <no-verify-fixpoint> when opt aborts"
    #    ...and the fallback is the ONLY reason a genuinely non-fixpoint file is measurable: pass a
    #    file opt truly cannot handle and `validate_file` reports `opt_ok: False` with no functions,
    #    which the corpus CLI prints to stderr and leaves OUT of the aggregate rather than counting
    #    it as zero work.
    broken = validate_file(z3, "this is not LLVM IR at all\n", opt)
    assert broken["opt_ok"] is False and broken["functions"] == [] and broken["counts"] == {}, \
        ("a file opt cannot process must be flagged opt_ok=False, not reported as empty", broken)

    # 5. THE SOLVER BUDGET IS DETERMINISTIC, NOT WALL-CLOCK. A wall-clock timeout makes a verdict
    #    depend on what else the machine is doing, and that was measured, not feared: the
    #    `icmp.ll test_sdiv_pos_*` family took 2.5s in one run and over 15s in another on
    #    BYTE-IDENTICAL query text (same sha256), flipping between `proved` and `timeout` and moving
    #    the corpus total by seven functions. z3's `rlimit` counts SOLVER WORK instead, so the same
    #    query gets the same verdict on a busy machine as on an idle one -- which is also what makes
    #    it safe to run a sweep in parallel with anything else.
    assert "(set-option :rlimit 500)" in si.with_rlimit("(set-logic QF_BV)\n(check-sat)\n", 500), \
        "the budget must be injected after the logic line, where z3 accepts it"
    assert ":rlimit" not in si.with_rlimit("(set-logic QF_BV)\n(check-sat)\n", 0), \
        "a zero/None budget must leave the query untouched (pure wall-clock behaviour)"
    #    A budget too small to finish must yield NO VERDICT -- never a wrong one. `unknown` is
    #    reported as `timeout` because it is the same outcome callers already treat as a sound
    #    non-answer, and unlike a wall-clock timeout it happens at the same point on every machine.
    starved = si.validate_transform(z3, src, good_opt, "t", timeout=30, rlimit=1)
    assert starved["status"] == "timeout", \
        ("an exhausted deterministic budget must be a non-answer, not a verdict", starved)
    #    ...and the DEFAULT budget still decides the same pair, so the guard above is not simply
    #    disabling the validator.
    assert si.validate_transform(z3, src, good_opt, "t", timeout=30)["status"] == "proved", \
        "the default budget must still decide an ordinary function"
    #    The starving budget must not turn a REFUTATION into a proof either -- the direction that
    #    would matter most if `unknown` were ever mapped to `unsat`.
    st2 = si.validate_transform(z3, src, bad_opt, "t", timeout=30, rlimit=1)
    assert st2["status"] != "proved", \
        ("an exhausted budget must never report a proof", st2)

    # 6. PARALLELISM MUST NOT CHANGE A VERDICT, and that is the whole reason it waited for the
    #    deterministic budget. Functions are independent solver queries, so a sweep is embarrassingly
    #    parallel -- but under a WALL-CLOCK budget contention alone flipped `proved` into `timeout`
    #    (the `test_sdiv_pos_*` family moved the corpus total by seven functions between runs of
    #    byte-identical query text). Asserted on the real corpus file: same verdicts, every function.
    seq = validate_file(z3, CORPUS.read_text(), opt, jobs=1)
    par = validate_file(z3, CORPUS.read_text(), opt, jobs=8)
    sm = {f["function"]: f["status"] for f in seq["functions"]}
    pm = {f["function"]: f["status"] for f in par["functions"]}
    assert sm == pm, ("running functions in parallel must give the identical verdict for every one",
                      {k: (sm.get(k), pm.get(k)) for k in set(sm) | set(pm) if sm.get(k) != pm.get(k)})
    assert [f["function"] for f in seq["functions"]] == [f["function"] for f in par["functions"]], \
        "the parallel run must also preserve function ORDER, so reports stay comparable"

    # 7. `llvm.assume` ESTABLISHES ITS ARGUMENT -- a UB term, not an opaque effect. Treated as
    #    "something unknown" the fact is DROPPED, and a target simplified USING the assumption is
    #    refuted on exactly the inputs the assumption excluded (three false refutations in LLVM's
    #    own tests). The model is `(not c) or poison(c)`, and it must reach the function's UB even
    #    though a void call has no result to carry it.
    dec = "declare void @llvm.assume(i1)\n"
    asm = ("define i8 @a(i1 %c, i8 %x, i8 %y) {\n  call void @llvm.assume(i1 %c)\n"
           "  %s = select i1 %c, i8 %x, i8 %y\n  ret i8 %s\n}\n" + dec)
    asm_t = ("define i8 @a(i1 %c, i8 %x, i8 %y) {\n  call void @llvm.assume(i1 %c)\n"
             "  ret i8 %x\n}\n" + dec)
    assert si.validate_transform(z3, asm, asm_t, "a")["status"] == "proved", \
        "a target simplified USING the assumption must prove, not be refuted on excluded inputs"
    #    ...and WITHOUT the assume the identical simplification is a miscompile and must refute, so
    #    the assertion above turns on the assumption rather than on anything else in the pair.
    no_a = ("define i8 @a(i1 %c, i8 %x, i8 %y) {\n"
            "  %s = select i1 %c, i8 %x, i8 %y\n  ret i8 %s\n}\n")
    assert si.validate_transform(z3, no_a,
                                 "define i8 @a(i1 %c, i8 %x, i8 %y) {\n  ret i8 %x\n}\n",
                                 "a")["status"] == "refuted", \
        "without the assumption, returning %x unconditionally is a miscompile"

    # 8. `llvm.bswap` / `llvm.bitreverse` are PERMUTATIONS of bits -- no arithmetic, so exact.
    for intr, w in (("bswap", 32), ("bitreverse", 64)):
        d = f"declare i{w} @llvm.{intr}.i{w}(i{w})\n"
        p_src = (f"define i1 @p(i{w} %x, i{w} %y) {{\n"
                 f"  %a = call i{w} @llvm.{intr}.i{w}(i{w} %x)\n"
                 f"  %b = call i{w} @llvm.{intr}.i{w}(i{w} %y)\n"
                 f"  %c = icmp eq i{w} %a, %b\n  ret i1 %c\n}}\n" + d)
        p_tgt = f"define i1 @p(i{w} %x, i{w} %y) {{\n  %c = icmp eq i{w} %x, %y\n  ret i1 %c\n}}\n"
        assert si.validate_transform(z3, p_src, p_tgt, "p")["status"] == "proved", \
            f"{intr} is injective, so comparing its results is comparing its inputs"
    #    ...and it must be a real permutation, not the identity -- otherwise the injectivity
    #    assertions above would pass for a model that did nothing at all.
    idb = ("define i32 @q(i32 %x) {\n  %a = call i32 @llvm.bswap.i32(i32 %x)\n  ret i32 %a\n}\n"
           "declare i32 @llvm.bswap.i32(i32)\n")
    assert si.validate_transform(z3, idb, "define i32 @q(i32 %x) {\n  ret i32 %x\n}\n",
                                 "q")["status"] == "refuted", \
        "bswap must not be modelled as the identity"

    print(f"corpus_tv_fixture OK: whole-function translation validation proved {proved} real "
          "InstCombine test transforms sound END-TO-END (real IR -> real `opt -passes=instcombine` -> "
          "Alive2-style refinement proof over the WHOLE function, verifying the composition of whatever "
          "folds fired), 0 refuted; a hand-built wrong optimization (and X,0 -> X) is refuted with a "
          "witness while the correct one proves -- the miscompile teeth bite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
