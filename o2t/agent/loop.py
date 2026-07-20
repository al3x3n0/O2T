#!/usr/bin/env python3
"""Per-pass agent loop: LLM observes evidence, picks a whitelisted action, a real tool runs.

Loop contract:
- ONE LLM call per step; the request carries the pass, accumulated evidence, remaining budget,
  and the advertised action registry with arg schemas (`answer_schema` mirrors brain.py's style).
- An invalid reply (transport failure, malformed JSON, unknown action, bad args) EXECUTES
  NOTHING: it becomes an `invalid-action` observation the LLM sees next turn. Two consecutive
  invalid replies degrade the pass (`status: degraded`) -- the loop never spins on a confused
  model, and a dead LLM command strikes out in two turns.
- Budget exhaustion winds down cleanly with partial evidence kept.
- Observations are truncated before entering the prompt; full tool verdicts live untruncated in
  `state.formal_checks` for the report.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from o2t.agent.actions import _truncate, advertise, validate_response

# Residue: the passes where deterministic orchestration left a human decision open.
RESIDUE_STATUSES = ("unclassified", "advisory", "skipped", "error", "refuted")
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
    instruction = (
        "You are triaging an LLVM optimization pass that O2T's deterministic pipeline could not "
        "settle. Choose exactly ONE next action from `actions` to make progress "
        + ("diagnosing the refutation" if state.mode == "diagnose" else "verifying the pass")
        + ". Formal verifiers decide soundness; your conclusions are advisory. Reply with JSON "
          "only, matching `answer_schema`.")
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
        state.invalid_strikes = 0
        observation = spec.handler(state, args_or_reason, ctx, services)
        state.evidence.append({"step": state.steps, "action": spec.name,
                               "args": _truncate(args_or_reason, max_chars=500),
                               "rationale": str(reply.get("rationale", ""))[:300],
                               "observation": _truncate(observation)})
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
