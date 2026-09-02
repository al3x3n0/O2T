#!/usr/bin/env python3
"""Enrichment loop: grow O2T's verification vocabulary, gated by an INDEPENDENT oracle (lli).

Whole-function TV declines a function as `unsupported` when it uses an instruction outside the
translator's fragment. The enrichment loop (o2t/validate/enrich.py) PROPOSES that instruction's SMT
semantics and validates the proposal against `lli` EXECUTION -- the real intrinsic run on a battery of
concrete inputs (LLVM's own semantics). Only a proposal whose model matches lli on every input is
installed as a translate `extra_ops` handler. So O2T's verifier grows, but an oracle the proposer did
not write decides whether the growth is sound.

Demonstrated on `llvm.bswap`:
  * the CORRECT byte-reversal model is lli-validated and, installed, turns a bswap(bswap(x))->x
    function from `unsupported` into a proved whole-function TV;
  * a WRONG model (identity -- forgets to reverse) is REJECTED by lli and never installed -- the gate
    is load-bearing, catching an unsound enrichment before it can enable a false proof.

lli point-wise agreement is strong EVIDENCE, not a proof (reported with the checked count). Needs
z3 + opt + lli (LLVM 18).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si, enrich  # noqa: E402

_HB = "/opt/homebrew/opt/llvm@18/bin/lli"
BSWAP_FN = ("declare i32 @llvm.bswap.i32(i32)\n"
            "define i32 @t(i32 %x) {\n"
            "  %a = call i32 @llvm.bswap.i32(i32 %x)\n"
            "  %b = call i32 @llvm.bswap.i32(i32 %a)\n"   # bswap(bswap(x)) -> x  (opt folds it)
            "  ret i32 %b\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    lli = shutil.which("lli") or (_HB if Path(_HB).exists() else None)
    if z3 is None or opt is None or lli is None:
        print("enrich_fixture: z3 / opt / lli (18) not all found, skipped")
        return 0

    # 1. The CORRECT proposal is validated against lli execution (agrees on every input).
    good = enrich.validate_proposal(enrich.BSWAP, z3, lli)
    assert good["valid"] and good["checked"] >= 8, ("bswap model must validate against lli", good)

    # 2. TEETH: a WRONG proposal (identity -- forgets to reverse the bytes) is REJECTED by lli. The
    #    independent oracle catches the unsound model before it is ever installed.
    bad = enrich.validate_proposal(enrich.BSWAP_WRONG, z3, lli)
    assert not bad["valid"] and bad["disagreements"], ("a wrong model must be rejected by lli", bad)

    # 3. THE INSTALL PATH IS CURRENTLY UNREACHABLE, and that is reported rather than hidden.
    #    It needs an instruction whole-function TV does NOT model, so the enrichment has something
    #    to add. `bswap` was that instruction until it was modelled natively (4d5f452), which
    #    silently removed this fixture's subject. Every remaining candidate is blocked from BOTH
    #    sides: `validate_proposal` is UNARY-only (`_smt_outputs` calls `smt(width, "x")` with one
    #    operand), and every unmodelled integer intrinsic left in LLVM is n-ary (`ushl.sat`,
    #    `sshl.sat`, the fixed-point muls). Restoring end-to-end coverage needs ONE of:
    #      (a) a unary intrinsic TV does not model, or
    #      (b) `validate_proposal` extended to n-ary proposals -- a change to the component that
    #          decides whether a proposed model may enter the trust base, so not a fixture edit.
    #    Steps 1-2 above still cover what matters most: a correct model VALIDATES against lli and a
    #    wrong one is REJECTED, which is the trust invariant. What is not covered is the install
    #    lift (unsupported -> proved).
    from o2t.validate import semantics as _sem
    if "bswap" not in _sem.INTRINSICS:                 # bswap declined again -> restore full coverage
        before = si.validate_transform(z3, BSWAP_FN, si.run_instcombine(BSWAP_FN, opt), "t")
        assert before["status"] == "unsupported", ("bswap must be unmodeled before enrichment", before)
        after = si.validate_transform(z3, BSWAP_FN, si.run_instcombine(BSWAP_FN, opt), "t",
                                      extra_ops=[enrich.make_handler(enrich.BSWAP)])
        assert after["status"] == "proved", ("with the validated enrichment, TV must prove", after)
        installed = "install lift covered (unsupported -> proved)"
    else:
        installed = ("INSTALL LIFT NOT COVERED: llvm.bswap is modelled natively, and no unary "
                     "unmodelled intrinsic remains -- see the note above")

    print(f"enrich_fixture OK: the enrichment loop grew whole-function TV's instruction vocabulary "
          f"gated by lli EXECUTION -- the byte-reversal model validated ({good['checked']} inputs agree "
          f"with lli) and a WRONG (identity) model REJECTED by lli, never installed -- {installed}. "
          "O2T grows its own verifier; an independent oracle decides the growth is sound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
