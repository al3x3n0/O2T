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
    # Any parameter this model has a BIT view of -- integers and floating-point alike, the same
    # rule `_translate_parsed` applies -- so a float parameter is a term here rather than a hole.
    env = {p.name: (p.name, sem.bit_width(p.type), "false", "false")
           for p in f.params if sem.bit_width(p.type) is not None}
    for i in f.blocks[0].instructions:
        if i.op == "ret":
            return sem.value(i.operands[0], env, sem.bit_width(i.operands[0].type))[:3]
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
        "bswap": [("A", w, "pa", "ua")],
        "bitreverse": [("A", w, "pa", "ua")],
    }
    assert set(arg_sets) == set(sem.INTRINSICS), "every modeled intrinsic must be differentiated"
    #    The drift check compares against the TEXT-PATH builder it was relocated from, so it applies
    #    only to intrinsics that HAVE such a counterpart. One modelled here for the first time has
    #    none, and copying it back into the old table to satisfy this would create the very second
    #    model the check exists to catch. Those are covered by behavioural assertions further down
    #    (a permutation must not be the identity, and must be injective).
    for name, ops in arg_sets.items():
        if name not in si._INTRINSICS:
            assert name in ("bswap", "bitreverse"), \
                (f"{name} has no text-path counterpart -- give it behavioural teeth and list it "
                 "here deliberately, rather than letting it slip past the drift check", name)
            continue
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

    # 6) FLOATS ARE CARRIED AS BITS, AND ONLY WHERE BITS ARE EXACT. `fneg` and `llvm.copysign` are
    #    defined BIT-WISE by LLVM -- a sign-bit flip and a sign-bit copy, with no rounding, no trap
    #    and no special case for NaN or zero -- so modelling them on bitvectors is EXACT, not an
    #    approximation. `select` and `freeze` over a float choose between bit patterns and need no
    #    FP view either. That is the whole of what a bits-only model may claim.
    fneg_t, fneg_w = _new("define float @f(float %x){\n %r = fneg float %x\n ret float %r\n}\n",
                          "f")[:2]
    assert fneg_w == 32 and "bvxor" in fneg_t and sem.const(1 << 31, 32) in fneg_t, \
        ("fneg must be a sign-bit flip at the float's own width", fneg_t, fneg_w)
    cs = _new("define float @f(float %x, float %y){\n"
              " %r = call float @llvm.copysign.f32(float %x, float %y)\n ret float %r\n}\n"
              "declare float @llvm.copysign.f32(float, float)\n", "f")[0]
    assert "bvor" in cs and sem.const(1 << 31, 32) in cs and sem.const((1 << 31) - 1, 32) in cs, \
        ("copysign must take the sign from one operand and every other bit from the other", cs)
    #    A float CONSTANT is its IEEE bit pattern, taken from LLVM rather than from the printed
    #    text: 1.0f is 0x3F800000. Reaching it as text ("float 1.000000e+00") is what made
    #    `operand kind 'other_const'` its own decline bucket.
    one = _new("define float @f(){\n ret float 1.0\n}\n", "f")[0]
    assert one == sem.const(0x3F800000, 32), ("1.0f must be its bit pattern", one)
    assert _new("define double @f(){\n ret double 1.0\n}\n", "f")[0] \
        == sem.const(0x3FF0000000000000, 64), "1.0 as a double is its own 64-bit pattern"

    # 7) AND THE CONTAINMENT THAT KEEPS THAT SOUND: nothing may read a float as an FP NUMBER. Every
    #    operation that would round, compare numerically, or convert MUST still decline -- a bits
    #    model has no honest answer for them (+0.0 and -0.0 differ in bits and compare equal; NaN
    #    compares unequal to itself). If one of these ever starts being accepted without a real FP
    #    theory behind it, that is a false-proof seam, not a new capability.
    for body, why in ((" %r = fadd float %x, %y\n ret float %r", "fadd"),
                      (" %r = fmul float %x, %y\n ret float %r", "fmul"),
                      (" %r = fdiv float %x, %y\n ret float %r", "fdiv"),
                      (" %r = frem float %x, %y\n ret float %r", "frem"),
                      (" %c = fcmp oeq float %x, %y\n %r = select i1 %c, float %x, float %y\n"
                       " ret float %r", "fcmp"),
                      (" %i = fptosi float %x to i32\n %r = sitofp i32 %i to float\n"
                       " ret float %r", "fptosi/sitofp"),
                      (" %d = fpext float %x to double\n %r = fptrunc double %d to float\n"
                       " ret float %r", "fpext/fptrunc")):
        src = f"define float @f(float %x, float %y){{\n{body}\n}}\n"
        try:
            _new(src, "f")
            raise AssertionError(f"{why} needs real FP semantics and must decline in a bits model")
        except sem.Unsupported:
            pass

    # 8) FAST-MATH FLAGS ARE IGNORED ON THE SOURCE AND DECLINED ON THE TARGET, and the asymmetry
    #    is the whole point. FMF only ENLARGE a value's real behaviour set -- `nnan`/`ninf` make the
    #    result POISON on a NaN or infinity, the rest license alternative results. On the SOURCE
    #    that means reality has more behaviours than the model, and refining into a larger source
    #    set is only easier, so a proof stays valid. On the TARGET refinement requires every target
    #    behaviour to be a source one, so modelling the target SMALLER than it really is lets a pair
    #    prove where reality refutes -- a false proof. Found because Alive2 refused to verify a
    #    `select arcp nnan` fold it called an approximation, which is what prompted looking at all.
    fneg_fm = "define float @f(float %x){\n %r = fneg nnan float %x\n ret float %r\n}\n"
    fn = ir.parse(fneg_fm).function("f")
    env = {"%x": ("%x", 32, "false", "false")}
    sem.evaluate(fn.blocks[0].instructions[0], env, {"side": "source", "fresh": None})
    assert "%r" in env, "a fast-math flag on the SOURCE is ignored, not declined"
    try:
        sem.evaluate(fn.blocks[0].instructions[0], {"%x": ("%x", 32, "false", "false")},
                     {"side": "target", "fresh": None})
        raise AssertionError("a fast-math flag on the TARGET must decline -- ignoring it models the "
                             "target as more defined than it is, which is the false-proof direction")
    except sem.Unsupported:
        pass

    # 9) CONSTANT EXPRESSIONS: FOLD WHAT LLVM CAN COMPUTE, SYMBOLISE ONLY WHAT IT CANNOT. These are
    #    two different things wearing one label. `bitcast (<2 x i32> <i32 1, i32 -1> to i64)` is a
    #    fixed number, and InstCombine EVALUATES it -- icmp.ll test12 folds to `xor %A, true`. Hand
    #    a validator an opaque symbol for that and the fold cannot be proved, so it REFUTES a sound
    #    transform. The dumper therefore folds first, and only what survives becomes a symbol.
    folded = _new("define i64 @f(){\n ret i64 bitcast (<2 x i32> <i32 1, i32 -1> to i64)\n}\n", "f")[0]
    assert folded == sem.const(-4294967295 & ((1 << 64) - 1), 64), \
        ("a computable constant expression must arrive already evaluated, not as a symbol", folded)
    #    What is left depends on an address no compiler knows. Its value is FIXED but unknown, and a
    #    transform involving it must hold for EVERY address the global could have -- so an
    #    unconstrained constant is the correct reading. Keyed by the printed text, so the SAME
    #    expression on both sides is the same symbol and a pair merely carrying it through proves.
    ce = "define i32 @f(i32 %x){\n %r = sub i32 %x, ptrtoint (ptr @g to i32)\n ret i32 %r\n}\n" \
         "@g = external global i32\n"
    t1 = _new(ce, "f")[0]
    assert "cexpr_" in t1, ("an uncomputable constant expression must become a symbol", t1)
    assert _new(ce, "f")[0] == t1, "the same expression must yield the same symbol"
    #    ...and two DIFFERENT expressions must stay independent, or the model would be asserting an
    #    equality between two addresses it knows nothing about.
    ce2 = ce.replace("@g", "@h")
    assert _new(ce2, "f")[0] != t1, "different constant expressions must not share a symbol"

    # 10) INTRINSIC NAMES WITH NO TYPE SUFFIX still have to resolve. `llvm.assume` splits into a
    #     single part, so a lookup that only ever inspects a PREFIX of a longer name never matched
    #     it -- and it was reported as an unmodelled call, which is how its semantics went missing.
    assert sem.intrinsic_name("@llvm.assume") == "assume", \
        "an intrinsic with no type suffix must still be recognised"
    assert sem.intrinsic_name("@llvm.ctpop.i32") == "ctpop", "suffixed intrinsics still resolve"
    assert sem.intrinsic_name("@llvm.smin.v2i32") == "smin", "vector intrinsics still resolve"
    assert sem.intrinsic_name("@not_an_intrinsic") is None, "a plain call is not an intrinsic"

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
