#!/usr/bin/env python3
"""One instruction-semantics layer, over a real parse, shared by both tracks.

This is the middle of three layers:

    ir_model  (syntax -- LLVM 18's own parser)
        |
    semantics (THIS: what an instruction computes, and when it is poison or UB)
        |
    +--> scalar_ir / mem_state / vec_tv ...   bounded discharge (bit-vector refinement)
    +--> loop_induction / closed_form ...     unbounded discharge (induction, integer ring)

WHY IT IS SEPARATE. The peephole and loop tracks each grew their own reading of what an LLVM
instruction means, and DUPLICATE MODELS are where soundness bugs breed: round 6 of the 2026-07 review
found a live false proof in the loop track's `min`/`max` alias that the peephole track did not share,
while round 3's flag fix in `formal_ir` automatically covered the loop path precisely BECAUSE that
part was shared. The lesson was explicit -- "the danger to hunt is duplicate models" -- so the value,
poison and UB rules live here once, and a fix lands in both tracks at the same time.

WHAT IS *NOT* UNIFIED, DELIBERATELY. Only the semantics are shared, not the proof strategy. The
bounded track discharges over bit-vectors; the loop track lifts to an integer ring where the
ℤ -> ℤ/2ⁿ homomorphism proves a recurrence for EVERY bitwidth at once (E3: 0.105s against a 10s
bit-blasting timeout). Collapsing those into one prover would trade that away for uniformity, so the
tracks share what an instruction MEANS and keep their own way of proving things about it.

Every value carries a 4-tuple `(term, width, poison, ub)` of SMT-LIB strings, the same shape the
validators already consume:
  * `term`   -- the bit-vector value;
  * `width`  -- its bit width;
  * `poison` -- a boolean term, true exactly when the value is poison;
  * `ub`     -- a boolean term, true when COMPUTING it is undefined behaviour.

The poison/UB rules are LLVM-faithful and are the surface every false proof in the review lived on, so
they are stated once, here, and tested by the flag matrix (`flag_matrix_fixture` enumerates every
`(op, flag)` in `VALID_FLAGS` so none can be a silent no-op again).
"""

from __future__ import annotations

from o2t.formal_ir import VALID_FLAGS, flag_poison_smt, smt_and, smt_or
from o2t.validate import ir_model as ir


class Unsupported(Exception):
    """An instruction, type or shape outside the modeled fragment: a sound DECLINE, never a guess."""


# LLVM opcode -> SMT-LIB bit-vector operator.
BIN = {"add": "bvadd", "sub": "bvsub", "mul": "bvmul", "and": "bvand", "or": "bvor",
       "xor": "bvxor", "shl": "bvshl", "lshr": "bvlshr", "ashr": "bvashr",
       "udiv": "bvudiv", "sdiv": "bvsdiv", "urem": "bvurem", "srem": "bvsrem"}

# LLVM's predicate names, as `CmpInst::getPredicateName` spells them.
ICMP = {"eq": "(= {a} {b})", "ne": "(distinct {a} {b})",
        "ult": "(bvult {a} {b})", "ule": "(bvule {a} {b})",
        "ugt": "(bvugt {a} {b})", "uge": "(bvuge {a} {b})",
        "slt": "(bvslt {a} {b})", "sle": "(bvsle {a} {b})",
        "sgt": "(bvsgt {a} {b})", "sge": "(bvsge {a} {b})"}

MINMAX = {"smin": "bvsle", "smax": "bvsge", "umin": "bvule", "umax": "bvuge"}


def const(value: int, width: int) -> str:
    return f"(_ bv{value % (1 << width)} {width})"


def own_poison(name: str, op: str, flags, a: str, b: str, w: int) -> str:
    """Poison introduced by the operation itself, independent of operand poison.

    Three sources, all LLVM-faithful: a declared wrap/exact flag that the operation violates; `or
    disjoint` where the operands share a bit; and a plain shift by at least the bit width, which is
    poison whether or not a flag is present."""
    conds = []
    fl = [f for f in flags if f in VALID_FLAGS.get(op, set())]
    if fl:
        conds.append(flag_poison_smt(op, fl, a, b, w))
    if name == "or" and "disjoint" in flags:
        conds.append(f"(not (= (bvand {a} {b}) (_ bv0 {w})))")
    if name in ("shl", "lshr", "ashr"):
        conds.append(f"(bvuge {b} (_ bv{w} {w}))")
    return smt_or(conds)


