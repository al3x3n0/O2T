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

import re
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

# --- why a non-byte-width access DECLINES, so nobody re-derives it -------------------------------
# `store i1` / `load i1` look like the cheapest win in the decline census (i1 is everywhere in real
# code), and they are not available to this representation at all. Memory here is an array of whole
# BYTES. Alive2 tracks how many BITS of each byte were written, and the difference is observable:
#
#   store i1 %c, ptr %p ; %v = load i8, ptr %p     -- Alive2: %v is POISON
#                                                     ("written with 1 bits", byte #0)
#
# so a byte written by an i1 store is PARTIALLY DEFINED. Modelling it as a zero-padded byte asserts
# memory is more defined than reality: `store i8 0` and `store i1 false` would compare EQUAL, while
# the second leaves the padding unwritten -- a false proof. Alive2 confirms the asymmetry directly:
# `zext i1` REFINES the loaded byte, and the reverse does not verify. Taking the low bit on the load
# side does not work either -- `load i1` and `trunc (load i8)` fail to verify in BOTH directions.
#
# So the whole non-byte-width bucket (i1, i4, i67, i177) is ONE job -- per-bit definedness in the
# memory model -- not a small one, and not several. Declining is the sound answer until that exists.

def _addr_off(addr, off, pw=64):
    """Step an address by `off` BYTES, in the module's own pointer width. Doing this at a fixed 64
    bits was the model's oldest wrong assumption: address arithmetic WRAPS at 2**pw, and computing
    it wider silently keeps two addresses distinct that a narrower target makes equal -- which
    under-approximates aliasing, the direction false proofs come from."""
    return addr if off == 0 else f"(bvadd {addr} (_ bv{off} {pw}))"


