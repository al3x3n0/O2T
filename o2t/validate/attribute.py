#!/usr/bin/env python3
"""Attribution: the A<->B seam -- explain a proved whole-function transform by recovered folds.

Track B (corpus_tv) proves opt's WHOLE-function transform `f -> f'` sound; Track A recovers individual
folds from pass SOURCE. Attribution welds them: for a function `f` that opt rewrites to `f'`, find a
recovered fold whose `(before, after)` matches the transform -- i.e. some variable mapping makes the
fold's `before` equal `f` and its `after` equal `f'` (checked by SMT, so an equivalent form still
matches). A hit is an EXPLAINED transform: sound (Track B) AND accounted for by a source-recovered
fold (Track A). A miss is honest RESIDUE -- opt did something no single recovered fold explains (a
composed transform, or a fold O2T has not recovered) -- which is exactly the work-list for enrichment.

v1 scope: single-BB scalar functions; single-fold attribution under a positional/permuted mapping of
the fold's variables onto the function's parameters. Multi-fold compositions fall to residue (sound
decline), never a false attribution.
"""

from __future__ import annotations

import re
from itertools import permutations

from o2t.intent import pass_graph as pg
from o2t.validate import scalar_ir as si
from o2t.validate.observe import _terms_equal


def _fold_side_smt(fold: dict, side: str, param_names: tuple, z3_bin: str):
    """Emit a fold's `before`/`after` as IR with its variables renamed to `param_names` (positional),
    then translate to (params, ret_term) -- comparable to a corpus function's translate. None if the
    fold has no IR lowering or the renamed IR is outside the translator's fragment."""
    ir = pg.to_llvm_ir(fold, side, "g")
    if ir is None:
        return None
    for var, pname in zip(fold["variables"], param_names):
        ir = re.sub(rf"%{re.escape(var)}\b", pname, ir)    # %x -> %A  (params only; lets are %tN)
    try:
        params, ret, _, _, _ = si.translate(ir, "g")
    except si.Unsupported:
        return None
    return params, ret


def attribute_function(z3_bin: str, ll_text: str, opt_text: str, func: str, folds: list[dict]) -> dict:
    """Attribute one function's opt transform to a recovered fold, or mark it residue/unsupported."""
    try:
        pf, f_ret, wf, _, _ = si.translate(ll_text, func)
        po, o_ret, wo, _, _ = si.translate(opt_text, func)
    except si.Unsupported as exc:
        return {"function": func, "status": "unsupported", "reason": str(exc)}
    if po != pf:
        return {"function": func, "status": "unsupported", "reason": "signature changed"}
    param_names = sorted(pf)
    for fold in folds:
        nvars = len(fold.get("variables", []))
        if nvars == 0 or nvars > len(param_names):
            continue
        for perm in permutations(param_names, nvars):
            b = _fold_side_smt(fold, "before", perm, z3_bin)
            a = _fold_side_smt(fold, "after", perm, z3_bin)
            if b is None or a is None:
                continue
            if _terms_equal(z3_bin, pf, f_ret, b[1]) and _terms_equal(z3_bin, pf, o_ret, a[1]):
                return {"function": func, "status": "attributed",
                        "fold": fold.get("marker", "?"), "mapping": list(perm)}
    return {"function": func, "status": "residue"}


def attribute_file(z3_bin: str, ll_text: str, folds: list[dict], opt_bin: str = "opt") -> dict:
    """Run opt once, then attribute every function's transform to a recovered fold. Returns
    {"functions": [...], "counts": {status: n}}. `folds` is a recovered-fold corpus (Track A)."""
    from collections import Counter
    opt_text = si.run_instcombine(ll_text, opt_bin)
    if opt_text is None:
        return {"functions": [], "counts": {}}
    results = [attribute_function(z3_bin, ll_text, opt_text, fn, folds)
               for fn in si.function_names(ll_text)]
    return {"functions": results, "counts": dict(Counter(r["status"] for r in results))}
