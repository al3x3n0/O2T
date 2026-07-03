#!/usr/bin/env python3
"""Independently confirm every verdict: witness re-validation + second-solver cross-check.

For each deep obligation: (1) re-validate -- substitute a refutation's witness back and confirm
the obligation is genuinely false there (an independent query, not the prover's word); and (2)
cross-check -- replay the proof through every available SMT-LIB2 solver and require agreement.
cvc5/cvc4 are auto-detected; without a second solver the cross-check is reported skipped (honest)
while witness re-validation still gates. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.meta.cross_check import run_cross_check  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--solver", action="append", default=[], metavar="NAME=PATH",
                    help="add a second SMT-LIB2 solver (repeatable), e.g. cvc5=/path/to/cvc5")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin) or args.z3_bin
    if shutil.which(args.z3_bin) is None and not Path(args.z3_bin).exists():
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    extra = []
    for spec in args.solver:
        if "=" in spec:
            name, path = spec.split("=", 1)
            extra.append((name, path))

    rep = run_cross_check(z3, extra_solvers=extra)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")

    for r in rep["proof_rows"]:
        flag = "ok " if r["agree"] else "DISAGREE"
        print(f"  {flag} [{r['theory']:7}] {r['obligation']:18} {r['results']}", file=sys.stderr)
    for r in rep["reval_rows"]:
        if r["confirmed"] is None:
            print(f"  --  {r['obligation']:18} witness re-validation skipped (array model)", file=sys.stderr)
        else:
            flag = "ok " if (r["refuted"] and r["confirmed"]) else "BOGUS"
            print(f"  {flag} {r['obligation']:18} witness confirmed ({r.get('witness_vars')} vars)", file=sys.stderr)
    print(f"  solvers={rep['solvers']} second_solver={rep['second_solver']}", file=sys.stderr)

    print(json.dumps({"solvers": rep["solvers"], "second_solver": rep["second_solver"],
                      "witnesses_revalidated": rep["witnesses_revalidated"],
                      "witnesses_confirmed": rep["witnesses_confirmed"],
                      "cross_checked": rep["cross_checked"], "cross_agree": rep["cross_agree"],
                      "ok": rep["ok"]}, sort_keys=True))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
