#!/usr/bin/env python3
r"""Vectors: whole-function TV via a LANE MODEL (fixed-width, element-wise + shuffle/extract/insert).

A vector value is modeled as a LIST of per-lane `(value, poison)` pairs (a scalar is a 1-lane list),
so element-wise operations lower lane-by-lane and the cross-lane instructions -- `extractelement`,
`insertelement`, `shufflevector` -- are exact index/permutation operations on the lists. The
obligation is Alive2-style REFINEMENT applied per lane -- wherever a source lane is defined, the
target's lane must be defined and equal -- which is the same thing `scalar_ir` discharges. So a vector
fold (`and <2 x i32> %x, <-1,-1> -> %x`) proves, a wrong lane refutes, a fold that EXPLOITS poison
(`ashr x,x -> 0`) proves, and one that INTRODUCES it (adding `exact`) refutes even though every lane
value is identical.

Scope: fixed-width `<N x iW>` vectors; lane-wise binops/icmp; `extractelement`/`insertelement` with a
CONSTANT index; `shufflevector` with a constant, fully-defined mask (an undef/poison mask lane declines);
integer element constants / `zeroinitializer` / `splat`. Single-BB. Variable indices, reductions, FP and
memory decline (a sound decline, never a mis-model); scalable vectors are handled by the per-lane model
below.

`undef` is modelled, but only where its freedom is not SHARED: it is named fresh at every read of a
literal (each use may observe a different value), and a register that carries that freedom is declined
on its second read rather than modelled as agreeing with itself. Sharing it was a false proof, not a
precision gap -- see `_lanes_of`.

The module reads LLVM's OWN parse (`ir_model`), not instruction text. The reader it replaces did
string surgery on types -- `<(\d+) x i(\d+)>` for the lane count and width, a comma split for a vector
literal, a regex for a shuffle mask -- and its signature reader carried the same truncation bug the
rest of Track B had. Lane counts, element types, shuffle masks (with -1 for an undef lane) and splats
are all structured data in the parse, so the whole file is now free of regexes.
"""

from __future__ import annotations

import subprocess

from o2t.validate import ir_model as ir
from o2t.validate import scalar_ir as si
from o2t.validate import semantics as sem



def _signature(ll_text, func):
    r"""[(type-string, name)] for the parameters, from the parse. The regex this replaces captured
    the parenthesised list with `([^)]*)` and split it on commas, so a parameter attribute containing either -- `ptr byval({ i32,
    i64 }) %s` is valid LLVM 18 -- truncated the list and silently dropped every later parameter."""
    fn = ir.parse(ll_text).function(func)
    if fn is None:
        return []
    return [(str(p.type), p.name) for p in fn.params]


# --- the fixed-width lane model, over LLVM's own parse ------------------------------------------
# The reader this replaces did string surgery on types: `<(\d+) x i(\d+)>` for the lane count and
# width, a comma split over `<i32 1, i32 2>` for a vector literal, and a regex over `<i32 0, i32 5>`
# for a shuffle mask. All three are structured data in the parse (`type.n`, `type.elem.bits`,
# `Value.elements`, `Instruction.mask`, where LLVM already reports -1 for an undef lane).
#
# The poison flags the old binop regex discarded in a non-capturing group are now USED. For a while
# this model ignored nsw/nuw/exact/disjoint and leaned on two whole-function guards instead -- refuse
# to prove if the target could be poison anywhere, refuse to refute if the source could. Both were
# blunt, because poison in LLVM is PER ELEMENT: one flagged lane disqualified an entire function. The
# lane model now carries a poison term beside each lane's value and discharges the same refinement
# obligation the scalar validator does, so the guards are gone from this path (the SCALABLE model
# below is still value-equality and still needs them).

