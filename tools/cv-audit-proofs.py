#!/usr/bin/env python3
"""Meta-verify the verifiers: audit what every "proved" verdict means (anti-vacuity + mutation).

For each proved deep contract across all families, this checks (1) the contract's assumptions are
jointly satisfiable -- no contradictory guard makes the proof vacuously true -- and (2) every
single-point corruption of the transform (swap a lane, drop a guard, flip a condition, make an op
non-associative, expose an initializer) is REFUTED with a witness. A mutant that still proves is a
SURVIVOR: the obligation did not constrain that point, so the proof had a teeth gap. Exit 0 iff
every premise is satisfiable and every mutant is killed. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.meta.proof_audit import run_audit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    rep = run_audit(z3)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")

    for r in rep["rows"]:
        mark = "ok " if r["ok"] else "GAP"
        kills = sum(1 for m in r["mutants"] if m["killed"])
        print(f"  {mark} [{r['family']:16}] {r['contract']:32} "
              f"{kills}/{len(r['mutants'])} mutants killed"
              + (f"  SURVIVORS={r['survivors']}" if r["survivors"] else ""), file=sys.stderr)
    for p in rep["premise_checks"]:
        if not p["ok"]:
            print(f"  GAP premise unsatisfiable: {p['contract']}", file=sys.stderr)

    print(json.dumps({"families": len(rep["families"]),
                      "contracts_audited": rep["contracts_audited"],
                      "mutants": rep["mutants"], "killed": rep["killed"],
                      "survivors": len(rep["survivors"]),
                      "premises_satisfiable": rep["premises_satisfiable"],
                      "ok": rep["ok"]}, sort_keys=True))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
