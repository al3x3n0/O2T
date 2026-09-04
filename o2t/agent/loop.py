#!/usr/bin/env python3
"""Per-pass agent loop: LLM observes evidence, picks a whitelisted action, a real tool runs.

Loop contract:
- ONE LLM call per step; the request carries the pass, accumulated evidence, remaining budget,
  and the advertised action registry with arg schemas (`answer_schema` mirrors brain.py's style).
- An invalid reply (transport failure, malformed JSON, unknown action, bad args) EXECUTES
  NOTHING: it becomes an `invalid-action` observation the LLM sees next turn. Two consecutive
  invalid replies degrade the pass (`status: degraded`), and a dead LLM command strikes out in two
  turns. A VALID reply that makes NO PROGRESS -- the same action, the same arguments and the same
  observation as an earlier step -- strikes the same way, because a model can spin just as
  effectively with well-formed replies as with malformed ones (observed live: three of four calls
  spent re-running one `classify` that answered "no family matched" every time).
- Budget exhaustion winds down cleanly with partial evidence kept.
- Observations are truncated before entering the prompt; full tool verdicts live untruncated in
  `state.formal_checks` for the report.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from o2t.agent.actions import _truncate, advertise, validate_response

# Residue: the passes where deterministic orchestration left a human decision open.
#
# `planned` was missing, and it is the plainest residue of all: it means checks were planned and
# produced no attributable verdict. It became reachable in earnest once the headline stopped
# counting proofs from checks that never read the pass -- a vendor pass whose only source-targeted
# check answered `inconclusive` now lands here instead of being certified `proved`, which is
# exactly the pass a human would want triaged. It was previously excluded on the reasoning that
# applies to `skipped` too (the agent cannot conjure a missing binary), but `skipped` is already
# residue: the agent's move is to route to a DIFFERENT strategy, not to re-run the one that could
# not start. Measured: on that snippet the agent then dispatches `slp-source` and refutes the
# planted unguarded FP reduction the whole plan had missed.
RESIDUE_STATUSES = ("unclassified", "advisory", "skipped", "error", "refuted", "planned")
_EXCERPT_CHARS = 6000


def select_residue(report: dict) -> list[dict]:
    """Entries worth spending LLM budget on. A `refuted` entry is included for DIAGNOSIS (explain
    the witness, propose a fix direction) -- never to relitigate the formal refutation."""
    out = []
    for entry in report.get("passes", []):
        status = (entry.get("headline") or {}).get("status")
        if status in RESIDUE_STATUSES:
            out.append(entry)
    return out


@dataclass
class AgentState:
    source: Path | None
    pass_name: str | None
    mode: str                              # "verify" | "diagnose"
    headline: dict
    source_text: str = ""
    evidence: list = field(default_factory=list)
    formal_checks: list = field(default_factory=list)
    staged: list = field(default_factory=list)
    steps: int = 0
    invalid_strikes: int = 0
    seen: set = field(default_factory=set)   # (action, args, observation) already produced
    status: str = "running"                # running|concluded|budget-exhausted|degraded|step-cap
    conclusion: dict | None = None


def _state_for(entry: dict) -> AgentState:
    src = Path(entry["source"]) if entry.get("source") else None
    text = ""
    if src is not None:
        try:
            text = src.read_text()
        except OSError:
            text = ""
    headline = entry.get("headline") or {}
    mode = "diagnose" if headline.get("status") == "refuted" else "verify"
    return AgentState(source=src, pass_name=entry.get("pass_name"), mode=mode,
                      headline=headline, source_text=text)


def build_request(state: AgentState, registry: dict, ctx: dict, client, max_steps: int) -> dict:
    # THE INSTRUCTION HAS TO POINT AT THE EVIDENCE, or the evidence goes unused. Adding the
    # strategy catalog stopped the model claiming "no formal strategy is applicable"; it still did
    # not ROUTE, because 32 of 33 entries say `runnable_here: true` and nothing said which had
    # anything to work on. `would_recover` now answers that, and these three sentences say how to
    # read it, that a strategy outside the classified family is a legitimate choice -- the one move
    # the deterministic planner structurally cannot make -- and that declining is a real outcome.
    # That last clause is deliberate: an instruction that only rewards finding things buys its
    # activity with false positives, and on this project a false refutation is as costly as a false
    # proof.
    instruction = (
        "You are triaging an LLVM optimization pass that O2T's deterministic pipeline could not "
        "settle. Choose exactly ONE next action from `actions` to make progress "
        + ("diagnosing the refutation" if state.mode == "diagnose" else "verifying the pass")
        + ". In `actions[].strategy_catalog`, `would_recover` is how many folds that strategy's "
          "miner ACTUALLY finds in this source: prefer a strategy with would_recover > 0 over one "
          "that is merely runnable, and note that `would_recover` absent means unmeasured, not "
          "zero. A strategy belonging to a family the classifier did NOT pick is a legitimate "
          "choice -- the deterministic pipeline only ran the matched family's strategies, so that "
          "is where you can add something it could not. If nothing has anything to work on, "
          "conclude `inconclusive`: that is a real answer, not a failure. Formal verifiers decide "
          "soundness; your conclusions are advisory. Reply with JSON only, matching "
          "`answer_schema`.")
    return {
        "task": "agent-verify-llvm-pass",
        "instruction": instruction,
        "pass": {"source": str(state.source) if state.source else None,
                 "pass_name": state.pass_name, "mode": state.mode,
                 "headline": {"status": state.headline.get("status"),
                              "reason": state.headline.get("reason")}},
        "source_excerpt": state.source_text[:_EXCERPT_CHARS],
        "evidence": state.evidence,
        "budget": {"llm_calls_remaining": client.remaining,
                   "steps_remaining": max(0, max_steps - state.steps)},
        "actions": advertise(registry, state, ctx),
        "answer_schema": {"action": "<one of actions[].name>", "args": {},
                          "rationale": "<short>"},
    }


def run_pass_agent(entry: dict, ctx: dict, client, services: dict, registry: dict,
                   max_steps: int = 8) -> dict:
    """Drive one residue pass to a conclusion (or a clean wind-down). Returns the quarantined
    `pass["agent"]` record; the caller merges it into the report."""
    state = _state_for(entry)
    while state.status == "running":
        if state.steps >= max_steps:
            state.status = "step-cap"
            break
        if client.remaining <= 0:
            state.status = "budget-exhausted"
            break
        reply = client.call(build_request(state, registry, ctx, client, max_steps))
        state.steps += 1
        # A None reply is a transport failure OR non-JSON output -- indistinguishable, and both
        # recoverable (a flaky provider, a chatty model). Treat it as a strike like any other
        # invalid reply; a dead command strikes out in two turns (degraded), never spins.
        spec, args_or_reason = validate_response(registry, reply)
        if spec is None:
            state.invalid_strikes += 1
            state.evidence.append({"step": state.steps,
                                   "action": reply.get("action") if isinstance(reply, dict) else None,
                                   "observation": {"error": "invalid-action",
                                                   "reason": args_or_reason}})
            if state.invalid_strikes >= 2:
                state.status = "degraded"
                break
            continue
        observation = spec.handler(state, args_or_reason, ctx, services)
        state.evidence.append({"step": state.steps, "action": spec.name,
                               "args": _truncate(args_or_reason, max_chars=500),
                               "rationale": str(reply.get("rationale", ""))[:300],
                               "observation": _truncate(observation)})
        # A VALID reply can spin just as effectively as an invalid one. The strike counter only
        # ever watched malformed replies, so a model that keeps choosing the SAME action and keeps
        # getting the SAME answer looped until the step cap, burning budget for no evidence --
        # observed live, three of four calls spent on one `classify` that returned "no family
        # matched" every time. The request already carries `evidence`, so the model could SEE its
        # own repetition and chose it anyway; prompting is not the fix, a structural guard is.
        #
        # Keyed on (action, args, OBSERVATION): repeating an action that produced something NEW is
        # legitimate progress, and only an identical outcome counts as no information. The reset
        # must happen here rather than before the handler runs -- resetting first would clear the
        # counter between two consecutive repeats, so it could never reach the threshold.
        sig = json.dumps([spec.name, args_or_reason, observation], sort_keys=True, default=str)
        if sig in state.seen:
            state.invalid_strikes += 1
            state.evidence[-1]["observation"] = {"error": "no-progress",
                                                 "reason": "action already produced this result"}
            if state.invalid_strikes >= 2:
                state.status = "degraded"
                break
            continue
        state.seen.add(sig)
        state.invalid_strikes = 0
    return {
        "attempted": True,
        "status": state.status,
        "mode": state.mode,
        "llm_calls": state.steps,
        "steps": state.evidence,
        "formal_checks": state.formal_checks,
        "conclusion": state.conclusion,
        "staged_tools": state.staged,
        "source_sha256": (hashlib.sha256(state.source_text.encode()).hexdigest()
                          if state.source_text else None),
    }
