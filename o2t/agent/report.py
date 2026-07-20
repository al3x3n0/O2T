#!/usr/bin/env python3
"""Merge agent findings into the orchestrator report, with trust quarantine.

Invariants:
- The deterministic `pass["headline"]` (computed by cv-orchestrate) is NEVER rewritten.
- Everything agent-derived lives under `pass["agent"]`, mirroring the advisory `pass["llm"]`.
- The agent's own headline is computed from the deterministic checks PLUS the agent-dispatched
  REAL-verifier verdicts (`origin: "agent"`), using the same collapse rules -- and is provenance-
  tagged `deterministic+agent-formal` so a reader always knows which tier produced it.
- LLM conclusions (`trust: advisory`) and staged-tool results (`trust: advisory-staged`) carry no
  verdict weight anywhere.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Same verdict tiers as tools/cv-orchestrate.py (POSITIVE_VERDICTS / NEGATIVE_VERDICTS).
POSITIVE_VERDICTS = {"proved", "sound", "validated"}
NEGATIVE_VERDICTS = {"refuted", "miscompile"}


def agent_headline(entry: dict, record: dict) -> dict:
    """Collapse deterministic checks + agent formal checks with the orchestrator's rules.
    Unlike the deterministic headline, ALL formal checks count (the agent may have run a
    strategy outside the primary family precisely because classification failed)."""
    checks = [c for c in (entry.get("checks") or []) if isinstance(c, dict)]
    checks += [c for c in record.get("formal_checks", []) if isinstance(c, dict)]
    verdicts = [str(c.get("verdict") or "unknown") for c in checks]
    if not verdicts:
        status = "no-formal-evidence"
    elif any(v in NEGATIVE_VERDICTS for v in verdicts):
        status = "refuted"
    elif any(v == "error" for v in verdicts):
        status = "error"
    elif any(v in POSITIVE_VERDICTS for v in verdicts):
        status = "proved"
    else:
        status = "advisory"
    return {
        "status": status,
        "provenance": "deterministic+agent-formal",
        "checks": [{"strategy": c.get("strategy"), "verdict": c.get("verdict"),
                    "origin": c.get("origin", "deterministic")} for c in checks],
        "verdicts": dict(sorted((v, verdicts.count(v)) for v in set(verdicts))),
    }


def _key(entry: dict) -> tuple:
    return (str(entry.get("source") or ""), str(entry.get("pass_name") or ""))


def merge(base_report: dict, agent_records: dict, run_meta: dict) -> dict:
    """Attach each agent record (keyed like `_key`) under its pass entry; the base report's
    structure and deterministic headlines are otherwise untouched."""
    for entry in base_report.get("passes", []):
        record = agent_records.get(_key(entry))
        if record is None:
            continue
        record["headline"] = agent_headline(entry, record)
        entry["agent"] = record
    base_report["agent_run"] = run_meta
    base_report.setdefault("summary", {})["agent"] = summarize_agent(base_report)
    return base_report


def summarize_agent(report: dict) -> dict:
    records = [e["agent"] for e in report.get("passes", []) if isinstance(e.get("agent"), dict)]
    formal: dict[str, int] = {}
    upgrades = 0
    for entry in report.get("passes", []):
        record = entry.get("agent")
        if not isinstance(record, dict):
            continue
        for check in record.get("formal_checks", []):
            v = str(check.get("verdict") or "unknown")
            formal[v] = formal.get(v, 0) + 1
        det = (entry.get("headline") or {}).get("status")
        ag = (record.get("headline") or {}).get("status")
        if ag == "proved" and det not in ("proved",):
            upgrades += 1
    statuses = [r.get("status") for r in records]
    return {
        "attempted": len(records),
        "concluded": statuses.count("concluded"),
        "degraded": statuses.count("degraded"),
        "budget_exhausted": statuses.count("budget-exhausted"),
        "step_cap": statuses.count("step-cap"),
        "agent_formal": dict(sorted(formal.items())),
        "staged_tools": sum(len(r.get("staged_tools", [])) for r in records),
        "headline_upgrades": upgrades,
    }


def render_summary_text(report: dict) -> str:
    """A human-readable agent section, appended after the orchestrator summary text."""
    lines = ["", "== Agent (advisory tier; formal verifiers decided every verdict) =="]
    run = report.get("agent_run", {})
    agent = (report.get("summary") or {}).get("agent", {})
    lines.append(f"residue selected: {run.get('residue_selected', 0)}   "
                 f"attempted: {agent.get('attempted', 0)}   "
                 f"llm calls: {run.get('llm_calls_used', 0)}/{run.get('budget', 0)}")
    lines.append(f"outcomes: concluded={agent.get('concluded', 0)} "
                 f"degraded={agent.get('degraded', 0)} "
                 f"budget-exhausted={agent.get('budget_exhausted', 0)} "
                 f"step-cap={agent.get('step_cap', 0)}")
    if agent.get("agent_formal"):
        lines.append(f"agent-dispatched formal verdicts: {agent['agent_formal']}")
    if agent.get("headline_upgrades"):
        lines.append(f"headline upgrades (formal, provenance-tagged): {agent['headline_upgrades']}")
    if agent.get("staged_tools"):
        lines.append(f"staged tool candidates awaiting human review: {agent['staged_tools']} "
                     f"(under {run.get('staging_dir', 'agent-staging/')}; advisory-staged)")
    for entry in report.get("passes", []):
        record = entry.get("agent")
        if not isinstance(record, dict):
            continue
        label = entry.get("source") or entry.get("pass_name") or "<unknown>"
        det = (entry.get("headline") or {}).get("status")
        ag = (record.get("headline") or {}).get("status")
        conc = record.get("conclusion") or {}
        lines.append(f"- {label}: deterministic={det} agent-formal={ag} "
                     f"proposal={conc.get('proposal', '-')} ({record.get('status')}, "
                     f"{record.get('llm_calls', 0)} llm calls)")
    return "\n".join(lines) + "\n"


def previously_concluded(prior_report: dict) -> dict:
    """(source, pass_name) -> agent record for entries already concluded, guarded by the stored
    source sha256 so an edited pass is re-triaged. Used by --resume for idempotent re-runs."""
    out = {}
    for entry in prior_report.get("passes", []):
        record = entry.get("agent")
        if not isinstance(record, dict) or record.get("status") != "concluded":
            continue
        src = entry.get("source")
        if src:
            try:
                digest = hashlib.sha256(Path(src).read_text().encode()).hexdigest()
            except OSError:
                continue
            if digest != record.get("source_sha256"):
                continue
        out[_key(entry)] = record
    return out
