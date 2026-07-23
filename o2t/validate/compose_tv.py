#!/usr/bin/env python3
"""Whole-PASS composition: verify a pass PIPELINE by composing per-pass TVs via refinement transitivity.

Whole-function TV (corpus_tv) validates the net effect of ONE `opt` pass on a function -- black-box over
that pass's internal worklist fixpoint. A real pipeline runs SEVERAL passes in sequence, and a plugin
under test is usually one pass among many. This module verifies the whole pipeline compositionally:

    f0 --p1--> f1 --p2--> f2 --...--> pn --> fn

run each pass stage in turn, capture the intermediate IR, and translation-validate EACH step
(f_{i+1} refines f_i). Refinement is a preorder, so if every step is a refinement then f_n refines f_0
by TRANSITIVITY -- the whole pipeline is sound, and no direct f0->fn proof is needed (each step is a
smaller, more tractable obligation). A miscompiling pass is LOCALIZED: the step whose TV refutes names
the culprit. A step outside the scalar translator's fragment makes the chain `inconclusive` (a sound
decline for the composition), never a false whole-pipeline proof.

Scope: single-BB scalar, value-preserving scalar passes (instcombine, reassociate, early-cse, gvn,
sccp, dce, ...). Cross-function / module-level composition (function deletion, IPO) is still per-function
and not modeled here.
"""

from __future__ import annotations

from o2t.validate import scalar_ir as si


def pipeline_irs(ll_text: str, stages, opt_bin: str = "opt") -> list | None:
    """Run each pass stage in sequence; return [f0, f1, ..., fn] (IR captured after each stage)."""
    irs = [ll_text]
    for stage in stages:
        out = si.run_passes(irs[-1], stage, opt_bin)
        if out is None:
            return None
        irs.append(out)
    return irs


def compose_tv(z3_bin: str, ll_text: str, func: str, stages, opt_bin: str = "opt",
               timeout: int = 15, irs: list | None = None) -> dict:
    """Verify a pass pipeline compositionally. Returns {function, stages, steps:[{stage,status}...],
    composed, net}.

    `composed` is the WHOLE-PIPELINE verdict (does f_n refine f_0?):
      * `proved` iff EVERY step is proved -- f_n refines f_0 by refinement transitivity, with NO
        direct f0->fn proof needed (the compositional fast path);
      * otherwise the per-step chain does NOT settle the pipeline, because a refuted or unsupported
        step localizes a per-PASS issue that a LATER pass may MASK (e.g. one pass adds an unsound
        `nsw`, the next removes it -- net sound). So the honest whole-pipeline verdict is the DIRECT
        f0->fn refinement `net`: `proved` (output sound despite a buggy intermediate pass), `refuted`
        (genuinely unsound, with a witness), else `inconclusive` (net not decidable in-fragment).
    `steps` always carries the per-pass localization -- a refuted step names a miscompiling pass even
    when `composed` is `proved` because a later pass masked it. This keeps the transitivity fast path
    while NEVER falsely refuting a pipeline whose net output is actually a sound refinement.
    `irs` may be supplied to validate a specific (e.g. teeth-injected) run of intermediates."""
    if irs is None:
        irs = pipeline_irs(ll_text, stages, opt_bin)
    if irs is None or len(irs) != len(stages) + 1:
        return {"function": func, "stages": list(stages), "steps": [], "composed": "error", "net": None}
    steps = []
    for i, stage in enumerate(stages):
        v = si.validate_transform(z3_bin, irs[i], irs[i + 1], func, timeout=timeout)
        entry = {"stage": stage, "status": v["status"]}
        if v.get("witness"):
            entry["witness"] = True
        steps.append(entry)
    statuses = [s["status"] for s in steps]
    if all(s == "proved" for s in statuses):
        return {"function": func, "stages": list(stages), "steps": steps,
                "composed": "proved", "net": "proved"}   # transitivity -- direct proof implied, skipped
    # A step refuted or fell outside the fragment: consult the DIRECT f0->fn refinement so a later
    # pass masking an earlier miscompile is not reported as a whole-pipeline refutation.
    net = si.validate_transform(z3_bin, irs[0], irs[-1], func, timeout=timeout)["status"]
    composed = net if net in ("proved", "refuted") else "inconclusive"
    return {"function": func, "stages": list(stages), "steps": steps, "composed": composed, "net": net}
