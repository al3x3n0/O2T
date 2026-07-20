#!/usr/bin/env python3
"""E2: the aggregate mutation catch-rate is gated -- zero survivors across every teeth tier.

Rolls the three independent teeth tiers into one measurement and pins the invariant that matters:
every seeded corruption is caught, no survivors, no vacuous premises. The per-tier and total
counts are machine-stable (they are structural, not timing), so unlike E3 the fixture can assert
them; the measured table lands in docs/e2-mutation.md. Needs z3."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.meta import mutation_catchrate as mc  # noqa: E402


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("mutation_catchrate_fixture: z3 not found, skipped")
        return 0

    r = mc.run(z3)

    # 1. THE INVARIANT: zero survivors anywhere -- every seeded corruption is caught.
    assert r["survivors"] == [], r["survivors"]
    assert r["total_caught"] == r["total_seeded"] and r["total_seeded"] >= 40, \
        (r["total_caught"], r["total_seeded"])

    # 2. Deep-contract tier: every mutant killed (with a witness -- _killed requires one), no
    #    vacuous premises, and every audited family present.
    d = r["deep_contracts"]
    assert d["killed"] == d["mutants"] and d["witnessed"] == d["killed"], d
    assert d["survivors"] == [] and d["premises_satisfiable"], d
    assert set(d["by_family"]) >= {"vectorize-slp", "memory-dse", "cleanup-dce", "global",
                                   "loop-structural", "cfg"}, sorted(d["by_family"])
    for fam, v in d["by_family"].items():
        assert v["killed"] == v["mutants"] and v["mutants"] > 0, (fam, v)

    # 3. Recovery tier (E7): every misrecovery class caught, zero escapes.
    assert r["recovery"]["caught"] == r["recovery"]["classes"] and r["recovery"]["escapes"] == []

    # 4. Registry tier: every perturbed intent rejected (a perturbed intent that still "proves"
    #    would be a vacuous prover); every negative rejected.
    rg = r["registry"]
    if rg.get("available"):
        assert rg["mutations_rejected"] == rg["mutations"] and rg["mutations"] > 0, rg
        assert rg["negatives_rejected"] == rg["negatives"], rg

    print(f"mutation_catchrate_fixture OK: {r['total_caught']}/{r['total_seeded']} seeded "
          "corruptions caught with zero survivors across all three teeth tiers -- deep-contract "
          "single-point mutations (each killed with a witness, premises SAT), the recovery-side "
          "misrecovery classes (E7, zero escapes), and the registry perturbed intents; witness "
          "minimality is scoped to the loop track, not overclaimed here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