def _free(ctx, w, kind):
    """A fresh per-lane value for an `undef` or `poison` element, or for a `freeze`'s choice.

    Returns `(term, poison)` for an element and the bare NAME for a freeze, whose poison the caller
    sets itself (a freeze is poison nowhere, whatever its operand was).

    `poison` is an arbitrary value whose poison bit is SET; `undef` is an arbitrary value that is
    perfectly defined. Neither is one value, so neither can be a constant -- and the freedom is not
    transparent either: `and undef, 0` is 0, not "anything", so a lane cannot simply be dropped from
    the obligation because an undef reached it. The choice is named instead, and the SIDE decides its
    quantifier: a TARGET choice is existential (free, so the solver may expose a miscompile) while a
    SOURCE choice is universal (the target must match EVERY value the source could have produced).
    """
    if ctx is None or ctx.get("fresh") is None:
        # A caller that cannot quantify still declines, so nothing that used to be quantifier-free
        # silently becomes quantified -- the same rule `semantics.py` applies to a scalar freeze.
        raise sem.Unsupported(f"{kind} choice (this caller does not quantify)"
                              if kind == "freeze" else f"vector element {kind}")
    fresh = ctx["fresh"]
    name = f"vfr{len(fresh)}_{ctx.get('side', 'source')}"
    fresh.append((name, w))
    if kind == "freeze":
        # NOT undef freedom: a freeze picks ONE value and every use sees it, so this term may be
        # shared across uses. Only `undef` gets the per-use rule.
        return name
    if kind == "undef":
        # Only UNDEF's freedom is per-use (see `_lanes_of`). A poison choice may be shared freely:
        # its poison BIT is set, and every operation that reads it propagates that bit, so the value
        # term underneath is never what the obligation turns on.
        ctx["undef_hit"] = True
    return name, ("true" if kind == "poison" else "false")


def _lanes_of(v, n, w, env, ctx=None):
    """A scalar/vector operand -> a list of n `(term, poison)` lanes of width w.

    A literal `undef` is named FRESH at every read, which is exactly its semantics -- each use may
    observe a different value. But an SSA register that CARRIES that freedom (`%u = and undef, -1`)
    is one term in `env`, so reading it twice models the two uses as agreeing, and they need not.
    That is not a precision gap, it is unsound in the proving direction: it shrinks the TARGET's
    behaviour set, and a target with fewer behaviours is easier to prove a refinement. `xor %u, %u`
    modelled 0, so `ret zeroinitializer -> xor %u, %u` PROVED here while reference Alive2 refutes it
    with a witness (lane 1: source 0, target 1).

    Per-use instantiation is a change to the value model, not to this read (an undef value is not one
    term at all). So an undef-tainted register is DECLINED on its second read instead -- a sound
    non-answer. A single read is exactly one observation of undef and stays decided, and the taint is
    transitive, so a register computed from a tainted one is tainted too."""
    if v.is_reg:
        if v.name not in env:
            raise sem.Unsupported(f"operand {v.name!r}")
        ls, _ = env[v.name]
        if len(ls) != n:
            raise sem.Unsupported("lane-count mismatch")
        if ctx is not None and v.name in ctx.get("undef_regs", ()):
            ctx["undef_hit"] = True
            reads = ctx.setdefault("undef_reads", {})
            reads[v.name] = reads.get(v.name, 0) + 1
            if reads[v.name] > 1:
                raise sem.Unsupported(f"undef-derived {v.name!r} is used more than once (each use may "
                                      "observe a different value; this model has one term per value)")
        return ls
    if v.kind == "zeroinit":
        return [(sem.const(0, w), "false")] * n
    if v.kind == "splat":                      # every lane the same value
        elem = v.splat_elem
        if elem is None or elem.kind != "int":
            raise sem.Unsupported("non-integer splat")
        return [(sem.const(elem.int_value, w), "false")] * n
    if v.kind == "vector":
        elems = v.elements
        if len(elems) != n:
            raise sem.Unsupported("vector-literal arity")
        out = []
        for e in elems:
            if e.kind == "int":
                out.append((sem.const(e.int_value, w), "false"))
            elif e.is_undef or e.is_poison:
                out.append(_free(ctx, w, "poison" if e.is_poison else "undef"))
            else:
                raise sem.Unsupported(f"vector element {e.kind}")
        return out
    if v.is_undef or v.is_poison:
        return [_free(ctx, w, "poison" if v.is_poison else "undef") for _ in range(n)]
    if v.kind == "int" and n == 1:
        return [(sem.const(v.int_value, w), "false")]
    raise sem.Unsupported(f"operand {v.kind}")


