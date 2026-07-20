#!/usr/bin/env python3
"""Deterministic multi-turn LLM stub for the verification-agent fixtures.

Pure function of the request (no state files, no network): the turn index is
`len(request["evidence"])` -- the agent's own evidence log is the turn counter -- and the reply is
that index into a scenario, a JSON array of replies loaded from the file named by the
AGENT_STUB_SCENARIO environment variable. A scenario entry that is the string "malformed" emits
non-JSON garbage (to exercise the transport-failure path); past-the-end turns emit a conclude, so
scenarios always terminate. This mirrors the orchestrate_llm_stub convention: fixtures drive the
agent loop deterministically with zero model access.
"""

import json
import os
import sys


def main() -> int:
    request = json.load(sys.stdin)
    scenario_path = os.environ.get("AGENT_STUB_SCENARIO")
    if not scenario_path:
        print(json.dumps({"action": "conclude",
                          "args": {"proposal": "inconclusive"},
                          "rationale": "stub: no scenario configured"}))
        return 0
    with open(scenario_path) as fh:
        scenario = json.load(fh)
    turn = len(request.get("evidence", []))
    if turn >= len(scenario):
        print(json.dumps({"action": "conclude",
                          "args": {"proposal": "inconclusive"},
                          "rationale": "stub: scenario exhausted"}))
        return 0
    reply = scenario[turn]
    if reply == "malformed":
        print("this is not JSON at all -- transport must fail gracefully")
        return 0
    print(json.dumps(reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
