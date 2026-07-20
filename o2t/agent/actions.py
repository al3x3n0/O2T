#!/usr/bin/env python3
"""Whitelisted action registry for the verification agent.

The LLM never emits shell. It selects ONE action per step from this registry; each action's args
are validated field-by-field against a small schema (type, enum, length cap) BEFORE the handler
runs, and each handler either runs a REAL O2T verifier (whose verdict is formal, `origin: agent`),
routes an LLM proposal through an existing formal gate (Z3 proof / recovery cross-check), stages a
tool candidate in quarantine, or concludes (advisory). An invalid selection never executes anything:
it becomes an `invalid-action` observation the LLM sees next turn.

Action kinds:
- `evidence`  -- read-only probes (classification, source mining); no verdict weight.
- `formal`    -- a real verifier runs; its verdict counts toward the agent's provenance-tagged
                 headline (never the deterministic one).
- `synthesis` -- quarantined tool staging (only with --enable-synthesis; advisory-staged).
- `control`   -- `conclude`: ends the loop with an ADVISORY proposal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from o2t.orchestrate.classify import classify
from o2t.orchestrate.plan import STRATEGIES, PlannedCheck
from o2t.orchestrate.run import execute_check

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"

# Hard caps applied during arg validation, independent of any schema entry.
_MAX_STRING = 20000
_MAX_LIST = 50


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    args_schema: dict                       # field -> {"type", "required"?, "enum"?, "max_len"?}
    kind: str                               # evidence | formal | synthesis | control
    handler: Callable                       # (state, args, ctx, services) -> observation dict
    needs: tuple[str, ...] = field(default=())   # required ctx binaries (plan.Strategy vocabulary)


# --- arg validation -------------------------------------------------------------------------------
def _validate_args(spec: ActionSpec, args: dict) -> str | None:
    """Return an error string if `args` do not satisfy the spec's schema, else None."""
    if not isinstance(args, dict):
        return "args must be an object"
    for name, rule in spec.args_schema.items():
        if name not in args:
            if rule.get("required"):
                return f"missing required arg {name!r}"
            continue
        val = args[name]
        want = rule.get("type", "string")
        if want == "string":
            if not isinstance(val, str):
                return f"arg {name!r} must be a string"
            if len(val) > rule.get("max_len", _MAX_STRING):
                return f"arg {name!r} exceeds {rule.get('max_len', _MAX_STRING)} chars"
            if "enum" in rule and val not in rule["enum"]:
                return f"arg {name!r} must be one of {sorted(rule['enum'])}"
        elif want == "list":
            if not isinstance(val, list) or len(val) > _MAX_LIST:
                return f"arg {name!r} must be a list of at most {_MAX_LIST} items"
    unknown = set(args) - set(spec.args_schema)
    if unknown:
        return f"unknown args {sorted(unknown)}"
    return None


def validate_response(registry: dict, response: dict | None):
    """Validate the LLM's reply against the registry. Returns (spec, args) or (None, reason)."""
    if not isinstance(response, dict):
        return None, "no valid JSON reply"
    name = response.get("action")
    spec = registry.get(name) if isinstance(name, str) else None
    if spec is None:
        return None, f"unknown action {name!r}"
    args = response.get("args", {})
    err = _validate_args(spec, args if isinstance(args, dict) else {})
    if err:
        return None, f"{name}: {err}"
    return spec, args


# --- observation helpers --------------------------------------------------------------------------
def _truncate(obj, max_chars: int = 2000, max_records: int = 10):
    """Bound an observation for the prompt: lists capped, long strings elided."""
    if isinstance(obj, list):
        out = [_truncate(x, max_chars, max_records) for x in obj[:max_records]]
        if len(obj) > max_records:
            out.append({"truncated": len(obj) - max_records})
        return out
    if isinstance(obj, dict):
        return {k: _truncate(v, max_chars, max_records) for k, v in obj.items()}
    if isinstance(obj, str) and len(obj) > max_chars:
        return obj[:max_chars] + f"...[+{len(obj) - max_chars} chars]"
    return obj


