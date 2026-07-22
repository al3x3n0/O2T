#!/usr/bin/env python3
"""Pointer-side-effect memory: whole-function TV over the MEMORY STATE (SMT theory of arrays).

Local-memory TV models non-escaping allocas. This handles functions whose observable behavior includes
MEMORY WRITES through pointer arguments (store to `ptr %p`). Memory is modeled as an SMT array
`(Array (_ BitVec 64) (_ BitVec 32))` -- word-addressed by an opaque 64-bit pointer address; a `store`
is `(store mem addr v)`, a `load` is `(select mem addr)`. The array theory models ALIASING PRECISELY:
`select(store(m,p,v),q) = ite(p=q, v, select(m,q))`, so two pointer arguments that may alias are handled
soundly with no alias analysis.

A transform is a refinement iff, for the same initial memory and arguments, the RETURN VALUE and the
FINAL MEMORY STATE agree. So DSE removing a dead (overwritten) store proves; removing a live store, or
changing a stored value, refutes. Scope: single-BB, i32 word load/store to opaque pointer ARGUMENTS
(no gep / pointer arithmetic / alloca -> decline); pointer validity / null-deref UB is not modeled (a
documented gap -- sound for store removal/reordering, which introduce no new dereferences).
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate import scalar_ir as si

_PARAM_RE = re.compile(r"(ptr|i(\d+))\s+(%[\w.]+)")


def _signature(ll_text, func):
    """[(kind, name)] for params -- kind is 'ptr' or an int width. None if the function is absent."""
    m = re.search(r"@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
    if not m:
        return None
    out = []
    for part in m.group(1).split(","):
        pm = _PARAM_RE.search(part.strip())
        if pm:
            out.append(("ptr" if pm.group(1) == "ptr" else int(pm.group(2)), pm.group(3)))
    return out


def _addr_off(addr, off):
    return addr if off == 0 else f"(bvadd {addr} (_ bv{off} 64))"


def _store_bytes(mem, addr, value, width):
    """Store a `width`-bit value at byte address `addr`, LITTLE-ENDIAN (byte 0 = LSB)."""
    for i in range(width // 8):
        mem = f"(store {mem} {_addr_off(addr, i)} ((_ extract {i * 8 + 7} {i * 8}) {value}))"
    return mem


def _load_bytes(mem, addr, width):
    """Load a `width`-bit value from byte address `addr`, little-endian."""
    nb = width // 8
    if nb == 1:
        return f"(select {mem} {addr})"
    parts = [f"(select {mem} {_addr_off(addr, i)})" for i in range(nb)]
    return f"(concat {' '.join(reversed(parts))})"      # MSB-first concat, byte 0 is the LSB


def _idx64(tok, env):
    """A gep index operand -> a 64-bit SMT term (sign-extended, as gep indices are signed)."""
    if tok in env:
        term, w, _, _ = env[tok]
        if w == 64:
            return term
        return f"((_ sign_extend {64 - w}) {term})" if w < 64 else f"((_ extract 63 0) {term})"
    if re.fullmatch(r"-?\d+", tok):
        return si._const(int(tok), 64)
    raise si.Unsupported(f"gep index {tok!r}")


def _scaled(base, idx64, stride):
    return f"(bvadd {base} {idx64})" if stride == 1 else \
        f"(bvadd {base} (bvmul {idx64} (_ bv{stride} 64)))"


def _field_offsets(fields_bits, packed):
    """BYTE offset of each field of an integer struct (alignment = field size unless packed)."""
    offs, cur = [], 0
    for w in fields_bits:
        sz = w // 8
        if not packed and sz:
            cur = (cur + sz - 1) // sz * sz
        offs.append(cur); cur += sz
    return offs


def _gep(line, addr, env):
    """`getelementptr` -> (dst, new BYTE address). Handles a scalar element `iW, ptr %b, iW %i`
    (byte stride W/8), the array form `[N x iW], ptr %b, iW 0, iW %i`, and an integer-struct field
    `{iA, iB, ...}, ptr %b, iW 0, iW K` (constant K -> field byte offset). Because memory is byte-
    addressable, i8/i32/struct/type-punning all share ONE model and the array theory handles aliasing.
    None if not a gep; declines an unmodeled gep (nested/pointer fields, non-byte width)."""
    m = re.fullmatch(r"(%[\w.]+)\s*=\s*getelementptr\s+(?:inbounds\s+)?(.+)", line)
    if not m:
        return None
    dst, rest = m.group(1), m.group(2)
    e1 = re.fullmatch(r"i(\d+),\s+ptr\s+(%[\w.]+),\s+i\d+\s+(\S+)", rest)
    e2 = re.fullmatch(r"\[\d+\s+x\s+i(\d+)\],\s+ptr\s+(%[\w.]+),\s+i\d+\s+0,\s+i\d+\s+(\S+)", rest)
    if e1 or e2:
        g = e1 or e2
        w = int(g.group(1))
        if w % 8 or g.group(2) not in addr:
            raise si.Unsupported("gep non-byte element / non-pointer base")
        return dst, _scaled(addr[g.group(2)], _idx64(g.group(3), env), w // 8)
    e3 = re.fullmatch(r"(<)?\{\s*(.+?)\s*\}>?,\s+ptr\s+(%[\w.]+),\s+i\d+\s+0,\s+i\d+\s+(\d+)", rest)
    if e3:
        fm = [re.fullmatch(r"i(\d+)", f.strip()) for f in e3.group(2).split(",")]
        if not all(fm) or e3.group(3) not in addr:
            raise si.Unsupported("non-integer struct field / non-pointer base")
        fields = [int(f.group(1)) for f in fm]
        k = int(e3.group(4))
        if any(w % 8 for w in fields) or k >= len(fields):
            raise si.Unsupported("struct field out of range / non-byte field")
        return dst, _addr_off(addr[e3.group(3)], _field_offsets(fields, e3.group(1) == "<")[k])
    raise si.Unsupported(f"gep form {rest[:40]!r}")


def _mem_translate(ll_text, func):
    """Symbolically execute a single-BB function over the memory array; return
    (ret_term|None, ret_width, final_mem_term). Reuses scalar_ir for the scalar instructions."""
    body = si._function_body(ll_text, func)
    if body is None:
        raise si.Unsupported(f"function {func} not found")
    if re.search(r"^\s*br\b", body, re.M):
        raise si.Unsupported("multi-block")
    sig = _signature(ll_text, func) or []
    env = {n: (n, w, "false", "false") for w, n in sig if w != "ptr"}
    addr = {n: n for w, n in sig if w == "ptr"}         # pointer arg -> its (opaque i64) address term
    mem = "mem0"
    ret_term, ret_width = None, None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line == "ret void":
            ret_term = None
            break
        rm = re.fullmatch(r"ret\s+i(\d+)\s+(\S+)", line)
        if rm:
            ret_width = int(rm.group(1))
            ret_term, _, _, _ = si._operand(rm.group(2), ret_width, env)
            break
        sm = re.fullmatch(r"store\s+i(\d+)\s+(\S+),\s+ptr\s+(%[\w.]+)(?:,.*)?", line)
        if sm:
            w = int(sm.group(1))
            if w % 8 or sm.group(3) not in addr:
                raise si.Unsupported("store width/target out of scope")
            vt, _, _, _ = si._operand(sm.group(2), w, env)
            mem = _store_bytes(mem, addr[sm.group(3)], vt, w)
            continue
        lm = re.fullmatch(r"(%[\w.]+)\s*=\s*load\s+i(\d+),\s+ptr\s+(%[\w.]+)(?:,.*)?", line)
        if lm:
            w = int(lm.group(2))
            if w % 8 or lm.group(3) not in addr:
                raise si.Unsupported("load width/target out of scope")
            env[lm.group(1)] = (_load_bytes(mem, addr[lm.group(3)], w), w, "false", "false")
            continue
        gm = _gep(line, addr, env)                       # getelementptr on an i32 pointer -> a new address
        if gm:
            addr[gm[0]] = gm[1]
            continue
        si._instruction(line, env, None, None)          # scalar op (alloca/other-gep decline here)
    return ret_term, ret_width, mem


def mem_state_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15) -> dict:
    """TV a pointer-side-effect function over its memory state. Proved iff the return value AND the
    final memory state agree for all initial memories and arguments; refuted on a witness."""
    if _signature(before_ll, func) != _signature(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "signature changed"}
    try:
        rb, wb, mb = _mem_translate(before_ll, func)
        ra, wa, ma = _mem_translate(after_ll, func)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if wb != wa or (rb is None) != (ra is None):
        return {"status": "error", "function": func, "reason": "return kind changed"}
    sig = _signature(before_ll, func) or []
    decls = ["(declare-const mem0 (Array (_ BitVec 64) (_ BitVec 8)))"]
    for w, n in sig:
        decls.append(f"(declare-const {n} (_ BitVec {64 if w == 'ptr' else w}))")
    diffs = ([f"(not (= {rb} {ra}))"] if rb is not None else []) + [f"(not (= {mb} {ma}))"]
    smt = "\n".join(["(set-logic QF_ABV)", *decls,
                     f"(assert {si.smt_or(diffs)})", "(check-sat)", "(get-model)", ""])
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
