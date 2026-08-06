#!/usr/bin/env python3
r"""Vectors: whole-function TV via a LANE MODEL (fixed-width, element-wise + shuffle/extract/insert).

A vector value is modeled as a LIST of per-lane scalar SMT terms (a scalar is a 1-lane list), so
element-wise operations lower lane-by-lane and the cross-lane instructions -- `extractelement`,
`insertelement`, `shufflevector` -- are exact index/permutation operations on the lists. A transform is
a refinement iff, for all inputs, every lane of the result agrees (scalars are the 1-lane case). So a
vector fold (`and <2 x i32> %x, <-1,-1> -> %x`) proves, and a wrong lane refutes.

Scope: fixed-width `<N x iW>` vectors; lane-wise binops/icmp; `extractelement`/`insertelement` with a
CONSTANT index; `shufflevector` with a constant, fully-defined mask (an undef/poison mask lane declines);
integer element constants / `zeroinitializer` / `splat`. Single-BB. Variable indices, reductions, FP,
memory, and undef decline (a sound decline, never a mis-model); scalable vectors are handled by the
per-lane model below.

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
# Ported FAITHFULLY -- same lane terms, same declines -- with one deliberate non-change: the old
# binop regex discarded poison flags in a non-capturing group, so this model ignores nsw/nuw/exact/
# disjoint entirely and gates refutation on `poison_risk` instead. The parse now makes those flags
# available, but using them would change verdicts, and a refactor is the wrong place to do that.

def _lanes_of(v, n, w, env):
    """A scalar/vector operand -> a list of n lane terms of width w."""
    if v.is_reg:
        if v.name not in env:
            raise sem.Unsupported(f"operand {v.name!r}")
        ls, _ = env[v.name]
        if len(ls) != n:
            raise sem.Unsupported("lane-count mismatch")
        return ls
    if v.kind == "zeroinit":
        return [sem.const(0, w)] * n
    if v.kind == "splat":                      # every lane the same value
        elem = v.splat_elem
        if elem is None or elem.kind != "int":
            raise sem.Unsupported("non-integer splat")
        return [sem.const(elem.int_value, w)] * n
    if v.kind == "vector":
        elems = v.elements
        if len(elems) != n:
            raise sem.Unsupported("vector-literal arity")
        out = []
        for e in elems:
            if e.kind != "int":                        # an undef/poison element -> decline
                raise sem.Unsupported(f"vector element {e.kind}")
            out.append(sem.const(e.int_value, w))
        return out
    if v.kind == "int" and n == 1:
        return [sem.const(v.int_value, w)]
    raise sem.Unsupported(f"operand {v.kind}")


def _vshape(t):
    """(lanes, width) for a vector or scalar integer type."""
    if t.kind == "vector" and not t.scalable and t.elem and t.elem.is_int():
        return t.n, t.elem.bits
    if t.is_int():
        return 1, t.bits
    raise sem.Unsupported(f"type {t}")


def _vec_instr(inst, env):
    op = inst.op
    if op in sem.BIN:
        n, w = _vshape(inst.type)
        a = _lanes_of(inst.operands[0], n, w, env)
        b = _lanes_of(inst.operands[1], n, w, env)
        smt = sem.BIN[op]
        env[inst.result] = ([f"({smt} {a[i]} {b[i]})" for i in range(n)], w)
        return
    if op == "icmp":
        if inst.pred not in sem.ICMP:
            raise sem.Unsupported(f"icmp predicate {inst.pred!r}")
        n, w = _vshape(inst.operands[0].type)
        a = _lanes_of(inst.operands[0], n, w, env)
        b = _lanes_of(inst.operands[1], n, w, env)
        env[inst.result] = ([f"(ite {sem.ICMP[inst.pred].format(a=a[i], b=b[i])} "
                             f"{sem.const(1, 1)} {sem.const(0, 1)})" for i in range(n)], 1)
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
        c = _lanes_of(inst.operands[0], cn, 1, env)
        a = _lanes_of(inst.operands[1], n, w, env)
        b = _lanes_of(inst.operands[2], n, w, env)
        env[inst.result] = ([f"(ite (= {c[i if cn == n else 0]} {sem.const(1, 1)}) {a[i]} {b[i]})"
                             for i in range(n)], w)
        return
    if op in ("zext", "sext"):
        n, w = _vshape(inst.type)
        sn, sw = _vshape(inst.src_type or inst.operands[0].type)
        if sn != n:
            raise sem.Unsupported("extension changes the lane count")
        if sw >= w:
            raise sem.Unsupported(f"{op} from i{sw} to i{w} does not widen")
        a = _lanes_of(inst.operands[0], n, sw, env)
        kind = "zero_extend" if op == "zext" else "sign_extend"
        env[inst.result] = ([f"((_ {kind} {w - sw}) {a[i]})" for i in range(n)], w)
        return
    if op == "extractelement":
        n, w = _vshape(inst.operands[0].type)
        idx = inst.operands[1]
        if idx.kind != "int":
            raise sem.Unsupported("variable extractelement index")
        k = idx.int_value
        ls = _lanes_of(inst.operands[0], n, w, env)
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
        ls = list(_lanes_of(inst.operands[0], n, w, env))
        elt = _lanes_of(inst.operands[1], 1, w, env)[0]
        if k < 0 or k >= n:
            raise sem.Unsupported("insertelement index out of range")
        ls[k] = elt
        env[inst.result] = (ls, w)
        return
    if op == "shufflevector":
        n, w = _vshape(inst.operands[0].type)
        a = _lanes_of(inst.operands[0], n, w, env)
        b = _lanes_of(inst.operands[1], n, w, env)
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


def _vtranslate(ll_text, func):
    """Single-BB vector function -> (result lanes, lane width, param declarations)."""
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    if len(fn.blocks) > 1:
        raise sem.Unsupported("multi-block")
    env, decls = {}, []
    for p in fn.params:
        t = p.type
        if t.kind == "vector" and not t.scalable and t.elem and t.elem.is_int():
            ls = [f"{p.name}!{i}" for i in range(t.n)]
            decls += [(lane, t.elem.bits) for lane in ls]
            env[p.name] = (ls, t.elem.bits)
        elif t.is_int():
            decls.append((p.name, t.bits))
            env[p.name] = ([p.name], t.bits)
        else:
            raise sem.Unsupported(f"parameter type {t}")
    for inst in fn.blocks[0].instructions:
        if inst.op == "ret":
            if not inst.operands:
                raise sem.Unsupported("no vector/scalar ret")
            n, w = _vshape(inst.operands[0].type)
            return _lanes_of(inst.operands[0], n, w, env), w, decls
        _vec_instr(inst, env)
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
    try:
        rb, wb, decls = _vtranslate(before_ll, func)
        ra, wa, _ = _vtranslate(after_ll, func)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    except ir.IrParseError as exc:
        return {"status": "error", "function": func,
                "reason": f"module is not valid LLVM IR: {str(exc).splitlines()[0][:120]}"}
    if wb != wa or len(rb) != len(ra):
        return {"status": "error", "function": func, "reason": "result shape changed"}
    ds = [f"(declare-const {n} (_ BitVec {w}))" for n, w in sorted(set(decls))]
    refute = si.smt_or([f"(not (= {rb[i]} {ra[i]}))" for i in range(len(rb))])
    smt = "\n".join(["(set-logic QF_BV)", *ds, f"(assert {refute})", "(check-sat)", "(get-model)", ""])
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
