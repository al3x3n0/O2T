#!/usr/bin/env python3
"""Vectors: whole-function TV via a LANE MODEL (fixed-width, element-wise + shuffle/extract/insert).

A vector value is modeled as a LIST of per-lane scalar SMT terms (a scalar is a 1-lane list), so
element-wise operations lower lane-by-lane and the cross-lane instructions -- `extractelement`,
`insertelement`, `shufflevector` -- are exact index/permutation operations on the lists. A transform is
a refinement iff, for all inputs, every lane of the result agrees (scalars are the 1-lane case). So a
vector fold (`and <2 x i32> %x, <-1,-1> -> %x`) proves, and a wrong lane refutes.

Scope: fixed-width `<N x iW>` vectors; lane-wise binops/icmp; `extractelement`/`insertelement` with a
CONSTANT index; `shufflevector` with a constant, fully-defined mask (an undef/poison mask lane declines);
integer element constants / `zeroinitializer`. Single-BB. Scalable vectors, variable indices, reductions,
FP, memory, and undef decline (a sound decline, never a mis-model).
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate import scalar_ir as si

_VEC = re.compile(r"<(\d+)\s+x\s+i(\d+)>")


def _vtype(t):
    m = _VEC.fullmatch(t.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _signature(ll_text, func):
    m = re.search(r"@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
    out = []
    if m:
        for part in m.group(1).split(","):
            pm = re.search(r"(<\d+\s+x\s+i\d+>|i\d+)\s+(%[\w.]+)", part.strip())
            if pm:
                out.append((pm.group(1), pm.group(2)))
    return out


def _lanes(tok, n, w, env):
    """A scalar/vector operand -> a list of n lane terms of width w."""
    tok = tok.strip()
    if tok in env:
        lanes, _ = env[tok]
        if len(lanes) != n:
            raise si.Unsupported("lane-count mismatch")
        return lanes
    if tok == "zeroinitializer":
        return [si._const(0, w)] * n
    m = re.fullmatch(r"<(.+)>", tok)
    if m:                                              # a vector literal <iW c0, iW c1, ...>
        elems = [e.strip() for e in m.group(1).split(",")]
        if len(elems) != n:
            raise si.Unsupported("vector-literal arity")
        out = []
        for e in elems:
            em = re.fullmatch(r"i\d+\s+(-?\d+|true|false)", e)
            if not em:
                raise si.Unsupported(f"vector element {e!r}")
            v = em.group(1)
            out.append(si._const(1 if v == "true" else 0 if v == "false" else int(v), w))
        return out
    if n == 1:                                         # a scalar literal
        if re.fullmatch(r"-?\d+", tok):
            return [si._const(int(tok), w)]
        if tok in ("true", "false"):
            return [si._const(1 if tok == "true" else 0, w)]
    raise si.Unsupported(f"operand {tok!r}")


def _vtranslate(ll_text, func):
    """Single-BB vector function -> (result lanes, lane width, param declarations)."""
    body = si._function_body(ll_text, func)
    if body is None:
        raise si.Unsupported(f"function {func} not found")
    if re.search(r"^\s*br\b", body, re.M):
        raise si.Unsupported("multi-block")
    env, decls = {}, []
    for typ, name in _signature(ll_text, func):
        vt = _vtype(typ)
        if vt:
            nn, ww = vt
            lanes = [f"{name}!{i}" for i in range(nn)]
            decls += [(lane, ww) for lane in lanes]
            env[name] = (lanes, ww)
        else:
            ww = int(typ[1:])
            decls.append((name, ww)); env[name] = ([name], ww)
    ret = ret_w = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        rm = re.fullmatch(r"ret\s+(<\d+\s+x\s+i\d+>|i\d+)\s+(\S+)", line)
        if rm:
            vt = _vtype(rm.group(1))
            n, w = vt if vt else (1, int(rm.group(1)[1:]))
            ret, ret_w = _lanes(rm.group(2), n, w, env), w
            break
        m = re.fullmatch(r"(%[\w.]+)\s*=\s*(.+)", line)
        if not m:
            raise si.Unsupported(line)
        dst, rhs = m.group(1), m.group(2)
        _vec_instr(dst, rhs, env)
    if ret is None:
        raise si.Unsupported("no vector/scalar ret")
    return ret, ret_w, decls


def _vec_instr(dst, rhs, env):
    _OPD = r"((?:<[^>]*>)|[^,\s]+)"                     # an operand: a `<...>` literal (has commas) or a token
    bm = re.fullmatch(r"(\w+)(?:\s+(?:nsw|nuw|exact|disjoint))*\s+<(\d+)\s+x\s+i(\d+)>\s+"
                      + _OPD + r",\s+" + _OPD, rhs)
    if bm and bm.group(1) in si._BIN:
        n, w = int(bm.group(2)), int(bm.group(3))
        a, b = _lanes(bm.group(4), n, w, env), _lanes(bm.group(5), n, w, env)
        op = si._BIN[bm.group(1)]
        env[dst] = ([f"({op} {a[i]} {b[i]})" for i in range(n)], w)
        return
    im = re.fullmatch(r"icmp\s+(\w+)\s+<(\d+)\s+x\s+i(\d+)>\s+" + _OPD + r",\s+" + _OPD, rhs)
    if im and im.group(1) in si._ICMP:
        n, w = int(im.group(2)), int(im.group(3))
        a, b = _lanes(im.group(4), n, w, env), _lanes(im.group(5), n, w, env)
        env[dst] = ([f"(ite {si._ICMP[im.group(1)].format(a=a[i], b=b[i])} {si._const(1, 1)} {si._const(0, 1)})"
                     for i in range(n)], 1)
        return
    em = re.fullmatch(r"extractelement\s+<(\d+)\s+x\s+i(\d+)>\s+(\S+),\s+i\d+\s+(\d+)", rhs)
    if em:
        n, w, k = int(em.group(1)), int(em.group(2)), int(em.group(4))
        lanes = _lanes(em.group(3).rstrip(","), n, w, env)
        if k >= n:
            raise si.Unsupported("extractelement index out of range")
        env[dst] = ([lanes[k]], w)
        return
    nm = re.fullmatch(r"insertelement\s+<(\d+)\s+x\s+i(\d+)>\s+(\S+),\s+i\d+\s+(\S+),\s+i\d+\s+(\d+)", rhs)
    if nm:
        n, w, k = int(nm.group(1)), int(nm.group(2)), int(nm.group(5))
        lanes = list(_lanes(nm.group(3).rstrip(","), n, w, env))
        elt = _lanes(nm.group(4).rstrip(","), 1, w, env)[0]
        if k >= n:
            raise si.Unsupported("insertelement index out of range")
        lanes[k] = elt
        env[dst] = (lanes, w)
        return
    sm = re.fullmatch(r"shufflevector\s+<(\d+)\s+x\s+i(\d+)>\s+(\S+),\s+<\d+\s+x\s+i\d+>\s+(\S+),\s+"
                      r"<(\d+)\s+x\s+i\d+>\s+<(.+)>", rhs)
    if sm:
        n, w = int(sm.group(1)), int(sm.group(2))
        a = _lanes(sm.group(3).rstrip(","), n, w, env)
        b = _lanes(sm.group(4).rstrip(","), n, w, env)
        pool = a + b
        mask = []
        for e in sm.group(6).split(","):
            km = re.fullmatch(r"i\d+\s+(-?\d+)", e.strip())
            if not km:                                 # an undef/poison mask lane -> decline
                raise si.Unsupported(f"shuffle mask {e!r}")
            idx = int(km.group(1))
            if idx < 0 or idx >= len(pool):
                raise si.Unsupported("shuffle index out of range")
            mask.append(pool[idx])
        env[dst] = (mask, w)
        return
    raise si.Unsupported(rhs)


def vec_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15) -> dict:
    """TV a vector function lane-by-lane. Proved iff every result lane agrees for all inputs."""
    if _signature(before_ll, func) != _signature(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "signature changed"}
    try:
        rb, wb, decls = _vtranslate(before_ll, func)
        ra, wa, _ = _vtranslate(after_ll, func)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
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
    if head == "unsat":
        return {"status": "proved", "function": func}
    if head == "sat":
        return {"status": "refuted", "function": func, "witness": out}
    return {"status": "error", "function": func, "reason": head}