def _store_bytes(mem, addr, value, width, pw=64):
    for i in range(width // 8):
        mem = f"(store {mem} {_addr_off(addr, i, pw)} ((_ extract {i * 8 + 7} {i * 8}) {value}))"
    return mem


def _load_bytes(mem, addr, width, pw=64):
    nb = width // 8
    if nb == 1:
        return f"(select {mem} {addr})"
    parts = [f"(select {mem} {_addr_off(addr, i, pw)})" for i in range(nb)]
    return f"(concat {' '.join(reversed(parts))})"


# --- poison, stored alongside the bytes ----------------------------------------------------------
# Poison is a property of a VALUE, and a value put into memory keeps it: storing poison and loading
# it back yields poison, not some defined byte. So the model carries a second array, of the same
# addresses, holding one boolean per byte. Without it this validator could only compare values, and
# needed two whole-function guards to stay sound -- refuse to prove if the target could be poison
# anywhere, refuse to refute if the source could.

def _store_poison(mp, addr, poison, width, pw=64):
    for i in range(width // 8):
        mp = f"(store {mp} {_addr_off(addr, i, pw)} {poison})"
    return mp


def _load_poison(mp, addr, width, pw=64):
    """A loaded value is poison if ANY byte it is assembled from is."""
    return si.smt_or([f"(select {mp} {_addr_off(addr, i, pw)})" for i in range(width // 8)])


def _idx_term(v, env, pw=64):
    """A gep index operand -> a term at the ADDRESS width (sign-extended: gep indices are signed).

    An index wider than the address is truncated and one narrower is sign-extended, which is what
    LLVM does -- the index is converted to the pointer's index-type width before scaling."""
    if v.is_reg:
        if v.name not in env:
            raise sem.Unsupported(f"gep index {v.name!r}")
        term, w, _, _ = env[v.name]
        if w == pw:
            return term
        return (f"((_ sign_extend {pw - w}) {term})" if w < pw
                else f"((_ extract {pw - 1} 0) {term})")
    if v.kind == "int":
        return sem.const(v.int_value, pw)
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


def _ptr_term(v, addr, apois=None, pw=64):
    """A pointer OPERAND -> its 64-bit address term (and, via `apois`, its poison), or a decline.

    Pointers used to be first-class only when they were parameters (or geps of them): `addr` was
    keyed by register name and populated from the signature alone. Everything else -- a `select`
    between two pointers, a pointer loaded from memory, a global, an `alloca` -- fell outside, which
    is why `getelementptr`, `ptrtoint`, "non-integer type ptr" and `alloca` all showed up in the
    decline census as separate buckets for ONE missing capability.

    `null` is address 0. A GLOBAL or an `alloca` still declines: those are distinct objects, and
    giving them addresses means ASSERTING they do not alias what the caller passed in -- an
    under-approximation of aliasing, which is the direction false proofs come from. That is a
    separate change with its own teeth, deliberately not folded in here.

    A pointer needs its own POISON term, not a guard: an address loaded out of memory is never
    syntactically poison-free, so requiring that would decline every `load ptr` and defeat the point.
    `apois` carries it alongside `addr`, defaulting to `false` for parameters and geps of them."""
    # ADDRESS SPACE 0 ONLY. `null` is address 0 below, and that is a claim about address space 0:
    # elsewhere null may be a perfectly good address, which is why LLVM's tests carry `_as1` and
    # `_neg` variants asserting a fold does NOT happen there. This model has no notion of address
    # spaces, so it declines rather than treating them alike -- the alternative is silently reading
    # `ptr addrspace(1)` as `ptr`.
    if v.type is not None and v.type.kind == "ptr" and v.type.addrspace != 0:
        raise sem.Unsupported(f"pointer in address space {v.type.addrspace}")
    if v.is_reg:
        if v.name in addr:
            return addr[v.name], (apois or {}).get(v.name, "false")
        raise sem.Unsupported(f"pointer {v.name!r} has no known address")
    if v.kind == "null" or (v.kind == "int" and v.int_value == 0):
        return sem.const(0, pw), "false"
    if v.is_poison:
        return sem.const(0, pw), "true"
    raise sem.Unsupported(f"pointer operand {v.kind}")


def _gep_address(inst, addr, env, pw=64):
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
    cur = _scaled(cur, _idx_term(idxs[0], env, pw), _type_size(t), pw)
    for v in idxs[1:]:
        if t.kind == "struct":
            if v.kind != "int":
                raise sem.Unsupported("variable struct field index")
            k = v.int_value
            offs, _ = _field_offsets(t)
            if k < 0 or k >= len(offs):
                raise sem.Unsupported("struct field out of range")
            cur = _addr_off(cur, offs[k], pw)
            t = t.fields[k]
        elif t.kind == "array":
            cur = _scaled(cur, _idx_term(v, env, pw), _type_size(t.elem), pw)
            t = t.elem
        else:
            raise sem.Unsupported(f"gep into {t}")
    return cur


def _scaled(base, idx, stride, pw=64):
    if idx == sem.const(0, pw):          # a zero index moves nowhere; emit no term for it
        return base
    return f"(bvadd {base} {idx})" if stride == 1 else \
        f"(bvadd {base} (bvmul {idx} (_ bv{stride} {pw})))"


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


def _ret_kind(ll_text, func):
    """The return type as a comparable tag -- `('ptr', addrspace)` or `('int', bits)`."""
    fn = ir.parse(ll_text).function(func)
    if fn is None:
        return None
    t = fn.ret_type
    return (t.kind, t.addrspace if t.kind == "ptr" else t.bits)


def _mem_translate(ll_text, func, module_text=None, bind=None, depth=0):
    """Symbolically execute a single-BB function over the memory array; return
    (ret_term|None, ret_width, final_mem_term, derefs, ret_poison, ub, final_poison_mem). Same model as the text reader it replaces:
    pointer arguments are opaque address terms, a store/load splits into little-endian bytes, and a
    direct call to a DEFINED callee is inlined THROUGH the memory array."""
    module_text = module_text if module_text is not None else ll_text
    module = ir.parse(module_text)
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    if len(fn.blocks) > 1:
        raise sem.Unsupported("multi-block")
    # THE ADDRESS WIDTH COMES FROM THE MODULE'S DATALAYOUT, and every address term in this
    # function is built at it: `null`, each gep, each loaded pointer, the memory array's own index
    # sort. It used to be 64 everywhere, which is not a cosmetic mismatch on a module declaring
    # `p:32`. There `ptrtoint ptr to i32` is EXACT rather than a truncation -- and a 64-bit model
    # reads InstCombine's fold of `(ptrtoint A | ptrtoint B) == 0` to `A == null && B == null` as
    # unsound and REFUTES it. The reverse direction is worse: address arithmetic WRAPS at 2**pw,
    # so computing gep offsets wider keeps two addresses distinct that the real target makes equal,
    # under-approximating aliasing -- where false proofs come from.
    pw = ir.parse(module_text).ptr_bits
    if pw % 8 or not 8 <= pw <= 64:
        # The byte-addressed array wants whole bytes, and above 64 the gep index handling would
        # need widening rather than truncation. Neither is hard; neither is exercised.
        raise sem.Unsupported(f"unsupported pointer width {pw}")
    if bind is not None:
        env, addr, mem, mp = dict(bind[0]), dict(bind[1]), bind[2], bind[3]
        bound_apois = dict(bind[4])
    else:
        sig = _signature(ll_text, func) or []
        env = {n: (n, w, "false", "false") for w, n in sig if w != "ptr"}
        addr = {n: n for w, n in sig if w == "ptr"}
        mem = "mem0"
        # The initial memory is arbitrary but DEFINED -- the same reading `mem0` already had. Both
        # sides start from it, so anything neither writes cancels out of the comparison.
        mp = f"((as const (Array (_ BitVec {pw}) Bool)) false)"
        bound_apois = {}
    ret_term, ret_width = None, None
    ret_poison, ub = "false", []
    derefs = []
    # Per-pointer poison, parallel to `addr` (default false). It must arrive WITH the bound
    # addresses when a callee is inlined: a pointer argument that is poison in the caller is poison
    # in the callee, and a frame that started this map empty read every argument as definitely-
    # defined. That silently dropped the callee's poison-deref UB, so the same program written with
    # the access inside a call and with it inlined by hand REFUTED against itself.
    apois: dict = dict(bound_apois)
    # Allocas get real addresses, and every register carrying one is tracked so that ACCESS through
    # them can be declined -- see the alloca handler for why that restriction is what keeps this
    # increment sound.
    allocas: list = []                   # [(symbol, size in bytes)] for this function
    stack: set = set()                   # registers holding an alloca-derived address

    for inst in fn.blocks[0].instructions:
        op = inst.op
        if op == "ret":
            if not inst.operands:                       # `ret void`
                break
            t = inst.operands[0].type
            if t.kind == "ptr":
                # A returned POINTER is its address, compared like any other 64-bit value. It was
                # previously "non-integer return" -- one of the four labels that were really this
                # one gap.
                ret_term, ret_poison = _ptr_term(inst.operands[0], addr, apois, pw)
                ret_width = pw
                break
            if not t.is_int():
                raise sem.Unsupported("non-integer return")
            ret_width = t.bits
            ret_term, _, ret_poison, rub = sem.value(inst.operands[0], env, ret_width)
            ub.append(rub)
            break
        if op == "store":
            val, ptr = inst.operands[0], inst.operands[1]
            w = val.type.bits if val.type.is_int() else None
            if w is None or w % 8 or not ptr.is_reg or ptr.name not in addr:
                raise sem.Unsupported("store width/target out of scope")
            if ptr.name in stack:
                raise sem.Unsupported("store to an alloca (its bytes are not modelled)")
            vt, _, vp, vub = sem.value(val, env, w)
            ub.append(vub)
            # Dereferencing a poison pointer is UB whichever way the access goes. The load path
            # says the same thing; a store that did NOT say it left the TARGET looking defined
            # where it is not, and a target that is secretly UB is the direction a false proof
            # comes from -- it removes the very disjunct that should have refuted the pair.
            ub.append(apois.get(ptr.name, "false"))
            derefs.append(addr[ptr.name])
            mem = _store_bytes(mem, addr[ptr.name], vt, w, pw)
            mp = _store_poison(mp, addr[ptr.name], vp, w, pw)  # the stored value keeps its poison
            continue
        if op == "load":
            ptr = inst.operands[0]
            # A pointer-typed RESULT is loaded exactly like an i64 and then becomes an address in
            # its own right, poison and all -- which is why pointers need a poison channel rather
            # than a poison-free guard: `_load_poison` is never syntactically false, so a guard
            # would decline every `load ptr` and there would be no point to any of this.
            as_ptr = inst.type.kind == "ptr"
            w = pw if as_ptr else (inst.type.bits if inst.type.is_int() else None)
            if w is None or w % 8 or not ptr.is_reg or ptr.name not in addr:
                raise sem.Unsupported("load width/target out of scope")
            if ptr.name in stack:
                raise sem.Unsupported("load from an alloca (its bytes are not modelled)")
            base = addr[ptr.name]
            ub.append(apois.get(ptr.name, "false"))     # dereferencing a poison pointer is UB
            derefs.append(base)
            lp = _load_poison(mp, base, w, pw)
            # `!noundef` PROMISES the loaded value is neither undef nor poison, and makes it UB if
            # it ever is. Both halves matter: taking the result as definite WITHOUT the UB term
            # would leave a target that violates the promise looking defined, which is the
            # direction a false proof comes from. So the result's poison becomes `false` and the
            # would-be poison joins `ub` -- the reading Alive2 gives it.
            if inst.noundef:
                ub.append(lp)
                lp = "false"
            if as_ptr:
                addr[inst.result] = _load_bytes(mem, base, pw, pw)
                apois[inst.result] = lp
                continue
            env[inst.result] = (_load_bytes(mem, base, w, pw), w, lp, "false")
            continue
        if op == "select" and inst.type.kind == "ptr":
            c, _, cp, cub = sem.value(inst.operands[0], env, 1)
            a, ap = _ptr_term(inst.operands[1], addr, apois, pw)
            b, bp = _ptr_term(inst.operands[2], addr, apois, pw)
            for src_op in (inst.operands[1], inst.operands[2]):
                if src_op.is_reg and src_op.name in stack:
                    stack.add(inst.result)
            picks_a = f"(= {c} {sem.const(1, 1)})"
            addr[inst.result] = f"(ite {picks_a} {a} {b})"
            # the condition's poison always propagates; only the SELECTED arm's does -- the same
            # rule the scalar and lane models apply, not a second reading of it
            apois[inst.result] = si.smt_or([cp, f"(ite {picks_a} {ap} {bp})"])
            ub.append(cub)
            continue
        if op == "icmp" and inst.operands[0].type.kind == "ptr":
            if inst.pred not in sem.ICMP:
                raise sem.Unsupported(f"icmp predicate {inst.pred!r}")
            # COMPARING AN ALLOCA WITH SOMETHING THAT IS NOT ONE HAS NO STATED ANSWER HERE. The
            # facts asserted about an alloca cover null (never) and another alloca (never equal);
            # against a POINTER PARAMETER the model deliberately says nothing, because the caller's
            # object extents are unknown. Left alone the solver is free to alias them and the pair
            # REFUTES -- and LLVM really does fold such a comparison using alias analysis, so this
            # would manufacture a false refutation on real code rather than a missed proof.
            # Declining keeps the over-approximation from becoming a wrong verdict.
            def _stacky(o):
                return o.is_reg and o.name in stack
            lhs, rhs = inst.operands[0], inst.operands[1]
            if _stacky(lhs) != _stacky(rhs) and not (lhs.kind == "null" or rhs.kind == "null"):
                raise sem.Unsupported("icmp between an alloca and a pointer of unknown provenance")
            a, ap = _ptr_term(inst.operands[0], addr, apois, pw)
            b, bp = _ptr_term(inst.operands[1], addr, apois, pw)
            # Comparing ADDRESSES ignores provenance, which over-approximates aliasing (two
            # pointers into different objects that happen to share an address compare equal here).
            # That direction costs refutations and cannot manufacture proofs.
            env[inst.result] = (f"(ite {sem.ICMP[inst.pred].format(a=a, b=b)} "
                                f"{sem.const(1, 1)} {sem.const(0, 1)})", 1,
                                si.smt_or([ap, bp]), "false")
            continue
        if op == "freeze" and inst.type.kind == "ptr":
            # Freeze over a pointer: the address is whatever it was, and it is no longer poison.
            # A source-side choice would need a quantifier this validator does not emit, so the
            # only case decided here is the one where there is no freedom to collapse.
            a, ap = _ptr_term(inst.operands[0], addr, apois, pw)
            if ap != "false":
                # This validator does not know which SIDE it is translating, and the quantifier
                # depends on it: a source-side choice is universal, a target-side one is free.
                # Without that distinction the only sound answer is to decline whenever there is
                # real freedom to collapse. `!noundef` on the producing load removes the freedom
                # and is what makes the freeze.ll cases decidable.
                raise sem.Unsupported("freeze over a possibly-poison pointer (side unknown here)")
            addr[inst.result] = a
            apois[inst.result] = "false"
            continue
        if op == "ptrtoint" and inst.operands[0].type.kind == "ptr":
            # An address IS a 64-bit term here, so `ptrtoint` is close to an identity -- the
            # capability the pointer-values work built without ever connecting it to the
            # instruction that most wants it. To a NARROWER integer it truncates, which loses
            # information exactly as the real instruction does: two distinct addresses can share
            # their low bits, so equalities downstream hold in more cases than reality. Like the
            # pointer `icmp` above, that over-approximates -- it costs refutations and cannot
            # manufacture proofs. Provenance is ignored for the same reason and in the same
            # direction. A WIDER target type would have to invent high bits, so it declines.
            w = inst.type.bits if inst.type.is_int() else None
            if w is None:
                raise sem.Unsupported(f"ptrtoint to {inst.type}")
            if w > pw:
                raise sem.Unsupported(f"ptrtoint to i{w} (wider than an i{pw} address)")
            a, ap = _ptr_term(inst.operands[0], addr, apois, pw)
            term = a if w == pw else f"((_ extract {w - 1} 0) {a})"
            env[inst.result] = (term, w, ap, "false")
            continue
        if op == "alloca":
            # AN ALLOCA IS A FRESH OBJECT, and the two facts these folds turn on are simply TRUE of
            # it: its address is never null, and distinct allocas never overlap. Asserting a true
            # fact is not an approximation -- it narrows the model TOWARDS reality, and the proofs
            # it enables are correct. (Disjointness from a POINTER PARAMETER is a different matter:
            # the caller's object extents are unknown, so it cannot be stated exactly and is simply
            # LEFT UNSAID. The solver may then alias them, which over-approximates -- it costs
            # refutations and cannot manufacture proofs, the same trade the pointer `icmp` makes.)
            #
            # ACCESS through an alloca is DECLINED below, deliberately. Alloca memory is dead at
            # return, so it must not take part in the final-memory comparison -- and excluding
            # bytes from that comparison is exactly the shape that hides a real difference. Keeping
            # alloca bytes out of memory entirely leaves nothing to exclude. The folds this reaches
            # are pure address reasoning; the ones that store need the external-call clobber they
            # are really waiting on anyway.
            if inst.alloc_type is None:
                raise sem.Unsupported("alloca without an allocated type")
            # An alloca always carries an element-count operand; only the constant-1 case has a
            # size this model can state, and the size is what disjointness is written in terms of.
            n_elems = inst.operands[0] if inst.operands else None
            if n_elems is not None and not (n_elems.kind == "int" and n_elems.int_value == 1):
                raise sem.Unsupported("alloca with a non-unit element count")
            sym = f"alloca_{func}_{str(inst.result).lstrip('%').replace('.', '_')}"
            allocas.append((sym, _type_size(inst.alloc_type)))
            addr[inst.result] = sym
            stack.add(inst.result)
            continue
        if op == "getelementptr":
            base = inst.operands[0]
            if base.is_reg and base.name in stack:
                stack.add(inst.result)
            addr[inst.result] = _gep_address(inst, addr, env, pw)
            continue
        if op == "call" and not inst.indirect and sem.intrinsic_name(inst.callee) is None:
            callee = module.function(inst.callee) if inst.callee else None
            if callee is not None and not callee.is_declaration:
                if depth >= _MAX_CALL_DEPTH:
                    raise sem.Unsupported("call too deep / recursion")
                cparams = _signature(module_text, callee.name) or []
                if len(inst.args) != len(cparams):
                    raise sem.Unsupported("call arity mismatch")
                cenv, caddr, capois = {}, {}, {}
                for (kind, pname), a in zip(cparams, inst.args):
                    if kind == "ptr":
                        if not a.is_reg or a.name not in addr:
                            raise sem.Unsupported("pointer argument is not a known address")
                        caddr[pname] = addr[a.name]
                        capois[pname] = apois.get(a.name, "false")
                    else:
                        cenv[pname] = sem.value(a, env, kind)
                cret, cw, mem, cderefs, cp, cub, mp, calloc = _mem_translate(
                    module_text, callee.name, module_text, (cenv, caddr, mem, mp, capois),
                    depth + 1)
                allocas.extend(calloc)   # an inlined callee's allocas are objects here too
                derefs.extend(cderefs)
                ub.append(cub)
                if inst.result is not None and not inst.type.kind == "void":
                    if cret is None:
                        raise sem.Unsupported("value use of a void call")
                    env[inst.result] = (cret, cw, cp, "false")
                continue
        sem.evaluate(inst, env, {})                     # scalar op; alloca/other shapes decline here
    # UB is a whole-function property: a div-by-zero anywhere is UB even if its result is dead.
    ub += [v[3] for v in env.values()]
    return ret_term, ret_width, mem, derefs, ret_poison, si.smt_or(ub), mp, allocas


def mem_state_tv(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15,
                 cross_check: bool = False, extra_solvers=(),
                 rlimit: int = si.DEFAULT_RLIMIT) -> dict:
    """TV a pointer-side-effect function over its memory state. Proved iff the return value AND the
    final memory state agree for all initial memories and arguments; refuted on a witness.

    `cross_check` replays the decided query through a second, independently implemented SMT solver
    (see scalar_ir.cross_check_smt). It matters most HERE: this is the only QF_ABV encoding in Track B,
    and the array theory is the least-exercised corner of the solver stack. No vacuity probe: this
    model compares VALUES and has no UB/poison term to over-approximate (the poison-risk gate on the
    refutation side is the corresponding guard)."""
    if _signature(before_ll, func) != _signature(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "signature changed"}
    # The RETURN TYPE is part of the signature too, and only became able to differ silently once a
    # returned pointer stopped declining: `ptr` and `i64` are both 64 bits here, so a pair whose
    # sides return different types passed the width check below and got a verdict. Two functions
    # returning the same 64 bits under different types then PROVED equivalent. Nothing in the
    # corpus builds such a pair -- a fold does not change a return type -- but the model should
    # not answer a question it was not asked.
    if _ret_kind(before_ll, func) != _ret_kind(after_ll, func):
        return {"status": "unsupported", "function": func, "reason": "return type changed"}
    try:
        rb, wb, mb, db, rbp, sub, mbp, alc_b = _mem_translate(before_ll, func)
        ra, wa, ma, da, rap, tub, map_, alc_a = _mem_translate(after_ll, func)
    except si.Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if wb != wa or (rb is None) != (ra is None):
        return {"status": "error", "function": func, "reason": "return kind changed"}
    sig = _signature(before_ll, func) or []
    # The two sides must agree on the address width before anything is compared -- the memory
    # array's index sort and every pointer parameter are declared at it, so a pair whose halves
    # disagree is not two spellings of one program.
    pw, pw_after = ir.parse(before_ll).ptr_bits, ir.parse(after_ll).ptr_bits
    if pw != pw_after:
        return {"status": "unsupported", "function": func, "reason": "pointer width changed"}
    decls = [f"(declare-const mem0 (Array (_ BitVec {pw}) (_ BitVec 8)))"]
    for w, n in sig:
        decls.append(f"(declare-const {n} (_ BitVec {pw if w == 'ptr' else w}))")

    # ALLOCA FACTS, asserted as ASSUMPTIONS on both queries. Each alloca is named after the
    # function and its result register, so the two sides agree on which object is which; an alloca
    # only one side has simply carries its own facts. Two facts, both TRUE of a fresh object:
    # its address is not null, and distinct objects do not overlap. Nothing is said about a
    # POINTER PARAMETER -- the caller's extents are unknown, so the solver stays free to alias
    # them, which over-approximates in the direction that costs refutations, never proofs.
    alloc_all = {n: sz for n, sz in [*alc_b, *alc_a]}
    for n in alloc_all:
        decls.append(f"(declare-const {n} (_ BitVec {pw}))")
    facts = []
    for n, sz in alloc_all.items():
        # Not null...
        facts.append(f"(not (= {n} {sem.const(0, pw)}))")
        # ...and the object does not WRAP the address space. Without this the disjointness below
        # is satisfiable while two allocas are EQUAL: at a = c = 2**pw - 2, `a + 4` wraps to 2 and
        # `a + 4 <= c` holds, so the solver reported an overlap-free model in which %a and %c are
        # the same address, and test40 (`icmp eq` between distinct allocas) REFUTED. A real object
        # never wraps, so saying it is a true fact, not a convenience.
        facts.append(f"(bvule {n} (bvadd {n} {sem.const(sz, pw)}))")
    names = sorted(alloc_all)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            # Disjoint byte ranges: a + size(a) <= b  OR  b + size(b) <= a, unsigned.
            facts.append(si.smt_or([
                f"(bvule (bvadd {a} {sem.const(alloc_all[a], pw)}) {b})",
                f"(bvule (bvadd {b} {sem.const(alloc_all[b], pw)}) {a})"]))
    assume = si.smt_and(facts) if facts else "true"

    # NEW-DEREFERENCE guard: this model does not track pointer validity, so it is only sound when the
    # TARGET dereferences no address the SOURCE does not (store removal / reordering / load-hoisting
    # where the load already occurred). If some target address can differ from EVERY source address,
    # the target may fault where the source is defined -- an unmodeled null-deref UB -- so DECLINE
    # rather than mis-prove. (`(and)` over an empty source deref-set is true, so any target deref with
    # an empty source set is flagged; a target deref matching a source deref on all inputs is unsat.)
    new_deref_text = ""
    if da:
        new_deref = si.smt_or([si.smt_and([f"(not (= {a} {b}))" for b in db]) for a in da])
        new_deref_text = new_deref
        pdecls = decls + [f"(declare-const poison_{w} (_ BitVec {w}))"
                          for w in sorted({int(m) for m in re.findall(r"\bpoison_(\d+)\b", new_deref)})]
        pdecls += sem.const_expr_decls(new_deref) + sem.uf_decls(new_deref)
        probe = "\n".join(["(set-logic QF_ABV)", *pdecls,
                           f"(assert {assume})", f"(assert {new_deref})", "(check-sat)", ""])
        try:
            pout = subprocess.run([z3_bin, "-in"], input=si.with_rlimit(probe, rlimit),
                                  capture_output=True, text=True, timeout=si.wall_backstop(timeout, rlimit)).stdout
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "function": func}
        if (pout.strip().splitlines() or ["error"])[0].strip() != "unsat":
            return {"status": "unsupported", "function": func, "guard": "new-deref",
                    "reason": "target introduces a dereference the source lacks (null-deref UB not modeled)"}

    # ALIVE2-STYLE REFINEMENT over the returned value AND the final memory. This model used to
    # compare values and lean on two whole-function guards; each stood in for an obligation it could
    # not state. Both are gone: the value carries its poison, and so does every byte of memory.
    #
    # The memory half needs NO quantifier, which is worth noticing. To refute, it is enough that
    # SOME address misbehaves -- and "some" is exactly what a satisfying assignment provides, so a
    # free probe address does the work a `forall` would have to do in the proving direction.
    ret_bad = ([si.smt_and([f"(not {rbp})", si.smt_or([rap, f"(not (= {rb} {ra}))"])])]
               if rb is not None else [])
    probe = "probe_addr"
    decls.append(f"(declare-const {probe} (_ BitVec {pw}))")
    sbyte, tbyte = f"(select {mb} {probe})", f"(select {ma} {probe})"
    spois, tpois = f"(select {mbp} {probe})", f"(select {map_} {probe})"
    mem_bad = si.smt_and([f"(not {spois})",
                          si.smt_or([tpois, f"(not (= {sbyte} {tbyte}))"])])
    refute = si.smt_and([f"(not {sub})", si.smt_or([tub, *ret_bad, mem_bad])])
    # A literal `poison` operand is an ARBITRARY value with its poison bit set, spelled
    # `poison_<width>` by the semantics layer -- and nothing here declared it, so any function
    # containing one produced an undeclared symbol and came back a solver ERROR (or, via the
    # new-deref probe, a spurious decline) instead of a verdict. `scalar_ir` closed this same gap;
    # an unconstrained constant is the right declaration, the poison-ness being carried separately.
    decls += [f"(declare-const poison_{w} (_ BitVec {w}))"
              for w in sorted({int(m) for m in re.findall(r"\bpoison_(\d+)\b", refute + new_deref_text)})]
    # ...and the symbols the SHARED semantics layer can introduce from anywhere it is called. A
    # CONSTANT EXPRESSION reaches here through any `sem.value` -- `store i32 ptrtoint (ptr @g to
    # i32), ptr %p` is enough -- and this model declared none of them, so such a function came back
    # a solver ERROR ("unknown constant cexpr_...") rather than a verdict. Every validator that
    # calls into `semantics` owes these declarations, not just the scalar one.
    decls += sem.const_expr_decls(refute, new_deref_text)
    decls += sem.uf_decls(refute, new_deref_text)
    smt = "\n".join(["(set-logic QF_ABV)", *decls,
                     f"(assert {assume})", f"(assert {refute})", "(check-sat)", "(get-model)", ""])
    try:
        out = subprocess.run([z3_bin, "-in"], input=si.with_rlimit(smt, rlimit),
                             capture_output=True, text=True, timeout=si.wall_backstop(timeout, rlimit)).stdout
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "function": func}
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    xc = ({"cross_check": si.cross_check_smt(smt, head, z3_bin, extra_solvers)}
          if cross_check and head in ("sat", "unsat") else {})
    if head == "unsat":
        return {"status": "proved", "function": func, **xc}
    if head == "sat":
        return {"status": "refuted", "function": func, "witness": out, **xc}
    if head == "unknown":                     # deterministic budget exhausted -- no verdict
        return {"status": "timeout", "function": func, "reason": "rlimit exhausted"}
    return {"status": "error", "function": func, "reason": head}