def _vshape(t):
    """(lanes, width) for a vector or scalar integer type."""
    if t.kind == "vector" and not t.scalable and t.elem and t.elem.is_int():
        return t.n, t.elem.bits
    if t.is_int():
        return 1, t.bits
    raise sem.Unsupported(f"type {t}")


def _step(inst, env, ctx):
    """Model one instruction and PROPAGATE the undef taint onto its result.

    `_lanes_of`/`_free` set `undef_hit` whenever a read brought undef freedom in; whatever this
    instruction defines therefore carries it too, so the per-use rule keeps applying downstream."""
    ctx["undef_hit"] = False
    _vec_instr(inst, env, ctx)
    if ctx.pop("undef_hit", False) and inst.result:
        ctx.setdefault("undef_regs", set()).add(inst.result)


def _vec_instr(inst, env, ctx=None):
    op = inst.op
    ub = ctx.setdefault("ub", []) if ctx is not None else []
    if op in sem.BIN:
        n, w = _vshape(inst.type)
        a = _lanes_of(inst.operands[0], n, w, env, ctx)
        b = _lanes_of(inst.operands[1], n, w, env, ctx)
        smt = sem.BIN[op]
        lanes = []
        for i in range(n):
            (av, ap), (bv, bp) = a[i], b[i]
            # the SHARED poison/UB rules, per lane -- not a second reading of LLVM. A duplicate model
            # is what round 6 of the 2026-07 review found a false proof inside.
            lanes.append((f"({smt} {av} {bv})",
                          si.smt_or([ap, bp, sem.own_poison(op, smt, inst.flags, av, bv, w)])))
            if op in ("udiv", "sdiv", "urem", "srem"):
                # a poison DIVISOR is UB, not merely poison: it decides whether the division traps
                ub.append(si.smt_or([bp, sem.own_ub(op, av, bv, w)]))
        env[inst.result] = (lanes, w)
        return
    if op == "icmp":
        if inst.pred not in sem.ICMP:
            raise sem.Unsupported(f"icmp predicate {inst.pred!r}")
        n, w = _vshape(inst.operands[0].type)
        a = _lanes_of(inst.operands[0], n, w, env, ctx)
        b = _lanes_of(inst.operands[1], n, w, env, ctx)
        env[inst.result] = ([(f"(ite {sem.ICMP[inst.pred].format(a=a[i][0], b=b[i][0])} "
                              f"{sem.const(1, 1)} {sem.const(0, 1)})",
                              si.smt_or([a[i][1], b[i][1]])) for i in range(n)], 1)
        return
    # ELEMENT-WISE and therefore exactly what a lane model is for: each lane's result depends only on
    # that lane's inputs, so the vector case is the scalar case repeated. Measured over LLVM 18's
    # InstCombine tests these three were the largest decline causes left in this validator by a wide
    # margin -- `select` 60, `zext` 28, `sext` 9 -- because every other vector shape it handles tends
    # to be reached THROUGH one of them.
    if op == "select":
        n, w = _vshape(inst.type)
        # the condition is either one i1 (a scalar select over vectors) or one i1 PER LANE
        cn, cw = _vshape(inst.operands[0].type)
        if cw != 1:
            raise sem.Unsupported(f"select condition of width {cw}")
        if cn not in (1, n):
            raise sem.Unsupported("select condition lane count differs from the result")
        c = _lanes_of(inst.operands[0], cn, 1, env, ctx)
        a = _lanes_of(inst.operands[1], n, w, env, ctx)
        b = _lanes_of(inst.operands[2], n, w, env, ctx)
        lanes = []
        for i in range(n):
            cv, cp = c[i if cn == n else 0]
            test = f"(= {cv} {sem.const(1, 1)})"
            # a select is poison if its condition is, or if the arm it SELECTS is -- the unselected
            # arm's poison does not propagate, which is the whole reason `freeze` exists
            lanes.append((f"(ite {test} {a[i][0]} {b[i][0]})",
                          si.smt_or([cp, f"(ite {test} {a[i][1]} {b[i][1]})"])))
        env[inst.result] = (lanes, w)
        return
    if op in ("zext", "sext", "trunc"):
        n, w = _vshape(inst.type)
        sn, sw = _vshape(inst.src_type or inst.operands[0].type)
        if sn != n:
            raise sem.Unsupported(f"{op} changes the lane count")
        a = _lanes_of(inst.operands[0], n, sw, env, ctx)
        if op == "trunc":
            # NARROWING, and the lane's own poison is all there is: a truncation introduces none of
            # its own. That is not an assumption about LLVM 18 -- its parser REJECTS `trunc nuw`
            # ("expected type"), and this module reads IR through that same parser, so the flag
            # cannot reach here. Same reading as the scalar model; a second reading of LLVM is what
            # round 6 of the 2026-07 review found a false proof inside.
            if sw <= w:
                raise sem.Unsupported(f"trunc from i{sw} to i{w} does not narrow")
            env[inst.result] = ([(f"((_ extract {w - 1} 0) {a[i][0]})", a[i][1]) for i in range(n)], w)
            return
        if sw >= w:
            raise sem.Unsupported(f"{op} from i{sw} to i{w} does not widen")
        kind = "zero_extend" if op == "zext" else "sign_extend"
        env[inst.result] = ([(f"((_ {kind} {w - sw}) {a[i][0]})", a[i][1]) for i in range(n)], w)
        return
    if op == "call":
        # THE SAME RULE `scalar_ir` USES, not a second reading of it. A VOID call to a bodiless
        # declaration -- `call void @use(<2 x i32> %x)`, which LLVM's tests use so DCE cannot delete
        # the value a fold is about -- cannot change what this function returns, but it IS
        # observable, so it is recorded as an effect and the target must make the same calls.
        #
        # NOT an `@llvm.*` intrinsic: that is not an unknown external function, it has semantics LLVM
        # defines, and an unmodelled one must decline on its NAME. `llvm.assume` is the case that
        # proves it -- it ESTABLISHES its argument rather than doing something unknown, and treating
        # it as opaque refuted three correct transforms in LLVM's own tests.
        effects = ctx.get("effects") if ctx is not None else None
        callee = inst.callee
        if (effects is not None and callee and inst.result is None and not inst.indirect
                and not sem.intrinsic_name(callee)
                and not callee.lstrip("@").startswith("llvm.")):
            fn = ctx["module"].function(callee.lstrip("@")) if ctx.get("module") else None
            if fn is None or fn.is_declaration:
                args = []
                for a in inst.args:
                    an, aw = _vshape(a.type)      # a ptr/FP argument declines here, as it should
                    args.append((_lanes_of(a, an, aw, env, ctx), an, aw))
                effects.append((callee, args))
                return
        raise sem.Unsupported(f"call to {callee or '<indirect>'}")
    if op == "freeze":
        # Lane by lane, the encoding scalar_ir uses: a FRESH value exactly where the operand is
        # poison, and the result is poison nowhere. The choice's quantifier comes from the side, as
        # for an undef element -- universal on the source (the target must match EVERY value the
        # freeze could have picked), free on the target.
        #
        # This is only sound because vector PARAMETERS carry a poison flag (see `_vtranslate`).
        # Without one, every lane's poison term is `false`, freeze collapses to the identity, and
        # `freeze %x -> %x` PROVES -- which reference Alive2 refutes, witness `<3 [based on undef],
        # poison>`. The two go in together or not at all.
        n, w = _vshape(inst.type)
        lanes = []
        for v, p in _lanes_of(inst.operands[0], n, w, env, ctx):
            if p == "false":
                # No represented freedom to collapse, so the quantifier has a one-element domain and
                # disappears. The operand's UNDEF-ness is not represented for a parameter, which
                # under-approximates the source's freedom -- sound in the proving direction (a
                # smaller source set only makes refinement harder) and matched on the target side,
                # where an undef literal is already a free choice and freeze of it is the same set.
                lanes.append((v, "false"))
            else:
                lanes.append((f"(ite {p} {_free(ctx, w, 'freeze')} {v})", "false"))
        env[inst.result] = (lanes, w)
        # ...AND THE RESULT CARRIES NO PER-USE FREEDOM, whatever the operand had. Freeze is the
        # instruction that COLLAPSES undef into one fixed value, so its uses legitimately agree --
        # which is the whole point of it. Letting the taint propagate here declined five real
        # functions in LLVM's own tests (`and_freeze_undef_multipleuses` and friends, where `%f =
        # freeze i32 undef` is used twice and reference Alive2 confirms the fold is correct). The
        # per-use rule is about undef reaching a use UNFROZEN; past a freeze it has no force.
        if ctx is not None:
            ctx["undef_hit"] = False
        return
    if op == "extractelement":
        n, w = _vshape(inst.operands[0].type)
        idx = inst.operands[1]
        if idx.kind != "int":
            raise sem.Unsupported("variable extractelement index")
        k = idx.int_value
        ls = _lanes_of(inst.operands[0], n, w, env, ctx)
        if k < 0 or k >= n:
            raise sem.Unsupported("extractelement index out of range")
        env[inst.result] = ([ls[k]], w)
        return
    if op == "insertelement":
        n, w = _vshape(inst.type)
        idx = inst.operands[2]
        if idx.kind != "int":
            raise sem.Unsupported("variable insertelement index")
        k = idx.int_value
        ls = list(_lanes_of(inst.operands[0], n, w, env, ctx))
        elt = _lanes_of(inst.operands[1], 1, w, env, ctx)[0]
        if k < 0 or k >= n:
            raise sem.Unsupported("insertelement index out of range")
        ls[k] = elt
        env[inst.result] = (ls, w)
        return
    if op == "shufflevector":
        n, w = _vshape(inst.operands[0].type)
        a = _lanes_of(inst.operands[0], n, w, env, ctx)
        b = _lanes_of(inst.operands[1], n, w, env, ctx)
        pool = a + b
        out = []
        for idx in inst.mask:
            if idx < 0:                                # LLVM reports -1 for an undef/poison lane
                raise sem.Unsupported("shuffle mask undef lane")
            if idx >= len(pool):
                raise sem.Unsupported("shuffle index out of range")
            out.append(pool[idx])
        env[inst.result] = (out, w)
        return
    raise sem.Unsupported(f"instruction {op!r}")


