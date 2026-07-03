#!/usr/bin/env python3
"""Simulation-relation loop equivalence: prove structurally-different loops equal for all trip counts.

When a transform reshapes a loop's state (reindex, redundant/extra induction variable), positional
state equality no longer holds, so equivalence needs a simulation relation R(s,t) proved inductive
(init / guard-sync / step-preserves-R / result). This validates the bundled simulation contracts:
two loops with a different state shape (a redundant second accumulator) proved equivalent under the
relating R, with two-sided teeth (a miscompiled loop and an insufficient R each fail an obligation
with a witness). Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_simulation as S  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "loop_simulation_cases.ll"


def _contracts(z3, src):
    # base state [i, acc]; dup state [i, acc, acc2]. R: i==i, acc==acc, and the redundant acc2==acc.
    R = S.mapped_relation([(0, 0), (1, 1)], extra=[(1, 2)])
    weak = S.mapped_relation([(0, 0), (1, 1)])                       # forgets acc2 -> result fails
    bad = src.replace("%acc2.n = add i32 %acc2, %i", "%acc2.n = add i32 %acc2, 1")
    return [
        ("redundant-state-sim", S.validate_simulation(z3, src, "base", src, "dup", R), "proved"),
        ("teeth-corrupt-step", S.validate_simulation(z3, src, "base", bad, "dup", R), "refuted"),
        ("teeth-insufficient-R", S.validate_simulation(z3, src, "base", src, "dup", weak), "refuted"),
        # AUTO-INFERRED relation (Houdini), no hand-given R: recovers the redundant-state relation.
        ("auto-inferred-sim", S.validate_simulation_auto(z3, src, "base", src, "dup"), "proved"),
        ("auto-corrupted", S.validate_simulation_auto(z3, src, "base", bad, "dup"), "refuted"),
        # STRENGTH REDUCTION: the affine relation j == 3*i is inferred and carries the proof.
        ("auto-strength-reduction",
         S.validate_simulation_auto(z3, src, "withmul", src, "strred"), "proved"),
        ("auto-strred-wrong-stride",
         S.validate_simulation_auto(z3, src, "withmul",
                                    src.replace("%j.n = add i32 %j, 3", "%j.n = add i32 %j, 2"),
                                    "strred"), "refuted"),
        # NON-UNIT-STRIDE strength reduction: IV strides by 2, accumulator by 10 -> c = 5.
        ("auto-nonunit-stride",
         S.validate_simulation_auto(z3, src, "mul2", src, "sr2"), "proved"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    src = args.source.read_text()
    results = [{"contract": name, "status": r["status"], "expect": exp,
                "ok": r["status"] == exp, "failed": r.get("failed")}
               for name, r, exp in _contracts(z3, src)]
    proved = [r for r in results if r["status"] == "proved"]
    ok = all(r["ok"] for r in results)
    report = {"results": results, "proved": len(proved),
              "refuted": sum(1 for r in results if r["status"] == "refuted"), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"contracts": len(results), "proved": len(proved),
                      "refuted": report["refuted"], "ok": ok}, sort_keys=True))
    for r in results:
        mark = "ok " if r["ok"] else "!! "
        print(f"  {mark}[{r['status']:8}] {r['contract']} {r.get('failed') or ''}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
