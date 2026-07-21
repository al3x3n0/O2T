#!/usr/bin/env python3
"""SOURCE-FILE mode: the Clang-AST front-end recovers folds from source parsed against REAL headers.

The stub-mode front-end (clang_tree_fixture) proved the parser-free principle but reached 0 verbatim
upstream folds -- it parses against a minimal API stub. This gates the real thing: fold bodies
(verbatim upstream, in tests/fixtures/vendor_folds/) are compiled against the ACTUAL LLVM 18 public
headers (PatternMatch.h / IRBuilder.h / Instructions.h), so clang produces the genuine AST -- no
stub, no approximation -- and the front-end reads its matcher/rewrite trees from it with O2T's regex
parser fully out of the loop. Each obligation is byte-identical to the regex path, proved/refuted
correctly. Skips unless clang 18 (with its LLVM headers) is present. Needs z3 + clang 18 headers.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.mine import clang_tree as ct  # noqa: E402
from o2t.intent import pass_graph as pg, corpus  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

_HOMEBREW_CLANG = "/opt/homebrew/opt/llvm@18/bin/clang"
VENDOR = Path(__file__).resolve().parent / "vendor_folds" / "instcombine_real_folds.cpp"
FOLDS = [("combineAddSubWithShlAddSub", "proved"),
         ("foldMulAddZeroOne", "proved"),
         ("foldSubWrong", "refuted")]


def main() -> int:
    z3 = shutil.which("z3")
    # Prefer a clang that ACTUALLY ships the LLVM headers: a PATH `clang` is often Apple clang (no
    # llvm/IR headers), so fall back to the homebrew llvm@18 clang when the PATH one can't parse the
    # verbatim folds -- otherwise the fixture skips spuriously on a machine that has the headers.
    clang = shutil.which("clang")
    if (clang is None or ct.llvm_include_dir(clang) is None) and Path(_HOMEBREW_CLANG).exists():
        clang = _HOMEBREW_CLANG
    inc = ct.llvm_include_dir(clang) if clang else None
    if z3 is None or clang is None or inc is None:
        print("clang_tree_source_fixture: needs z3 + clang 18 with LLVM headers, skipped")
        return 0

    body_text = VENDOR.read_text()
    bodies = {f["name"]: f["full"] for f in corpus.extract_functions(body_text)}
    proved = refuted = 0
    for name, expect in FOLDS:
        # 1. Recover from the REAL-headers AST -- the regex parser is NOT in this path, and the
        #    source is parsed against actual LLVM 18 headers (no stub).
        pair = ct.recover_from_source_file(str(VENDOR), name, [inc], clang_bin=clang)
        assert pair is not None, ("source-file mode must recover", name)

        # 2. Verdict is correct (teeth: the wrong sub-fold refutes via the real-AST path).
        status, cex = ma.prove(pair, z3)
        assert status == expect, (name, status, expect)
        if status == "refuted":
            assert cex, (name, "refutation needs a witness")
            refuted += 1
        else:
            proved += 1
            assert pg.reconcile(pair, z3)["agree"], (name, "reconcile must agree")

        # 3. CROSS-FRONT-END AGREEMENT on REAL source: the obligation from the real-headers AST is
        #    byte-identical to the regex path's reading of the same fold body. Two independent
        #    front-ends -- one via the C++ compiler's parser, one via O2T's regex -- agree.
        regex_pair = pg.recover_from_function(bodies[name])
        assert regex_pair is not None, name
        assert pair["before"] == regex_pair["before"], (name, "before diverged")
        assert pair["after"] == regex_pair["after"], (name, "after diverged")
        assert pair["variables"] == regex_pair["variables"], name

    # 4. CASCADE: a verbatim multi-arm fold (foldXorToXor, 3 arms) recovers every arm from the
    #    real-headers AST -- the assert/getOperand prelude is tolerated (scoped per-arm), and each
    #    arm is a real Boolean identity (A&B)^(A|B) -> A^B that proves. arm 0 is a pass-level claim;
    #    later arms are standalone (the pass_graph cascade caveat).
    arms = ct.recover_folds_from_source_file(str(VENDOR), "foldXorToXor", [inc], clang_bin=clang)
    assert len(arms) == 3, ("cascade must recover 3 arms", len(arms))
    assert [a["standalone"] for a in arms] == [False, True, True]
    assert all(ma.prove(a, z3)[0] == "proved" for a in arms), \
        [ma.prove(a, z3)[0] for a in arms]
    proved += 3

    # 5. TWO-ICMP CALLER CONTRACT (pass_graph phase-40 shape) from real source: verbatim upstream
    #    foldIsPowerOf2OrZero recovers BOTH arms parser-free -- two-primary composition under the
    #    IsAnd-selected connective, the `PredK == ICMP_*` guards, and `Cmp0->getOperand(0)` projection.
    #    The ONE datum clang's typed AST elides (the `m_Intrinsic<Intrinsic::ctpop>` id -- it prints
    #    only IntrinsicID_match) is read at the DeclRefExpr span the compiler itself pins, not by a
    #    structural parse. Each arm is a real ctpop theorem (ctpop(X)!=1 && X!=0 <-> ctpop(X)>1 and its
    #    or-dual), byte-identical to the regex path, reconcile-checked.
    pow2 = ct.recover_folds_from_source_file(str(VENDOR), "foldIsPowerOf2OrZero", [inc], clang_bin=clang)
    assert [(a["arm"], a["case"]["IsAnd"]) for a in pow2] == [(0, True), (1, False)], pow2
    two_regex = pg.recover_folds_from_function(bodies["foldIsPowerOf2OrZero"])
    for a, s in zip(pow2, two_regex):
        assert ma.prove(a, z3)[0] == "proved", (a["arm"], ma.prove(a, z3))
        assert pg.reconcile(a, z3)["agree"], ("two-icmp reconcile", a["arm"])
        for key in ("before", "after", "variables", "assumptions"):
            assert a[key] == s[key], ("two-icmp", key, "diverged from the regex path")
    proved += 2

    # 6. TEETH on the two-icmp path: a UGE-for-UGT rewrite (admits ctpop == 1) refutes with a witness
    #    from the REAL-headers AST -- the source-file path is not vacuously accepting. Mutate a temp
    #    copy of the vendored source; the -I include path is absolute, so it compiles from anywhere.
    import tempfile
    mut_src = VENDOR.read_text().replace("CreateICmpUGT", "CreateICmpUGE")
    with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False) as tf:
        tf.write(mut_src)
        mut_path = tf.name
    try:
        mut = ct.recover_folds_from_source_file(mut_path, "foldIsPowerOf2OrZero", [inc], clang_bin=clang)
        mst, mcex = ma.prove(mut[0], z3)
        assert mst == "refuted" and mcex, ("AST two-icmp UGE mutation must refute with a witness", mst)
    finally:
        Path(mut_path).unlink(missing_ok=True)
    refuted += 1

    # 7. simplifyXInst NAME CONTRACT (pass_graph phase 37) from real source: the fold NAME declares
    #    the instruction, so the front-end synthesizes the phantom `m_<Op>(m_Value(Op0), m_Value(Op1))`
    #    and splices each arm's `match(OpK, ...)` into slot K. Faithful free-function renderings
    #    (X-0->X, X^0->X, X^X->0), each byte-identical to the regex path -- SHAPE coverage, parser-free.
    #    (Not counted in verbatim reach: the X^X arm is matcher-form where upstream uses pointer-eq.)
    for fn, narms in (("simplifySubInst", 1), ("simplifyXorInst", 2)):
        sarms = ct.recover_folds_from_source_file(str(VENDOR), fn, [inc], clang_bin=clang)
        sreg = pg.recover_folds_from_function(bodies[fn])
        assert len(sarms) == narms, (fn, len(sarms))
        for a, s in zip(sarms, sreg):
            assert ma.prove(a, z3)[0] == "proved", (fn, a["arm"], ma.prove(a, z3))
            for key in ("before", "after", "variables", "assumptions"):
                assert a[key] == s[key], (fn, key, "diverged from the regex path")
        proved += narms

    # 8. ORIENTATION teeth (phase 37's soundness centerpiece): the name fixes Op0 as sub's minuend,
    #    so a swapped `0 - X -> X` reading refutes with a witness through the real-AST path -- the
    #    phantom synthesis is not commuting operands. Self-contained temp source (absolute -I).
    orient_swap = ('#include "llvm/IR/PatternMatch.h"\n#include "llvm/IR/Constants.h"\n'
                   "using namespace llvm;\nusing namespace llvm::PatternMatch;\n"
                   "static Value *simplifySubInst(Value *Op0, Value *Op1) {\n"
                   "  if (match(Op0, m_Zero()))\n    return Op1;\n  return nullptr;\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False) as tf:
        tf.write(orient_swap)
        swap_path = tf.name
    try:
        sw = ct.recover_folds_from_source_file(swap_path, "simplifySubInst", [inc], clang_bin=clang)
        sst, scex = ma.prove(sw[0], z3)
        assert sst == "refuted" and scex, ("0 - X -> X orientation must refute with a witness", sst)
    finally:
        Path(swap_path).unlink(missing_ok=True)
    refuted += 1

    print(f"clang_tree_source_fixture OK: {proved} proved + {refuted} refuted recovered from fold "
          "source parsed against the REAL LLVM 18 headers (no stub) -- including VERBATIM upstream "
          "combineAddSubWithShlAddSub, the foldXorToXor 3-arm cascade, and BOTH arms of the two-icmp "
          "contract foldIsPowerOf2OrZero (ctpop theorems, m_Intrinsic id read at the compiler-pinned "
          "span) -- plus the simplifyXInst NAME contract (phantom-instruction synthesis + operand "
          "splice: X-0->X, X^0->X, X^X->0), each obligation byte-identical to the regex path, the "
          "wrong fold / UGE mutation / swapped 0-X orientation all refuted with a witness. Verbatim "
          "reach: the regex parser is out of the loop on real compiler-parsed source, not a stub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
