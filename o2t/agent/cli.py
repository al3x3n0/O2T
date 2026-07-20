#!/usr/bin/env python3
"""cv-agent front door: autonomous batch triage of a pass tree with an LLM in the loop.

Flow: run the DETERMINISTIC orchestrator first (as a subprocess -- its headline logic is reused
verbatim, never reimplemented); select the residue (unclassified / advisory / skipped / error /
refuted); spend the global LLM budget driving the per-pass agent loop over the residue; merge the
quarantined `pass["agent"]` records into one report. Exit gates:
- `--fail-on-refuted` reads ONLY the deterministic headline (same semantics as cv-orchestrate).
- `--fail-on-agent-refuted` reads only agent-dispatched FORMAL refutations (origin: agent).
Advisory content (LLM conclusions, staged-tool results) can never trip either gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATE = ROOT / "tools" / "cv-orchestrate.py"

from o2t.agent.actions import build_registry                      # noqa: E402
from o2t.agent.llm import LLMClient                               # noqa: E402
from o2t.agent.loop import run_pass_agent, select_residue         # noqa: E402
from o2t.agent.report import (NEGATIVE_VERDICTS, merge,           # noqa: E402
                              previously_concluded, render_summary_text)
from o2t.agent.staging import StagingArea                         # noqa: E402
from o2t.orchestrate.run import resolve_context                   # noqa: E402


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="LLM-driven batch triage over the O2T toolchain")
    ap.add_argument("--source", nargs="*", type=Path, default=[],
                    help="pass source file(s) or director(y/ies)")
    ap.add_argument("--pass", dest="passes", action="append", default=[],
                    help="pass name hint (repeatable)")
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--llm-command",
                    help="provider-agnostic LLM command (JSON stdin -> JSON stdout); "
                         "e.g. 'claude -p --output-format json'")
    ap.add_argument("--budget", type=int, default=25, help="global LLM call budget")
    ap.add_argument("--max-steps-per-pass", type=int, default=8)
    ap.add_argument("--action-timeout", type=int, default=120)
    ap.add_argument("--llm-timeout", type=int, default=60)
    ap.add_argument("--out-dir", type=Path, help="staging + artifacts directory")
    ap.add_argument("--enable-synthesis", action="store_true",
                    help="allow the synthesize-tool action (staged, advisory, human-promoted)")
    ap.add_argument("--resume", type=Path, help="prior agent report; concluded passes skipped")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--summary-text", type=Path)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--clang-bin", default="clang")
    ap.add_argument("--ast-miner", type=Path)
    ap.add_argument("--fail-on-refuted", action="store_true",
                    help="non-zero when a DETERMINISTIC headline is refuted")
    ap.add_argument("--fail-on-agent-refuted", action="store_true",
                    help="non-zero when an agent-dispatched FORMAL check refuted")
    ap.add_argument("--selftest", action="store_true")
    return ap.parse_args(argv)


def _run_orchestrator(args) -> dict:
    """The trusted, deterministic tier -- ran verbatim as a subprocess."""
    argv = [sys.executable, str(ORCHESTRATE)]
    for src in args.source:
        argv += ["--source", str(src)]
    for name in args.passes:
        argv += ["--pass", name]
    for pat in args.include:
        argv += ["--include", pat]
    for pat in args.exclude:
        argv += ["--exclude", pat]
    argv += ["--z3-bin", args.z3_bin, "--opt-bin", args.opt_bin, "--clang-bin", args.clang_bin]
    if args.ast_miner:
        argv += ["--ast-miner", str(args.ast_miner)]
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rep = Path(tf.name)
    try:
        subprocess.run(argv + ["--report", str(rep)], capture_output=True, text=True)
        return json.loads(rep.read_text()) if rep.stat().st_size else {}
    finally:
        rep.unlink(missing_ok=True)


def run_agent(args) -> tuple[dict, int]:
    report = _run_orchestrator(args)
    if not report:
        print("cv-agent: orchestrator produced no report", file=sys.stderr)
        return {}, 2

    ctx = resolve_context(args.z3_bin, args.opt_bin, args.clang_bin, args.ast_miner)
    out_dir = args.out_dir or Path(tempfile.mkdtemp(prefix="cv-agent-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = StagingArea(out_dir / "agent-staging") if args.enable_synthesis else None
    services = {"staging": staging, "workdir": out_dir, "action_timeout": args.action_timeout}
    registry = build_registry(enable_synthesis=args.enable_synthesis)
    client = LLMClient(args.llm_command, timeout=args.llm_timeout, budget=args.budget)

    skip = {}
    if args.resume and args.resume.exists():
        try:
            skip = previously_concluded(json.loads(args.resume.read_text()))
        except (OSError, json.JSONDecodeError):
            skip = {}

    residue = select_residue(report)
    records = {}
    for entry in residue:
        key = (str(entry.get("source") or ""), str(entry.get("pass_name") or ""))
        if key in skip:
            records[key] = {**skip[key], "resumed": True}
            continue
        if client.remaining <= 0:
            break
        records[key] = run_pass_agent(entry, ctx, client, services, registry,
                                      max_steps=args.max_steps_per_pass)

    run_meta = {
        "budget": args.budget,
        "llm_calls_used": client.used,
        "residue_selected": len(residue),
        "attempted": len(records),
        "enable_synthesis": bool(args.enable_synthesis),
        "staging_dir": str(staging.root) if staging else None,
    }
    merge(report, records, run_meta)

    exit_code = 0
    if args.fail_on_refuted and any(
            (e.get("headline") or {}).get("status") == "refuted"
            for e in report.get("passes", [])):
        exit_code = 1
    if args.fail_on_agent_refuted and any(
            c.get("origin") == "agent" and str(c.get("verdict")) in NEGATIVE_VERDICTS
            for e in report.get("passes", [])
            for c in (e.get("agent") or {}).get("formal_checks", [])):
        exit_code = 1
    return report, exit_code


def _selftest() -> int:
    """Plumbing check without an LLM or verifiers: registry validation + trust invariants."""
    from o2t.agent.actions import validate_response
    reg = build_registry(enable_synthesis=False)
    assert "synthesize-tool" not in reg, "synthesis must be off by default"
    assert set(reg) >= {"classify", "run-strategy", "conclude"}, sorted(reg)
    spec, args = validate_response(reg, {"action": "conclude", "args": {"proposal": "proved"}})
    assert spec is not None and args["proposal"] == "proved"
    none_spec, reason = validate_response(reg, {"action": "rm -rf /", "args": {}})
    assert none_spec is None and "unknown action" in reason
    none_spec, reason = validate_response(
        reg, {"action": "run-strategy", "args": {"strategy": "not-a-strategy"}})
    assert none_spec is None, "invalid strategy enum must be rejected"
    reg2 = build_registry(enable_synthesis=True)
    assert "synthesize-tool" in reg2
    print("cv-agent selftest OK: registry whitelists actions, rejects unknown actions and "
          "out-of-enum strategies; synthesis is opt-in")
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.llm_command:
        print("cv-agent: --llm-command is required (or use --selftest)", file=sys.stderr)
        return 2
    report, exit_code = run_agent(args)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    if args.summary_text:
        args.summary_text.write_text(render_summary_text(report))
    else:
        print(render_summary_text(report))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