def _vtranslate(ll_text, func, side="source", fresh=None, effects=None):
    """Single-BB vector function -> (result lanes, lane width, param declarations, ub, poison flags)."""
    module = ir.parse(ll_text)
    fn = module.function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    if len(fn.blocks) > 1:
        raise sem.Unsupported("multi-block")
    env, decls, pflags = {}, [], []
    ctx = {"side": side, "fresh": fresh, "ub": [], "undef_regs": set(), "undef_reads": {},
           "effects": effects, "module": module}
    # A PARAMETER MAY ARRIVE POISON, per lane. LLVM only promises otherwise with `noundef`, and
    # modelling a parameter as definite is what makes `freeze %x -> %x` look like the identity. The
    # flag is part of the INPUT, so it needs no quantifier: it is one Bool per lane, SHARED by both
    # sides (the names derive from the parameter, and the two sides have the same parameters), which
    # is exactly the shape `scalar_ir.param_poison_flag` has.
    def _flag(name, noundef):
        if noundef:
            return "false"
        pflags.append(f"{name}?p")
        return f"{name}?p"
    for p in fn.params:
        t = p.type
        nu = p.noundef                      # declared definite -> no flag, and freeze is the identity
        if t.kind == "vector" and not t.scalable and t.elem and t.elem.is_int():
            ls = [f"{p.name}!{i}" for i in range(t.n)]
            decls += [(lane, t.elem.bits) for lane in ls]
            env[p.name] = ([(lane, _flag(lane, nu)) for lane in ls], t.elem.bits)
        elif t.is_int():
            decls.append((p.name, t.bits))
            env[p.name] = ([(p.name, _flag(p.name, nu))], t.bits)
        else:
            raise sem.Unsupported(f"parameter type {t}")
    ctx["pflags"] = pflags
    for inst in fn.blocks[0].instructions:
        if inst.op == "ret":
            if not inst.operands:
                raise sem.Unsupported("no vector/scalar ret")
            n, w = _vshape(inst.operands[0].type)
            return (_lanes_of(inst.operands[0], n, w, env, ctx), w, decls,
                    si.smt_or(ctx["ub"]), pflags)
        _step(inst, env, ctx)
    raise sem.Unsupported("no vector/scalar ret")


