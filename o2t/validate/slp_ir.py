#!/usr/bin/env python3
"""Closed-loop translation validation for the SLP vectorizer: prove the REAL `opt -passes=slp-vectorizer`.

Extends the real-opt closed loop (indvars / simplifycfg / dse / instcombine) to SLP. Unlike a
scalar peephole, SLP CHANGES the instruction shape: a bundle of scalar loads/ops/stores becomes a
vector load / vector op / vector store. To prove the real before (scalar) equivalent to the real
after (vectorized), we model memory as compile-time-known cells -- each address is `(base, offset)`
from a pointer argument via `getelementptr` -- translate BOTH functions to "what value is stored at
each output cell" (lanes decomposed), and prove those per-cell values equal for all input-cell
values (QF_BV). The same `(base, offset)` reads/writes the same cell symbol, so a scalar load and
a vector lane refer to the same memory; a vectorization that permutes lanes wrongly is refuted.

Supported (else declined `unsupported`, never falsely proved): pointer-arg `getelementptr` with a
constant index, scalar/vector load and store, lane-wise integer add/sub/mul/and/or/xor, and
extractelement / insertelement / shufflevector. Single basic block.

The per-cell obligation is Alive2 REFINEMENT, not raw equality: each value carries a poison term and
the function carries a UB term, and each output cell must satisfy
``src_poison OR (NOT tgt_poison AND src == tgt)`` plus ``NOT tgt_ub`` where the source is well
defined. So a vectorization that adds an unjustified nsw/nuw/exact (poison) or a div/rem-by-zero (UB)
is refuted, while value-equal lane packing still proves.
"""

from __future__ import annotations

import re
import subprocess

from o2t.formal_ir import smt_and, smt_or
from o2t.validate import ir_model as ir
from o2t.validate.scalar_ir import _BIN, _const, _own_poison, _own_ub, Unsupported

_VEC = r"<(\d+)\s+x\s+i(\d+)>"
_DIV = ("udiv", "sdiv", "urem", "srem")


class _Env:
    def __init__(self):
        self.scalar = {}     # %name -> (term, width, poison)
        self.vector = {}     # %name -> [ (term, width, poison), ... ] lanes
        self.addr = {}       # %name -> (base, offset) memory cell address
        self.cells = {}      # (base, offset, width) -> symbol  (declared inputs read by loads)
        self.stores = {}     # (base, offset) -> (term, width, poison)  output written
        self.ub = []         # function-level UB conditions (div/rem-by-zero etc., any executed op)


def _cell(env, base, offset, width):
    key = (base, offset, width)
    if key not in env.cells:
        env.cells[key] = f"cell_{base.lstrip('%')}_{offset}"
    return env.cells[key]


def _addr_of(env, value):
    """The (base, offset) of a pointer operand: a bare pointer arg is offset 0."""
    name = value.name if getattr(value, "is_reg", False) else str(value)
    if name in env.addr:
        return env.addr[name]
    return name, 0                                    # pointer argument -> base, offset 0


def translate(ll_text, func):
    """Translate a single-BB function to (env). `env.stores` maps each output cell to its stored
    term; `env.cells` are the input cells read. Raises Unsupported on any unmodeled shape.

    Read from LLVM's parse: lane counts and widths come from the types, a shuffle mask from
    `Instruction.mask` (where LLVM already reports -1 for an undef lane), and poison flags from
    LLVM's accessors -- all of which this module previously recovered from instruction text."""
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        raise Unsupported(f"function {func} not found")
    if len(fn.blocks) > 1:
        raise Unsupported("multi-block function")
    env = _Env()
    for inst in fn.blocks[0].instructions:
        if inst.op == "ret":
            continue
        _instruction(inst, env)
    return env


def _vshape(t):
    """(lanes, width) for a fixed vector of integers."""
    if t.kind == "vector" and not t.scalable and t.elem and t.elem.is_int():
        return t.n, t.elem.bits
    raise Unsupported(f"type {t}")


def _scalar_operand(value, width, env):
    """An operand -> (term, poison). Constants and input cells are defined (poison "false")."""
    if value.is_reg:
        name = value.name
        if name in env.scalar:
            term, _w, poison = env.scalar[name]
            return term, poison
        raise Unsupported(f"scalar operand {name!r}")
    if value.kind == "int":
        return _const(value.int_value, width), "false"
    raise Unsupported(f"scalar operand {value.kind}")


def _binop(env, name, flags, w, at, ap, bt, bp):
    """Lower one (scalar or per-lane) binop to (term, poison), accumulating any UB into env.ub."""
    op = _BIN[name]
    poison = smt_or([ap, bp, _own_poison(name, op, flags, at, bt, w)])
    env.ub.append(_own_ub(name, at, bt, w))
    if name in _DIV:
        env.ub.append(bp)                       # a poison divisor is UB
    return f"({op} {at} {bt})", poison


