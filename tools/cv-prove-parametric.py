#!/usr/bin/env python3
"""Re-prove the deep contracts across a grid of bit widths and lane counts (generalization).

Every width-parametric deep contract (SLP pack + reduction, GlobalOpt dead-initializer, LICM
hoist-invariance, DSE memory) is re-discharged at widths {8,16,32,64}; the width-insensitive
DCE cleanup contracts are replayed at the same width buckets; and the arity-parametric SLP
contracts also at n {2,4,8,16}. At each point the sound contract must still PROVE and its
single-point corruption must still REFUTE, so the universal claim is backed by proof at every
width/arity, not one sample. A failing point is a real width/arity-specific soundness finding.
Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.meta.parametric import run_parametric  # noqa: E402


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

    rep = run_parametric(z3)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")

    for r in rep["rows"]:
        if not r["ok"]:
            print(f"  FAIL {r['contract']} width={r['width']} n={r['n']} "
                  f"proved={r['proved']} teeth={r['teeth']}", file=sys.stderr)
    print(f"  grid: widths {rep['widths']} x n {rep['lane_counts']} -> {rep['points']} points; "
          f"{rep['proofs_held']} proofs held, {rep['teeth_bit']} teeth bit", file=sys.stderr)

    print(json.dumps({"contracts": len(rep["contracts"]), "points": rep["points"],
                      "proofs_held": rep["proofs_held"], "teeth_bit": rep["teeth_bit"],
                      "failures": len(rep["failures"]), "ok": rep["ok"]}, sort_keys=True))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
