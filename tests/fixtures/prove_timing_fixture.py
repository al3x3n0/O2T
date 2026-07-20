#!/usr/bin/env python3
"""E3: the performance harness is gated -- robust facts only, measured numbers live in the doc.

Wall-clock numbers are machine-specific, so this fixture asserts ORDERINGS and generous bounds,
never absolute times: the integer-ring discharge of the nonlinear Faulhaber STEP succeeds well
under the cap while the bit-blasted bv32 twin exhausts it (or is at least an order slower --
either outcome validates the claim, and which one occurred is recorded); the batched synthesis
discharge agrees candidate-by-candidate with the per-candidate path and does not lose to it; and
every representative fold obligation proves within a generous bound. Needs z3."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.prove import timing  # noqa: E402


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("prove_timing_fixture: z3 not found, skipped")
        return 0

    # A short cap keeps CI fast; the claim needs only the CONTRAST, not the full 10s wait.
    r = timing.run(z3, cap_s=4.0)

    rb = r["ring_vs_bv32"]
    # 1. The integer discharge is a fast theorem...
    assert rb["integer"]["verdict"] == "unsat", rb
    assert rb["integer"]["seconds"] < 2.0, rb
    # ...and the bv32 twin either times out or is at least an order of magnitude slower.
    assert rb["bv32"]["verdict"] == "timeout" or \
        rb["bv32"]["seconds"] >= 10 * max(rb["integer"]["seconds"], 0.01), rb

    bp = r["batch_vs_per_candidate"]
    # 2. Batch and per-candidate AGREE (asserted inside the harness) and exactly one candidate is
    #    valid; batching does not lose.
    assert bp["valid_found"] == 1, bp
    assert bp["batch_seconds"] <= bp["per_candidate_seconds"], bp

    # 3. Every representative fold obligation proves, within a generous bound.
    for name, v in r["per_obligation"].items():
        assert v["verdict"] == "proved" and v["seconds"] < 5.0, (name, v)

    print("prove_timing_fixture OK: the integer-ring discharge proves the nonlinear Faulhaber "
          "STEP fast while its bit-blasted bv32 twin exhausts the cap (or trails by an order); "
          "batched synthesis discharge agrees with per-candidate and does not lose; every "
          "representative fold obligation proves within bound -- the E3 mechanisms are gated, "
          "the measured table lives in docs/e3-timing.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
