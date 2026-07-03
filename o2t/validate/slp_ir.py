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
from o2t.validate.scalar_ir import _BIN, _const, _function_body, _own_poison, _own_ub, Unsupported

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


def _addr_of(env, tok):
    """The (base, offset) of a pointer operand: a bare pointer arg is offset 0."""
    if tok in env.addr:
        return env.addr[tok]
    return tok, 0                                     # pointer argument -> base, offset 0


def translate(ll_text, func):
    """Translate a single-BB function to (env). `env.stores` maps each output cell to its stored
    term; `env.cells` are the input cells read. Raises Unsupported on any unmodeled shape."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    if len(re.findall(r"^\s*[\w.]+:", body, re.M)) > 1:
        raise Unsupported("multi-block function")
    env = _Env()
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line == "ret void" or re.fullmatch(r"ret\s+.*", line):
            continue
        _instruction(line, env)
    return env


def _scalar_operand(tok, width, env):
    """An operand -> (term, poison). Constants and input cells are defined (poison "false")."""
    tok = tok.strip().rstrip(",")
    if tok in env.scalar:
        t, _w, p = env.scalar[tok]
        return t, p
    if re.fullmatch(r"-?\d+", tok):
        return _const(int(tok), width), "false"
    raise Unsupported(f"scalar operand {tok!r}")


def _binop(env, name, flags, w, at, ap, bt, bp):
    """Lower one (scalar or per-lane) binop to (term, poison), accumulating any UB into env.ub."""
    op = _BIN[name]
    poison = smt_or([ap, bp, _own_poison(name, op, flags, at, bt, w)])
    env.ub.append(_own_ub(name, at, bt, w))
    if name in _DIV:
        env.ub.append(bp)                       # a poison divisor is UB
    return f"({op} {at} {bt})", poison


def _instruction(line, env):
    m = re.fullmatch(r"(%[\w.]+)\s*=\s*(.+)", line)
    rhs = m.group(2) if m else line
    dst = m.group(1) if m else None

    gm = re.fullmatch(r"getelementptr\s+(?:inbounds\s+)?i(\d+),\s+ptr\s+(%[\w.]+),\s+i\d+\s+(-?\d+)", rhs)
    if gm and dst:
        base, off = _addr_of(env, gm.group(2))
        env.addr[dst] = (base, off + int(gm.group(3)))
        return

    # vector load: lanes from consecutive cells at the base address.
    vlm = re.fullmatch(r"load\s+" + _VEC + r",\s+ptr\s+(%[\w.]+)(?:,\s+align\s+\d+)?", rhs)
    if vlm and dst:
        n, w = int(vlm.group(1)), int(vlm.group(2))
        base, off = _addr_of(env, vlm.group(3))
        env.vector[dst] = [(_cell(env, base, off + i, w), w, "false") for i in range(n)]
        return

    slm = re.fullmatch(r"load\s+i(\d+),\s+ptr\s+(%[\w.]+)(?:,\s+align\s+\d+)?", rhs)
    if slm and dst:
        w = int(slm.group(1))
        base, off = _addr_of(env, slm.group(2))
        env.scalar[dst] = (_cell(env, base, off, w), w, "false")
        return

    # vector store: write each lane to consecutive cells.
    vsm = re.fullmatch(r"store\s+" + _VEC + r"\s+(%[\w.]+),\s+ptr\s+(%[\w.]+)(?:,\s+align\s+\d+)?", rhs)
    if vsm:
        n, w = int(vsm.group(1)), int(vsm.group(2))
        lanes = env.vector[vsm.group(3)]
        base, off = _addr_of(env, vsm.group(4))
        for i in range(n):
            env.stores[(base, off + i)] = lanes[i]
        return

    ssm = re.fullmatch(r"store\s+i(\d+)\s+(\S+),\s+ptr\s+(%[\w.]+)(?:,\s+align\s+\d+)?", rhs)
    if ssm:
        w = int(ssm.group(1))
        term, poison = _scalar_operand(ssm.group(2), w, env)
        base, off = _addr_of(env, ssm.group(3))
        env.stores[(base, off)] = (term, w, poison)
        return

    # vector binop: lane-wise.
    vbm = re.fullmatch(r"(\w+)((?:\s+(?:nsw|nuw|exact|disjoint))*)\s+" + _VEC + r"\s+(%[\w.]+),\s+(%[\w.]+)", rhs)
    if vbm and dst and vbm.group(1) in _BIN:
        name, flags, n, w = vbm.group(1), re.findall(r"nsw|nuw|exact|disjoint", vbm.group(2)), \
            int(vbm.group(3)), int(vbm.group(4))
        x, y = env.vector[vbm.group(5)], env.vector[vbm.group(6)]
        lanes = []
        for i in range(n):
            term, poison = _binop(env, name, flags, w, x[i][0], x[i][2], y[i][0], y[i][2])
            lanes.append((term, w, poison))
        env.vector[dst] = lanes
        return

    sbm = re.fullmatch(r"(\w+)((?:\s+(?:nsw|nuw|exact|disjoint))*)\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if sbm and dst and sbm.group(1) in _BIN:
        name, flags, w = sbm.group(1), re.findall(r"nsw|nuw|exact|disjoint", sbm.group(2)), int(sbm.group(3))
        at, ap = _scalar_operand(sbm.group(4), w, env)
        bt, bp = _scalar_operand(sbm.group(5), w, env)
        term, poison = _binop(env, name, flags, w, at, ap, bt, bp)
        env.scalar[dst] = (term, w, poison)
        return

    em = re.fullmatch(r"extractelement\s+" + _VEC + r"\s+(%[\w.]+),\s+i\d+\s+(\d+)", rhs)
    if em and dst:
        env.scalar[dst] = env.vector[em.group(3)][int(em.group(4))]
        return

    im = re.fullmatch(r"insertelement\s+" + _VEC + r"\s+(%[\w.]+|poison|undef),\s+i\d+\s+(\S+),\s+i\d+\s+(\d+)", rhs)
    if im and dst:
        n, w = int(im.group(1)), int(im.group(2))
        base = list(env.vector.get(im.group(3), [(None, w, "false")] * n))
        term, poison = _scalar_operand(im.group(4), w, env)
        base[int(im.group(5))] = (term, w, poison)
        env.vector[dst] = base
        return

    fm = re.fullmatch(r"shufflevector\s+<(\d+)\s+x\s+i(\d+)>\s+(%[\w.]+),\s+"
                      r"<\d+\s+x\s+i\d+>\s+(%[\w.]+|poison|undef),\s+"
                      r"<\d+\s+x\s+i32>\s+<([^>]*)>", rhs)
    if fm and dst:
        v1 = env.vector[fm.group(3)]
        v2 = env.vector.get(fm.group(4), [])
        mask = [int(x) for x in re.findall(r"i32\s+(-?\d+)", fm.group(5))]
        combined = v1 + v2
        # a negative mask index is `poison`/`undef`; only sound when that lane is unused downstream.
        if any(k < 0 or k >= len(combined) for k in mask):
            raise Unsupported("shuffle with poison/out-of-range lane")
        env.vector[dst] = [combined[k] for k in mask]
        return

    raise Unsupported(rhs)


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
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)
