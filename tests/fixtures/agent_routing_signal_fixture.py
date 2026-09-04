#!/usr/bin/env python3
"""The agent's prompt must say which strategy has something to WORK ON, not just what may run.

WHY. Four live-model runs were examined and three concluded `inconclusive` after
`mine-source` -> `classify`, spending almost none of their budget. The catalog already listed all
33 strategies with a description and a runnability flag, and it still did not route -- because for
a typical pass 32 of the 33 come back `runnable_here: true`. That is a permissions list, not
evidence: the model saw 32 equally endorsed options and had to guess relevance from the source text.

(The fourth run and the three `inconclusive` verdicts were, notably, CORRECT: they targeted
`agent_residue_snippet.cpp`, which is deliberately vendor bookkeeping with no transform in it. The
harness was being judged on the one input where success was impossible. Assertion 3 pins that the
signal says so explicitly rather than leaving the model to infer it.)

WHAT THIS PINS. Source-targeted strategies report `would_recover`: how many folds that strategy's
miner actually recovers from THIS source. Mining is regex/AST work -- the expensive half of these
strategies is the Z3 discharge -- so it costs well under a millisecond per miner and turns 33
opaque ids into a ranked list. On the positive control exactly one strategy is non-zero, and it is
the one that finds the planted bug.

`would_recover` ABSENT means unmeasured, never zero. A strategy with no wired miner must not be
reported as inapplicable; that is the absence-of-evidence-as-evidence error this project keeps
finding elsewhere, and here it would steer a model away from a strategy that may well apply.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.actions import _SOURCE_MINERS, build_registry  # noqa: E402
from o2t.agent.loop import _state_for, build_request  # noqa: E402
from o2t.orchestrate.run import resolve_context  # noqa: E402

FX = Path(__file__).resolve().parent


class _Budget:
    remaining = 10


def _catalog(source: Path) -> dict:
    entry = {"source": str(source), "pass_name": None, "checks": [],
             "headline": {"status": "unclassified", "reason": "no family matched"}}
    req = build_request(_state_for(entry), build_registry(),
                        resolve_context("z3", "opt", "clang"), _Budget(), 8)
    cat = {c["id"]: c for a in req["actions"] if a.get("strategy_catalog")
           for c in a["strategy_catalog"]}
    assert cat, "the strategy catalog must reach the prompt"
    return {"catalog": cat, "instruction": req["instruction"]}


def main() -> int:
    # 1) THE POSITIVE CONTROL: exactly one strategy has anything to work on, and it is the one that
    #    finds the planted unguarded FP reduction. This is the discrimination the model lacked.
    r = _catalog(FX / "agent_positive_control_snippet.cpp")
    found = {k: v["would_recover"] for k, v in r["catalog"].items() if v.get("would_recover")}
    assert found == {"slp-source": 3}, \
        ("exactly one strategy must stand out on the positive control -- if several do, the signal "
         "is not discriminating; if none does, it is not working", found)

    # 2) CROSS-FAMILY: the same signal must point OUTSIDE the classified family. This snippet wears
    #    peephole idiom over an SLP fold, so the planner never runs slp-source; the catalog is what
    #    lets the agent make the one move the planner structurally cannot.
    rx = _catalog(FX / "cross_family_unattributed_snippet.cpp")
    foundx = {k: v["would_recover"] for k, v in rx["catalog"].items() if v.get("would_recover")}
    assert foundx == {"slp-source": 3}, foundx

    # 3) THE HONEST NEGATIVE. On the snippet the live runs actually used, NOTHING is recoverable --
    #    so `inconclusive` was the right answer and the catalog now says why. A signal that only
    #    ever points somewhere would be an invitation to manufacture findings.
    rn = _catalog(FX / "agent_residue_snippet.cpp")
    assert not any(v.get("would_recover") for v in rn["catalog"].values()), \
        ("no miner recovers anything from the deliberately transform-free residue snippet",
         {k: v.get("would_recover") for k, v in rn["catalog"].items() if v.get("would_recover")})
    assert any(v.get("would_recover") == 0 for v in rn["catalog"].values()), \
        "and the zero must be REPORTED, not omitted -- silence is not the same as measured-zero"

    # 4) UNMEASURED IS NOT ZERO. Strategies with no wired miner carry no `would_recover` key at all.
    unwired = [k for k in r["catalog"] if k not in _SOURCE_MINERS]
    assert unwired and all("would_recover" not in r["catalog"][k] for k in unwired), \
        ("a strategy with no miner must be UNMEASURED, not reported as recovering zero -- "
         "reporting 0 would steer the model away from a strategy that may well apply", unwired[:5])

    # 5) THE INSTRUCTION MUST POINT AT ALL OF IT. Evidence nobody is told to read goes unused --
    #    which is exactly what happened with the catalog before `would_recover` existed.
    ins = r["instruction"]
    for phrase in ("would_recover", "unmeasured, not", "did NOT pick", "inconclusive"):
        assert phrase in ins, (f"the instruction must mention {phrase!r}", ins)

    print("agent_routing_signal_fixture OK: the prompt now ranks strategies by what their miners "
          "actually recover from this source (positive control: slp-source 3, everything else 0), "
          "points outside the classified family where that is where the fold is, reports an honest "
          "zero on the transform-free snippet the live runs used, keeps unwired miners UNMEASURED "
          "rather than zero, and the instruction tells the model how to read all of it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
