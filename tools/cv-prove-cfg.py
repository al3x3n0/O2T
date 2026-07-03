#!/usr/bin/env python3
"""Prove control-flow transforms (SimplifyCFG family) over a real branch model.

The registry's cfg-bv32 intents are abstract stand-ins (before == after). Here the
control flow is modeled for real: a branch is an `ite` over its condition, a phi is
the `ite` that merges the arms, reachability is the nesting, and a SimplifyCFG
transform is sound iff it preserves the observable (reachable) result.

Proved / refuted with Z3:
  * identical-arm fold: `if (c) r=a else r=a` -> `r=a`;
  * branch folding on a tautological guard: `if (a==a) A else B` -> A;
  * nested-branch collapse: `if(c1){ if(c2) A else B } else B` -> `if(c1 && c2) A else B`
    (collapsing with `||` instead of `&&` is UNSOUND -- caught);
  * common-code sink: `if(c) a+x else a+y` -> `a + (c ? x : y)`.

A branch condition `c` is modeled as `c != 0`. UNSAT == sound; a broken transform
expects SAT.
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

ZERO = {"op": "bvconst", "bits": 32, "value": 0}


def v(name):
    return {"op": "var", "name": name}


def cond(name):
    """Branch taken iff the condition value is non-zero."""
    return {"op": "ne", "args": [v(name), ZERO]}


def ite(c, t, e):
    return {"op": "ite", "args": [c, t, e]}


def op(o, *args):
    return {"op": o, "args": list(args)}


A, B = v("a"), v("b")
X, Y = v("x"), v("y")

CASES = [
    # both arms identical -> branch removed
    ("identical-arms-fold-sound", ite(cond("c"), A, A), A, "unsat"),
    # tautological guard a==a is always true -> take the then arm
    ("branch-tautology-fold-sound", ite(op("eq", A, A), A, B), A, "unsat"),
    # the same fold with a non-tautological guard a==b is UNSOUND
    ("branch-nontautology-fold-unsound", ite(op("eq", A, B), A, B), A, "sat"),
    # nested-branch collapse: inner-else and outer-else share value B
    ("nested-branch-collapse-and-sound",
     ite(cond("c1"), ite(cond("c2"), A, B), B),
     ite(op("and", cond("c1"), cond("c2")), A, B), "unsat"),
    # collapsing with || instead of && is UNSOUND
    ("nested-branch-collapse-or-unsound",
     ite(cond("c1"), ite(cond("c2"), A, B), B),
     ite(op("or", cond("c1"), cond("c2")), A, B), "sat"),
    # common-code sink: factor the shared `a +` out of both arms
    ("common-code-sink-sound",
     ite(cond("c"), op("bvadd", A, X), op("bvadd", A, Y)),
     op("bvadd", A, ite(cond("c"), X, Y)), "unsat"),
]

VARS = ["a", "b", "c", "c1", "c2", "x", "y"]


def formal_for(before, after):
    return {"domain": "scalar-bv32", "equivalence": "result", "variables": VARS,
            "poison_variables": [], "refinement": "refinement", "before": before, "after": after}


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
        verdict = run_z3(z3_bin, equivalence_smt(label, "cfg", pair))
        results.append({"case": label, "expected": expected, "verdict": verdict,
                        "ok": verdict == expected})

    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['case']}: expected {r['expected']}, got {r['verdict']}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