def own_ub(name: str, a: str, b: str, w: int) -> str:
    """Undefined behaviour introduced by the operation itself: division by zero, and the signed
    INT_MIN / -1 overflow."""
    conds = []
    if name in ("udiv", "sdiv", "urem", "srem"):
        conds.append(f"(= {b} (_ bv0 {w}))")
    if name in ("sdiv", "srem"):
        conds.append(f"(and (= {a} {const(1 << (w - 1), w)}) (= {b} {const((1 << w) - 1, w)}))")
    return smt_or(conds)


# --- values --------------------------------------------------------------------------------------

def value(v: ir.Value, env: dict, width: int | None = None):
    """An operand -> `(term, width, poison, ub)`.

    A register resolves through `env`; a constant is a definite input. `undef` DECLINES: this model
    has no undef level, and an undef value is not one value (each USE may observe a different one), so
    modeling it as a constant would be a false-proof source rather than an approximation. A literal
    `poison` is representable exactly -- an arbitrary value whose poison bit is true."""
    if v.is_reg:
        name = v.name
        if name not in env:
            raise Unsupported(f"operand {name!r}")
        return env[name]
    if v.kind == "int":
        # A constant carries its OWN type, and that wins over any width hint from the surrounding
        # instruction: `@llvm.abs.i32(i32 %x, i1 true)` has an i1 flag beside an i32 value, and
        # widening the flag to 32 bits turns `true` into 0xFFFFFFFF and silently breaks the model.
        w = v.type.bits if (v.type and v.type.is_int()) else width
        if w is None:
            raise Unsupported("untyped integer constant")
        return const(v.int_value, w), w, "false", "false"
    if v.is_undef:
        raise Unsupported("undef operand (this model has no undef level; each use may differ)")
    if v.is_poison:
        w = width if width is not None else (v.type.bits if v.type and v.type.is_int() else None)
        if w is None:
            raise Unsupported("untyped poison")
        return f"poison_{w}", w, "true", "false"
    if v.kind == "zeroinit":
        w = width if width is not None else (v.type.bits if v.type and v.type.is_int() else None)
        if w is None:
            raise Unsupported("untyped zeroinitializer")
        return const(0, w), w, "false", "false"
    raise Unsupported(f"operand kind {v.kind!r}")


def int_width(t: ir.Type) -> int:
    if not t.is_int():
        raise Unsupported(f"non-integer type {t}")
    return t.bits


def bit_width(t: ir.Type) -> int | None:
    """The number of BITS in a scalar type, or None if this model has no bit view of it.

    Integers and floating-point types both have one, and LLVM reports it (the dumper carries
    `getPrimitiveSizeInBits()` for FP, the same accessor `bitcast` legality is decided by). This is
    NOT `int_width`: it says how wide the value is, not that it may be used as an integer. Only
    `bitcast` consults it, which is what keeps a float confined to being bits."""
    if t is None:
        return None
    return t.bits if t.kind in ("int", "float") else None


# --- intrinsics ----------------------------------------------------------------------------------
# These models are RELOCATED VERBATIM from scalar_ir, not re-derived. Each is lli-validated
# (intrinsics_ir_fixture pins hand-computed ground truth AND checks real LLVM agrees), and rewriting
# them would create exactly the duplicate model this module exists to remove -- a first attempt at
# re-deriving them inverted the ctlz/cttz bit order, which is precisely the kind of silent divergence
# a second implementation introduces.

def _p(ops):                                          # combined operand poison / ub
    return smt_or([o[2] for o in ops]), smt_or([o[3] for o in ops])


def _i_ctpop(ops, w):
    a, (p, u) = ops[0][0], _p(ops)
    bits = [f"((_ zero_extend {w - 1}) ((_ extract {i} {i}) {a}))" for i in range(w)]
    return f"(bvadd {' '.join(bits)})", w, p, u


def _i_abs(ops, w):
    if len(ops) != 2:
        raise Unsupported("abs arity")
    a, np = ops[0][0], ops[1][0]                       # np = the i1 is_int_min_poison flag
    _, u = _p(ops[:1])
    val = f"(ite (bvslt {a} (_ bv0 {w})) (bvneg {a}) {a})"
    pois = smt_or([ops[0][2], f"(and (= {np} (_ bv1 1)) (= {a} {const(1 << (w - 1), w)}))"])
    return val, w, pois, u


