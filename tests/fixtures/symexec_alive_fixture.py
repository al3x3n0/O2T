#!/usr/bin/env python3
"""The symexec track's proofs are checked by an EXTERNAL oracle, not only by themselves.

Every symexec proof is `output == input` where BOTH terms are built by the shim. That is circular: a
systematically wrong encoding -- a matcher binding the wrong operand, an opcode mapped to the wrong
SMT operator -- yields a wrong input AND a matching wrong output, and z3 proves them equal. Every
such proof looks perfect. The z3-vs-shim relationship cannot detect it, because z3 only ever sees
what the shim wrote.

So each proved arm is rendered back into LLVM IR and put to reference Alive2, which never sees the
shim and knows what LLVM's operators actually mean. Agreement on all arms means the shim's terms
denote the instructions it claims.

Gated here:
  * every proved arm is CONFIRMED by Alive2 (33 of them, across thirteen upstream folds);
  * a FACT-DEPENDENT arm is confirmed under the facts its own branches established,
    rendered as `llvm.assume` -- and REFUTED without them, so the facts are shown to be
    load-bearing in the oracle and not decoration;
  * NOTE the scope: this renders the VALUE terms, so it checks the value encoding. Poison-flag
    correctness (e.g. propagating `exact` only when both sources had it) is checked by z3 through
    the refinement obligation instead -- Alive2 sees identical values there and would agree either
    way;
  * TEETH -- a corrupted rewrite is REFUTED by Alive2, so the oracle can fail;
  * a NON-ANSWER is not agreement. `alive_refines` reports "skip" for a timeout, and rendering these
    without `noundef` makes Alive2 quantify over every use of a multiply-used argument -- which times
    out even on `(A&B)^(A|B) -> A^B`. Reading that as confirmation would have made this whole fixture
    a rubber stamp: it reported 13/13 "skip" on the first run. Both halves are pinned;
  * the renderer FAILS LOUD on a term it does not model, rather than emitting something plausible.

Needs clang++, z3 and alive-tv; self-skips without them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.symexec import real_pass as R  # noqa: E402
from o2t.symexec.smt_to_ir import UntranslatableTerm, render_pair  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402

VENDOR = ROOT / "tests" / "fixtures" / "vendor_folds"
ADDSUB = VENDOR / "upstream_addsub_fold.cpp"
ANDORXOR = VENDOR / "upstream_andorxor_fold.cpp"
MASKEDICMP = VENDOR / "upstream_maskedicmp_fold.cpp"
MASKED_ARM = "foldLogOpOfMaskedICmps_NotAllZeros_BMask_Mixed"
SELECTSHIFT = VENDOR / "upstream_select_lshrashr_fold.cpp"
SELECTSHIFT_ARM = "foldSelectICmpLshrAshr"
ZEROORONES = VENDOR / "upstream_select_zeroorones_fold.cpp"
ZEROORONES_ARM = "foldSelectZeroOrOnes"
ICMPANDAND = VENDOR / "upstream_select_icmpandand_fold.cpp"
ICMPANDAND_ARMS = ("foldSelectICmpAndAnd", "foldSelectICmpAndAnd@shift")
ADDCONST = VENDOR / "upstream_icmp_addconst_fold.cpp"
ADDCONST_ARMS = ("addconst_ult", "addconst_ule", "addconst_ugt", "addconst_slt", "addconst_sgt",
                 "addconst_slt_neg", "addconst_sgt_neg")
SETCLEAR = VENDOR / "upstream_setclearbits_fold.cpp"
SETCLEAR_ARMS = ("setclear_clear_first", "setclear_set_first")
EQICMP = VENDOR / "upstream_icmpeq_and_icmp_fold.cpp"
# NOT the logical arm: its target contains the value `freeze` chose, which is selected on a POISON
# FLAG -- a Bool with no counterpart in IR. The renderer refuses it rather than emitting something
# plausible, which is the right answer and the documented split: Alive2 checks the VALUE encoding
# here, and poison-flag correctness is z3's job through the refinement obligation.
EQICMP_ARMS = ("eqicmp_or", "eqicmp_and")
POW2 = VENDOR / "upstream_icmps_and_pow2_fold.cpp"
POW2_ARMS = ("pow2_and", "pow2_or")
ANDORXOR_ARMS = ("foldNotXor", "foldNotXor@2",
                 "foldXorToXor", "foldXorToXor@2", "foldXorToXor@3", "foldXorToXor@4",
                 "foldOrToXor", "foldOrToXor@2", "foldOrToXor@3",
                 "foldXorToXor#c2", "foldXorToXor#c3", "foldXorToXor#c4",
                 "foldAndToXor", "foldAndToXor@2")


def _clang():
    for cand in ("clang++", "/opt/homebrew/opt/llvm@18/bin/clang++", "/usr/bin/clang++"):
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


def _terms(exe, fold, choices=("0", "0", "0", "0")):
    """The input/output SMT terms of one concrete execution.

    `choices` selects the path: a fold whose rewrite is GUARDED by analysis queries does not rewrite
    at all on the all-false path, so the arms that need a fact must be run on the path that
    establishes it.
    """
    out = subprocess.run([exe, fold, *choices], capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def main() -> int:
    z3, clang, alive = shutil.which("z3"), _clang(), shutil.which("alive-tv")
    if z3 is None or clang is None or alive is None:
        print("symexec_alive_fixture: needs clang++, z3 and alive-tv, skipped")
        return 0

    exe1 = R.compile_harness(str(ADDSUB), clang=clang)
    exe2 = R.compile_harness(str(ANDORXOR), clang=clang)
    assert exe1 and exe2, "the vendored upstream folds must compile against the shim"
    exe3 = R.compile_harness(str(MASKEDICMP), clang=clang)
    assert exe3, "the vendored masked-icmp fold must compile against the shim"
    exe4 = R.compile_harness(str(SELECTSHIFT), clang=clang)
    assert exe4, "the vendored select/shift fold must compile against the shim"
    arms = ([(exe1, "combineAddSubWithShlAddSub")] + [(exe2, a) for a in ANDORXOR_ARMS] +
            [(exe3, MASKED_ARM), (exe4, SELECTSHIFT_ARM),
             (R.compile_harness(str(ZEROORONES), clang=clang), ZEROORONES_ARM)] +
            [(R.compile_harness(str(ICMPANDAND), clang=clang), a) for a in ICMPANDAND_ARMS] +
            [(R.compile_harness(str(ADDCONST), clang=clang), a) for a in ADDCONST_ARMS] +
            [(R.compile_harness(str(SETCLEAR), clang=clang), a) for a in SETCLEAR_ARMS] +
            [(R.compile_harness(str(EQICMP), clang=clang), a) for a in EQICMP_ARMS])

    # 1) EVERY proved arm is independently confirmed.
    confirmed = 0
    for exe, fold in arms:
        rec = _terms(exe, fold)
        assert rec and rec["output"], (f"{fold} must rewrite to produce a checkable pair", rec)
        src, tgt = render_pair(rec["input"], rec["output"])
        status = alive_refines(src, tgt, alive).get("status")
        assert status == "proved", (f"Alive2 does not confirm {fold} -- either the shim's encoding "
                                    "does not mean what it claims, or the oracle gave a non-answer",
                                    fold, status)
        confirmed += 1
    assert confirmed == len(arms), (confirmed, len(arms))

    # 1b) A FACT-DEPENDENT fold is confirmed WITH ITS FACTS, and only with them. Every arm above is
    #     valid unconditionally, so rendering its bare value terms asks Alive2 the same question z3
    #     was asked. `foldAndOrOfICmpsOfAndWithPow2` is the first that is NOT: its rewrite is false
    #     unless both masks are powers of two, a fact that comes from `isKnownToBeAPowerOfTwo` and
    #     not from the pattern. So the facts the path established travel into the IR as `llvm.assume`
    #     -- and the check has two sides, because an assumption that changed nothing would confirm
    #     nothing: WITH the facts Alive2 proves both arms, WITHOUT them it REFUTES them.
    exe5 = R.compile_harness(str(POW2), clang=clang)
    assert exe5, "the vendored power-of-two icmp fold must compile against the shim"
    for fold in POW2_ARMS:
        rec = _terms(exe5, fold, choices=("1", "1", "1", "1"))
        assert rec and rec["output"], (f"{fold} must rewrite on the path where both facts hold", rec)
        facts, ungrounded = R._path_condition(rec["decisions"])
        assert len(facts) == 2 and not ungrounded, (fold, facts, ungrounded)
        src, tgt = render_pair(rec["input"], rec["output"], assumptions=facts)
        assert alive_refines(src, tgt, alive).get("status") == "proved", \
            (f"Alive2 does not confirm {fold} under the facts its own branches established", fold)
        bare_src, bare_tgt = render_pair(rec["input"], rec["output"])
        assert alive_refines(bare_src, bare_tgt, alive).get("status") == "refuted", \
            (f"{fold} must be REFUTED without its facts -- if it holds unconditionally then the "
             "assumptions are decoration and this cross-check proves nothing about them", fold)
        confirmed += 1

    # 2) TEETH. The oracle must be able to FAIL: corrupt a rewrite and require a refutation.
    bad = ANDORXOR.read_text().replace("return BinaryOperator::CreateOr(X, NotY);",
                                       "return BinaryOperator::CreateOr(X, Y);", 1)
    assert bad != ANDORXOR.read_text(), "the corruption must apply"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.cpp"
        p.write_text(bad)
        rec = _terms(R.compile_harness(str(p), clang=clang), "foldNotXor")
        src, tgt = render_pair(rec["input"], rec["output"])
        assert alive_refines(src, tgt, alive).get("status") == "refuted", \
            "a corrupted rewrite must be REFUTED by Alive2, or this oracle proves nothing"

    # 3) A NON-ANSWER IS NOT AGREEMENT, and this is not hypothetical: rendered without `noundef`,
    #    Alive2 quantifies over every use of a multiply-used argument and TIMES OUT on
    #    `(A&B)^(A|B) -> A^B`, reporting "failed-to-prove" -> status "skip". The first run of this
    #    fixture returned 13/13 skip, which a `!= "refuted"` check would have called success.
    rec = _terms(exe2, "foldXorToXor")
    src, tgt = render_pair(rec["input"], rec["output"])
    plain_src, plain_tgt = src.replace("i32 noundef ", "i32 "), tgt.replace("i32 noundef ", "i32 ")
    assert alive_refines(plain_src, plain_tgt, alive).get("status") != "refuted", \
        "sanity: dropping noundef must not make Alive2 REFUTE a true transform"

    # 4) The renderer refuses terms it does not model rather than emitting something plausible.
    for junk in ("(bvcomedy X Y)", "(concat X Y)"):
        try:
            render_pair(junk, "X")
            raise AssertionError(f"the renderer must refuse {junk!r}, not guess at it")
        except UntranslatableTerm:
            pass

    print(f"symexec_alive_fixture OK: all {confirmed} proved symexec arms are independently "
          "CONFIRMED by reference Alive2, which never sees the shim. This breaks a circle the track "
          "could not otherwise escape -- the shim builds both the input and the output term, so a "
          "systematically wrong encoding would produce a wrong input AND a matching wrong output "
          "that z3 proves equal. A corrupted rewrite is refuted, so the oracle can fail; the "
          "renderer refuses terms it does not model rather than guessing; and a non-answer is not "
          "counted as agreement -- rendered without `noundef` Alive2 times out on a trivial boolean "
          "identity, and this fixture reported 13/13 'skip' before that was understood")
    return 0


if __name__ == "__main__":
    sys.exit(main())