def _run_tool_json(argv: list[str], report_flag: str = "--report", timeout: int = 120) -> dict:
    """Run a tools/ script with a temp report file; parse the JSON (or {} on any failure)."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rep = Path(tf.name)
    try:
        subprocess.run(argv + [report_flag, str(rep)], capture_output=True, text=True,
                       timeout=timeout)
        return json.loads(rep.read_text()) if rep.stat().st_size else {}
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}
    finally:
        rep.unlink(missing_ok=True)


# --- handlers -------------------------------------------------------------------------------------
def _h_classify(state, args, ctx, services) -> dict:
    src = state.source_text or ""
    c = classify(src, state.pass_name)
    return {"primary_family": c.primary, "families": c.families, "scores": c.scores,
            "strategies": c.strategies}


def _h_mine_source(state, args, ctx, services) -> dict:
    if state.source is None:
        return {"error": "no source file for this pass"}
    tool = str(TOOLS / "cv-mine-pass-source.py")
    try:
        proc = subprocess.run([sys.executable, tool, str(state.source), "--format", "json"],
                              capture_output=True, text=True,
                              timeout=services.get("action_timeout", 120))
        out = proc.stdout.strip()
        findings = json.loads(out) if out.startswith(("[", "{")) else []
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return {"error": f"mine-source failed: {exc}"}
    records = findings if isinstance(findings, list) else findings.get("findings", [])
    return {"findings": _truncate(records), "total": len(records)}


def _h_run_strategy(state, args, ctx, services) -> dict:
    sid = args["strategy"]
    strat = STRATEGIES[sid]
    missing = [n for n in strat.needs if not ctx.get(n)]
    if missing:
        return {"error": f"strategy {sid} missing prerequisites: {', '.join(missing)}"}
    if strat.target == "source" and state.source is None:
        return {"error": f"strategy {sid} needs a pass source file"}
    check = PlannedCheck(strat.sid, strat.label, True, strat.target)
    verdict = execute_check(check, state.source, state.pass_name, ctx)
    # A REAL verifier ran: record its verdict with agent provenance (feeds the agent headline,
    # never the deterministic one).
    state.formal_checks.append({**verdict, "strategy": sid, "label": strat.label,
                                "origin": "agent"})
    return _truncate(verdict)


def _h_recover_fold(state, args, ctx, services) -> dict:
    from o2t.intent import pass_graph as pg
    src = args.get("function_source") or state.source_text
    if not src:
        return {"error": "no source to recover from"}
    pair = pg.recover_from_function(src)
    if pair is None:
        return {"recovered": False,
                "reason": "outside the modeled fragment (sound decline, not a verdict)"}
    if not ctx.get("z3"):
        return {"recovered": True, "obligation": _truncate(pair), "proof": "skipped: no z3"}
    rec = pg.reconcile(pair, ctx["z3"])
    state.formal_checks.append({"strategy": "recover-fold", "label": "Pass-IR fold recovery",
                                "verdict": rec.get("z3", "error"),
                                "reconcile_agree": rec.get("agree"), "origin": "agent"})
    return {"recovered": True, "obligation": _truncate(pair), "z3": rec.get("z3"),
            "reconcile": {k: rec.get(k) for k in ("concrete", "agree", "checked")}}


def _h_propose_intent_candidates(state, args, ctx, services) -> dict:
    """Route LLM-proposed intent-candidate records through the EXISTING Z3 proof gate
    (cv-validate-intent-candidates). The proposal is data; the proof decides."""
    if not ctx.get("z3"):
        return {"error": "z3 unavailable; cannot proof-gate candidates"}
    tool = str(TOOLS / "cv-validate-intent-candidates.py")
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as cf:
        for record in args["candidates"]:
            cf.write(json.dumps(record) + "\n")
        cand = Path(cf.name)
    with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as vf:
        val = Path(vf.name)
    try:
        subprocess.run([sys.executable, tool, "--z3", ctx["z3"], "--input", str(cand),
                        "--out", str(val)], capture_output=True, text=True,
                       timeout=services.get("action_timeout", 120))
        records = [json.loads(l) for l in val.read_text().splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return {"error": f"candidate validation failed: {exc}"}
    finally:
        cand.unlink(missing_ok=True)
        val.unlink(missing_ok=True)
    statuses = [r.get("proof_status") for r in records]
    proved = sum(s == "proved" for s in statuses)
    refuted = sum(s in ("refuted", "unsound") for s in statuses)
    if records:
        verdict = ("refuted" if refuted else "proved" if proved == len(records)
                   else "partial" if proved else "inconclusive")
        state.formal_checks.append({"strategy": "propose-intent-candidates",
                                    "label": "LLM intent candidates (Z3 proof-gated)",
                                    "verdict": verdict, "candidates": len(records),
                                    "proved": proved, "refuted": refuted, "origin": "agent"})
    return {"candidates": len(records), "proved": proved, "refuted": refuted,
            "statuses": _truncate(statuses)}


def _h_propose_fold_obligation(state, args, ctx, services) -> dict:
    """Route an LLM-proposed (predicate, rewrite) fold through pass_graph's recovery + reconcile
    (+ compiler grounding when clang is present). Recovery declines anything unmodeled; the
    proof and cross-checks decide -- a wrong proposal is refuted or declined, never trusted."""
    from o2t.intent import pass_graph as pg
    pair = pg.recover_pair(args["predicate_source"], args["rewrite_source"])
    if pair is None:
        return {"recovered": False,
                "reason": "outside the modeled fragment (sound decline, not a verdict)"}
    if not ctx.get("z3"):
        return {"recovered": True, "proof": "skipped: no z3"}
    rec = pg.reconcile(pair, ctx["z3"])
    obs = {"recovered": True, "z3": rec.get("z3"),
           "reconcile": {k: rec.get(k) for k in ("concrete", "agree", "checked")}}
    if ctx.get("clang"):
        grounded = pg.ground_recovery(pair, args["rewrite_source"], ctx["z3"])
        obs["grounding"] = {k: grounded.get(k) for k in ("grounded", "divergence", "reason")}
    state.formal_checks.append({"strategy": "propose-fold-obligation",
                                "label": "LLM fold obligation (recovered + cross-checked)",
                                "verdict": rec.get("z3", "error"),
                                "reconcile_agree": rec.get("agree"), "origin": "agent"})
    return obs


def _h_synthesize_tool(state, args, ctx, services) -> dict:
    staging = services.get("staging")
    if staging is None:
        return {"error": "tool synthesis is disabled (--enable-synthesis not set)"}
    record = staging.stage_tool(args["name"], args["purpose"], args["tool_source"],
                                args["fixture_source"])
    if "error" in record:
        return record
    result = staging.run_fixture(record, timeout=services.get("action_timeout", 120))
    record["fixture_result"] = result
    state.staged.append(record)
    return _truncate({**record, "trust": "advisory-staged"})


def _h_conclude(state, args, ctx, services) -> dict:
    state.conclusion = {"proposal": args["proposal"], "rationale": args.get("rationale", "")[:1000],
                        "trust": "advisory"}
    state.status = "concluded"
    return {"concluded": args["proposal"], "trust": "advisory"}


# --- registry -------------------------------------------------------------------------------------
def build_registry(enable_synthesis: bool = False) -> dict[str, ActionSpec]:
    reg = {
        "classify": ActionSpec(
            "classify", "Score the pass source into O2T's transform families (deterministic).",
            {}, "evidence", _h_classify),
        "mine-source": ActionSpec(
            "mine-source", "Mine the pass source for optimization-intent findings.",
            {}, "evidence", _h_mine_source),
        "run-strategy": ActionSpec(
            "run-strategy", "Dispatch ONE named verification strategy to its real O2T verifier; "
            "the verifier's verdict is formal.",
            {"strategy": {"type": "string", "required": True, "enum": sorted(STRATEGIES)}},
            "formal", _h_run_strategy),
        "recover-fold": ActionSpec(
            "recover-fold", "Structurally recover a fold obligation from a fold FUNCTION's source "
            "(Pass IR) and prove + cross-check it. Declines outside the modeled fragment.",
            {"function_source": {"type": "string"}}, "formal", _h_recover_fold, needs=("z3",)),
        "propose-intent-candidates": ActionSpec(
            "propose-intent-candidates", "Propose intent-candidate records; each is proof-gated "
            "by Z3 via cv-validate-intent-candidates. The proof decides, not the proposal.",
            {"candidates": {"type": "list", "required": True}}, "formal",
            _h_propose_intent_candidates, needs=("z3",)),
        "propose-fold-obligation": ActionSpec(
            "propose-fold-obligation", "Propose a (match-predicate, rewrite) fold; it is recovered "
            "by pass_graph, proved, reconciled, and compiler-grounded when clang is present.",
            {"predicate_source": {"type": "string", "required": True},
             "rewrite_source": {"type": "string", "required": True}}, "formal",
            _h_propose_fold_obligation, needs=("z3",)),
        "conclude": ActionSpec(
            "conclude", "End this pass's investigation with an ADVISORY proposal.",
            {"proposal": {"type": "string", "required": True,
                          "enum": ["proved", "refuted", "inconclusive", "needs-human"]},
             "rationale": {"type": "string", "max_len": 1000}}, "control", _h_conclude),
    }
    if enable_synthesis:
        reg["synthesize-tool"] = ActionSpec(
            "synthesize-tool", "Stage a NEW candidate tool + fixture in quarantine "
            "(agent-staging/); the fixture runs isolated and the result is advisory-staged. "
            "Promotion to tools/ requires human review.",
            {"name": {"type": "string", "required": True, "max_len": 48},
             "purpose": {"type": "string", "required": True, "max_len": 500},
             "tool_source": {"type": "string", "required": True},
             "fixture_source": {"type": "string", "required": True}},
            "synthesis", _h_synthesize_tool)
    return reg


def advertise(registry: dict, state, ctx: dict) -> list[dict]:
    """Render the registry for the LLM prompt, marking per-pass availability honestly."""
    out = []
    for spec in registry.values():
        missing = [n for n in spec.needs if not ctx.get(n)]
        available, reason = True, ""
        if missing:
            available, reason = False, f"missing: {', '.join(missing)}"
        elif spec.kind == "synthesis" and state.mode == "diagnose":
            available, reason = False, "synthesis disabled while diagnosing a refutation"
        out.append({"name": spec.name, "description": spec.description, "kind": spec.kind,
                    "args_schema": spec.args_schema, "available": available,
                    "unavailable_reason": reason})
    return out
