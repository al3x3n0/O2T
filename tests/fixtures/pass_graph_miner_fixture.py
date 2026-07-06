#!/usr/bin/env python3
"""Validate Pass-IR recovery against REAL AST-miner output (not hand-crafted strings).

Every other pass_graph test feeds `recover_pair` matcher/rewrite strings written by hand to match the
recovery's own expectations. This fixture instead reads the golden findings emitted by the real
`cv-mine-pass-source-ast` miner (recorded as JSONL) and drives them through `recover_from_finding`,
which bridges the miner's OPERAND-LEVEL schema -- an `opcode` plus operand guards like
`match(Op1, m_Zero())` / `Op0 == Op1`, and a rewrite that may `return <value>` directly -- to the
whole-instruction structural recovery. This is what closes the "never tested on real miner output"
gap: it exercises the actual string format the miner produces.

It pins that the engine, on genuine miner findings:
  * PROVES the sound folds -- add-zero, and the non-trivial fact-guarded urem->and-mask and
    sdiv->udiv (whose preconditions the miner recovered from ValueTracking queries);
  * REFUTES the planted bug in foldadd_multibranch.cpp (`add x, x -> x`) with a witness -- teeth on
    real data, not just crafted inputs.

Needs z3.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg
from o2t import mini_alive as ma

FIX = Path(__file__).resolve().parent


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIX / name).read_text().splitlines() if line.strip()]


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_miner_fixture: z3 not found, skipped")
        return 0

    def verdict(finding: dict):
        pair = pg.recover_from_finding(finding)
        if pair is None:
            return "declined", None, None
        status, cex = ma.prove(pair, z3)
        return status, cex, pair

    # 1. The bridge reconstructs a whole-instruction matcher from the operand-level schema.
    assert pg.finding_to_predicate("add", "match(Op1, m_Zero())") == \
        "match(&I, m_Add(m_Value(Op0), m_Zero()))", "operand match -> instruction matcher"
    assert pg.finding_to_predicate("add", "Op0 == Op1") == \
        "match(&I, m_Add(m_Value(Op0), m_Deferred(Op0)))", "operand equality -> deferred match"
    assert pg.finding_to_predicate("sdiv", "isKnownNonNegative(Op0) && isKnownNonNegative(Op1)") == \
        "match(&I, m_SDiv(m_Value(Op0), m_Value(Op1))) && isKnownNonNegative(Op0) && isKnownNonNegative(Op1)", \
        "value facts flow through unchanged"
    assert pg.finding_to_predicate("frobnicate", "match(Op1, m_Zero())") is None, "unknown opcode declines"

    # 2. foldadd_multibranch.cpp mined branches: add-zero proves, the planted `add x,x -> x` refutes.
    add = _load("foldadd_branches.jsonl")
    assert len(add) == 3, ("expected 3 mined add branches", len(add))
    for finding in add:
        status, cex, _ = verdict(finding)
        if "==" in finding["predicate_source"]:                       # the planted bug: add x, x -> x
            assert status == "refuted" and cex, ("planted add x,x->x must refute with a witness", status)
        else:                                                          # add x, 0 -> x
            assert status == "proved", ("mined add-zero must prove", finding, status)

    # 3. Fact-guarded folds mined from real source prove ONLY because the miner recovered the
    #    ValueTracking precondition; the recovery lowers that guard to the SMT side condition.
    guarded = {(f["opcode"]): f for f in _load("fact_guarded_branches.jsonl")}
    urem_status, _, urem_pair = verdict(guarded["urem"])
    assert urem_status == "proved", ("mined urem->and-mask (pow2 guard) must prove", urem_status)
    assert any(a.get("op") == "power-of-two" for a in urem_pair["assumptions"]), urem_pair["assumptions"]
    sdiv_status, _, sdiv_pair = verdict(guarded["sdiv"])
    assert sdiv_status == "proved", ("mined sdiv->udiv (nonneg guard) must prove", sdiv_status)
    assert {a["name"] for a in sdiv_pair["assumptions"]} == {"op0", "op1"}, sdiv_pair["assumptions"]

    # 4. TEETH on the real fold: dropping the mined precondition makes sdiv->udiv unsound -> refuted.
    stripped = dict(guarded["sdiv"], predicate_source="")
    status, cex, _ = verdict(stripped)
    assert status == "refuted" and cex, ("unguarded sdiv->udiv must refute", status)

    print("pass_graph_miner_fixture OK: recovery drives REAL cv-mine-pass-source-ast findings -- "
          "proves add-zero and the fact-guarded urem->and-mask / sdiv->udiv folds (preconditions "
          "recovered from ValueTracking), and refutes the planted add x,x->x bug with a witness")
    return 0


if __name__ == "__main__":
    sys.exit(main())