# --- the scalable-vector model, over the same parse ----------------------------------------------
# A `<vscale x N x iW>` value is ONE symbolic per-lane term. Because element-wise ops do not cross
# lanes, proving the lanes equal for an unconstrained lane index proves it for ALL lanes -- and any
# CROSS-lane operation (extract/insert/shuffle/reduce) is simply not modeled here, so it declines and
# the per-lane abstraction stays sound. The parse removes the `<vscale x N x iW>` regex and, with it,
# the separate `splat (iW C)` spelling the text reader had to special-case.

def _sv_width(t):
    """The lane width of a scalable vector, or the width of a plain integer."""
    if t.kind == "vector" and t.scalable and t.elem and t.elem.is_int():
        return t.elem.bits
    if t.is_int():
        return t.bits
    raise sem.Unsupported(f"type {t}")


def _sv_lane(v, w, env):
    """A scalable-vector operand -> its value at THE symbolic lane. A splat/zeroinitializer is that
    constant at every lane; a vector register is its per-lane symbol; a scalar literal is itself."""
    if v.is_reg:
        if v.name not in env:
            raise sem.Unsupported(f"scalable operand {v.name!r}")
        return env[v.name][0]
    if v.kind == "zeroinit":
        return sem.const(0, w)
    if v.kind == "splat":                      # `splat (iW C)` -- LLVM reports the repeated value
        elem = v.splat_elem
        if elem is None or elem.kind != "int":
            raise sem.Unsupported("non-integer splat")
        return sem.const(elem.int_value, w)
    if v.kind == "vector":                     # an enumerated constant vector that is uniform
        elems = v.elements
        if not elems or any(e.kind != "int" or e.int_value != elems[0].int_value for e in elems):
            raise sem.Unsupported("non-splat scalable constant")
        return sem.const(elems[0].int_value, w)
    if v.kind == "int":
        return sem.const(v.int_value, w)
    raise sem.Unsupported(f"scalable operand {v.kind}")