def _i_ctz(ops, w, leading):
    """ctlz/cttz as a bounded nested-ite over the bits: the position of the highest (ctlz) or lowest
    (cttz) set bit; W if the input is zero. Poison when the is_zero_poison flag is set and x == 0."""
    if len(ops) != 2:
        raise Unsupported("ct{l,t}z arity")
    a, izp = ops[0][0], ops[1][0]
    expr = const(w, w)                                # x == 0 -> W
    order = range(w) if leading else range(w - 1, -1, -1)   # leading: MSB ends outermost
    for i in order:
        val = (w - 1 - i) if leading else i            # count of leading/trailing zeros if bit i is it
        expr = f"(ite (= ((_ extract {i} {i}) {a}) #b1) {const(val, w)} {expr})"
    pois = smt_or([ops[0][2], f"(and (= {izp} (_ bv1 1)) (= {a} (_ bv0 {w})))"])
    return expr, w, pois, ops[0][3]


def _i_funnel(ops, w, right):
    if len(ops) != 3:
        raise Unsupported("funnel-shift arity")
    a, b, c = ops[0][0], ops[1][0], ops[2][0]
    p, u = _p(ops)
    s = f"(bvurem {c} (_ bv{w} {w}))"
    cat = f"(concat {a} {b})"
    if right:
        return f"((_ extract {w - 1} 0) (bvlshr {cat} ((_ zero_extend {w}) {s})))", w, p, u
    return f"((_ extract {2 * w - 1} {w}) (bvshl {cat} ((_ zero_extend {w}) {s})))", w, p, u


def _i_uadd_sat(ops, w):
    a, b = ops[0][0], ops[1][0]
    p, u = _p(ops)
    s = f"(bvadd {a} {b})"
    return f"(ite (bvult {s} {a}) (bvnot (_ bv0 {w})) {s})", w, p, u


def _i_usub_sat(ops, w):
    a, b = ops[0][0], ops[1][0]
    p, u = _p(ops)
    return f"(ite (bvult {a} {b}) (_ bv0 {w}) (bvsub {a} {b}))", w, p, u


def _i_s_sat(ops, w, sub):
    """s{add,sub}.sat: compute in w+1 bits (no overflow), then clamp to [INT_MIN, INT_MAX]."""
    a, b = ops[0][0], ops[1][0]
    p, u = _p(ops)
    a1, b1 = f"((_ sign_extend 1) {a})", f"((_ sign_extend 1) {b})"
    s = f"({'bvsub' if sub else 'bvadd'} {a1} {b1})"
    imax_w, imin_w = const((1 << (w - 1)) - 1, w), const(1 << (w - 1), w)   # 0x7f.. and 0x80..
    imax_e, imin_e = f"((_ sign_extend 1) {imax_w})", f"((_ sign_extend 1) {imin_w})"
    lo = f"((_ extract {w - 1} 0) {s})"
    return f"(ite (bvsgt {s} {imax_e}) {imax_w} (ite (bvslt {s} {imin_e}) {imin_w} {lo}))", w, p, u


# Note: `bswap` is deliberately NOT built in -- it is the worked example for the lli-gated
# self-enrichment path (enrich_fixture), which demonstrates growing the vocabulary from outside.
INTRINSICS = {
    "ctpop": _i_ctpop, "abs": _i_abs,
    "ctlz": lambda ops, w: _i_ctz(ops, w, leading=True),
    "cttz": lambda ops, w: _i_ctz(ops, w, leading=False),
    "fshl": lambda ops, w: _i_funnel(ops, w, right=False),
    "fshr": lambda ops, w: _i_funnel(ops, w, right=True),
    "uadd.sat": _i_uadd_sat, "usub.sat": _i_usub_sat,
    "sadd.sat": lambda ops, w: _i_s_sat(ops, w, sub=False),
    "ssub.sat": lambda ops, w: _i_s_sat(ops, w, sub=True),
}


def intrinsic_name(callee: str | None) -> str | None:
    """`@llvm.uadd.sat.i32` -> `uadd.sat`; None if this is not a modeled intrinsic call."""
    if not callee:
        return None
    name = callee.lstrip("@")
    if not name.startswith("llvm."):
        return None
    body = name[len("llvm."):]
    parts = body.split(".")
    for take in (2, 1):                      # `uadd.sat.i32` then `ctpop.i32`
        if len(parts) > take:
            cand = ".".join(parts[:take])
            if cand in INTRINSICS or cand in MINMAX:
                return cand
    return None


# --- the instruction dispatcher ------------------------------------------------------------------

