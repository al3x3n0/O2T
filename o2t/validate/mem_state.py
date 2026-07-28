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
ARGUMENTS, direct defined-callee inlining (bounded recursion). Types come from LLVM's parse, so a
struct's field offsets are a walk over its parsed fields and the NAMED form real IR uses is
handled -- the reader this replaces could only match a struct spelled inline in the gep text. Pointer validity / null-deref UB is not
modeled, but the gap is ENFORCED rather than assumed: a NEW-DEREFERENCE guard declines any transform
whose TARGET dereferences an address the SOURCE does not (a load/store the source lacks could fault
where the source is defined). So store removal/reordering and load-hoisting-where-the-load-already-
occurred (argument promotion) prove, while an introduced dereference declines -- never a false proof.
"""

from __future__ import annotations

import subprocess

from o2t.validate import ir_model as ir
from o2t.validate import scalar_ir as si
from o2t.validate import semantics as sem

# --- the byte-addressable memory model, over LLVM's own parse -------------------------------------
# `getelementptr` is where this pays off. The reader replaced here matched THREE separate regexes
# against the gep text -- a scalar element, an array, and an integer struct -- and then re-derived the
# struct's field offsets by splitting `{i32, i64}` on commas. It therefore only understood a struct
# spelled INLINE in the gep, while real IR names its structs (`%T = type {...}`; `getelementptr %T,
# ...`), so struct-field support largely did not fire on real code. LLVM already knows the layout:
# `Instruction.source_type` arrives structured, field offsets are a walk over `type.fields`, and the
# three patterns collapse into one traversal of the index list.

def _addr_off(addr, off):
    return addr if off == 0 else f"(bvadd {addr} (_ bv{off} 64))"


def _store_bytes(mem, addr, value, width):
    for i in range(width // 8):
        mem = f"(store {mem} {_addr_off(addr, i)} ((_ extract {i * 8 + 7} {i * 8}) {value}))"
    return mem


def _load_bytes(mem, addr, width):
    nb = width // 8
    if nb == 1:
        return f"(select {mem} {addr})"
    parts = [f"(select {mem} {_addr_off(addr, i)})" for i in range(nb)]
    return f"(concat {' '.join(reversed(parts))})"


def _idx64(v, env):
    """A gep index operand -> a 64-bit SMT term (sign-extended: gep indices are signed)."""
    if v.is_reg:
        if v.name not in env:
            raise sem.Unsupported(f"gep index {v.name!r}")
        term, w, _, _ = env[v.name]
        if w == 64:
            return term
        return f"((_ sign_extend {64 - w}) {term})" if w < 64 else f"((_ extract 63 0) {term})"
    if v.kind == "int":
        return sem.const(v.int_value, 64)
    raise sem.Unsupported(f"gep index {v.kind}")


def _type_size(t):
    """Size in BYTES of a modeled type. Only whole-byte integers and aggregates of them."""
    if t.is_int():
        if t.bits % 8:
            raise sem.Unsupported(f"non-byte type i{t.bits}")
        return t.bits // 8
    if t.kind == "array":
        return t.n * _type_size(t.elem)
    if t.kind == "struct":
        return _field_offsets(t)[1]
    raise sem.Unsupported(f"type {t}")


def _field_offsets(t):
    """(byte offset of each field, total size) for an integer struct. Alignment = field size unless
    the struct is packed -- the same rule the text reader implemented, now over parsed fields."""
    offs, cur = [], 0
    for f in t.fields:
        sz = _type_size(f)
        if not t.packed and sz:
            cur = (cur + sz - 1) // sz * sz
        offs.append(cur)
        cur += sz
    return offs, cur


def _gep_address(inst, addr, env):
    """`getelementptr` -> a new BYTE address. One traversal of the index list over the structured
    source type, replacing three separate text patterns and the struct-layout regex."""
    base = inst.operands[0]
    if not base.is_reg or base.name not in addr:
        raise sem.Unsupported("gep on a non-pointer base")
    cur = addr[base.name]
    t = inst.source_type
    if t is None:
        raise sem.Unsupported("gep without a source type")
    idxs = inst.operands[1:]
    if not idxs:
        raise sem.Unsupported("gep without indices")
    # The first index strides over the source type itself.
    cur = _scaled(cur, _idx64(idxs[0], env), _type_size(t))
    for v in idxs[1:]:
        if t.kind == "struct":
            if v.kind != "int":
                raise sem.Unsupported("variable struct field index")
            k = v.int_value
            offs, _ = _field_offsets(t)
            if k < 0 or k >= len(offs):
                raise sem.Unsupported("struct field out of range")
            cur = _addr_off(cur, offs[k])
            t = t.fields[k]
        elif t.kind == "array":
            cur = _scaled(cur, _idx64(v, env), _type_size(t.elem))
            t = t.elem
        else:
            raise sem.Unsupported(f"gep into {t}")
    return cur


def _scaled(base, idx, stride):
    if idx == sem.const(0, 64):          # a zero index moves nowhere; emit no term for it
        return base
    return f"(bvadd {base} {idx})" if stride == 1 else \
        f"(bvadd {base} (bvmul {idx} (_ bv{stride} 64)))"


_MAX_CALL_DEPTH = 6


def _signature(ll_text, func):
    """[(kind, name)] for the parameters -- kind is 'ptr' or an integer width."""
    fn = ir.parse(ll_text).function(func)
    if fn is None:
        return None
    out = []
    for p in fn.params:
        if p.type.kind == "ptr":
            out.append(("ptr", p.name))
        elif p.type.is_int():
            out.append((p.type.bits, p.name))
    return out


def _mem_translate(ll_text, func, module_text=None, bind=None, depth=0):
    """Symbolically execute a single-BB function over the memory array; return
    (ret_term|None, ret_width, final_mem_term, derefs). Same model as the text reader it replaces:
    pointer arguments are opaque address terms, a store/load splits into little-endian bytes, and a
    direct call to a DEFINED callee is inlined THROUGH the memory array."""
    module_text = module_text if module_text is not None else ll_text
    module = ir.parse(module_text)
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    if len(fn.blocks) > 1:
        raise sem.Unsupported("multi-block")
    if bind is not None:
        env, addr, mem = dict(bind[0]), dict(bind[1]), bind[2]
    else:
        sig = _signature(ll_text, func) or []
        env = {n: (n, w, "false", "false") for w, n in sig if w != "ptr"}
        addr = {n: n for w, n in sig if w == "ptr"}
        mem = "mem0"
    ret_term, ret_width = None, None
    derefs = []

    for inst in fn.blocks[0].instructions:
        op = inst.op
        if op == "ret":
            if not inst.operands:                       # `ret void`
                break
            t = inst.operands[0].type
            if not t.is_int():
                raise sem.Unsupported("non-integer return")
            ret_width = t.bits
            ret_term = sem.value(inst.operands[0], env, ret_width)[0]
            break
        if op == "store":
            val, ptr = inst.operands[0], inst.operands[1]
            w = val.type.bits if val.type.is_int() else None
            if w is None or w % 8 or not ptr.is_reg or ptr.name not in addr:
                raise sem.Unsupported("store width/target out of scope")
            vt = sem.value(val, env, w)[0]
            derefs.append(addr[ptr.name])
            mem = _store_bytes(mem, addr[ptr.name], vt, w)
            continue
        if op == "load":
            ptr = inst.operands[0]
            w = inst.type.bits if inst.type.is_int() else None
            if w is None or w % 8 or not ptr.is_reg or ptr.name not in addr:
                raise sem.Unsupported("load width/target out of scope")
            derefs.append(addr[ptr.name])
            env[inst.result] = (_load_bytes(mem, addr[ptr.name], w), w, "false", "false")
            continue
        if op == "getelementptr":
            addr[inst.result] = _gep_address(inst, addr, env)
            continue
        if op == "call" and not inst.indirect and sem.intrinsic_name(inst.callee) is None:
            callee = module.function(inst.callee) if inst.callee else None
            if callee is not None and not callee.is_declaration:
                if depth >= _MAX_CALL_DEPTH:
                    raise sem.Unsupported("call too deep / recursion")
                cparams = _signature(module_text, callee.name) or []
                if len(inst.args) != len(cparams):
                    raise sem.Unsupported("call arity mismatch")
                cenv, caddr = {}, {}
                for (kind, pname), a in zip(cparams, inst.args):
                    if kind == "ptr":
                        if not a.is_reg or a.name not in addr:
                            raise sem.Unsupported("pointer argument is not a known address")
                        caddr[pname] = addr[a.name]
                    else:
                        cenv[pname] = sem.value(a, env, kind)
                cret, cw, mem, cderefs = _mem_translate(module_text, callee.name, module_text,
                                                       (cenv, caddr, mem), depth + 1)
                derefs.extend(cderefs)
                if inst.result is not None and not inst.type.kind == "void":
                    if cret is None:
                        raise sem.Unsupported("value use of a void call")
                    env[inst.result] = (cret, cw, "false", "false")
                continue
        sem.evaluate(inst, env, {})                     # scalar op; alloca/other shapes decline here
    return ret_term, ret_width, mem, derefs


def mem_state_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15,
                 cross_check: bool = False, extra_solvers=()) -> dict:
    """TV a pointer-side-effect function over its memory state. Proved iff the return value AND the
    final memory state agree for all initial memories and arguments; refuted on a witness.

    `cross_check` replays the decided query through a second, independently implemented SMT solver
    (see scalar_ir.cross_check_smt). It matters most HERE: this is the only QF_ABV encoding in Track B,
    and the array theory is the least-exercised corner of the solver stack. No vacuity probe: this
    model compares VALUES and has no UB/poison term to over-approximate (the poison-risk gate on the
    refutation side is the corresponding guard)."""
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
    xc = ({"cross_check": si.cross_check_smt(smt, head, z3_bin, extra_solvers)}
          if cross_check and head in ("sat", "unsat") else {})
    if head == "unsat":
        return {"status": "proved", "function": func, **xc}   # value-equal everywhere => sound refinement
    if head == "sat":
        # This model compares VALUES, not poison-refinement. So a value mismatch is a genuine miscompile
        # ONLY when the source is poison-free; otherwise the mismatch may be a SOUND poison exploitation
        # (opt folding a poison `ashr x,x` to 0), and refuting it would be a false refutation. Decline
        # rather than refute when the source carries poison risk.
        if si.poison_risk(before_ll, func):
            return {"status": "unsupported", "function": func,
                    "reason": "value mismatch under possible poison (memory model lacks poison refinement)"}
        return {"status": "refuted", "function": func, "witness": out, **xc}
    return {"status": "error", "function": func, "reason": head}
