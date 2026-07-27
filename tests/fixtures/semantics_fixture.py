#!/usr/bin/env python3
"""The shared semantics layer, proved equivalent to the text path it replaces.

`o2t/validate/semantics.py` is the middle layer of the new stack -- LLVM's own parser below it
(`ir_model`), the two discharge strategies above it. It exists because the peephole and loop tracks
each grew their own reading of what an LLVM instruction means, and DUPLICATE MODELS are where
soundness bugs breed: round 6 of the 2026-07 review found a live false proof in the loop track's
`min`/`max` alias, while round 3's flag fix covered the loop path automatically precisely because that
part was shared.

The migration risk is obvious: a rewritten semantics layer that *almost* matches the old one silently
changes verdicts. So this fixture is a DIFFERENTIAL, the same discipline the Clang-AST front-end used
against the regex parser -- for each shape, the SMT the new layer emits must be BYTE-IDENTICAL to what
`scalar_ir`'s text path emits. Not "equivalent", identical: an equivalence check would need a solver
and would hide exactly the drift being hunted.

Two findings from building it, both pinned below:

  * re-deriving the intrinsic models from scratch INVERTED the ctlz/cttz bit order. They are
    lli-validated, so the models are RELOCATED verbatim rather than rewritten -- creating a second
    implementation of a validated model is the very hazard this module removes;
  * taking a constant's width from the surrounding instruction rather than from the constant's own
    type turned the `i1 true` flag of `@llvm.abs.i32(i32 %x, i1 true)` into 0xFFFFFFFF.

Needs `cv-ir-dump`; self-skips if it is not built.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import ir_model as ir  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate import semantics as sem  # noqa: E402

DECLS = ("declare i32 @llvm.ctpop.i32(i32)\n"
         "declare i32 @llvm.abs.i32(i32, i1)\n"
         "declare i32 @llvm.ctlz.i32(i32, i1)\n"
         "declare i32 @llvm.cttz.i32(i32, i1)\n"
         "declare i32 @llvm.smax.i32(i32, i32)\n"
         "declare i32 @llvm.umin.i32(i32, i32)\n"
         "declare i32 @llvm.fshl.i32(i32, i32, i32)\n"
         "declare i32 @llvm.fshr.i32(i32, i32, i32)\n"
         "declare i32 @llvm.sadd.sat.i32(i32, i32)\n"
         "declare i32 @llvm.usub.sat.i32(i32, i32)\n")

# (label, signature, body) -- one shape per modeled construct.
SHAPES = [
    ("binop + flags",   "i32 @f(i32 %x, i32 %y)", "  %a = add nsw i32 %x, %y\n  %b = mul i32 %a, 3\n  ret i32 %b"),
    ("nuw",             "i32 @f(i32 %x, i32 %y)", "  %a = sub nuw i32 %x, %y\n  ret i32 %a"),
    ("or disjoint",     "i32 @f(i32 %x, i32 %y)", "  %a = or disjoint i32 %x, %y\n  ret i32 %a"),
    ("udiv exact",      "i32 @f(i32 %x, i32 %y)", "  %a = udiv exact i32 %x, %y\n  ret i32 %a"),
    ("sdiv (UB)",       "i32 @f(i32 %x, i32 %y)", "  %a = sdiv i32 %x, %y\n  ret i32 %a"),
    ("srem (UB)",       "i32 @f(i32 %x, i32 %y)", "  %a = srem i32 %x, %y\n  ret i32 %a"),
    ("lshr exact",      "i32 @f(i32 %x, i32 %y)", "  %a = lshr exact i32 %x, %y\n  ret i32 %a"),
    ("variable shift",  "i32 @f(i32 %x, i32 %y)", "  %a = shl i32 %x, %y\n  ret i32 %a"),
    ("oversize shift",  "i32 @f(i32 %x)",         "  %a = ashr i32 %x, 40\n  ret i32 %a"),
    ("icmp",            "i1 @f(i32 %x, i32 %y)",  "  %c = icmp slt i32 %x, %y\n  ret i1 %c"),
    ("icmp eq",         "i1 @f(i32 %x, i32 %y)",  "  %c = icmp eq i32 %x, %y\n  ret i1 %c"),
    ("select",          "i32 @f(i32 %x, i32 %y)", "  %c = icmp slt i32 %x, %y\n  %s = select i1 %c, i32 %x, i32 %y\n  ret i32 %s"),
    ("select poison arm", "i32 @f(i32 %x, i32 %y)", "  %p = add nsw i32 %x, %y\n  %c = icmp slt i32 %x, %y\n  %s = select i1 %c, i32 %p, i32 %y\n  ret i32 %s"),
    ("zext",            "i64 @f(i32 %x)",         "  %z = zext i32 %x to i64\n  ret i64 %z"),
    ("trunc + sext",    "i32 @f(i32 %x)",         "  %t = trunc i32 %x to i16\n  %e = sext i16 %t to i32\n  ret i32 %e"),
    ("ctpop",           "i32 @f(i32 %x)",         "  %r = call i32 @llvm.ctpop.i32(i32 %x)\n  ret i32 %r"),
    ("abs (i1 flag)",   "i32 @f(i32 %x)",         "  %r = call i32 @llvm.abs.i32(i32 %x, i1 true)\n  ret i32 %r"),
    ("ctlz",            "i32 @f(i32 %x)",         "  %r = call i32 @llvm.ctlz.i32(i32 %x, i1 false)\n  ret i32 %r"),
    ("cttz",            "i32 @f(i32 %x)",         "  %r = call i32 @llvm.cttz.i32(i32 %x, i1 true)\n  ret i32 %r"),
    ("smax",            "i32 @f(i32 %x, i32 %y)", "  %r = call i32 @llvm.smax.i32(i32 %x, i32 %y)\n  ret i32 %r"),
    ("umin",            "i32 @f(i32 %x, i32 %y)", "  %r = call i32 @llvm.umin.i32(i32 %x, i32 %y)\n  ret i32 %r"),
    ("fshl",            "i32 @f(i32 %x, i32 %y, i32 %z)", "  %r = call i32 @llvm.fshl.i32(i32 %x, i32 %y, i32 %z)\n  ret i32 %r"),
    ("fshr",            "i32 @f(i32 %x, i32 %y, i32 %z)", "  %r = call i32 @llvm.fshr.i32(i32 %x, i32 %y, i32 %z)\n  ret i32 %r"),
    ("sadd.sat",        "i32 @f(i32 %x, i32 %y)", "  %r = call i32 @llvm.sadd.sat.i32(i32 %x, i32 %y)\n  ret i32 %r"),
    ("usub.sat",        "i32 @f(i32 %x, i32 %y)", "  %r = call i32 @llvm.usub.sat.i32(i32 %x, i32 %y)\n  ret i32 %r"),
    ("constant fold",   "i32 @f(i32 %x)",         "  %a = add i32 %x, 0\n  ret i32 %a"),
    ("negative const",  "i32 @f(i32 %x)",         "  %a = and i32 %x, -1\n  ret i32 %a"),
]


def _new(text, fn):
    """Translate through the new stack: LLVM's parse + the shared semantics."""
    f = ir.parse(text).function(fn)
    env = {p.name: (p.name, p.type.bits, "false", "false") for p in f.params if p.type.is_int()}
    for i in f.blocks[0].instructions:
        if i.op == "ret":
            return sem.value(i.operands[0], env, i.operands[0].type.bits)[:3]
        sem.evaluate(i, env, {"side": "source", "fresh": None})
    raise sem.Unsupported("no ret")