def evaluate(inst: ir.Instruction, env: dict, ctx: dict | None = None) -> None:
    """Interpret one instruction into `env[result] = (term, width, poison, ub)`.

    `ctx` carries the pieces that are not the instruction's own business: `side`/`fresh` for the
    nondeterministic choice `freeze` makes, `extra_ops` for lli-validated enrichments, and `mem` for
    the local-alloca model. An unmodeled opcode raises `Unsupported` -- and because the front-end is a
    real parse, that decline is on the OPCODE, not on a regex quietly failing to match."""
    ctx = ctx or {}
    dst = inst.result
    op = inst.op

    if op in BIN:
        w = int_width(inst.type)
        a, _, ap, au = value(inst.operands[0], env, w)
        b, _, bp, bu = value(inst.operands[1], env, w)
        smt_op = BIN[op]
        poison = smt_or([ap, bp, own_poison(op, smt_op, inst.flags, a, b, w)])
        # A poison divisor is UB, not merely poison: it controls whether the division traps.
        div_ub = bp if op in ("udiv", "sdiv", "urem", "srem") else "false"
        ub = smt_or([au, bu, div_ub, own_ub(op, a, b, w)])
        env[dst] = (f"({smt_op} {a} {b})", w, poison, ub)
        return

    if op == "icmp":
        pred = inst.pred
        if pred not in ICMP:
            raise Unsupported(f"icmp predicate {pred!r}")
        w = int_width(inst.operands[0].type) if inst.operands[0].type.is_int() else None
        if w is None:
            raise Unsupported("icmp on a non-integer type")
        a, _, ap, au = value(inst.operands[0], env, w)
        b, _, bp, bu = value(inst.operands[1], env, w)
        env[dst] = (f"(ite {ICMP[pred].format(a=a, b=b)} {const(1, 1)} {const(0, 1)})",
                    1, smt_or([ap, bp]), smt_or([au, bu]))
        return

    if op == "select":
        w = int_width(inst.type)
        c, _, cp, cu = value(inst.operands[0], env, 1)
        t, _, tp, tu = value(inst.operands[1], env, w)
        f, _, fp, fu = value(inst.operands[2], env, w)
        picks_t = f"(= {c} {const(1, 1)})"
        # The condition's poison always propagates; only the SELECTED arm's poison reaches the result.
        arm = tp if tp == fp else f"(ite {picks_t} {tp} {fp})" if "false" not in (tp, fp) \
            else smt_and([picks_t, tp]) if fp == "false" else smt_and([f"(not {picks_t})", fp])
        env[dst] = (f"(ite {picks_t} {t} {f})", w, smt_or([cp, arm]), smt_or([cu, tu, fu]))
        return

    if op in ("zext", "sext", "trunc"):
        src = inst.src_type or inst.operands[0].type
        src_w, dst_w = int_width(src), int_width(inst.type)
        v, _, vp, vu = value(inst.operands[0], env, src_w)
        if op == "trunc":
            env[dst] = (f"((_ extract {dst_w - 1} 0) {v})", dst_w, vp, vu)
        else:
            ext = "zero_extend" if op == "zext" else "sign_extend"
            env[dst] = (f"((_ {ext} {dst_w - src_w}) {v})", dst_w, vp, vu)
        return

    if op == "bitcast":
        # A bitcast REINTERPRETS bits and changes no value, so it is the identity on the term --
        # provided the two types really are the same number of bits, which LLVM guarantees for valid
        # IR but which is checked here rather than assumed (an unequal or unknown pair declines).
        #
        # This is what lets `bitcast float %b to i32` be modelled with NO floating-point semantics
        # whatsoever: the parameter is carried as an opaque bitvector and every bit pattern is a
        # valid float, so the model is exact rather than approximate. Soundness rests on a float
        # value being unable to reach anywhere that would treat it as an FP VALUE, and it cannot:
        # `ret` requires an integer type, `int_width` declines on every non-integer, an observable
        # call's arguments must be integers, and any real FP operation declines on its opcode. A
        # VECTOR bitcast declines -- reinterpreting lanes needs a lane<->flat-bits correspondence
        # this scalar model does not have, and guessing one is how a false proof gets in.
        src_t = inst.src_type or inst.operands[0].type
        sw, dw = bit_width(src_t), bit_width(inst.type)
        if sw is None or dw is None or sw != dw:
            raise Unsupported(f"bitcast {src_t} -> {inst.type}")
        v, _, vp, vu = value(inst.operands[0], env, sw)
        env[dst] = (v, dw, vp, vu)
        return

    if op == "freeze":
        # See the long note in scalar_ir: the nondeterministic choice is EXISTENTIAL on the target
        # (a free constant) and UNIVERSAL on the source, which QF_BV cannot express -- so a
        # source-side freeze declines, even when the operand looks poison-free, because parameters
        # are modeled as definite while LLVM lets an argument be `undef` unless `noundef`.
        w = int_width(inst.type)
        v, _, vp, vu = value(inst.operands[0], env, w)
        if ctx.get("side") != "target" or ctx.get("fresh") is None:
            # A source-side freeze is the IDENTITY exactly when its operand has no freedom to
            # collapse -- neither poison nor undef. Then the universal quantifier has a one-element
            # domain and disappears, so no new machinery is needed for this case.
            #
            # Both halves are required and neither is sufficient. `freeze %x` on a plain parameter
            # is NOT the identity (LLVM lets an argument be `undef` unless `noundef`, and freeze is
            # precisely the instruction that observes that -- reference Alive2 refutes removing it),
            # and `freeze` over a poison-capable value is not the identity either. Both still
            # decline, and only the doubly-free case is decided.
            operand = inst.operands[0]
            free = ctx.get("undef_free") or set()
            undef_free = operand.name in free if operand.is_reg else not operand.is_undef
            if vp == "false" and undef_free:
                env[dst] = (v, w, "false", vu)
                return
            # Otherwise the choice is real, and it is UNIVERSAL: the target must differ from EVERY
            # value the source freeze could have picked. That is expressible once the caller is
            # willing to quantify -- it records the variable as source-side and binds it in a
            # `forall` around the whole refutation. A caller that cannot (no `fresh` list) still
            # declines, so nothing that used to be quantifier-free silently becomes quantified.
            if ctx.get("fresh") is None:
                raise Unsupported("freeze in the source over a value that may be poison or undef "
                                  "(its nondeterministic choice is universal, and this caller does "
                                  "not quantify)")
            fresh = ctx["fresh"]
            name = f"frz{len(fresh)}_{ctx.get('side', 'source')}"
            fresh.append((name, w))
            env[dst] = (f"(ite {vp} {name} {v})", w, "false", vu)
            return
        fresh = ctx["fresh"]
        name = f"frz{len(fresh)}_{ctx['side']}"
        fresh.append((name, w))
        env[dst] = (f"(ite {vp} {name} {v})", w, "false", vu)
        return

    if op == "call":
        intr = intrinsic_name(inst.callee)
        if intr in MINMAX:
            w = int_width(inst.type)
            a, _, ap, au = value(inst.args[0], env, w)
            b, _, bp, bu = value(inst.args[1], env, w)
            env[dst] = (f"(ite ({MINMAX[intr]} {a} {b}) {a} {b})", w, smt_or([ap, bp]), smt_or([au, bu]))
            return
        if intr in INTRINSICS:
            w = int_width(inst.type)
            ops = [value(a, env, w if a.type.is_int() else None) for a in inst.args]
            env[dst] = INTRINSICS[intr](ops, w)
            return
        # A VOID call to a function with no body -- overwhelmingly `call void @use(i32 %x)`, which
        # LLVM's own tests use to keep a value alive so the fold under test is not deleted by DCE.
        # It cannot change the value this function returns: it returns nothing, and with no pointer
        # in the scalar fragment there is nothing for it to write through. What it CAN do is be
        # observable, so it is not simply ignored -- it is recorded as an EFFECT, and the caller
        # requires the target to make the same calls with the same argument values. Dropping one, or
        # passing it something different, is then a refutation rather than something unnoticed.
        #
        # Only void, and only when the caller asked for effects. A call whose RESULT is used would
        # need the callee to be a function of its arguments, and a bodiless declaration promises no
        # such thing -- modelling it as one would assume purity LLVM does not give.
        effects = ctx.get("effects")
        callee = inst.callee
        # ...but NOT an LLVM intrinsic. `@llvm.*` is not an unknown external function: it has
        # semantics LLVM defines, and an unmodelled one must decline on its NAME rather than be
        # waved through as an opaque effect. `llvm.assume` is the case that proves it -- it does not
        # "do something unknown", it ESTABLISHES that its argument is true, and treating it as opaque
        # drops that fact, so a target simplified USING the assumption is refuted on the inputs the
        # assumption excluded. Three false refutations in LLVM's own tests, immediately.
        if (effects is not None and callee and inst.result is None and not intr
                and not callee.lstrip("@").startswith("llvm.")):
            fn = ctx["module"].function(callee.lstrip("@")) if ctx.get("module") else None
            if fn is None or fn.is_declaration:
                args = []
                for a in inst.args:
                    if not (a.type and a.type.is_int()):
                        raise Unsupported(f"call to {callee} with a non-integer argument")
                    av, aw, ap, au = value(a, env, int_width(a.type))
                    args.append((av, aw, ap, au))
                effects.append((callee, args))
                return
        raise Unsupported(f"call to {inst.callee or '<indirect>'}")

    raise Unsupported(f"instruction {op!r}")
