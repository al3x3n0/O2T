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
            if int(sm.group(1)) != 32 or sm.group(3) not in addr:
                raise si.Unsupported("store width/target out of scope")
            vt, _, _, _ = si._operand(sm.group(2), 32, env)
            mem = f"(store {mem} {addr[sm.group(3)]} {vt})"
            continue
        lm = re.fullmatch(r"(%[\w.]+)\s*=\s*load\s+i(\d+),\s+ptr\s+(%[\w.]+)(?:,.*)?", line)
        if lm:
            if int(lm.group(2)) != 32 or lm.group(3) not in addr:
                raise si.Unsupported("load width/target out of scope")
            env[lm.group(1)] = (f"(select {mem} {addr[lm.group(3)]})", 32, "false", "false")
            continue
        si._instruction(line, env, None, None)          # scalar op (alloca/gep/etc. decline here)
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
    decls = ["(declare-const mem0 (Array (_ BitVec 64) (_ BitVec 32)))"]
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
