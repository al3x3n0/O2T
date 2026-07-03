#!/usr/bin/env python3
"""BOUNDED closed-loop translation validation for loop-CFG transforms (loop-rotate, unswitch).

Loop transforms restructure the loop's control flow but keep a loop, so the acyclic validators
(scalar_ir / mem2reg_ir) cannot consume them directly, and an unbounded proof needs an induction
invariant. This takes the standard BOUNDED route: for a loop with a constant (compile-time) trip
count, fully UNROLL both the original and the transformed loop to acyclic straight-line IR and prove
those equivalent for all inputs (the existing acyclic executor). So it validates that the loop
transform preserved the computation for that trip count -- a real, sound BOUNDED check with
two-sided teeth (a miscompiling transform makes the unrolled forms differ).

Concretely: `ref  = normalize(L)` and `test = normalize(transform(L))`, where
`normalize = loop-simplify, loop-unroll, simplifycfg, instsimplify` runs the SAME semantics-
preserving cleanup on both, so only the transform-under-test differs. The final acyclic equivalence
is proved semantically (instruction order / folding differences don't matter). A loop whose trip
count is NOT a compile-time constant is not fully unrolled -> the executor declines `unsupported`
(the bound does not apply), never a false proof.
"""

from __future__ import annotations

from o2t.validate import scalar_ir, mem2reg_ir

NORMALIZE = "loop-simplify,loop-unroll,simplifycfg,instsimplify"


def normalize(src_text, transform, opt_bin="opt"):
    """Run the cleanup pipeline (optionally with `transform` first) and return acyclic IR."""
    pipeline = f"loop-simplify,{transform},{NORMALIZE}" if transform else NORMALIZE
    return scalar_ir.run_passes(src_text, pipeline, opt_bin)


def _prove_equiv(z3_bin, ref, test, func):
    """Prove two acyclic functions return the same value -- scalar (single-block) or mem2reg
    (multi-block + phi) executor, whichever supports the shape."""
    r = scalar_ir.validate_transform(z3_bin, ref, test, func)
    if r["status"] == "unsupported":
        r = mem2reg_ir.validate_mem2reg(z3_bin, ref, test, func)
    return r


def validate_loop_transform(z3_bin, src_text, transform, func, opt_bin="opt"):
    """Bounded-validate one loop-CFG transform on `func`: normalize with and without it, prove the
    fully-unrolled forms equal. Returns a verdict dict (status proved|refuted|unsupported|error)."""
    ref = normalize(src_text, "", opt_bin)
    test = normalize(src_text, transform, opt_bin)
    if ref is None or test is None:
        return {"status": "error", "function": func, "reason": f"opt failed for {transform}"}
    out = _prove_equiv(z3_bin, ref, test, func)
    out.setdefault("function", func)
    out["transform"] = transform
    return out


def function_names(ll_text):
    return scalar_ir.function_names(ll_text)
