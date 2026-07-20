#!/usr/bin/env python3
"""Cover the agent's tool-synthesis quarantine: staged code is data until a human promotes it.

Pins the TCB boundary: a synthesized tool + fixture land ONLY under the run's agent-staging/
directory (never tools/, never tests/), the fixture executes once via `python -I` in a fresh temp
cwd with a minimal environment, its result is labeled `advisory-staged`, unsafe names are refused,
and the whole action is absent unless --enable-synthesis is passed. No z3 needed.
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
from o2t.agent.actions import build_registry  # noqa: E402
from o2t.agent.cli import run_agent  # noqa: E402
from o2t.agent.staging import StagingArea  # noqa: E402

STUB = Path(__file__).resolve().parent / "agent_llm_stub.py"
SNIPPET = Path(__file__).resolve().parent / "agent_residue_snippet.cpp"

TOOL_SRC = '#!/usr/bin/env python3\nprint("candidate tool: would mine widget passes")\n'
FIXTURE_SRC = (
    "import sys\n"
    "# a staged fixture proves only that the CANDIDATE runs; it carries no verdict weight\n"
    "assert 1 + 1 == 2\n"
    "print('staged fixture ran isolated; cwd =', __import__('os').getcwd())\n"
    "sys.exit(0)\n")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) The action is OPT-IN: absent from the registry (and hence the LLM's menu) by default.
        assert "synthesize-tool" not in build_registry(enable_synthesis=False)
        assert "synthesize-tool" in build_registry(enable_synthesis=True)

        # 2) StagingArea rejects unsafe names and path escapes -- staged code cannot land outside
        #    the staging root, let alone in tools/.
        staging = StagingArea(tmp / "agent-staging")
        for bad in ("../evil", "cv-agent-", "evil", "cv-agent-UPPER", "cv-agent-a/../../x"):
            assert "error" in staging.stage_tool(bad, "p", "x", "y"), bad

        # 3) End-to-end through the agent loop: the scripted LLM stages a tool; the fixture runs
        #    isolated and the result is advisory-staged; nothing appears under tools/.
        tools_before = sorted(p.name for p in (ROOT / "tools").iterdir())
        scenario = tmp / "synth.json"
        scenario.write_text(json.dumps([
            {"action": "synthesize-tool",
             "args": {"name": "cv-agent-widget-miner", "purpose": "mine widget passes",
                      "tool_source": TOOL_SRC, "fixture_source": FIXTURE_SRC},
             "rationale": "no existing tool covers widget passes"},
            {"action": "conclude", "args": {"proposal": "needs-human",
                                            "rationale": "staged a candidate tool for review"}},
        ]))
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        args = SimpleNamespace(
            source=[SNIPPET], passes=[], include=[], exclude=[],
            llm_command=f"{sys.executable} {STUB}",
            budget=10, max_steps_per_pass=8, action_timeout=120, llm_timeout=60,
            out_dir=tmp / "out", enable_synthesis=True, resume=None,
            report=None, summary_text=None,
            z3_bin="z3", opt_bin="opt", clang_bin="clang", ast_miner=None,
            fail_on_refuted=False, fail_on_agent_refuted=False, selftest=False)
        report, exit_code = run_agent(args)
        del os.environ["AGENT_STUB_SCENARIO"]
        assert exit_code == 0
        entry = next(e for e in report["passes"] if e.get("source", "").endswith(SNIPPET.name))
        record = entry["agent"]
        assert record["status"] == "concluded"
        staged = record["staged_tools"]
        assert len(staged) == 1, staged
        tool = staged[0]
        staging_root = (tmp / "out" / "agent-staging").resolve()
        assert Path(tool["path"]).is_relative_to(staging_root), tool["path"]
        assert Path(tool["path"]).exists() and Path(tool["fixture"]).exists()
        assert tool["sha256"] and tool["fixture_sha256"]
        result = tool["fixture_result"]
        assert result["trust"] == "advisory-staged" and result["exit_code"] == 0, result
        assert "isolated" in result["stdout_tail"]
        # the isolated cwd is a temp dir, not the repo -- staged code never runs "in" the tree.
        assert str(ROOT) not in result["stdout_tail"], result["stdout_tail"]
        # manifest records the staged candidate by content hash (promotion is pinned to a review).
        manifest = json.loads((staging_root / "manifest.json").read_text())
        assert manifest[0]["name"] == "cv-agent-widget-miner" and manifest[0]["sha256"] == tool["sha256"]
        # nothing landed in tools/ -- promotion is a human act, not an agent one.
        assert sorted(p.name for p in (ROOT / "tools").iterdir()) == tools_before
        # a staged-tool "success" is NOT formal evidence: the agent headline has none.
        assert record["headline"]["status"] == "no-formal-evidence", record["headline"]
        assert report["summary"]["agent"]["staged_tools"] == 1

        # 4) Without --enable-synthesis the same scenario cannot stage: the action is unknown to
        #    the registry, so the reply is an invalid-action strike, and nothing is written.
        os.environ["AGENT_STUB_SCENARIO"] = str(scenario)
        args.enable_synthesis = False
        args.out_dir = tmp / "out2"
        report, _ = run_agent(args)
        del os.environ["AGENT_STUB_SCENARIO"]
        entry = next(e for e in report["passes"] if e.get("source", "").endswith(SNIPPET.name))
        record = entry["agent"]
        assert record["staged_tools"] == []
        assert not (tmp / "out2" / "agent-staging").exists()
        assert any(s["observation"].get("error") == "invalid-action" for s in record["steps"])

    print("agent_synthesis_fixture OK: synthesize-tool is opt-in; staged candidates land only "
          "under agent-staging/ (hash-pinned manifest, unsafe names refused), their fixtures run "
          "isolated (python -I, temp cwd) with advisory-staged results carrying zero verdict "
          "weight, tools/ is untouched, and without the flag the action does not even exist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