def _svtranslate(ll_text, func):
    """Translate a scalable-vector function at ONE symbolic lane -> (lane term, width, declarations)."""
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    if len(fn.blocks) > 1:
        raise sem.Unsupported("multi-block")
    env, decls = {}, []
    for p in fn.params:
        w = _sv_width(p.type)
        decls.append((p.name, w))
        env[p.name] = (p.name, w)              # a scalable vector's value AT the symbolic lane
    for inst in fn.blocks[0].instructions:
        if inst.op == "ret":
            if not inst.operands:
                raise sem.Unsupported("no ret")
            rw = _sv_width(inst.operands[0].type)
            return _sv_lane(inst.operands[0], rw, env), rw, decls
        if inst.op in sem.BIN:
            w = _sv_width(inst.type)
            env[inst.result] = (f"({sem.BIN[inst.op]} {_sv_lane(inst.operands[0], w, env)} "
                                f"{_sv_lane(inst.operands[1], w, env)})", w)
            continue
        if inst.op == "icmp" and inst.pred in sem.ICMP:
            w = _sv_width(inst.operands[0].type)
            pred = sem.ICMP[inst.pred].format(a=_sv_lane(inst.operands[0], w, env),
                                              b=_sv_lane(inst.operands[1], w, env))
            env[inst.result] = (f"(ite {pred} {sem.const(1, 1)} {sem.const(0, 1)})", 1)
            continue
        raise sem.Unsupported(f"instruction {inst.op!r}")   # cross-lane / unmodeled -> sound decline
    raise sem.Unsupported("no ret")


