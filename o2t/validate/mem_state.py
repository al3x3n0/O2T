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
changing a stored value, refutes.

A direct `call` to a DEFINED callee is inlined THROUGH the memory array: pointer arguments bind to the
caller's addresses, scalar arguments to terms, and the callee's memory effects thread back into the
caller. This reaches the two composition-axis edges Track B named last -- NON-SCALAR (pointer/void)
callees, and ARGUMENT PROMOTION (a `ptr` parameter a callee only loads, turned into a by-value scalar
parameter with the load hoisted to callers; see o2t/validate/argprom_tv.py).

Scope: single-BB per function, byte-addressable load/store and getelementptr over opaque pointer
ARGUMENTS, direct defined-callee inlining (bounded recursion). Pointer validity / null-deref UB is not
modeled, but the gap is ENFORCED rather than assumed: a NEW-DEREFERENCE guard declines any transform
whose TARGET dereferences an address the SOURCE does not (a load/store the source lacks could fault
where the source is defined). So store removal/reordering and load-hoisting-where-the-load-already-
occurred (argument promotion) prove, while an introduced dereference declines -- never a false proof.
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate import scalar_ir as si

_PARAM_RE = re.compile(r"(ptr|i(\d+))\s+(%[\w.]+)")
_MAX_CALL_DEPTH = 6


def _split_args(arg_str):
    """`ptr %q, i32 %y` -> ['ptr %q', 'i32 %y'] (scalar/pointer args, no nested commas)."""
    return [a.strip() for a in arg_str.split(",")] if arg_str.strip() else []


