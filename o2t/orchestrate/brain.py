#!/usr/bin/env python3
"""Optional LLM "brain" for the orchestrator -- a provider-agnostic tie-breaker.

The feature-based classifier (`classify.py`) is deterministic and authoritative. When it is
AMBIGUOUS about a pass -- no family clears the threshold, or the top two families are within
a small margin -- this module can consult an external LLM to suggest a family. It follows
O2T's existing provider-agnostic convention: `--llm-command` is an arbitrary command that
receives a JSON request on stdin and returns a JSON verdict on stdout. O2T bakes in NO
provider; any model behind any CLI works.

The LLM is ADVISORY: its suggestion is recorded under `pass["llm"]`, and a DISAGREEMENT with
the deterministic primary is surfaced (never silently applied). When `--llm-command` is
absent or the call fails, the deterministic classification stands unchanged. This keeps the
trust model intact -- formal verifiers still decide soundness; the LLM only helps route.
"""

from __future__ import annotations

import json
import subprocess

from o2t.orchestrate.classify import FAMILIES

_FAMILY_NAMES = [f.name for f in FAMILIES]


def is_ambiguous(entry: dict, margin: float = 1.5) -> bool:
    """A classification is ambiguous when nothing clears the threshold, or the best RETAINED
    family is less than `margin`× the runner-up -- exactly where an LLM tie-break can help.
    Only families that were retained (in `families`) count; weak sub-threshold noise does not."""
    if not entry.get("primary_family"):
        return True
    retained = entry.get("families", [])
    scores = entry.get("scores", {})
    ranked = sorted((scores.get(f, 0) for f in retained), reverse=True)
    if len(ranked) < 2:
        return False
    return ranked[0] < margin * ranked[1]


def _build_request(entry: dict, source_excerpt: str) -> dict:
    return {
        "task": "classify-llvm-pass",
        "instruction": "Pick the single best transform family for this LLVM pass.",
        "families": [{"name": f.name, "description": f.description} for f in FAMILIES],
        "deterministic": {"primary": entry.get("primary_family"), "scores": entry.get("scores")},
        "source_excerpt": source_excerpt[:4000],
        "answer_schema": {"family": "<one of families[].name>", "confidence": "0..1",
                          "rationale": "<short>"},
    }


def call_llm(request: dict, llm_command: str, timeout: int = 60) -> dict | None:
    """Run the provider-agnostic LLM command (JSON request on stdin -> JSON verdict on stdout).
    Returns the parsed, validated verdict, or None on any failure (advisory, never fatal)."""
    try:
        proc = subprocess.run(llm_command, shell=True, input=json.dumps(request),
                              capture_output=True, text=True, timeout=timeout)
        out = proc.stdout.strip()
        verdict = json.loads(out[out.index("{"):out.rindex("}") + 1]) if "{" in out else {}
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None
    fam = verdict.get("family")
    if fam not in _FAMILY_NAMES:
        return None
    return {"family": fam, "confidence": verdict.get("confidence"),
            "rationale": str(verdict.get("rationale", ""))[:500]}


def maybe_llm_classify(report: dict, llm_command: str, read_source=None) -> dict:
    """Annotate each AMBIGUOUS pass in `report` with an LLM family suggestion. Adds
    `pass["llm"] = {family, confidence, rationale, agrees}`; never overrides the deterministic
    `primary_family`. `read_source(path)->str` supplies the excerpt (defaults to reading the file)."""
    import pathlib

    def _read(path):
        try:
            return pathlib.Path(path).read_text() if path else ""
        except OSError:
            return ""

    reader = read_source or _read
    for entry in report.get("passes", []):
        if not is_ambiguous(entry):
            continue
        verdict = call_llm(_build_request(entry, reader(entry.get("source"))), llm_command)
        if verdict is None:
            entry["llm"] = {"status": "unavailable"}
            continue
        verdict["agrees"] = (verdict["family"] == entry.get("primary_family"))
        entry["llm"] = verdict
        # Surface a disagreement; the deterministic primary still stands (advisory only).
        if not verdict["agrees"]:
            entry.setdefault("notes", []).append(
                f"LLM suggests '{verdict['family']}' vs deterministic '{entry.get('primary_family')}'")
    return report
