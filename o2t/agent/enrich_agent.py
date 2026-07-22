#!/usr/bin/env python3
"""Enrichment agent: an LLM DRIVES the enrichment loop; an independent oracle DECIDES.

This is the last mile of the autonomous harness. Whole-function TV declines some functions as
`unsupported` (an instruction outside the translator's fragment). This agent:

  1. DIAGNOSES the declines -- the distinct unmodeled instructions,
  2. asks the LLM to PROPOSE each instruction's SMT bit-vector semantics (provider-agnostic transport,
     the same one the pass-triage agent uses; a deterministic stub in fixtures, `claude -p` live),
  3. VALIDATES every proposal against `lli` EXECUTION (o2t/validate/enrich.validate_proposal) -- the
     LLM's proposal is DATA, ratified by an oracle it did not author, never trusted on its say-so,
  4. installs only the survivors and RE-RUNS TV, measuring the reach lift.

Trust model (unchanged): the LLM proposes; a formal/execution oracle decides. A hallucinated or unsound
model is rejected by lli before it can enable a false proof; a proposal is SMT data fed to z3, never
executed as code. lli point-wise agreement is strong evidence, not a proof (reported with the count).
"""

from __future__ import annotations

import re

from o2t.validate import enrich
from o2t.validate import scalar_ir as si

_OPERAND = "%OP%"                                      # the LLM writes its SMT model over this token


def diagnose(z3_bin: str, ll_text: str, opt_bin: str = "opt") -> dict:
    """Distinct unmodeled INSTRUCTIONS behind `unsupported` declines (structural declines -- multi-block,
    memory -- are not enrichable this way and are skipped). Maps a normalized signature -> an example."""
    opt_text = si.run_instcombine(ll_text, opt_bin)
    missing: dict = {}
    if opt_text is None:
        return missing
    for fn in si.function_names(ll_text):
        v = si.validate_transform(z3_bin, ll_text, opt_text, fn)
        reason = v.get("reason", "") if v.get("status") == "unsupported" else ""
        if reason.startswith("call") or "@llvm." in reason:      # a single missing instruction/intrinsic
            missing.setdefault(re.sub(r"%[\w.]+", "%_", reason), reason)
    return missing


def _proposal_from_reply(reply: dict) -> dict | None:
    """Turn an LLM reply into an enrich proposal (with a callable SMT builder over the operand). The
    reply's `smt` is an SMT-LIB template using %OP% for the operand -- DATA fed to z3, not code."""
    if not all(k in reply for k in ("name", "decl", "call", "regex", "smt")):
        return None
    template = reply["smt"]
    if not isinstance(template, str) or _OPERAND not in template:
        return None
    return {"name": reply["name"], "decl": reply["decl"], "call": reply["call"],
            "regex": reply["regex"], "smt": (lambda w, a, t=template: t.replace(_OPERAND, a))}


def run(ll_text: str, llm_client, z3_bin: str, lli_bin: str, opt_bin: str = "opt", width: int = 32) -> dict:
    """Diagnose -> LLM proposes -> lli validates -> install survivors -> re-run TV. Returns a report
    (diagnosed / per-instruction validated|rejected / proved_before / proved_after / installed)."""
    missing = diagnose(z3_bin, ll_text, opt_bin)
    report = {"diagnosed": sorted(missing), "enrichments": [], "installed": 0}
    handlers = []
    for sig, example in sorted(missing.items()):
        reply = llm_client.call({"task": "Propose the SMT bit-vector semantics of this LLVM instruction "
                                 f"as a function of its operand (write the operand as {_OPERAND}). "
                                 "Return JSON: name, decl, call, regex, smt.",
                                 "instruction": example, "width": width})
        proposal = _proposal_from_reply(reply) if reply else None
        if proposal is None:
            report["enrichments"].append({"instruction": example, "status": "no-proposal"})
            continue
        val = enrich.validate_proposal(proposal, z3_bin, lli_bin, width=width)
        entry = {"instruction": example, "proposal": proposal["name"], "checked": val.get("checked", 0)}
        if val["valid"]:
            handlers.append(enrich.make_handler(proposal))
            entry["status"] = "validated"
        else:
            entry["status"] = "rejected"                # the oracle caught an unsound / wrong model
            entry["disagreements"] = val.get("disagreements")
        report["enrichments"].append(entry)

    def _proved(ops):
        opt_text = si.run_instcombine(ll_text, opt_bin)
        return sum(1 for fn in si.function_names(ll_text)
                   if si.validate_transform(z3_bin, ll_text, opt_text, fn, extra_ops=ops)["status"] == "proved")

    report["proved_before"] = _proved(None)
    report["proved_after"] = _proved(handlers or None)
    report["installed"] = len(handlers)
    return report
