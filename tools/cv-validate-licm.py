#!/usr/bin/env python3
"""Discharge the deep LICM hoist contracts (loop-structural family).

Proves that hoisting a loop-invariant, safe-to-execute computation out of a loop preserves
behavior, with two-sided teeth: hoisting a varying (non-invariant) operand is REFUTED (stale
value), and hoisting a trapping op that is neither guaranteed-to-execute nor speculatable is
REFUTED (a new trap) -- each with a concrete witness. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate.loop_structural_model import run_contracts  # noqa: E402


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

    results = run_contracts(z3)
    ok = all(r["ok"] for r in results.values())
    refuted = [n for n, r in results.items() if r["status"] == "refuted"]
    proved = [n for n, r in results.items() if r["status"] == "proved"]
    report = {"results": results, "contracts": len(results),
              "proved": len(proved), "refuted": len(refuted), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"contracts": len(results), "proved": len(proved),
                      "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for n, r in results.items():
        mark = "ok " if r["ok"] else "!! "
        print(f"  {mark}[{r['status']:8}] {n} (expect {r['expect']})", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
