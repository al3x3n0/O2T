#!/usr/bin/env python3
"""Enrichment agent: the LLM DRIVES enrichment; the oracle DECIDES -- gated on a deterministic stub.

The last mile of the autonomous harness. o2t/agent/enrich_agent drives the loop: diagnose the
`unsupported` declines of whole-function TV, ask the LLM to propose each missing instruction's SMT
semantics (provider-agnostic transport -- a deterministic stub here, `claude -p --output-format json`
live), validate every proposal against `lli` EXECUTION, install only the survivors, and re-run TV.

Trust invariant, exercised: the LLM proposes; an oracle it did not author decides. This fixture drives
the SAME code with a deterministic stub (zero model access): a CORRECT bswap proposal is lli-validated,
installed, and lifts the reach (0 -> 2 proved); a WRONG (identity) proposal is REJECTED by lli, never
installed, and yields NO lift -- so a hallucinated model cannot enter the trust base or fake a proof.
Going live is one flag: point --llm-command at a real model. Needs z3 + opt + lli (18).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.llm import LLMClient  # noqa: E402
from o2t.agent import enrich_agent as ea  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402

STUB = ROOT / "tests" / "fixtures" / "agent_llm_stub.py"
_HB_LLI = "/opt/homebrew/opt/llvm@18/bin/lli"

# A corpus whose functions whole-function TV declines only for the missing `llvm.bswap` instruction.
CORPUS = ("declare i32 @llvm.bswap.i32(i32)\n"
          "define i32 @dbl(i32 %x) {\n"
          "  %a = call i32 @llvm.bswap.i32(i32 %x)\n"
          "  %b = call i32 @llvm.bswap.i32(i32 %a)\n  ret i32 %b\n}\n"   # bswap(bswap x) -> x
          "define i32 @swp(i32 %x) {\n"
          "  %a = call i32 @llvm.bswap.i32(i32 %x)\n  ret i32 %a\n}\n")   # bswap x (opt leaves it)

# The LLM's proposal (as the stub would emit it): the SMT model uses %OP% for the operand.
_BSWAP = {"name": "bswap", "decl": "declare i{w} @llvm.bswap.i{w}(i{w})",
          "call": "call i{w} @llvm.bswap.i{w}(i{w} {a})",
          "regex": r"call\s+i(32)\s+@llvm\.bswap\.i32\(\s*i32\s+(\S+?)\s*\)",
          "smt": ("(concat ((_ extract 7 0) %OP%) ((_ extract 15 8) %OP%) "
                  "((_ extract 23 16) %OP%) ((_ extract 31 24) %OP%))")}
CORRECT = dict(_BSWAP)
WRONG = {**_BSWAP, "smt": "%OP%"}                      # identity -- forgets to reverse; unsound


def _run_with(reply: dict, z3, lli, opt) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump([reply], tf)                         # turn 0 (no evidence) -> the stub returns reply
        scenario = tf.name
    os.environ["AGENT_STUB_SCENARIO"] = scenario
    try:
        client = LLMClient(command=f"{sys.executable} {STUB}", budget=8)
        return ea.run(CORPUS, client, z3, lli, opt)
    finally:
        Path(scenario).unlink(missing_ok=True)


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    lli = shutil.which("lli") or (_HB_LLI if Path(_HB_LLI).exists() else None)
    if z3 is None or opt is None or lli is None:
        print("enrich_agent_fixture: z3 / opt / lli (18) not all found, skipped")
        return 0

    # 1. The agent diagnoses the missing instruction, the (stub) LLM proposes bswap, lli VALIDATES it,
    #    it is installed, and the reach lifts 0 -> 2 -- the loop ran end-to-end with zero model access.
    good = _run_with(CORRECT, z3, lli, opt)
    assert good["diagnosed"], ("agent must diagnose the missing instruction", good)
    assert good["installed"] == 1, ("the validated proposal must be installed", good)
    assert [e["status"] for e in good["enrichments"]] == ["validated"], good
    assert good["proved_before"] == 0 and good["proved_after"] == 2, ("reach must lift 0 -> 2", good)

    # 2. TEETH: the SAME agent, given a WRONG (identity) proposal, has it REJECTED by lli -- nothing is
    #    installed, and the reach does NOT lift (0 -> 0). A hallucinated model cannot fake a proof.
    bad = _run_with(WRONG, z3, lli, opt)
    assert bad["installed"] == 0, ("a wrong proposal must not be installed", bad)
    assert [e["status"] for e in bad["enrichments"]] == ["rejected"], bad
    assert bad["proved_after"] == 0, ("a rejected proposal must not lift the reach", bad)

    print("enrich_agent_fixture OK: an LLM (deterministic stub) DROVE the enrichment loop -- diagnosed "
          "the missing llvm.bswap, proposed its SMT model, which lli VALIDATED (installed -> reach lifts "
          "0->2); a WRONG (identity) proposal was REJECTED by lli, never installed, no lift. The LLM "
          "proposes; an oracle it did not author decides -- and a hallucinated model cannot enter the "
          "trust base. Going live is one flag (--llm-command)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
