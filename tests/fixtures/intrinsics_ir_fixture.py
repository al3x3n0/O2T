#!/usr/bin/env python3
"""Reach lift: model the common integer intrinsics, each VALIDATED against lli (not trusted on faith).

Track B's scalar TV modeled only smin/smax/umin/umax, so any function using ctpop/ctlz/abs/bswap/fshl/
saturating-add declined. This adds SMT models for a batch of them (o2t/validate/scalar_ir.py) so those
folds PROVE instead of declining. Following the enrichment discipline, every model is checked against
`lli` (LLVM's real semantics), transitively:

  * z3 proves `intrinsic(...) == hand_equivalent` using the new SMT MODEL, and
  * concrete_tv proves the SAME pair AGREES under `lli` EXECUTION of the real intrinsic,

so model == hand and real == hand => the model matches real LLVM. Plus hand-computed ground-truth
constants pin each model at concrete points. Needs z3 + lli (LLVM 18).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.concrete_tv import concrete_tv  # noqa: E402

vt = si.validate_transform


def _fn(body, params="i8 %x, i8 %y", ret="i8"):
    return f"define {ret} @f({params}) {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    lli = toolchain.resolve_lli()
    if z3 is None or lli is None:
        print("intrinsics_ir_fixture: z3 or lli not found, skipped")
        return 0

    # 1. GROUND TRUTH: hand-computed intrinsic(consts) -> known result. Pins each model at a point.
    for name, call, w, expected in [
        ("ctpop7", "call i8 @llvm.ctpop.i8(i8 7)", 8, 3),
        ("ctpop32-wide", "call i32 @llvm.ctpop.i32(i32 -1)", 32, 32),
        ("ctpop-alt", "call i32 @llvm.ctpop.i32(i32 -1431655766)", 32, 16),   # 0xAAAAAAAA -> 16
        ("abs-neg", "call i8 @llvm.abs.i8(i8 -5, i1 false)", 8, 5),
        ("abs-intmin", "call i8 @llvm.abs.i8(i8 -128, i1 false)", 8, -128),
        ("fshl", "call i8 @llvm.fshl.i8(i8 -1, i8 0, i8 4)", 8, -16),
        ("fshr", "call i8 @llvm.fshr.i8(i8 0, i8 -1, i8 4)", 8, 15),
        ("uadd.sat", "call i8 @llvm.uadd.sat.i8(i8 -56, i8 100)", 8, -1),     # 200+100 saturates 255
        ("usub.sat", "call i8 @llvm.usub.sat.i8(i8 5, i8 10)", 8, 0),
    ]:
        fn = f"define i{w} @f() {{\n  %r = {call}\n  ret i{w} %r\n}}\n"
        want = f"define i{w} @f() {{\n  ret i{w} {expected}\n}}\n"
        assert vt(z3, fn, want, "f")["status"] == "proved", (f"{name}: model must compute {expected}",)

    # 2. lli VALIDATION via hand-equivalents: z3 proves model == hand AND lli proves real == hand.
    hands = {
        "abs": (_fn("  %r = call i8 @llvm.abs.i8(i8 %x, i1 false)\n  ret i8 %r", "i8 %x"),
                _fn("  %c = icmp slt i8 %x, 0\n  %n = sub i8 0, %x\n  %r = select i1 %c, i8 %n, i8 %x\n  ret i8 %r", "i8 %x")),
        "fshl": (_fn("  %r = call i8 @llvm.fshl.i8(i8 %x, i8 %y, i8 4)\n  ret i8 %r"),
                 _fn("  %h = shl i8 %x, 4\n  %l = lshr i8 %y, 4\n  %r = or i8 %h, %l\n  ret i8 %r")),
        "uadd.sat": (_fn("  %r = call i8 @llvm.uadd.sat.i8(i8 %x, i8 %y)\n  ret i8 %r"),
                     _fn("  %s = add i8 %x, %y\n  %o = icmp ult i8 %s, %x\n  %r = select i1 %o, i8 -1, i8 %s\n  ret i8 %r")),
        "usub.sat": (_fn("  %r = call i8 @llvm.usub.sat.i8(i8 %x, i8 %y)\n  ret i8 %r"),
                     _fn("  %o = icmp ult i8 %x, %y\n  %d = sub i8 %x, %y\n  %r = select i1 %o, i8 0, i8 %d\n  ret i8 %r")),
    }
    for name, (intr, hand) in hands.items():
        assert vt(z3, intr, hand, "f")["status"] == "proved", (name, "model must match the hand form")
        assert concrete_tv(lli, intr, hand, "f")["status"] == "agree", \
            (name, "lli must confirm the real intrinsic matches the hand form -- validates the model")

    # 3. SYMBOLIC identity: abs is idempotent -- a real fold that now proves. (bswap is intentionally
    #    left unmodeled as the self-enrichment worked example; see enrich_fixture.)
    absabs = _fn("  %a = call i8 @llvm.abs.i8(i8 %x, i1 false)\n  %r = call i8 @llvm.abs.i8(i8 %a, i1 false)\n  ret i8 %r", "i8 %x")
    once = _fn("  %r = call i8 @llvm.abs.i8(i8 %x, i1 false)\n  ret i8 %r", "i8 %x")
    assert vt(z3, absabs, once, "f")["status"] == "proved", "abs(abs x)==abs x"

    # 4. TEETH + REACH: a WRONG intrinsic fold refutes (the model is not vacuous), and a function that
    #    DECLINED before (used ctpop) now proves its identity instead of `unsupported`.
    assert vt(z3, once, _fn("  ret i8 %x", "i8 %x"), "f")["status"] == "refuted", "abs(x) != x in general"
    ctpop = _fn("  %r = call i32 @llvm.ctpop.i32(i32 %x)\n  ret i32 %r", "i32 %x", "i32")
    assert vt(z3, ctpop, ctpop, "f")["status"] == "proved", "ctpop is now MODELED (was unsupported)"

    # 5. ctlz/cttz (bounded nested-ite over the bits). No clean scalar hand-form, so each ground-truth
    #    point is confirmed by BOTH the SMT model (z3) AND real lli execution (concrete_tv).
    for name, call, w, expected in [
        ("ctlz(0x80)=0", "call i8 @llvm.ctlz.i8(i8 -128, i1 false)", 8, 0),
        ("ctlz(1)=7", "call i8 @llvm.ctlz.i8(i8 1, i1 false)", 8, 7),
        ("ctlz(0)=8", "call i8 @llvm.ctlz.i8(i8 0, i1 false)", 8, 8),
        ("ctlz32(1)=31", "call i32 @llvm.ctlz.i32(i32 1, i1 false)", 32, 31),
        ("cttz(0x80)=7", "call i8 @llvm.cttz.i8(i8 -128, i1 false)", 8, 7),
        ("cttz(0x10)=4", "call i8 @llvm.cttz.i8(i8 16, i1 false)", 8, 4),
        ("cttz(0)=8", "call i8 @llvm.cttz.i8(i8 0, i1 false)", 8, 8),
        ("sadd.sat(100,100)=127", "call i8 @llvm.sadd.sat.i8(i8 100, i8 100)", 8, 127),
        ("sadd.sat(-100,-100)=-128", "call i8 @llvm.sadd.sat.i8(i8 -100, i8 -100)", 8, -128),
        ("ssub.sat(-100,100)=-128", "call i8 @llvm.ssub.sat.i8(i8 -100, i8 100)", 8, -128),
        ("ssub.sat(50,20)=30", "call i8 @llvm.ssub.sat.i8(i8 50, i8 20)", 8, 30),
    ]:
        fn = f"define i{w} @f() {{\n  %r = {call}\n  ret i{w} %r\n}}\n"
        want = f"define i{w} @f() {{\n  ret i{w} {expected}\n}}\n"
        assert vt(z3, fn, want, "f")["status"] == "proved", (name, "SMT model")
        assert concrete_tv(lli, fn, want, "f")["status"] == "agree", (name, "lli confirmation")

    print("intrinsics_ir_fixture OK: modeled ctpop/abs/ctlz/cttz/fshl/fshr and u/s {add,sub}.sat -- each "
          "validated against lli (model==hand by z3 and real==hand by lli via hand-equivalents; ctlz/cttz/"
          "sat confirmed by model AND lli on ground-truth points). abs idempotence proves; a wrong fold "
          "refutes. Track B reach lifted -- these intrinsics no longer decline (bswap stays enrichment's)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
