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

        # 1) HAPPY PATH: the scripted LLM classifies, dispatches a REAL verifier (memory-model ->
        #    cv-validate-memory, Z3 decides), and concludes. The deterministic headline stays
        #    `unclassified` and the conclusion is advisory.
        #
        #    THIS CASE USED TO ASSERT THE HEADLINE BECAME `proved`, AND THAT WAS A DEFECT IT
        #    ENSHRINED. `memory-model` is a CANONICAL strategy: it runs `cv-validate-memory` with no
        #    `--source` at all and discharges fixed DSE/forwarding theorems that hold whatever pass
        #    you point the agent at. Its verdict is real, its ATTRIBUTION is empty -- so collapsing
        #    it into a pass-level `proved` marked a vendor bookkeeping snippet with no transform in
        #    it as verified. The agent-layer analogue of a vacuous proof: true, and about nothing.
        #    A live model refused to dispatch it here for exactly that reason, which is how this
        #    was found. The check is still RUN, RECORDED and NAMED -- only its attribution changed.
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
        #    The check ran and is recorded with its real verdict...
        assert record["headline"]["provenance"] == "deterministic+agent-formal"
        assert [c["strategy"] for c in record["headline"]["checks"]] == ["memory-model"], record
        #    ...but it is CANONICAL, so it decides nothing about this pass: the status stays
        #    `no-formal-evidence` and the strategy is named as canonical-only rather than folded in.
        assert record["headline"]["status"] == "no-formal-evidence", record["headline"]
        assert record["headline"]["canonical_only"] == ["memory-model"], record["headline"]
        assert record["conclusion"] == {"proposal": "proved",
                                        "rationale": "memory-model contracts proved",
                                        "trust": "advisory"}
        summary = report["summary"]["agent"]
        assert summary["attempted"] == 1 and summary["concluded"] == 1
        assert summary["agent_formal"].get("proved") == 1, summary
        assert summary["headline_upgrades"] == 0, \
            ("a canonical contract proof must not UPGRADE a pass's headline -- it holds whatever "
             "pass the agent was pointed at", summary)
        #    THE EXCLUSION MUST FAIL SAFE. Only a strategy KNOWN to be canonical is dropped from
        #    attribution; an unknown id counts, because the alternative is that a new or mistyped
        #    strategy silently vanishes -- taking a REFUTATION with it, which is the direction that
        #    hides a miscompile rather than one that over-claims.
        from o2t.agent.report import agent_headline
        unknown_neg = agent_headline({"checks": []},
                                     {"formal_checks": [{"strategy": "brand-new-strategy",
                                                         "verdict": "refuted", "origin": "agent"}]})
        assert unknown_neg["status"] == "refuted", \
            ("a check from an UNKNOWN strategy must still count -- excluding it by default would "
             "silently drop a refutation", unknown_neg)
        known_canon = agent_headline({"checks": []},
                                     {"formal_checks": [{"strategy": "memory-model",
                                                         "verdict": "proved", "origin": "agent"}]})
        assert known_canon["status"] == "no-formal-evidence", known_canon

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

        # 6) NO-PROGRESS: a VALID reply can spin as effectively as a malformed one. A model that
        #    keeps choosing the same action and keeps getting the same answer used to loop until
        #    the step cap, burning budget for no evidence -- the strike counter only ever watched
        #    MALFORMED replies. Found by running the agent against a LIVE model, which spent three
        #    of four calls re-running one `classify` that answered "no family matched" every time;
        #    the scenario stub cannot produce it, because a scenario scripts DISTINCT turns.
        #    The request already carries `evidence`, so the model could SEE its own repetition and
        #    repeated anyway -- prompting is not the fix, this guard is.
        scenario = _write_scenario(tmp, "spin.json", [
            {"action": "classify", "args": {}, "rationale": "first look"},
            {"action": "classify", "args": {}, "rationale": "again, learning nothing"},
            {"action": "classify", "args": {}, "rationale": "and again"},
            {"action": "classify", "args": {}, "rationale": "and again"},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        report, exit_code = run_agent(_args(scenario, tmp, budget=8, max_steps_per_pass=8))
        assert exit_code == 0, exit_code
        _, record = _agent_record(report)
        assert record["status"] == "degraded", \
            ("a repeated action with an identical observation must degrade the pass, not spin to "
             "the step cap", record["status"], record["llm_calls"])
        assert record["llm_calls"] == 3, \
            ("two strikes and out: one real step, then two no-progress repeats", record["llm_calls"])
        assert record["steps"][1]["observation"] == {
            "error": "no-progress", "reason": "action already produced this result"}, record["steps"][1]
        #    ...and the budget is what this protects: the spin must cost strictly less than the cap.
        assert record["llm_calls"] < 8, "the guard must save budget, not merely relabel the outcome"
        #    UNASSERTED, AND DELIBERATELY SO: that a repeat which DOES make progress is still
        #    allowed. The signature keys on (action, args, OBSERVATION) precisely so that re-running
        #    an action which now answers differently is legitimate -- but every action reachable
        #    from this stub is deterministic, so the same action with the same args cannot produce
        #    a different observation here and the case is not constructible. Ablating the
        #    observation out of the key therefore passes this fixture. If a non-deterministic
        #    action is ever added (one whose result depends on staged state), pin that case.

        # 7) THE MODEL TRANSCRIPT. The report records what the agent DID; the transcript records
        #    what was actually SAID. It matters most for the reply the report cannot show: a
        #    MALFORMED one reaches the evidence log only as `invalid-action`, with the text that
        #    would explain it discarded -- and a malformed reply is the failure a live model
        #    actually produces. Debugging one without the raw text means guessing.
        scenario = _write_scenario(tmp, "transcript.json", [
            {"action": "classify", "args": {}, "rationale": "first"},
            "malformed",
            {"action": "conclude", "args": {"proposal": "inconclusive"}},
        ])
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        out = tmp / "tout"
        report, _ = run_agent(_args(scenario, tmp, out_dir=out))
        tpath = Path(report["agent_run"]["llm_transcript"])
        assert tpath.exists(), "the run must write a transcript beside its other artifacts"
        lines = [json.loads(l) for l in tpath.read_text().splitlines() if l.strip()]
        assert len(lines) == report["agent_run"]["llm_exchanges"] >= 3, (len(lines), report["agent_run"])
        assert [r["seq"] for r in lines] == list(range(1, len(lines) + 1)), "seq must be dense"
        for r in lines:
            assert set(r) >= {"request", "stdout", "exit_status", "elapsed_s", "rejected_because"}, r
        #    THE MALFORMED TURN IS THE POINT: its raw text is preserved and a reason is given, where
        #    the evidence log has only `invalid-action`.
        bad = [r for r in lines if r.get("rejected_because")]
        assert bad, ("the malformed reply must be recorded with a reason", lines)
        assert bad[0]["stdout"].strip(), "the raw text of a rejected reply must be kept verbatim"
        assert bad[0]["parsed"] is None, bad[0]
        #    ...and the request is kept too, so a reply can be read against what was actually asked.
        assert "evidence" in bad[0]["request"] and "actions" in bad[0]["request"], bad[0]["request"]
        #    THE REQUEST MUST BE A SNAPSHOT, NOT A LIVE REFERENCE. `build_request` passes
        #    `state.evidence` -- the list the loop appends to -- so recording the reference made
        #    every entry show the FINAL evidence: seq 1 looked as though it had seen three prior
        #    steps. The model got the right data (the transport serialises at send time); only the
        #    transcript lied, which defeats its entire purpose. Evidence must GROW: the n-th call
        #    sees exactly the n-1 steps that preceded it.
        for i, rec in enumerate(lines):
            if "request" not in rec:
                continue
            assert len(rec["request"]["evidence"]) == i, (
                "each recorded request must show the evidence AS SENT -- entry i has i prior steps",
                i, [len(r["request"]["evidence"]) for r in lines if "request" in r])

    del os.environ["AGENT_STUB_SCENARIO"]
    print("agent_fixture OK: a scripted LLM drives an unclassified residue pass to a REAL proved "
          "verdict (origin: agent, provenance-tagged headline) while the deterministic headline "
          "stays untouched; an advisory 'refuted' conclusion trips no gate; invalid/malformed "
          "replies execute nothing (one strike recoverable, two degrade); budget exhaustion winds "
          "down cleanly; resume skips settled passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
