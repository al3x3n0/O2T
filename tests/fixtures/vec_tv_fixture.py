#!/usr/bin/env python3
"""Vectors: whole-function TV via a LANE MODEL (element-wise + shuffle/extract/insert).

A vector value is a list of per-lane scalar SMT terms (a scalar is a 1-lane list), so element-wise ops
lower lane-by-lane and `extractelement`/`insertelement`/`shufflevector` are exact index/permutation
operations on the lists (o2t/validate/vec_tv.py). A transform is a refinement iff every result lane
agrees for all inputs.

  * vector folds prove: `and <2 x i32> %x, <-1,-1> -> %x`, `add <4 x i32> %x, zeroinitializer -> %x`;
  * a shufflevector is proved equal to its explicit extract/insert form (the lane model gets the
    permutation exactly);
  * TEETH -- a wrong lane (`and X, <-1,0>` claimed == X; a wrong shuffle mask) REFUTES;
  * scalable vectors / variable indices / reductions / undef masks are a sound decline.
Scope: fixed-width <N x iW>, single-BB, constant indices/masks. Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.vec_tv import vec_tv, svec_tv  # noqa: E402

AND = ("define <2 x i32> @f(<2 x i32> %x) {\n"
       "  %r = and <2 x i32> %x, <i32 -1, i32 -1>\n  ret <2 x i32> %r\n}\n")
ADD = ("define <4 x i32> @g(<4 x i32> %x) {\n"
       "  %r = add <4 x i32> %x, zeroinitializer\n  ret <4 x i32> %r\n}\n")
SHUF = ("define <2 x i32> @s(<2 x i32> %a, <2 x i32> %b) {\n"
        "  %r = shufflevector <2 x i32> %a, <2 x i32> %b, <2 x i32> <i32 0, i32 3>\n"
        "  ret <2 x i32> %r\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("vec_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. Vector folds are proved against opt's own output, lane by lane.
    assert vec_tv(z3, AND, si.run_passes(AND, "instcombine", opt), "f")["status"] == "proved", "and X,-1->X"
    assert vec_tv(z3, ADD, si.run_passes(ADD, "instcombine", opt), "g")["status"] == "proved", "add X,0->X"

    # 2. A shufflevector <0,3> = <a0, b1> is proved EQUAL to its explicit extract/insert form -- the
    #    lane model captures the permutation exactly.
    eq = ("define <2 x i32> @s(<2 x i32> %a, <2 x i32> %b) {\n"
          "  %a0 = extractelement <2 x i32> %a, i32 0\n  %b1 = extractelement <2 x i32> %b, i32 1\n"
          "  %t = insertelement <2 x i32> zeroinitializer, i32 %a0, i32 0\n"
          "  %r = insertelement <2 x i32> %t, i32 %b1, i32 1\n  ret <2 x i32> %r\n}\n")
    assert vec_tv(z3, SHUF, eq, "s")["status"] == "proved", "shuffle == extract/insert form"

    # 3. TEETH -- a wrong lane refutes: `and X, <-1,0>` claimed to equal X (lane 1 is X&0=0 != X).
    bad_fold = ("define <2 x i32> @f(<2 x i32> %x) {\n  %r = and <2 x i32> %x, <i32 -1, i32 0>\n"
                "  ret <2 x i32> %r\n}\n")
    ident = "define <2 x i32> @f(<2 x i32> %x) {\n  ret <2 x i32> %x\n}\n"
    v = vec_tv(z3, bad_fold, ident, "f")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong vector lane must refute", v)
    #    ...and a wrong shuffle mask (<0,2> instead of <0,3>) refutes.
    wrong_shuf = SHUF.replace("i32 0, i32 3", "i32 0, i32 2")
    assert vec_tv(z3, SHUF, wrong_shuf, "s")["status"] == "refuted", "a wrong shuffle mask must refute"

    # 4. SCALABLE vectors (runtime length) are TV'd at ONE symbolic lane -- element-wise, so a proof
    #    for an unconstrained lane index covers all lanes. Folds prove; a wrong lane refutes; a
    #    cross-lane op (which the per-lane model cannot soundly handle) declines.
    sf = ("define <vscale x 4 x i32> @sf(<vscale x 4 x i32> %x) {\n"
          "  %r = add <vscale x 4 x i32> %x, zeroinitializer\n  ret <vscale x 4 x i32> %r\n}\n")
    assert svec_tv(z3, sf, si.run_passes(sf, "instcombine", opt), "sf")["status"] == "proved", "svec add X,0->X"
    sg = ("define <vscale x 4 x i32> @sg(<vscale x 4 x i32> %x) {\n"
          "  %r = and <vscale x 4 x i32> %x, splat (i32 -1)\n  ret <vscale x 4 x i32> %r\n}\n")
    assert svec_tv(z3, sg, si.run_passes(sg, "instcombine", opt), "sg")["status"] == "proved", "svec and X,-1->X"
    sbad = sg.replace("splat (i32 -1)", "splat (i32 0)")   # and X, 0 -> 0, not X
    sident = "define <vscale x 4 x i32> @sg(<vscale x 4 x i32> %x) {\n  ret <vscale x 4 x i32> %x\n}\n"
    assert svec_tv(z3, sbad, sident, "sg")["status"] == "refuted", "svec wrong lane must refute"
    xl = ("define i32 @h(<vscale x 4 x i32> %x) {\n"
          "  %e = extractelement <vscale x 4 x i32> %x, i32 0\n  ret i32 %e\n}\n")
    assert svec_tv(z3, xl, xl, "h")["status"] == "unsupported", "a cross-lane op must decline"

    # POISON EXPLOITATION IS NOW PROVED, not declined. `ashr x, x` is poison wherever the shift
    # reaches the width, so folding it to 0 is a sound refinement -- and reference Alive2 proves it.
    # A value-only lane model could not: it saw 0 != ashr(x,x) and had to decline rather than
    # false-refute, which is what the old `poison-risk` guard was for. Carrying poison PER LANE
    # replaces the guard with the real obligation. A poison-free wrong lane (above) still refutes.
    pv = ("define <2 x i32> @p(<2 x i32> %x) {\n  %r = ashr <2 x i32> %x, %x\n  ret <2 x i32> %r\n}\n")
    pz = ("define <2 x i32> @p(<2 x i32> %x) {\n  ret <2 x i32> zeroinitializer\n}\n")
    assert vec_tv(z3, pv, pz, "p")["status"] == "proved", \
        "a sound poison-exploiting vector fold must now PROVE -- the lane model carries poison"

    # ...and the other direction is the teeth: INTRODUCING poison the source did not have must
    # refute. Values are identical everywhere, so only the poison term can catch it.
    nf = ("define <2 x i32> @q(<2 x i32> %x, <2 x i32> %y) {\n  %r = lshr <2 x i32> %x, %y\n"
          "  ret <2 x i32> %r\n}\n")
    ex = ("define <2 x i32> @q(<2 x i32> %x, <2 x i32> %y) {\n  %r = lshr exact <2 x i32> %x, %y\n"
          "  ret <2 x i32> %r\n}\n")
    assert vec_tv(z3, nf, ex, "q")["status"] == "refuted", \
        "adding `exact` to a vector shift introduces poison and must refute, values being identical"

    # 5. THE ELEMENT-WISE OPERATIONS THE LANE MODEL WAS MISSING. Measured over LLVM 18's InstCombine
    #    tests, `select` (60), `zext` (28) and `sext` (9) were this validator's largest remaining
    #    decline causes -- and each is exactly what a lane model is for, since every lane's result
    #    depends only on that lane's inputs. A per-lane condition and a scalar condition are BOTH
    #    legal for a vector select, so both are exercised.
    SEL = ("define <2 x i32> @s(<2 x i1> %c, <2 x i32> %a, <2 x i32> %b) {\n"
           "  %r = select <2 x i1> %c, <2 x i32> %a, <2 x i32> %b\n  ret <2 x i32> %r\n}\n")
    assert vec_tv(z3, SEL, si.run_passes(SEL, "instcombine", opt), "s")["status"] == "proved", \
        "a per-lane vector select must be modelled lane by lane"
    SELSCALAR = ("define <2 x i32> @s(i1 %c, <2 x i32> %a, <2 x i32> %b) {\n"
                 "  %r = select i1 %c, <2 x i32> %a, <2 x i32> %b\n  ret <2 x i32> %r\n}\n")
    assert vec_tv(z3, SELSCALAR, si.run_passes(SELSCALAR, "instcombine", opt), "s")["status"] == "proved", \
        "a scalar condition selecting whole vectors must broadcast to every lane"
    for kind in ("zext", "sext"):
        # kept to ONE instruction on purpose: give `opt` an arithmetic op to work with and it infers
        # `nsw`, whereupon the target-poison guard correctly declines -- a value-equality model cannot
        # prove refinement against a target that can be poison. That guard firing is right, but it
        # would test the guard rather than the widening.
        EXT = ("define <2 x i32> @e(<2 x i8> %a) {\n  %r = " + kind +
               " <2 x i8> %a to <2 x i32>\n  ret <2 x i32> %r\n}\n")
        assert vec_tv(z3, EXT, si.run_passes(EXT, "instcombine", opt), "e")["status"] == "proved", \
            f"widening a vector lane by lane ({kind})"

    #    TEETH: the two extensions differ on a set sign bit, so swapping them must REFUTE. Without
    #    this, `zext` and `sext` could both be emitted as the same widening and nothing would notice.
    swapped = ("define <2 x i32> @e(<2 x i8> %a) {\n  %r = zext <2 x i8> %a to <2 x i32>\n"
               "  ret <2 x i32> %r\n}\n")
    sext_src = ("define <2 x i32> @e(<2 x i8> %a) {\n  %r = sext <2 x i8> %a to <2 x i32>\n"
                "  ret <2 x i32> %r\n}\n")
    assert vec_tv(z3, sext_src, swapped, "e")["status"] == "refuted", \
        "zext where sext is required must refute -- they differ wherever the sign bit is set"

    # 6. TRUNC, the third element-wise op the lane model was missing (10 declines on LLVM's tests).
    TR = ("define <2 x i8> @t(<2 x i32> %a) {\n  %r = trunc <2 x i32> %a to <2 x i8>\n"
          "  ret <2 x i8> %r\n}\n")
    assert vec_tv(z3, TR, si.run_passes(TR, "instcombine", opt), "t")["status"] == "proved", \
        "narrowing a vector lane by lane"
    #    TEETH: truncating different bits must refute -- otherwise `trunc` could extract any field.
    shifted = ("define <2 x i8> @t(<2 x i32> %a) {\n  %s = lshr <2 x i32> %a, <i32 8, i32 8>\n"
               "  %r = trunc <2 x i32> %s to <2 x i8>\n  ret <2 x i8> %r\n}\n")
    assert vec_tv(z3, TR, shifted, "t")["status"] == "refuted", \
        "a trunc of the wrong bits must refute"

    # 7. FREEZE, AND THE POISON-CAPABLE PARAMETERS IT NEEDS. A vector parameter without `noundef` may
    #    arrive poison per lane, so each lane carries a shared Bool flag -- part of the INPUT, so no
    #    quantifier. Without it every lane is definite, freeze collapses to the identity, and
    #    `freeze %x -> %x` would PROVE; reference Alive2 refutes it with witness
    #    `<2 x i32> %x = <3 [based on undef], poison>`. The freeze's own choice is side-quantified
    #    like an undef element: universal on the source, free on the target.
    FZ = ("define <2 x i32> @f(<2 x i32> %x) {\n  %r = freeze <2 x i32> %x\n  ret <2 x i32> %r\n}\n")
    ID = ("define <2 x i32> @f(<2 x i32> %x) {\n  ret <2 x i32> %x\n}\n")
    assert vec_tv(z3, FZ, ID, "f")["status"] == "refuted", \
        "removing a vector freeze must refute -- a parameter may arrive poison"
    assert vec_tv(z3, ID, FZ, "f")["status"] == "proved", \
        "INTRODUCING a freeze is sound and must prove"
    #    ABLATION by attribute, not by patch: `noundef` is exactly the promise the flag encodes, so
    #    declaring it removes the flag and the SAME pair flips to proved. The verdict turns on the
    #    model, not on the fixture.
    assert vec_tv(z3, FZ.replace("%x)", "noundef %x)"), ID.replace("%x)", "noundef %x)"),
                  "f")["status"] == "proved", \
        "with `noundef` the parameter is definite and freeze removal is the identity"

    # 8. OBSERVABLE CALLS reach the lane model too. LLVM's tests keep a vector alive with
    #    `call void @use(<2 x i32> %x)` exactly as they do for scalars, and this validator declined
    #    every such function (20 of its declines). Same split as the scalar path: the callee SEQUENCE
    #    is syntactic, the ARGUMENTS go to the solver, per lane.
    DECL = "declare void @use(<2 x i32>)\n"
    KEEP = (DECL + "define <2 x i32> @f(<2 x i32> %x) {\n"
            "  %a = and <2 x i32> %x, <i32 -1, i32 -1>\n  call void @use(<2 x i32> %a)\n"
            "  ret <2 x i32> %a\n}\n")
    FOLDED = (DECL + "define <2 x i32> @f(<2 x i32> %x) {\n  call void @use(<2 x i32> %x)\n"
              "  ret <2 x i32> %x\n}\n")
    assert vec_tv(z3, KEEP, FOLDED, "f")["status"] == "proved", \
        "a fold under a vector keep-alive call must prove -- the call is preserved and its argument agrees"
    #    TEETH: dropping the call DECLINES (a behaviour change this does not model), and passing a
    #    different value REFUTES (the callee observes it).
    dropped = DECL + "define <2 x i32> @f(<2 x i32> %x) {\n  ret <2 x i32> %x\n}\n"
    assert vec_tv(z3, KEEP, dropped, "f")["status"] == "unsupported", \
        "dropping an observable call must decline, not prove"
    wrong = (DECL + "define <2 x i32> @f(<2 x i32> %x) {\n"
             "  %b = add <2 x i32> %x, <i32 1, i32 1>\n  call void @use(<2 x i32> %b)\n"
             "  ret <2 x i32> %x\n}\n")
    assert vec_tv(z3, KEEP, wrong, "f")["status"] == "refuted", \
        "handing an observable call a different value must REFUTE"
    #    ...and an unmodelled `@llvm.*` declines ON ITS NAME rather than being swallowed as an opaque
    #    effect. `llvm.assume` ESTABLISHES its argument; treating it as unknown drops that fact.
    AS = ("declare void @llvm.assume(i1)\ndefine <2 x i32> @g(<2 x i32> %x, i1 %c) {\n"
          "  call void @llvm.assume(i1 %c)\n  ret <2 x i32> %x\n}\n")
    g = vec_tv(z3, AS, AS, "g")
    assert g["status"] == "unsupported" and "llvm.assume" in g.get("reason", ""), \
        ("an unmodelled intrinsic must decline on its name, not be waved through as observable", g)

    # 9. UNDEF'S FREEDOM IS PER USE, AND SHARING IT WAS A FALSE PROOF. A literal `undef` is named
    #    fresh at every read, which is right. But an SSA register CARRYING that freedom is one term
    #    in the environment, so two reads of it modelled the two uses as agreeing -- and they need
    #    not. `xor %u, %u` therefore modelled 0, and this pair PROVED while reference Alive2 refutes
    #    it with a witness (lane 1: source 0, target 1). It is unsound in the proving direction
    #    because it shrinks the TARGET's behaviour set, and a target with fewer behaviours is easier
    #    to prove a refinement of. An undef-tainted register is now declined on its SECOND read.
    zero = ("define <2 x i32> @f(<2 x i32> %x) {\n  ret <2 x i32> zeroinitializer\n}\n")
    undef_twice = ("define <2 x i32> @f(<2 x i32> %x) {\n"
                   "  %u = and <2 x i32> undef, <i32 -1, i32 -1>\n"
                   "  %r = xor <2 x i32> %u, %u\n  ret <2 x i32> %r\n}\n")
    v = vec_tv(z3, zero, undef_twice, "f")
    assert v["status"] == "unsupported" and "used more than once" in v.get("reason", ""), \
        ("a register carrying undef freedom, read twice, must DECLINE -- Alive2 refutes this pair", v)

    #    ABLATION -- the claim is that the VERDICT changes with the rule, not merely that the rule
    #    runs. `_step` is what propagates the taint onto an instruction's result; without it the
    #    taint never leaves the literal, the second read looks ordinary, and the pair PROVES again.
    import o2t.validate.vec_tv as _vt
    _step = _vt._step
    try:
        _vt._step = lambda inst, env, ctx: _vt._vec_instr(inst, env, ctx)
        assert vec_tv(z3, zero, undef_twice, "f")["status"] == "proved", \
            "ablation must restore the false proof -- otherwise this fixture proves nothing"
    finally:
        _vt._step = _step

    #    ...but a FROZEN undef used twice must still be DECIDED, and that is the boundary the rule
    #    has to get right rather than merely be conservative about. `freeze` is the instruction that
    #    collapses undef into one fixed value, so its uses legitimately agree; letting the taint
    #    propagate through it declined five real functions in LLVM 18's own tests
    #    (`and_freeze_undef_multipleuses` and friends), all of which reference Alive2 confirms.
    frozen_twice = ("declare void @use_i32(i32)\n"
                    "define i32 @h(i32 %x) {\n  %f = freeze i32 undef\n"
                    "  %res = and i32 %x, %f\n  call void @use_i32(i32 %f)\n  ret i32 %res\n}\n")
    folded = ("declare void @use_i32(i32)\n"
              "define i32 @h(i32 %x) {\n  call void @use_i32(i32 0)\n  ret i32 0\n}\n")
    assert vec_tv(z3, frozen_twice, folded, "h")["status"] == "proved", \
        "a FROZEN undef is one fixed value -- two uses of it agree, and the rule must not decline it"

    #    ...and ONE read of an undef-derived register is exactly one observation of undef, so it
    #    stays DECIDED rather than being swept up by the rule. `and undef, 0` is 0, not "anything".
    and_zero = ("define <2 x i32> @g() {\n  %u = and <2 x i32> undef, zeroinitializer\n"
                "  ret <2 x i32> %u\n}\n")
    ret_zero = ("define <2 x i32> @g() {\n  ret <2 x i32> zeroinitializer\n}\n")
    assert vec_tv(z3, and_zero, ret_zero, "g")["status"] == "proved", \
        "a single read of an undef-derived register must stay decided, not decline"

    # FLOAT LANES ARE BITS, like a scalar float, and the lane model only ever does BIT operations
    # on them -- `fneg` (a sign-bit flip), `llvm.copysign` (a sign-bit copy), `select`, and a
    # lane-preserving `bitcast`. LLVM defines those bit-wise, with no rounding, no trap and no
    # NaN/zero special case, so this is exact rather than an approximation of floating point.
    fneg_s = ("define <2 x float> @v(<2 x float> %x) {\n"
              "  %i = bitcast <2 x float> %x to <2 x i32>\n"
              "  %m = xor <2 x i32> %i, <i32 -2147483648, i32 -2147483648>\n"
              "  %r = bitcast <2 x i32> %m to <2 x float>\n  ret <2 x float> %r\n}\n")
    fneg_t = ("define <2 x float> @v(<2 x float> %x) {\n"
              "  %r = fneg <2 x float> %x\n  ret <2 x float> %r\n}\n")
    assert vec_tv(z3, fneg_s, fneg_t, "v")["status"] == "proved", \
        "flipping the sign bit of every lane IS fneg -- float lanes must be carried as bits"
    cs_s = ("define <2 x float> @c(<2 x float> %a, <2 x float> %b) {\n"
            "  %r = call <2 x float> @llvm.copysign.v2f32(<2 x float> %a, <2 x float> %b)\n"
            "  ret <2 x float> %r\n}\n"
            "declare <2 x float> @llvm.copysign.v2f32(<2 x float>, <2 x float>)\n")
    assert vec_tv(z3, cs_s, cs_s, "c")["status"] == "proved", "copysign must be decidable per lane"
    #    A bitcast that RESHAPES lanes (a different count or width) would have to split or join
    #    them, and declines rather than being waved through as an identity.
    resh = ("define i64 @r(<2 x float> %x) {\n  %i = bitcast <2 x float> %x to i64\n"
            "  ret i64 %i\n}\n")
    d = vec_tv(z3, resh, resh, "r")
    assert d["status"] == "unsupported" and "reshape" in d.get("reason", ""), \
        ("a lane-splitting/joining bitcast must decline, not pass as an identity", d)
    #    FAST-MATH FLAGS: ignored on the source, DECLINED on the target. They only ENLARGE a value's
    #    real behaviour set, so ignoring them shrinks whichever side they are on -- harmless for the
    #    source, a false proof for the target. Alive2 refusing to verify a `select arcp nnan` fold
    #    (calling it an approximation) is what prompted checking this at all.
    fm_src = ("define <2 x float> @m(<2 x float> %x) {\n"
              "  %r = fneg nnan <2 x float> %x\n  ret <2 x float> %r\n}\n")
    plain = ("define <2 x float> @m(<2 x float> %x) {\n"
             "  %r = fneg <2 x float> %x\n  ret <2 x float> %r\n}\n")
    assert vec_tv(z3, fm_src, plain, "m")["status"] == "proved", \
        "a fast-math flag on the SOURCE is ignored -- reality has more behaviours, which is safe"
    d = vec_tv(z3, plain, fm_src, "m")
    assert d["status"] == "unsupported" and "fast-math" in d.get("reason", ""), \
        ("a fast-math flag on the TARGET must decline -- ignoring it models the target as more "
         "defined than it is, and that is where a false proof comes from", d)

    # MIN/MAX INTRINSICS PER LANE. `llvm.smin/smax/umin/umax` are the TARGET of a whole family of
    # InstCombine folds -- it canonicalises an `icmp`+`select` pair into one -- so the lane model
    # could translate every such SOURCE and none of their targets, and the whole family declined.
    # The comparison table is the shared `sem.MINMAX` the scalar model uses, not a second reading.
    for intr, pred, arm in (("smin", "slt", "a"), ("smax", "sgt", "a"),
                            ("umin", "ult", "a"), ("umax", "ugt", "a")):
        src = (f"define <2 x i32> @m(<2 x i32> %a, <2 x i32> %b) {{\n"
               f"  %c = icmp {pred} <2 x i32> %a, %b\n"
               f"  %r = select <2 x i1> %c, <2 x i32> %{arm}, <2 x i32> %b\n"
               f"  ret <2 x i32> %r\n}}\n")
        tgt = (f"define <2 x i32> @m(<2 x i32> %a, <2 x i32> %b) {{\n"
               f"  %r = call <2 x i32> @llvm.{intr}.v2i32(<2 x i32> %a, <2 x i32> %b)\n"
               f"  ret <2 x i32> %r\n}}\n"
               f"declare <2 x i32> @llvm.{intr}.v2i32(<2 x i32>, <2 x i32>)\n")
        assert vec_tv(z3, src, tgt, "m")["status"] == "proved", \
            f"the icmp+select form of {intr} must prove against the intrinsic"
        # ...and the WRONG intrinsic must refute, so the four are not modelled interchangeably.
        other = {"smin": "smax", "smax": "smin", "umin": "umax", "umax": "umin"}[intr]
        wrong = tgt.replace(intr, other)
        assert vec_tv(z3, src, wrong, "m")["status"] == "refuted", \
            f"{intr} folded to {other} must refute -- the predicates must not be interchangeable"

    # ELEMENT-WISE INTRINSICS PER LANE, through the SHARED scalar models. Each was already
    # modelled for scalars and simply never reached the lane model, so any fold whose target is the
    # vector form declined on its last instruction. Asserted against the hand-written lane-wise
    # equivalent, so the per-lane application is checked and not merely that something came back.
    bs = ("define <2 x i32> @b(<2 x i32> %x) {\n"
          "  %r = call <2 x i32> @llvm.bswap.v2i32(<2 x i32> %x)\n  ret <2 x i32> %r\n}\n"
          "declare <2 x i32> @llvm.bswap.v2i32(<2 x i32>)\n")
    assert vec_tv(z3, bs, bs, "b")["status"] == "proved", "a vector bswap must be decidable"
    #   ...and it must be a real permutation per lane, not the identity.
    assert vec_tv(z3, bs, "define <2 x i32> @b(<2 x i32> %x) {\n  ret <2 x i32> %x\n}\n",
                  "b")["status"] == "refuted", "a vector bswap is not the identity"
    #   AN IMMARG FLAG IS SCALAR BESIDE A VECTOR OPERAND -- one flag for the whole operation, not
    #   one per lane -- so it is read once and given to every lane. `cttz(%v, i1 true)` says zero
    #   is POISON, and getting that wrong per-lane would change which lanes are poison.
    cz = ("define <2 x i8> @c(<2 x i8> %x) {\n"
          "  %r = call <2 x i8> @llvm.cttz.v2i8(<2 x i8> %x, i1 true)\n  ret <2 x i8> %r\n}\n"
          "declare <2 x i8> @llvm.cttz.v2i8(<2 x i8>, i1)\n")
    assert vec_tv(z3, cz, cz, "c")["status"] == "proved", \
        "a vector cttz with an immarg flag must be decidable"

    print("vec_tv_fixture OK: FIXED vectors are TV'd via a lane model -- element-wise folds prove, a "
          "shufflevector is proved equal to its explicit extract/insert form, a wrong lane or shuffle "
          "mask REFUTES; SCALABLE vectors (runtime length) are TV'd at ONE symbolic lane -- element-wise "
          "folds prove (add X,0->X, and X,splat(-1)->X), a wrong lane refutes, and a cross-lane op "
          "declines (the per-lane model stays sound). select/zext/sext are modelled lane by lane -- "
          "the three largest decline causes left in this validator on LLVM's own tests -- and zext "
          "where sext is required refutes; `trunc` narrows lane by lane and truncating the wrong "
          "bits refutes; `freeze` is decided in BOTH directions because a vector parameter now "
          "carries a poison flag per lane -- removal REFUTES (Alive2's witness is a poison lane), "
          "introduction proves, and adding `noundef` flips removal to proved; a vector keep-alive "
          "`call void @use(...)` is an observable EFFECT -- dropping it declines, handing it a "
          "different value refutes, and an unmodelled `@llvm.*` declines on its name. UNDEF'S FREEDOM IS PER USE: a register carrying it, read "
          "twice, is declined rather than modelled as agreeing with itself -- that sharing was a "
          "FALSE PROOF (Alive2 refutes the pair), and the ablation shows the verdict changes with "
          "the rule. The vector gap -- fixed and scalable -- closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
