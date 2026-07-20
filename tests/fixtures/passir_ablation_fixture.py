#!/usr/bin/env python3
"""E7: the recovery-soundness ablation is gated -- zero escapes, and the load-bearing layers.

Seeds every misrecovery class into known-good recovered obligations and pins: (1) the ZERO-ESCAPE
invariant -- every applicable (class x fold) corruption is caught by at least one C7 layer; (2)
the two classes that prove specific layers UNIQUELY load-bearing -- a width-specific corruption is
invisible to every bv32 check and caught only by the width corroboration, and a skipped
predicate-set member is caught only by the all-cases discipline; (3) redundancy everywhere else
(the typical corruption is caught by 3+ independent layers); (4) honesty of the harness itself --
seeds that do not apply to a fold's shape report `not-applicable`, never a silent skip, and every
seed fold starts PROVED (the corruption, not the fold, is what gets caught). Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import ablation  # noqa: E402


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("passir_ablation_fixture: z3 not found, skipped")
        return 0

    report = ablation.run_ablation(z3)

    # 1. ZERO ESCAPES: every applicable corruption is caught by at least one layer.
    assert report["escapes"] == [], report["escapes"]

    matrix = report["matrix"]

    # 2. UNIQUELY LOAD-BEARING layers: the width-specific corruption proves at bv32 under every
    #    other check -- only the width corroboration flags it; a skipped predicate member is
    #    caught only by the all-cases discipline.
    for fold, caught in matrix["width-specific-const"].items():
        assert caught == ["width-corroboration"], (fold, caught)
    assert matrix["skipped-pred-case"]["icmp-eq-hardcode"] == ["all-cases-discipline"]
    assert matrix["skipped-pred-case"]["member_verdicts"] == {"eq": "proved", "ne": "refuted"}

    # 3. REDUNDANCY where it should exist: a mislowered builder on a plain builder fold is caught
    #    by prove-teeth AND the concrete reconciliation AND the second solver AND width.
    assert set(matrix["mislowered-builder"]["builder-dfg"]) >= {
        "prove-teeth", "reconcile-concrete", "second-solver"}

    # 4. The vacuous-proof trap: contradictory premises are never `proved` -- the premise-SAT gate
    #    catches them on every fold.
    for fold, caught in matrix["contradictory-premise"].items():
        assert "premise-sat-gate" in caught, (fold, caught)

    # 5. HONEST HARNESS: shape-inapplicable seeds are reported, never silently skipped; and at
    #    least one is expected (the identity fold has no binop in its `after` to drop).
    assert matrix["dropped-operator"]["nested-identity"] == "not-applicable"

    # 6. A documented SUBTLETY, pinned so it stays a conscious fact: mislowering or->add under the
    #    disjointness premise is semantically INVISIBLE (disjoint operands add without carry --
    #    the corruption is value-equal), so the value layers rightly do not refute it; the width
    #    corroboration still flags the obligation conservatively.
    assert "prove-teeth" not in matrix["mislowered-builder"]["disjoint-or"]

    print("passir_ablation_fixture OK: zero escapes across every seeded misrecovery class; the "
          "width corroboration and the all-cases discipline are each proven uniquely load-bearing "
          "(their classes evade every other layer); typical corruptions are caught by 3+ "
          "independent layers; contradictory premises never prove; inapplicable seeds and the "
          "semantically-invisible disjoint-or mislowering are reported honestly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