def _instruction(inst, env):
    op, dst = inst.op, inst.result

    if op == "getelementptr" and dst:
        src_t = inst.source_type
        if src_t is None or not src_t.is_int():
            raise Unsupported("gep over a non-integer element")
        idxs = inst.operands[1:]
        if len(idxs) != 1 or idxs[0].kind != "int":
            raise Unsupported("gep with a non-constant or multi-level index")
        base, off = _addr_of(env, inst.operands[0])
        env.addr[dst] = (base, off + idxs[0].int_value)
        return

    if op == "load" and dst:
        base, off = _addr_of(env, inst.operands[0])
        if inst.type.kind == "vector":
            n, w = _vshape(inst.type)
            env.vector[dst] = [(_cell(env, base, off + i, w), w, "false") for i in range(n)]
        elif inst.type.is_int():
            w = inst.type.bits
            env.scalar[dst] = (_cell(env, base, off, w), w, "false")
        else:
            raise Unsupported(f"load of {inst.type}")
        return

    if op == "store":
        val, ptr = inst.operands[0], inst.operands[1]
        base, off = _addr_of(env, ptr)
        if val.type.kind == "vector":
            n, _w = _vshape(val.type)
            if not val.is_reg or val.name not in env.vector:
                raise Unsupported("vector store of a non-register")
            lanes = env.vector[val.name]
            for i in range(n):
                env.stores[(base, off + i)] = lanes[i]
        elif val.type.is_int():
            w = val.type.bits
            term, poison = _scalar_operand(val, w, env)
            env.stores[(base, off)] = (term, w, poison)
        else:
            raise Unsupported(f"store of {val.type}")
        return

    if op in _BIN and dst:
        flags = list(inst.flags)
        if inst.type.kind == "vector":
            n, w = _vshape(inst.type)
            a, b = inst.operands[0], inst.operands[1]
            if not (a.is_reg and b.is_reg and a.name in env.vector and b.name in env.vector):
                raise Unsupported("vector binop over a non-register operand")
            x, y = env.vector[a.name], env.vector[b.name]
            lanes = []
            for i in range(n):
                term, poison = _binop(env, op, flags, w, x[i][0], x[i][2], y[i][0], y[i][2])
                lanes.append((term, w, poison))
            env.vector[dst] = lanes
        elif inst.type.is_int():
            w = inst.type.bits
            at, ap = _scalar_operand(inst.operands[0], w, env)
            bt, bp = _scalar_operand(inst.operands[1], w, env)
            term, poison = _binop(env, op, flags, w, at, ap, bt, bp)
            env.scalar[dst] = (term, w, poison)
        else:
            raise Unsupported(f"binop on {inst.type}")
        return

    if op == "extractelement" and dst:
        vec, idx = inst.operands[0], inst.operands[1]
        if not vec.is_reg or idx.kind != "int":
            raise Unsupported("extractelement with a non-constant index")
        env.scalar[dst] = env.vector[vec.name][idx.int_value]
        return

    if op == "insertelement" and dst:
        n, w = _vshape(inst.type)
        vec, val, idx = inst.operands[0], inst.operands[1], inst.operands[2]
        if idx.kind != "int":
            raise Unsupported("insertelement with a non-constant index")
        base = list(env.vector.get(vec.name, [(None, w, "false")] * n)) if vec.is_reg \
            else [(None, w, "false")] * n
        term, poison = _scalar_operand(val, w, env)
        base[idx.int_value] = (term, w, poison)
        env.vector[dst] = base
        return

    if op == "shufflevector" and dst:
        a, b = inst.operands[0], inst.operands[1]
        v1 = env.vector[a.name] if a.is_reg else []
        v2 = env.vector.get(b.name, []) if b.is_reg else []
        combined = v1 + v2
        mask = inst.mask
        # LLVM reports -1 for a poison/undef mask lane; only sound when that lane is unused.
        if any(k < 0 or k >= len(combined) for k in mask):
            raise Unsupported("shuffle with poison/out-of-range lane")
        env.vector[dst] = [combined[k] for k in mask]
        return

    raise Unsupported(f"instruction {op!r}")


def _cell_refines(src_poison, tgt_poison, src_val, tgt_val):
    """src_poison OR (NOT tgt_poison AND src_val == tgt_val): the target cell refines the source."""
    return smt_or([src_poison, smt_and([f"(not {tgt_poison})", f"(= {src_val} {tgt_val})"])])


def run_slp(src_text, opt_bin="opt", threshold=None):
    argv = [opt_bin, "-passes=slp-vectorizer", "-S", "-o", "-"]
    if threshold is not None:
        argv.insert(1, f"-slp-threshold={threshold}")
    proc = subprocess.run(argv, input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def validate_slp(z3_bin, src_text, opt_text, func):
    """Translate before/after and prove every output cell gets the same value for all inputs."""
    try:
        b = translate(src_text, func)
        a = translate(opt_text, func)
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if set(b.stores) != set(a.stores):
        return {"status": "error", "function": func, "reason": "different output cells"}
    if not b.stores:
        return {"status": "unsupported", "function": func, "reason": "no stores to validate"}
    cells = dict(b.cells); cells.update(a.cells)
    decls = [f"(declare-const {sym} (_ BitVec {w}))" for (base, off, w), sym in cells.items()]
    # Alive2 refinement per output cell: where the source value is defined (not poison) the target
    # must agree and not be poison; plus the target must not introduce UB the source lacked. So a
    # vectorization that adds an unjustified nsw/nuw/exact (poison) or a div-by-zero (UB) is refuted,
    # while value-equal lane packing still proves.
    src_ub, tgt_ub = smt_or(b.ub), smt_or(a.ub)
    refine = smt_and([_cell_refines(b.stores[k][2], a.stores[k][2], b.stores[k][0], a.stores[k][0])
                      for k in b.stores])
    refute = smt_and([f"(not {src_ub})", smt_or([tgt_ub, f"(not {refine})"])])
    smt = "\n".join(["(set-logic QF_BV)", *decls,
                     f"(assert {refute})", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return {"status": "proved", "function": func, "cells": len(b.stores)}
    if head == "sat":
        return {"status": "refuted", "function": func, "witness": out}
    return {"status": "error", "function": func, "reason": head}


def function_names(ll_text):
    return ir.parse(ll_text).defined_names