def _old(text, fn):
    _, term, width, poison, _ = si.translate(text, fn)
    return term, width, poison


def main() -> int:
    if not ir.available():
        print("semantics_fixture: cv-ir-dump not built, skipped")
        return 0

    # 1) THE DIFFERENTIAL: byte-identical SMT for every modeled shape.
    for label, sig, body in SHAPES:
        src = DECLS + f"define {sig} {{\n{body}\n}}\n"
        old, new = _old(src, "f"), _new(src, "f")
        assert old == new, (f"semantics drift on {label}", old, new)

    # 2) The intrinsic models are RELOCATED, not rewritten -- assert the builders themselves agree,
    #    so a future edit to one copy cannot silently diverge. (A first attempt at re-deriving these
    #    inverted the ctlz/cttz bit order; the differential above catches it, this pins the cause.)
    w = 32
    arg_sets = {
        "ctpop": [("A", w, "pa", "ua")],
        "abs": [("A", w, "pa", "ua"), ("NP", 1, "pn", "un")],
        "ctlz": [("A", w, "pa", "ua"), ("Z", 1, "pz", "uz")],
        "cttz": [("A", w, "pa", "ua"), ("Z", 1, "pz", "uz")],
        "fshl": [("A", w, "pa", "ua"), ("B", w, "pb", "ub"), ("C", w, "pc", "uc")],
        "fshr": [("A", w, "pa", "ua"), ("B", w, "pb", "ub"), ("C", w, "pc", "uc")],
        "uadd.sat": [("A", w, "pa", "ua"), ("B", w, "pb", "ub")],
        "usub.sat": [("A", w, "pa", "ua"), ("B", w, "pb", "ub")],
        "sadd.sat": [("A", w, "pa", "ua"), ("B", w, "pb", "ub")],
        "ssub.sat": [("A", w, "pa", "ua"), ("B", w, "pb", "ub")],
    }
    assert set(arg_sets) == set(sem.INTRINSICS), "every modeled intrinsic must be differentiated"
    for name, ops in arg_sets.items():
        assert si._INTRINSICS[name](ops, w) == sem.INTRINSICS[name](ops, w), \
            (f"intrinsic model drift: {name}", name)

    # 3) The poison and UB rules -- the surface every false proof in the review lived on -- agree for
    #    every (op, flag) combination, not just a representative one.
    for name, op in (("add", "bvadd"), ("sub", "bvsub"), ("mul", "bvmul"), ("or", "bvor"),
                     ("shl", "bvshl"), ("lshr", "bvlshr"), ("ashr", "bvashr"),
                     ("udiv", "bvudiv"), ("sdiv", "bvsdiv")):
        for flags in ([], ["nsw"], ["nuw"], ["nsw", "nuw"], ["exact"], ["disjoint"]):
            assert si._own_poison(name, op, flags, "A", "B", w) == \
                sem.own_poison(name, op, flags, "A", "B", w), ("own_poison drift", name, flags)
        assert si._own_ub(name, "A", "B", w) == sem.own_ub(name, "A", "B", w), ("own_ub drift", name)

    # 4) A constant's width comes from its OWN type, not from the surrounding instruction. Getting
    #    this wrong widened the `i1 true` flag of `@llvm.abs.i32(i32 %x, i1 true)` to 0xFFFFFFFF.
    m = ir.parse(DECLS + "define i32 @f(i32 %x){\n  %r = call i32 @llvm.abs.i32(i32 %x, i1 true)\n  ret i32 %r\n}\n")
    call = next(i for i in m.function("f").instructions() if i.op == "call")
    flag_term, flag_w, _, _ = sem.value(call.args[1], {}, 32)
    assert (flag_term, flag_w) == (sem.const(1, 1), 1), ("the i1 flag must stay 1 bit", flag_term, flag_w)

    # 5) DECLINES stay declines, and `undef` declines for the documented reason rather than being
    #    modeled as a constant (each use of an undef value may observe a different one).
    for body, why in ((" %r = frem float 1.0, 2.0\n ret i32 0", "float op"),
                      (" %r = add i32 %x, undef\n ret i32 %r", "undef operand")):
        src = f"define i32 @f(i32 %x){{\n{body}\n}}\n"
        try:
            _new(src, "f")
            raise AssertionError(f"{why} must decline")
        except sem.Unsupported:
            pass

    print(f"semantics_fixture OK: the shared semantics layer emits BYTE-IDENTICAL SMT to the text "
          f"path it replaces across {len(SHAPES)} shapes, all 10 intrinsic models, and every "
          "(op, flag) poison/UB combination -- so moving both tracks onto one reading of LLVM cannot "
          "silently change a verdict. The intrinsic models are relocated verbatim rather than "
          "re-derived (re-deriving them inverted the ctlz/cttz bit order), and a constant's width "
          "comes from its own type (taking it from the instruction turned an `i1 true` flag into "
          "0xFFFFFFFF). `undef` declines rather than being modeled as a constant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