def _signature(ll_text, func):
    """[(kind, name)] for params -- kind is 'ptr' or an int width. None if the function is absent.
    Anchored on the `define` so a forward-reference CALL SITE above the definition is not misread as
    the signature (which would bind callee params to the caller's argument names)."""
    m = re.search(r"define\b[^@]*@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
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


def _mem_translate(ll_text, func, module=None, bind=None, depth=0):
    """Symbolically execute a single-BB function over the memory array; return
    (ret_term|None, ret_width, final_mem_term). Reuses scalar_ir for the scalar instructions.

    `module` is where callees are looked up (defaults to `ll_text`); `bind` = (env, addr, mem)
    supplies a caller's scalar-argument terms, pointer-argument ADDRESSES, and incoming memory when
    this function is being INLINED at a call site. So a `call` threads the memory array THROUGH the
    callee -- pointer-argument (non-scalar) callees and argument promotion are both verified this way.
    Bounded by `_MAX_CALL_DEPTH` (recursion / over-deep declines)."""
    module = module if module is not None else ll_text
    body = si._function_body(ll_text, func)
    if body is None:
        raise si.Unsupported(f"function {func} not found")
    if re.search(r"^\s*br\b", body, re.M):
        raise si.Unsupported("multi-block")
    if bind is not None:
        env, addr, mem = dict(bind[0]), dict(bind[1]), bind[2]
    else:
        sig = _signature(ll_text, func) or []
        env = {n: (n, w, "false", "false") for w, n in sig if w != "ptr"}
        addr = {n: n for w, n in sig if w == "ptr"}     # pointer arg -> its (opaque i64) address term
        mem = "mem0"
    ret_term, ret_width = None, None
    derefs = []                                          # address terms this function DEREFERENCES
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
            derefs.append(addr[sm.group(3)])
            mem = _store_bytes(mem, addr[sm.group(3)], vt, w)
            continue
        lm = re.fullmatch(r"(%[\w.]+)\s*=\s*load\s+i(\d+),\s+ptr\s+(%[\w.]+)(?:,.*)?", line)
        if lm:
            w = int(lm.group(2))
            if w % 8 or lm.group(3) not in addr:
                raise si.Unsupported("load width/target out of scope")
            derefs.append(addr[lm.group(3)])
            env[lm.group(1)] = (_load_bytes(mem, addr[lm.group(3)], w), w, "false", "false")
            continue
        gm = _gep(line, addr, env)                       # getelementptr on an i32 pointer -> a new address
        if gm:
            addr[gm[0]] = gm[1]
            continue
        cm = re.fullmatch(r"(?:(%[\w.]+)\s*=\s*)?call\s+(?:void|i(\d+))\s+@([\w.$]+)\s*\((.*)\)", line)
        if cm and si._function_body(module, cm.group(3)) is not None:      # a DEFINED callee -> inline it
            dst, callee, arg_str = cm.group(1), cm.group(3), cm.group(4)   # (declared/intrinsic fall thru)
            if depth >= _MAX_CALL_DEPTH:
                raise si.Unsupported("call too deep / recursion")
            cparams = _signature(module, callee) or []
            args = _split_args(arg_str)
            if len(args) != len(cparams):
                raise si.Unsupported("call arity mismatch")
            cenv, caddr = {}, {}
            for (kind, pname), a in zip(cparams, args):  # bind ptr args to ADDRESSES, scalars to terms
                am = re.fullmatch(r"(?:ptr|i\d+)\s+(\S+)", a)
                if not am:
                    raise si.Unsupported(f"call arg {a!r}")
                if kind == "ptr":
                    if am.group(1) not in addr:
                        raise si.Unsupported("pointer argument is not a known address")
                    caddr[pname] = addr[am.group(1)]
                else:
                    cenv[pname] = si._operand(am.group(1), kind, env)
            cret, cw, mem, cderefs = _mem_translate(module, callee, module, (cenv, caddr, mem), depth + 1)
            derefs.extend(cderefs)                       # the callee's dereferences thread up to the caller
            if dst is not None:                          # a value-returning call
                if cret is None:
                    raise si.Unsupported("value use of a void call")
                env[dst] = (cret, cw, "false", "false")
            continue
        si._instruction(line, env, None, None)          # scalar op (alloca/other-gep decline here)
    return ret_term, ret_width, mem, derefs


def mem_state_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15) -> dict:
    """TV a pointer-side-effect function over its memory state. Proved iff the return value AND the
    final memory state agree for all initial memories and arguments; refuted on a witness."""
    if _signature(before_ll, func) != _signature(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "signature changed"}
    try:
        rb, wb, mb, db = _mem_translate(before_ll, func)
        ra, wa, ma, da = _mem_translate(after_ll, func)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if wb != wa or (rb is None) != (ra is None):
        return {"status": "error", "function": func, "reason": "return kind changed"}
    sig = _signature(before_ll, func) or []
    decls = ["(declare-const mem0 (Array (_ BitVec 64) (_ BitVec 8)))"]
    for w, n in sig:
        decls.append(f"(declare-const {n} (_ BitVec {64 if w == 'ptr' else w}))")

    # NEW-DEREFERENCE guard: this model does not track pointer validity, so it is only sound when the
    # TARGET dereferences no address the SOURCE does not (store removal / reordering / load-hoisting
    # where the load already occurred). If some target address can differ from EVERY source address,
    # the target may fault where the source is defined -- an unmodeled null-deref UB -- so DECLINE
    # rather than mis-prove. (`(and)` over an empty source deref-set is true, so any target deref with
    # an empty source set is flagged; a target deref matching a source deref on all inputs is unsat.)
    if da:
        new_deref = si.smt_or([si.smt_and([f"(not (= {a} {b}))" for b in db]) for a in da])
        probe = "\n".join(["(set-logic QF_ABV)", *decls,
                           f"(assert {new_deref})", "(check-sat)", ""])
        try:
            pout = subprocess.run([z3_bin, "-in"], input=probe, capture_output=True, text=True,
                                  timeout=timeout).stdout
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "function": func}
        if (pout.strip().splitlines() or ["error"])[0].strip() != "unsat":
            return {"status": "unsupported", "function": func,
                    "reason": "target introduces a dereference the source lacks (null-deref UB not modeled)"}

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
        return {"status": "proved", "function": func}     # value-equal everywhere => a sound refinement
    if head == "sat":
        # This model compares VALUES, not poison-refinement. So a value mismatch is a genuine miscompile
        # ONLY when the source is poison-free; otherwise the mismatch may be a SOUND poison exploitation
        # (opt folding a poison `ashr x,x` to 0), and refuting it would be a false refutation. Decline
        # rather than refute when the source carries poison risk.
        if si.poison_risk(before_ll, func):
            return {"status": "unsupported", "function": func,
                    "reason": "value mismatch under possible poison (memory model lacks poison refinement)"}
        return {"status": "refuted", "function": func, "witness": out}
    return {"status": "error", "function": func, "reason": head}
