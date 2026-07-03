#!/usr/bin/env python3
"""Prove floating-point fast-math rewrites under refinement (nnan/ninf as poison).

Plain IEEE FP is not the reals: NaN and infinities break identities that integer
code takes for granted. LLVM's fast-math flags license those rewrites by declaring
the bad inputs out of scope -- modeled here, Alive2-style, as POISON: a `fadd nnan`
whose operand or result is NaN is poison, so a rewrite is only obliged to match
where the flagged form is well-defined.

  * x + (-x) -> 0.0 and x - x -> 0.0 are sound WITH nnan+ninf (poison on NaN/Inf),
    UNSOUND without them (x = +inf -> NaN, not 0.0);
  * x + 1.0 -> x stays unsound even with the flags (teeth: the prover is not
    vacuous -- flags excuse NaN/Inf, not arithmetic).

Each case lowers before/after through the real formal IR (QF_FP, Float32) and asks
Z3 the refinement query. UNSAT == sound; an unsound case expects SAT.

(nsz / reassoc / contract are NOT modeled -- they are value-nondeterminism /
inexactness, not poison, and need a separate slice.)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import equivalence_smt, pair_for_formal  # noqa: E402

X = {"op": "fpvar", "name": "x"}
ZERO = {"op": "fpconst", "value": "zero"}
ONE = {"op": "fpconst", "value": "one"}


def fpneg(a):
    return {"op": "fpneg", "args": [a]}


def fp(op, a, b, flags=None):
    node = {"op": op, "args": [a, b]}
    if flags:
        node["flags"] = flags
    return node


FM = ["nnan", "ninf"]
# (label, before, after, expected)
CASES = [
    ("fadd-x-negx-zero-fastmath-sound", fp("fpadd", X, fpneg(X), FM), ZERO, "unsat"),
    ("fadd-x-negx-zero-strict-unsound", fp("fpadd", X, fpneg(X)), ZERO, "sat"),
    ("fsub-x-x-zero-fastmath-sound", fp("fpsub", X, X, FM), ZERO, "unsat"),
    ("fsub-x-x-zero-strict-unsound", fp("fpsub", X, X), ZERO, "sat"),
    ("fadd-x-one-not-identity-even-with-flags", fp("fpadd", X, ONE, FM), X, "sat"),
]


def formal_for(before, after):
    return {"domain": "scalar-fp32", "equivalence": "result", "variables": ["x"],
            "poison_variables": [], "refinement": "refinement",
            "before": before, "after": after}


def run_z3(z3_bin, smt):
    proc = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
    return proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = []
    for label, before, after, expected in CASES:
        pair = pair_for_formal(formal_for(before, after))
        smt = equivalence_smt(label, "fastmath-selftest", pair)
        verdict = run_z3(z3_bin, smt)
        results.append({"case": label, "expected": expected, "verdict": verdict,
                        "ok": verdict == expected})

    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]},
                     sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['case']}: expected {r['expected']}, got {r['verdict']}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
