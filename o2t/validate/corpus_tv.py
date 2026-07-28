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


def validate_transform_ex(z3_bin: str, before_ll: str, after_ll: str, func: str, timeout: int = 15,
                          cross_check: bool = False) -> dict:
    """Track B DISPATCHER: try the scalar validator first (it covers the most -- scalar, branch/phi,
    interproc, local memory); if it DECLINES the shape (pointer-side-effect memory, vectors), fall
    through to the specialized validators. Each validator is sound within its scope and declines
    out-of-scope, so the first `proved`/`refuted` is the answer. This lets whole-function TV cover
    memory- and vector-heavy functions that the scalar path alone reports `unsupported`.

    `cross_check` replays each validator's decided query through a second SMT solver (the solver is
    otherwise unchecked -- lli/Alive2 test the ENCODING, not z3). Note the VACUITY flag exists only on
    the scalar path: it is the only refinement (UB/poison-carrying) encoding, so it is the only one
    with a definedness term that could be over-approximated; the fallback validators compare values
    and gate their refutations on `poison_risk` instead."""
    from o2t.validate.mem_state import mem_state_tv
    from o2t.validate.vec_tv import vec_tv, svec_tv
    v = si.validate_transform(z3_bin, before_ll, after_ll, func, timeout=timeout,
                              cross_check=cross_check)
    if v["status"] != "unsupported":
        return v
    # A GUARD decline is not a shape decline. `scalar_ir` declines an undef-risky transform because
    # the transform itself is out of scope (its soundness depends on an argument not being `undef`),
    # so falling through would hand the same pair to a VALUE-EQUALITY validator that has no such
    # guard -- and `ret 0 -> xor %p, %p` is value-equal, so it would be PROVED. That is a false proof
    # the synthesized-target fuzzer found at exactly this seam: the guard lived in one validator while
    # the dispatcher could route around it.
    if v.get("guard"):
        return v
    for name, validator in (("mem_state", mem_state_tv), ("vec", vec_tv), ("svec", svec_tv)):
        vv = validator(z3_bin, before_ll, after_ll, func, timeout=timeout, cross_check=cross_check)
        if vv["status"] in ("proved", "refuted"):
            return {**vv, "via": name}
    return v                                           # everything declined -> honest unsupported


def validate_file(z3_bin: str, ll_text: str, opt_bin: str = "opt", timeout: int = 15,
                  cross_check: bool = False) -> dict:
    """Run `opt -passes=instcombine` on `ll_text` once, then whole-function TV every function. Returns
    {"functions": [...per-function {name, status, ...}], "counts": {status: n}, "opt_ok": bool,
    "vacuous": n, "solver_disagreements": [...]}. Each function's z3 call is bounded by `timeout`
    seconds -- one pathological function times out (a sound decline) rather than stalling the sweep.

    `vacuous` counts proofs that hold only because the SOURCE is UB/poison on every input: valid
    refinements that say nothing about the transform, and the tell-tale of an over-approximated UB
    model. It is reported alongside `proved` so the reach number is never quietly inflated by them."""
    opt_text = si.run_instcombine(ll_text, opt_bin)
    if opt_text is None:
        return {"functions": [], "counts": {}, "opt_ok": False, "vacuous": 0,
                "solver_disagreements": []}
    results: list[dict] = []
    for fn in si.function_names(ll_text):
        try:
            v = validate_transform_ex(z3_bin, ll_text, opt_text, fn, timeout=timeout,
                                      cross_check=cross_check)
        except Exception as exc:                          # never let one function abort the sweep
            v = {"status": "error", "function": fn, "reason": str(exc)[:80]}
        results.append(v)
    counts = Counter(r["status"] for r in results)
    return {"functions": results, "counts": dict(counts), "opt_ok": True,
            "vacuous": sum(1 for r in results if r.get("vacuous") is True),
            "solver_disagreements": [r["function"] for r in results
                                     if r.get("cross_check", {}).get("status") == "disagree"]}


def _extract_define(ll_text: str, fn: str):
    """The full `define ... @fn(...) {...}` block as a standalone module string, or None."""
    import re
    m = re.search(r"define\b[^@]*@" + re.escape(fn) + r"\s*\([^)]*\)[^{]*\{", ll_text)
    if not m:
        return None
    depth, j = 1, m.end()
    while j < len(ll_text) and depth:
        depth += {"{": 1, "}": -1}.get(ll_text[j], 0)
        j += 1
    return ll_text[m.start():j]


def cross_check_file(z3_bin: str, ll_text: str, opt_bin: str = "opt", lli_bin: str | None = None,
                     alive_bin: str | None = None, timeout: int = 15) -> dict:
    """Run whole-function TV, then confirm every function O2T PROVED against the INDEPENDENT oracles
    (lli value execution and/or reference Alive2) that do NOT share O2T's SMT encoding. Returns
    {base, cross_checked, disagreements}. A non-empty `disagreements` is a FALSE PROOF on real code --
    an O2T `proved` an oracle contradicts. This operationalizes the weak-spot fixes: the oracles run on
    actual verdicts, not just demo fixtures."""
    from o2t.validate.concrete_tv import concrete_tv
    from o2t.validate.alive_diff import alive_refines
    base = validate_file(z3_bin, ll_text, opt_bin, timeout, cross_check=True)
    opt_text = si.run_instcombine(ll_text, opt_bin)
    disagreements, checked = [], 0
    if opt_text is None:
        return {"base": base["counts"], "cross_checked": 0, "disagreements": [],
                "vacuous": base.get("vacuous", 0)}
    for r in base["functions"]:
        if r["status"] != "proved":
            continue
        fn = r["function"]
        checked += 1
        if r.get("cross_check", {}).get("status") == "disagree":   # solver oracle (not the encoding)
            disagreements.append({"function": fn, "oracle": "second-solver",
                                  "witness": r["cross_check"]["solvers"]})
        if lli_bin:                                   # value oracle (real execution)
            cc = concrete_tv(lli_bin, ll_text, opt_text, fn)
            if cc["status"] == "disagree":
                disagreements.append({"function": fn, "oracle": "lli", "witness": cc.get("witness")})
        if alive_bin:                                 # poison/ground-truth oracle, per function
            bfn, afn = _extract_define(ll_text, fn), _extract_define(opt_text, fn)
            if bfn and afn and alive_refines(bfn, afn, alive_bin).get("status") == "refuted":
                disagreements.append({"function": fn, "oracle": "alive2"})
    return {"base": base["counts"], "cross_checked": checked, "disagreements": disagreements,
            "vacuous": base.get("vacuous", 0)}
