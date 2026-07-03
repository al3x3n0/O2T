#!/usr/bin/env python3
"""Prove poison-generating instruction flags (nsw/nuw/exact) under refinement.

LLVM instruction flags make a result poison when their no-overflow / exactness
precondition is violated. That single fact drives two soundness rules:

  * dropping a flag is a sound REFINEMENT (target is defined on more inputs);
  * adding a flag is UNSOUND (target is poison where the source was a real value);
  * a value rewrite that is correct only because of a flag is provable WITH the
    flag and refutable WITHOUT it.

Each case lowers `before`/`after` through the real formal IR (so flag-poison flows
into the existing Alive2-style refinement check) and asks Z3. A refinement proof
expects UNSAT (no counterexample); a deliberately-unsound case expects SAT.
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


def var(name):
    return {"op": "var", "name": name}


def binop(op, a, b, flags=None):
    node = {"op": op, "args": [a, b]}
    if flags:
        node["flags"] = flags
    return node


# (label, before, after, refinement, variables, expected) -- expected is the Z3
# verdict on the refinement query: "unsat" == sound, "sat" == unsound (cex found).
CASES = [
    # Dropping nsw is a sound refinement: source poison on overflow, target always
    # defined and equal where source is defined.
    ("drop-nsw-add-sound",
     binop("bvadd", var("x"), var("y"), ["nsw"]), binop("bvadd", var("x"), var("y")),
     "refinement", ["x", "y"], "unsat"),
    # Adding nsw is unsound: target becomes poison on overflow where source was a
    # genuine wrapped value.
    ("add-nsw-unsound",
     binop("bvadd", var("x"), var("y")), binop("bvadd", var("x"), var("y"), ["nsw"]),
     "refinement", ["x", "y"], "sat"),
    # Dropping nuw on mul: sound refinement.
    ("drop-nuw-mul-sound",
     binop("bvmul", var("x"), var("y"), ["nuw"]), binop("bvmul", var("x"), var("y")),
     "refinement", ["x", "y"], "unsat"),
    # Flag-justified rewrite: (x <<nuw k) >>l k == x. Sound ONLY because nuw says no
    # high bits were shifted out.
    ("shl-nuw-lshr-roundtrip-sound",
     binop("bvlshr", binop("bvshl", var("x"), var("k"), ["nuw"]), var("k")), var("x"),
     "refinement", ["x", "k"], "unsat"),
    # Same rewrite WITHOUT nuw is unsound: high bits are lost, so the value differs.
    ("shl-lshr-roundtrip-unsound",
     binop("bvlshr", binop("bvshl", var("x"), var("k")), var("k")), var("x"),
     "refinement", ["x", "k"], "sat"),
    # exact on lshr: (x >>exact 1) <<1 == x is sound only when the dropped bit is 0.
    ("ashr-exact-drop-sound",
     binop("bvashr", var("x"), var("k"), ["exact"]), binop("bvashr", var("x"), var("k")),
     "refinement", ["x", "k"], "unsat"),
]


def formal_for(before, after, refinement, variables):
    return {"domain": "scalar-bv32", "equivalence": "result", "variables": variables,
            "poison_variables": [], "refinement": refinement, "before": before, "after": after}


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
    for label, before, after, refinement, variables, expected in CASES:
        pair = pair_for_formal(formal_for(before, after, refinement, variables))
        smt = equivalence_smt(label, "flag-selftest", pair)
        verdict = run_z3(z3_bin, smt)
        results.append({"case": label, "expected": expected, "verdict": verdict,
                        "ok": verdict == expected})

    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results,
              "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]},
                     sort_keys=True))
    for r in results:
        mark = "ok" if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['case']}: expected {r['expected']}, got {r['verdict']}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
