#!/usr/bin/env python3
"""Enrichment: grow O2T's verification vocabulary, gated by an INDEPENDENT oracle.

When whole-function TV declines a function as `unsupported`, the cause is often a single instruction
outside scalar_ir.translate's fragment (an intrinsic it does not yet model). An enrichment PROPOSES
that instruction's SMT semantics -- but a proposed semantics could be WRONG (an unsound model), so it
is never trusted on its own say-so. It is validated against `lli` EXECUTION: the real intrinsic is run
on a battery of concrete inputs (LLVM's own semantics), and the proposed SMT model must agree on every
one. Only a proposal that survives is installed as an `extra_ops` handler for translate -- so O2T's
verifier grows, but an independent oracle (not the proposer) decides whether the growth is sound.

This is the load-bearing discipline for an autonomous harness that enriches O2T: the LLM may propose
the routine, but `lli` (or Alive2, or the mutation catalog) judges it. Point-wise lli agreement is
strong EVIDENCE, not a proof -- reported as such (checked count), never as certainty.
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate import scalar_ir as si

# Edge-case + spread 32-bit inputs -- enough to catch a wrong byte order / bit model, cheap to run.
_INPUTS32 = [0x00000000, 0x00000001, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000,
             0x12345678, 0xDEADBEEF, 0x80000000, 0x7FFFFFFF, 0xFFFFFFFF, 0x01020304, 0xCAFEBABE]


def make_handler(proposal: dict):
    """A translate `extra_ops` handler `(rhs, env) -> (smt, w, poison, ub) | None` from a proposal
    {regex, smt}. Poison/UB propagate from operands; a pure intrinsic adds none of its own."""
    rx = re.compile(proposal["regex"])
    build = proposal["smt"]

    def handler(rhs, env):
        m = rx.fullmatch(rhs)
        if m is None:
            return None
        w = int(m.group(1))
        ops = [si._operand(g.rstrip(","), w, env) for g in m.groups()[1:]]
        args = [o[0] for o in ops]
        return build(w, *args), w, si.smt_or([o[2] for o in ops]), si.smt_or([o[3] for o in ops])

    return handler


def _lli_outputs(proposal: dict, inputs, width: int, lli_bin: str, timeout: int = 30):
    """Run the UNARY intrinsic on each input via lli (LLVM's real semantics); return concrete outputs."""
    decl = proposal["decl"].format(w=width)
    call = proposal["call"].format(w=width, a="%x")
    lines = [decl, "declare i32 @printf(ptr, ...)", '@.f = private constant [4 x i8] c"%u\\0A\\00"',
             f"define i{width} @t(i{width} %x) {{\n  %r = {call}\n  ret i{width} %r\n}}",
             "define i32 @main() {"]
    for i, v in enumerate(inputs):
        lines.append(f"  %v{i} = call i{width} @t(i{width} {v})")
        if width == 32:
            reg = f"%v{i}"
        elif width < 32:
            lines.append(f"  %z{i} = zext i{width} %v{i} to i32"); reg = f"%z{i}"
        else:
            lines.append(f"  %z{i} = trunc i{width} %v{i} to i32"); reg = f"%z{i}"
        lines.append(f"  call i32 (ptr, ...) @printf(ptr @.f, i32 {reg})")
    lines += ["  ret i32 0", "}"]
    try:
        out = subprocess.run([lli_bin], input="\n".join(lines) + "\n",
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    vals = out.stdout.split()
    return [int(v) for v in vals] if len(vals) == len(inputs) else None


def _smt_outputs(proposal: dict, inputs, width: int, z3_bin: str):
    """Evaluate the proposed SMT model on each input via z3 get-value -- the model's own outputs."""
    smt_expr = proposal["smt"](width, "x")
    outs = []
    for v in inputs:
        q = (f"(declare-const x (_ BitVec {width}))\n(assert (= x (_ bv{v} {width})))\n"
             f"(declare-const r (_ BitVec {width}))\n(assert (= r {smt_expr}))\n"
             "(check-sat)\n(get-value (r))\n")
        out = subprocess.run([z3_bin, "-in"], input=q, capture_output=True, text=True).stdout
        m = re.search(r"\(\s*r\s+#x([0-9a-fA-F]+)\s*\)", out)
        if m is None:
            return None
        outs.append(int(m.group(1), 16))
    return outs


def validate_proposal(proposal: dict, z3_bin: str, lli_bin: str, width: int = 32, inputs=None) -> dict:
    """Validate a proposed instruction semantics against lli EXECUTION on a battery of inputs. Returns
    {valid, checked, disagreements}. Valid iff the proposed SMT model matches lli on every input --
    strong evidence (point-wise agreement with LLVM's own semantics), reported with the checked count."""
    inputs = list(inputs if inputs is not None else _INPUTS32)
    lli = _lli_outputs(proposal, inputs, width, lli_bin)
    smt = _smt_outputs(proposal, inputs, width, z3_bin)
    if lli is None or smt is None:
        return {"valid": False, "checked": 0, "reason": "oracle unavailable"}
    disagree = [{"input": v, "lli": l, "smt": s} for v, l, s in zip(inputs, lli, smt) if l != s]
    return {"valid": not disagree, "checked": len(inputs), "disagreements": disagree[:3]}


# --- a first proposal: llvm.bswap (byte reversal) -- correct + a deliberately wrong twin for teeth ---
def _bswap_smt(w: int, a: str) -> str:
    parts = [f"((_ extract {i * 8 + 7} {i * 8}) {a})" for i in range(w // 8)]  # byte0(low)..byteN(high)
    return f"(concat {' '.join(parts)})" if len(parts) > 1 else a       # concat is MSB-first -> reversed


BSWAP = {
    "name": "bswap",
    "decl": "declare i{w} @llvm.bswap.i{w}(i{w})",
    "call": "call i{w} @llvm.bswap.i{w}(i{w} {a})",
    "regex": r"call\s+i(\d+)\s+@llvm\.bswap\.i\d+\(\s*i\d+\s+(\S+?)\s*\)",
    "smt": _bswap_smt,
}
# TEETH: an UNSOUND model (identity -- forgets to reverse). lli disagrees -> rejected, never installed.
BSWAP_WRONG = {**BSWAP, "name": "bswap[WRONG-identity]", "smt": lambda w, a: a}
