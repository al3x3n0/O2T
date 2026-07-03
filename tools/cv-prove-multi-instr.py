#!/usr/bin/env python3
"""Prove multi-instruction (non-peephole) dataflow transforms: GVN/CSE/reassociate.

Peephole proofs rewrite a single instruction. These transforms span an instruction
DAG and rely on VALUE IDENTITY across instructions -- exactly what GVN/CSE and
Reassociate exploit. Each is lowered through the formal IR and proved (or refuted)
with Z3:

  * GVN commutative redundancy: t1=a+b and t2=b+a are the same value, so reusing
    t1 for t2 is sound (a-b vs b-a is NOT -- caught as a counterexample);
  * Reassociation: ((a+b)+c) == (a+(b+c)) for bitvectors;
  * Distribution / factoring: a*b + a*c == a*(b+c) (wrong factoring is refuted);
  * CSE doubling: (a*b)+(a*b) == (a*b)<<1.

UNSAT == sound; a deliberately-broken value-numbering case expects SAT.
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


def v(name):
    return {"op": "var", "name": name}


def op(o, *args):
    return {"op": o, "args": list(args)}


A, B, C = v("a"), v("b"), v("c")
ONE = {"op": "bvconst", "bits": 32, "value": 1}

# (label, before, after, expected)
CASES = [
    ("gvn-commutative-redundancy-sound",
     op("bvadd", op("bvadd", A, B), op("bvadd", B, A)),
     op("bvadd", op("bvadd", A, B), op("bvadd", A, B)), "unsat"),
    ("gvn-sub-noncommutative-unsound",
     op("bvadd", op("bvsub", A, B), op("bvsub", B, A)),
     op("bvadd", op("bvsub", A, B), op("bvsub", A, B)), "sat"),
    ("reassociate-add-chain-sound",
     op("bvadd", op("bvadd", A, B), C),
     op("bvadd", A, op("bvadd", B, C)), "unsat"),
    ("reassociate-mul-chain-sound",
     op("bvmul", op("bvmul", A, B), C),
     op("bvmul", A, op("bvmul", B, C)), "unsat"),
    ("distribute-mul-over-add-sound",
     op("bvadd", op("bvmul", A, B), op("bvmul", A, C)),
     op("bvmul", A, op("bvadd", B, C)), "unsat"),
    ("factor-wrong-sign-unsound",
     op("bvadd", op("bvmul", A, B), op("bvmul", A, C)),
     op("bvmul", A, op("bvsub", B, C)), "sat"),
    ("cse-double-to-shift-sound",
     op("bvadd", op("bvmul", A, B), op("bvmul", A, B)),
     op("bvshl", op("bvmul", A, B), ONE), "unsat"),
]


def formal_for(before, after, variables):
    return {"domain": "scalar-bv32", "equivalence": "result", "variables": variables,
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
        pair = pair_for_formal(formal_for(before, after, ["a", "b", "c"]))
        verdict = run_z3(z3_bin, equivalence_smt(label, "multi-instr", pair))
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
