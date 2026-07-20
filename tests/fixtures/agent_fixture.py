#!/usr/bin/env python3
"""Cover the verification agent's loop, trust quarantine, and budget/degradation behaviour.

The agent is ROUTING, not deciding: these tests pin that (1) a scripted LLM can drive a residue
pass to a REAL proved verdict via whitelisted actions (the verifier decides, `origin: agent`);
(2) the deterministic headline is never rewritten and an advisory `conclude("refuted")` trips no
gate; (3) invalid/malformed LLM replies execute nothing and two strikes degrade the pass;
(4) budget exhaustion winds down cleanly. Uses the deterministic scenario stub -- no model, no
network. Needs z3 (real verifier verdicts).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.cli import run_agent  # noqa: E402

STUB = Path(__file__).resolve().parent / "agent_llm_stub.py"
SNIPPET = Path(__file__).resolve().parent / "agent_residue_snippet.cpp"


def _args(scenario_path: Path, tmp: Path, **overrides) -> SimpleNamespace:
    base = dict(
        source=[SNIPPET], passes=[], include=[], exclude=[],
        llm_command=f"{sys.executable} {STUB}",
        budget=10, max_steps_per_pass=8, action_timeout=120, llm_timeout=60,
        out_dir=tmp / "out", enable_synthesis=False, resume=None,
        report=None, summary_text=None,
        z3_bin="z3", opt_bin="opt", clang_bin="clang", ast_miner=None,
        fail_on_refuted=False, fail_on_agent_refuted=False, selftest=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _write_scenario(tmp: Path, name: str, turns: list) -> Path:
    path = tmp / name
    path.write_text(json.dumps(turns))
    return path


def _agent_record(report: dict) -> tuple[dict, dict]:
    entry = next(e for e in report["passes"] if e.get("source", "").endswith(SNIPPET.name))
    record = entry.get("agent")
    assert isinstance(record, dict), "residue pass must carry a quarantined agent record"
    return entry, record


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("agent_fixture: z3 not found, skipped")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) HAPPY PATH: the scripted LLM classifies, dispatches a REAL canonical verifier
        #    (memory-model -> cv-validate-memory, Z3 decides), and concludes. The deterministic
        #    headline stays `unclassified`; the agent's provenance-tagged headline is `proved`
        #    from a formal check with `origin: "agent"`; the conclusion is advisory.
        scenario = _write_scenario(tmp, "happy.json", [
            {"action": "classify", "args": {}, "rationale": "see what the classifier says"},
            {"action": "run-strategy", "args": {"strategy": "memory-model"},
             "rationale": "canonical z3 contracts run without source"},
            {"action": "conclude", "args": {"proposal": "proved",
                                            "rationale": "memory-model contracts proved"}},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        report, exit_code = run_agent(_args(scenario, tmp))
        assert exit_code == 0, exit_code
        entry, record = _agent_record(report)
        assert entry["headline"]["status"] == "unclassified", \
            ("deterministic headline must be untouched", entry["headline"])
        assert record["status"] == "concluded" and record["llm_calls"] == 3, record["llm_calls"]
        formal = record["formal_checks"]
        assert formal and formal[0]["origin"] == "agent" and formal[0]["verdict"] == "proved", formal
        assert record["headline"]["status"] == "proved", record["headline"]
        assert record["headline"]["provenance"] == "deterministic+agent-formal"
        assert record["conclusion"] == {"proposal": "proved",
                                        "rationale": "memory-model contracts proved",
                                        "trust": "advisory"}
        summary = report["summary"]["agent"]
        assert summary["attempted"] == 1 and summary["concluded"] == 1
        assert summary["agent_formal"].get("proved") == 1 and summary["headline_upgrades"] == 1

        # 2) TRUST: an LLM that only "concludes refuted" changes NO headline and trips NO gate --
        #    an advisory opinion is not a refutation. Both fail gates stay 0.
        scenario = _write_scenario(tmp, "advisory-refuted.json", [
            {"action": "conclude", "args": {"proposal": "refuted",
                                            "rationale": "stub merely opines"}},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        report, exit_code = run_agent(_args(scenario, tmp, fail_on_refuted=True,
                                            fail_on_agent_refuted=True))
        assert exit_code == 0, "advisory conclusions must not trip fail gates"
        entry, record = _agent_record(report)
        assert entry["headline"]["status"] == "unclassified"
        assert record["conclusion"]["trust"] == "advisory"
        assert record["headline"]["status"] == "no-formal-evidence", record["headline"]

        # 3) INVALID REPLIES: an unknown action and a malformed reply each execute NOTHING and are
        #    fed back as invalid-action observations; two consecutive strikes degrade the pass.
        scenario = _write_scenario(tmp, "invalid.json", [
            {"action": "rm -rf /", "args": {}},
            "malformed",
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        report, _ = run_agent(_args(scenario, tmp))
        _, record = _agent_record(report)
        assert record["status"] == "degraded", record["status"]
        assert record["formal_checks"] == [], "invalid replies must execute nothing"
        obs = [s["observation"] for s in record["steps"]]
        assert obs[0]["error"] == "invalid-action" and "unknown action" in obs[0]["reason"], obs[0]
        # the malformed turn fails in transport (None reply): also recorded as a strike path.

        # 4) A SINGLE invalid reply is recoverable: strike, observe, continue to a valid action.
        scenario = _write_scenario(tmp, "recover.json", [
            {"action": "run-strategy", "args": {"strategy": "not-a-strategy"}},
            {"action": "run-strategy", "args": {"strategy": "dce-model"}},
            {"action": "conclude", "args": {"proposal": "proved"}},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        report, _ = run_agent(_args(scenario, tmp))
        _, record = _agent_record(report)
        assert record["status"] == "concluded", record["status"]
        assert record["formal_checks"][0]["verdict"] == "proved"
        assert any(s["observation"].get("error") == "invalid-action" for s in record["steps"])

        # 5) BUDGET: with --budget 2 the loop stops mid-investigation, cleanly, keeping evidence.
        scenario = _write_scenario(tmp, "budget.json", [
            {"action": "classify", "args": {}},
            {"action": "mine-source", "args": {}},
            {"action": "run-strategy", "args": {"strategy": "memory-model"}},
            {"action": "conclude", "args": {"proposal": "proved"}},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        report, exit_code = run_agent(_args(scenario, tmp, budget=2))
        assert exit_code == 0
        _, record = _agent_record(report)
        assert record["status"] == "budget-exhausted", record["status"]
        assert record["llm_calls"] == 2 and len(record["steps"]) == 2
        assert report["agent_run"]["llm_calls_used"] == 2

        # 6) RESUME: a concluded pass (unchanged source) is skipped on the next run -- zero LLM
        #    calls spent re-triaging settled work.
        scenario = _write_scenario(tmp, "happy2.json", [
            {"action": "run-strategy", "args": {"strategy": "dce-model"}},
            {"action": "conclude", "args": {"proposal": "proved"}},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        prior_path = tmp / "prior.json"
        report, _ = run_agent(_args(scenario, tmp))
        prior_path.write_text(json.dumps(report))
        report2, _ = run_agent(_args(scenario, tmp, resume=prior_path))
        _, record2 = _agent_record(report2)
        assert record2.get("resumed") is True, "unchanged concluded pass must be resumed, not re-run"
        assert report2["agent_run"]["llm_calls_used"] == 0

    del os.environ["AGENT_STUB_SCENARIO"]
    print("agent_fixture OK: a scripted LLM drives an unclassified residue pass to a REAL proved "
          "verdict (origin: agent, provenance-tagged headline) while the deterministic headline "
          "stays untouched; an advisory 'refuted' conclusion trips no gate; invalid/malformed "
          "replies execute nothing (one strike recoverable, two degrade); budget exhaustion winds "
          "down cleanly; resume skips settled passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
