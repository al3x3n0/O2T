#!/usr/bin/env python3
"""Whole-function observational TV over a corpus: real IR in, real `opt`, whole transform proved sound.

The per-fold observational check (observe.py) grounds ONE recovered fold against `opt` on minimal IR.
This goes end-to-end on real code: for every function in a `.ll` corpus it runs the ACTUAL
`opt -passes=instcombine` and translation-validates the WHOLE function's transformation
(scalar_ir.validate_transform, an Alive2-style refinement proof). It verifies the *composition* of
whatever folds fired -- not an isolated obligation -- so it attacks the "obligations, not passes" gap
directly. Anything scalar_ir cannot model (stores, multi-block, vectors, calls) is `unsupported` and
declined, never mis-proved; a real miscompile would be `refuted` with a witness.
"""

from __future__ import annotations

from collections import Counter

from o2t.validate import scalar_ir as si


def validate_file(z3_bin: str, ll_text: str, opt_bin: str = "opt", timeout: int = 15) -> dict:
    """Run `opt -passes=instcombine` on `ll_text` once, then whole-function TV every function. Returns
    {"functions": [...per-function {name, status, ...}], "counts": {status: n}, "opt_ok": bool}. Each
    function's z3 call is bounded by `timeout` seconds -- one pathological function times out (a sound
    decline) rather than stalling the whole sweep."""
    opt_text = si.run_instcombine(ll_text, opt_bin)
    if opt_text is None:
        return {"functions": [], "counts": {}, "opt_ok": False}
    results: list[dict] = []
    for fn in si.function_names(ll_text):
        try:
            v = si.validate_transform(z3_bin, ll_text, opt_text, fn, timeout=timeout)
        except Exception as exc:                          # never let one function abort the sweep
            v = {"status": "error", "function": fn, "reason": str(exc)[:80]}
        results.append(v)
    counts = Counter(r["status"] for r in results)
    return {"functions": results, "counts": dict(counts), "opt_ok": True}