def _signature_scal(ll_text, func):
    """[(type-string, name)] for the parameters -- kept as a comparable signature for the callers."""
    fn = ir.parse(ll_text).function(func)
    if fn is None:
        return []
    return [(str(p.type), p.name) for p in fn.params]


def svec_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15,
            cross_check: bool = False, extra_solvers=()) -> dict:
    """TV an element-wise scalable-vector function at one SYMBOLIC lane. Because element-wise ops do not
    cross lanes, proving the lanes equal for an unconstrained lane index proves it for ALL lanes.
    `cross_check` replays the decided query through a second, independent solver."""
    if _signature_scal(before_ll, func) != _signature_scal(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "signature changed"}
    try:
        rb, wb, decls = _svtranslate(before_ll, func)
        ra, wa, _ = _svtranslate(after_ll, func)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    except ir.IrParseError as exc:
        return {"status": "error", "function": func,
                "reason": f"module is not valid LLVM IR: {str(exc).splitlines()[0][:120]}"}
    if wb != wa:
        return {"status": "error", "function": func, "reason": "lane width changed"}
    ds = [f"(declare-const {n} (_ BitVec {w}))" for n, w in sorted(set(decls))]
    smt = "\n".join(["(set-logic QF_BV)", *ds, f"(assert (not (= {rb} {ra})))", "(check-sat)",
                     "(get-model)", ""])
    try:
        out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True,
                             timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "function": func}
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    xc = ({"cross_check": si.cross_check_smt(smt, head, z3_bin, extra_solvers)}
          if cross_check and head in ("sat", "unsat") else {})
    if head == "unsat":
        if si.target_may_poison(after_ll, func):
            return {"status": "unsupported", "function": func, "guard": "target-poison",
                    "reason": "target can produce poison; a value-equality model cannot prove "
                              "refinement against it (values agree, poison is not a value)"}
        return {"status": "proved", "function": func, **xc}
    if head == "sat":
        # value-only lane model: a value mismatch is a genuine miscompile ONLY when the source is
        # poison-free; otherwise it may be a sound poison exploitation (opt folding a poison vector
        # `ashr x,x` to 0), so decline rather than false-refute.
        if si.poison_risk(before_ll, func):
            return {"status": "unsupported", "function": func, "guard": "poison-risk",
                    "reason": "value mismatch under possible poison (lane model lacks poison refinement)"}
        return {"status": "refuted", "function": func, "witness": out, **xc}
    return {"status": "error", "function": func, "reason": head}


