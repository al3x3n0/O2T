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

# THE INSTRUCTION THIS FIXTURE ENRICHES MUST STILL BE UNMODELLED -- that is its PREMISE, and it is
# asserted below rather than assumed. It used to use `llvm.bswap`, which was later modelled natively
# (4d5f452); the premise vanished, the agent had nothing to diagnose, and the fixture failed with
# `diagnosed: []` -- a confusing symptom a long way from its cause. `llvm.ushl.sat` is unmodelled
# today; when it too gets modelled, the assertion says so and says what to do.
#
# A corpus whose functions whole-function TV declines ONLY for that missing instruction.
_INTR = "ushl.sat"
CORPUS = ("declare i32 @llvm.ushl.sat.i32(i32, i32)\n"
          "define i32 @sh1(i32 %x) {\n"
          "  %a = call i32 @llvm.ushl.sat.i32(i32 %x, i32 1)\n"
          "  %b = call i32 @llvm.ushl.sat.i32(i32 %a, i32 0)\n  ret i32 %b\n}\n"
          "define i32 @sh2(i32 %x) {\n"
          "  %a = call i32 @llvm.ushl.sat.i32(i32 %x, i32 2)\n  ret i32 %a\n}\n")

# The LLM's proposal (as the stub would emit it): the SMT model uses %OP% for the operand.
# `ushl.sat x, c` is `x << c` saturating to all-ones on unsigned overflow -- i.e. if shifting back
# does not recover x, the result is UINT_MAX.
def _shl_sat(c: int) -> str:
    return (f"(ite (= (bvlshr (bvshl %OP% (_ bv{c} 32)) (_ bv{c} 32)) %OP%) "
            f"(bvshl %OP% (_ bv{c} 32)) (_ bv4294967295 32))")

_SAT = {"name": _INTR, "decl": "declare i{w} @llvm.ushl.sat.i{w}(i{w}, i{w})",
        "call": "call i{w} @llvm.ushl.sat.i{w}(i{w} {a}, i{w} 1)",
        "regex": r"call\s+i(32)\s+@llvm\.ushl\.sat\.i32\(\s*i32\s+(\S+?)\s*,\s*i32\s+1\s*\)",
        "smt": _shl_sat(1)}
CORRECT = dict(_SAT)
WRONG = {**_SAT, "smt": "%OP%"}                        # identity -- forgets the shift; unsound


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

    # 0. THE PREMISE, asserted rather than assumed: the instruction this fixture enriches must still
    #    be UNMODELLED, or there is nothing to diagnose and every assertion below is vacuous.
    from o2t.validate import semantics as _sem
    assert _INTR not in _sem.INTRINSICS and _INTR not in _sem.MINMAX, (
        f"`llvm.{_INTR}` is now modelled natively, so this fixture has no missing instruction to "
        f"enrich. Point CORPUS/_SAT at an intrinsic that is still declined -- the fixture is about "
        f"the enrichment MECHANISM, not about this particular instruction.")

    # 1. The agent diagnoses the missing instruction, the (stub) LLM proposes it, lli VALIDATES it,
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

    print(f"enrich_agent_fixture OK: an LLM (deterministic stub) DROVE the enrichment loop -- diagnosed "
          f"the missing llvm.{_INTR}, proposed its SMT model, which lli VALIDATED (installed -> reach "
          "lifts 0->2); a WRONG (identity) proposal was REJECTED by lli, never installed, no lift. The LLM "
          "proposes; an oracle it did not author decides -- and a hallucinated model cannot enter the "
          "trust base. Going live is one flag (--llm-command)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
