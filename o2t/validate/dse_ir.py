#!/usr/bin/env python3
"""Closed-loop translation validation for DSE: prove the REAL `opt -passes=dse` output, not a model.

The source-recovery and canonical memory contracts prove an ABSTRACT model of dead-store
elimination. This closes the contract<->IR gap for DSE the way `cfg_shape` does for if-conversion:
it runs the actual `opt -passes=dse` pass, parses the LITERAL store/load instructions of the
before (input) and after (optimized) IR, and proves them equivalent over a theory of arrays
(`memory_model`) -- so the proof is about the instructions the compiler really emitted.

Each straight-line function is parsed into the shared `memory_model` op list (store/load), with
every distinct pointer SSA name a distinct address symbol (NO alias assumption -- sound, all
aliasings considered) and every stored value/literal a value symbol. Soundness is final-memory
equivalence for ALL memories/addresses/values AND per-load value equivalence (an eliminated store
whose value a surviving load observes is caught even when the final memory is unchanged): a correct
DSE is proved; a faulty output that drops a still-live store is REFUTED with a colliding-address
witness (the translation-validation teeth).

The word-level model is sound only for uniformly-word-width accesses to escaping memory. To stay
sound it DECLINES (`unsupported`) rather than guess on: a `volatile`/`atomic` store or load, an
`atomicrmw`/`cmpxchg`/`fence`/`va_arg`, a `memcpy`/`memset`/`memmove` or any `call` (may touch
memory), a vector/aggregate/otherwise non-scalar store/load shape, and mixed-width accesses to one
address (a narrower store partially overwrites a wider one -- the word model would falsely treat it
as a full kill). Those are exactly the shapes whose `\\S+`-swallowing predecessor mis-modeled as a
plain word store.
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate import ir_model as ir
from o2t.validate import memory_model as mm

# Memory ops the word model does not soundly capture -> decline rather than silently skip. Checked
# against LLVM's own opcodes and flags now, not by scanning the instruction text: the regex could not
# tell a `volatile` store from the word "volatile" appearing anywhere on the line, and it matched
# `call` before ever inspecting what was called.
_UNMODELED_OPS = {"atomicrmw", "cmpxchg", "fence", "va_arg"}
_UNMODELED_INTRINSICS = ("llvm.memcpy", "llvm.memset", "llvm.memmove")


class Unsupported(Exception):
    pass


def _sym(value):
    """An operand -> a stable memory-model symbol name."""
    if getattr(value, "kind", None) == "int":
        v = value.int_value
        return "lit_" + str(v).lstrip("-")
    name = value.name if getattr(value, "is_reg", False) else (value.raw.get("name") or "")
    return re.sub(r"\W", "_", str(name).lstrip("%@"))


def parse_mem_ops(ll_text, func):
    """Parse a straight-line function into the memory-model op list, in program order. Raises
    `Unsupported` on any memory op the word model cannot soundly represent (so the case is declined,
    never mis-modeled), and returns None only when the function is absent."""
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        return None
    ops, widths = [], {}                       # widths: address symbol -> {access type strings}
    for inst in fn.instructions():
        op = inst.op
        if op in _UNMODELED_OPS:
            raise Unsupported(f"unmodeled memory op: {op}")
        if op == "call":
            callee = inst.callee or "<indirect>"
            if any(callee.lstrip("@").startswith(i) for i in _UNMODELED_INTRINSICS):
                raise Unsupported(f"unmodeled memory op: {callee}")
            raise Unsupported(f"call in a DSE candidate: {callee}")
        if op == "store":
            if inst.raw.get("volatile"):
                raise Unsupported("volatile store")
            val, ptr = inst.operands[0], inst.operands[1]
            if not (val.type.is_int() or val.type.kind == "ptr"):
                raise Unsupported(f"unmodeled store type {val.type}")
            addr = _sym(ptr)
            widths.setdefault(addr, set()).add(str(val.type))
            ops.append(mm._store(addr, _sym(val)))
            continue
        if op == "load":
            if inst.raw.get("volatile"):
                raise Unsupported("volatile load")
            if not (inst.type.is_int() or inst.type.kind == "ptr"):
                raise Unsupported(f"unmodeled load type {inst.type}")
            addr = _sym(inst.operands[0])
            widths.setdefault(addr, set()).add(str(inst.type))
            ops.append(mm._load(re.sub(r"\W", "_", str(inst.result).lstrip("%")), addr))
            continue
        # otherwise a non-memory instruction (arithmetic, br, ret, gep, phi, ...) -> ignore.
    mixed = sorted(a for a, ws in widths.items() if len(ws) > 1)
    if mixed:
        raise Unsupported(f"mixed-width access at address {mixed[0]} (partial overwrite, needs byte model)")
    return ops


def run_dse(src_text, opt_bin="opt"):
    proc = subprocess.run([opt_bin, "-passes=dse", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def validate_dse(z3_bin, src_text, opt_text, func):
    """Validate one function's real DSE: parse before/after IR and prove both final-memory AND
    per-surviving-load equivalence. Returns a verdict dict (status proved|refuted|unsupported|error)."""
    try:
        before = parse_mem_ops(src_text, func)
        after = parse_mem_ops(opt_text, func)
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if before is None or after is None:
        return {"status": "unsupported", "reason": "function not found in before/after IR"}
    if not any(o["op"] == "store" for o in before):
        return {"status": "unsupported", "reason": "no stores to validate"}
    # Soundness boundary: the final-memory observable is exact only for ESCAPING memory (params /
    # globals), where DSE removes a store only if a later store overwrites it. A local `alloca`
    # can be dead at function exit, so DSE may legally drop a store the final-memory observable
    # would treat as live -- that needs escape analysis, so we decline rather than over-refute.
    fn = ir.parse(src_text).function(func)
    if fn is not None and any(i.op == "alloca" for i in fn.instructions()):
        return {"status": "unsupported", "reason": "local alloca (non-escaping) needs escape analysis"}
    out = {"status": "proved", "function": func,
           "before_ops": len(before), "after_ops": len(after)}
    # Observe BOTH the final memory and every load that survives on both sides: an eliminated store
    # whose value a surviving load reads is refuted even when the final memory matches.
    loads = {o["name"] for o in before if o["op"] == "load"} & {o["name"] for o in after if o["op"] == "load"}
    for observable in ["memory", *(f"load:{n}" for n in sorted(loads))]:
        status, info = mm.prove_memory_transform(z3_bin, before, after, observable=observable)
        if status != "proved":
            out["status"] = status
            out["observable"] = observable
            if status == "refuted":
                out["witness"] = info.get("witness", {})
            else:
                out["reason"] = info.get("reason", "")
            return out
    return out


def function_names(ll_text):
    return ir.parse(ll_text).defined_names