def vec_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15,
           cross_check: bool = False, extra_solvers=()) -> dict:
    """TV a vector function lane-by-lane. Proved iff every result lane agrees for all inputs.
    `cross_check` replays the decided query through a second, independent solver."""
    if _signature(before_ll, func) != _signature(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "signature changed"}
    fresh: list = []
    try:
        src_eff, tgt_eff = [], []
        rb, wb, decls, sub, pf = _vtranslate(before_ll, func, side="source", fresh=fresh,
                                             effects=src_eff)
        ra, wa, _, tub, _pf = _vtranslate(after_ll, func, side="target", fresh=fresh,
                                          effects=tgt_eff)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    except ir.IrParseError as exc:
        return {"status": "error", "function": func,
                "reason": f"module is not valid LLVM IR: {str(exc).splitlines()[0][:120]}"}
    if wb != wa or len(rb) != len(ra):
        return {"status": "error", "function": func, "reason": "result shape changed"}
    src_fresh = [(n, w) for n, w in fresh if n.endswith("_source")]
    tgt_fresh = [(n, w) for n, w in fresh if not n.endswith("_source")]
    ds = [f"(declare-const {n} (_ BitVec {w}))" for n, w in sorted(set(decls))]
    ds += [f"(declare-const {n} (_ BitVec {w}))" for n, w in tgt_fresh]
    # one SHARED Bool per poison-capable parameter lane: it is part of the input, not a choice
    # either side makes, so it is free in the refutation and needs no quantifier.
    ds += [f"(declare-const {n} Bool)" for n in sorted(set(pf) | set(_pf))]
    # ALIVE2-STYLE REFINEMENT, PER LANE. This model used to compare VALUES and lean on two guards --
    # decline if the target could produce poison anywhere, decline a mismatch if the source could.
    # Both were blunt: poison is per-element in LLVM, so one flagged lane disqualified the whole
    # function. The obligation is now the same one the scalar validator discharges, applied lane by
    # lane: wherever a SOURCE lane is defined, the target's lane must be defined and equal.
    # OBSERVABLE CALLS, split exactly as the scalar path splits them: the SEQUENCE of callees is
    # checked syntactically (dropping, adding or reordering one is a behaviour change this does not
    # model, so it declines), and the ARGUMENTS go to the solver, because rewriting them into
    # different-looking equal terms is what the pass under test does.
    if [c for c, _ in src_eff] != [c for c, _ in tgt_eff]:
        return {"status": "unsupported", "function": func,
                "reason": f"observable calls differ between source and target "
                          f"({[c for c, _ in src_eff]} vs {[c for c, _ in tgt_eff]})"}
    eff_bad = []
    for (_, sargs), (_, targs) in zip(src_eff, tgt_eff):
        if len(sargs) != len(targs) or [(n, w) for _, n, w in sargs] != [(n, w) for _, n, w in targs]:
            return {"status": "unsupported", "function": func,
                    "reason": "an observable call's arguments changed shape"}
        for (slanes, n, _), (tlanes, _, _) in zip(sargs, targs):
            # Per LANE, the same rule the returned value gets: where the SOURCE already passes
            # poison the callee may observe anything, so the target passing something else REFINES.
            # Comparing values unconditionally here is a false REFUTATION, which this project treats
            # as seriously as a false proof and which the corpus produced immediately on the scalar
            # path when it was written that way.
            #
            # ONE DELIBERATE DIFFERENCE FROM `scalar_ir`, recorded rather than left to be discovered:
            # there the effect terms sit INSIDE the guard on the returned value's poison, so an
            # effect difference goes unnoticed whenever the source's RESULT is poison. Here each
            # argument is gated on its OWN poison instead. An observable call is observable whatever
            # the result turns out to be, so this is the stricter and, I believe, the correct
            # reading -- it can only add refutations, and each added one is a real difference in
            # observable behaviour. The scalar path is a candidate for the same treatment; it is not
            # changed here because that is a separate obligation to re-measure.
            eff_bad += [si.smt_and([f"(not {slanes[i][1]})",
                                    si.smt_or([tlanes[i][1],
                                               f"(not (= {slanes[i][0]} {tlanes[i][0]}))"])])
                        for i in range(n)]
    lane_bad = si.smt_or([si.smt_and([f"(not {rb[i][1]})",
                                      si.smt_or([ra[i][1], f"(not (= {rb[i][0]} {ra[i][0]}))"])])
                          for i in range(len(rb))] + eff_bad)
    refute = si.smt_and([f"(not {sub})", si.smt_or([tub, lane_bad])])
    if src_fresh:
        binders = " ".join(f"({n} (_ BitVec {w}))" for n, w in src_fresh)
        refute = f"(forall ({binders}) {refute})"
    logic = "BV" if src_fresh else "QF_BV"
    smt = "\n".join([f"(set-logic {logic})", *ds, f"(assert {refute})", "(check-sat)", "(get-model)", ""])
    try:
        out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True,
                             timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "function": func}
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    xc = ({"cross_check": si.cross_check_smt(smt, head, z3_bin, extra_solvers)}
          if cross_check and head in ("sat", "unsat") else {})
    if head == "unsat":
        return {"status": "proved", "function": func, **xc}
    if head == "sat":
        return {"status": "refuted", "function": func, "witness": out, **xc}
    return {"status": "error", "function": func, "reason": head}
