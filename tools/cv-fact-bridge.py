#!/usr/bin/env python3
"""Analysis-fact -> SMT bridge: prove fact-GUARDED InstCombine folds, with teeth.

A large share of real InstCombine folds are legal only because a ValueTracking
analysis PROVED a fact about an operand first -- `isKnownToBeAPowerOfTwo(P)` makes
`urem X, P` collapse to `X & (P-1)`; `isKnownNonNegative(X)` makes `ashr X, c`
equal `lshr X, c`. The AST miner already RECOGNIZES these predicates; this is the
discharge that gives each one FORMAL SEMANTICS so the guarded fold is provable.

The bridge lives in `predicate_to_guard` (cv-extract-pass-model): a fact lowers to
a path-condition conjunct in the EXISTING bit-vector DSL -- the bit trick
`(X & (X-1)) == 0` *is* "X is a power of two", no new opcode, no axiom to trust.
This tool exercises that lowering end to end against the real symexec discharge:

  * WITH the fact-guard  -> the rewrite is proved equivalent for all inputs (SOUND).
  * DROP the fact-guard  -> the same rewrite is REFUTED with a concrete input that
    falsifies it (a non-power-of-two P, a negative X). Two-sided teeth: the fact is
    load-bearing, and removing it is caught.

Self-contained: builds one-branch symexec models and runs the unchanged
cv-symexec-pass discharge on each.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.intent.extract_pass_model import predicate_to_guard  # noqa: E402

SYMEXEC = Path(__file__).resolve().parent / "cv-symexec-pass.py"


def v(name):
    return {"op": "var", "name": name}


def bvc(x):
    return {"op": "bvconst", "bits": 32, "value": x}


# Each case is a real fact-guarded identity: the rewrite `output` equals
# `opcode(operands)` exactly when the analysis `fact` holds.
CASES = [
    {
        "name": "pow2: urem X,P -> X & (P-1)",
        "fact": "isKnownToBeAPowerOfTwo(P)",
        "opcode": "bvurem", "operands": ["X", "P"],
        "output": {"op": "bvand", "args": [v("X"), {"op": "bvsub", "args": [v("P"), bvc(1)]}]},
    },
    {
        "name": "nonneg: ashr X,S -> lshr X,S",
        "fact": "isKnownNonNegative(X)",
        "opcode": "bvashr", "operands": ["X", "S"],
        "output": {"op": "bvlshr", "args": [v("X"), v("S")]},
    },
    {
        "name": "nonneg: sdiv X,Y -> udiv X,Y (both operands nonneg)",
        "fact": "isKnownNonNegative(X) && isKnownNonNegative(Y) && isKnownNonZero(Y)",
        "opcode": "bvsdiv", "operands": ["X", "Y"],
        "output": {"op": "bvudiv", "args": [v("X"), v("Y")]},
    },
    {
        "name": "disjoint: add X,Y -> or X,Y (no common bits)",
        "fact": "haveNoCommonBitsSet(X, Y)",
        "opcode": "bvadd", "operands": ["X", "Y"],
        "output": {"op": "bvor", "args": [v("X"), v("Y")]},
    },
]


def model_for(case, guarded):
    """One-branch symexec model. `guarded` chooses the mined fact predicate vs the
    empty predicate (which lowers to an always-true guard -- the fold with its
    analysis precondition stripped)."""
    guard, _ = predicate_to_guard(case["fact"] if guarded else "")
    return {
        "function": case["name"],
        "opcode": case["opcode"],
        "operands": case["operands"],
        "branches": [{"name": "fold", "guard": guard, "output": case["output"]}],
    }


def discharge(model, z3_bin, py):
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rep = Path(tf.name)
    try:
        subprocess.run([py, str(SYMEXEC), "--model", "/dev/stdin", "--z3-bin", z3_bin,
                        "--report", str(rep)], input=json.dumps(model),
                       capture_output=True, text=True)
        data = json.loads(rep.read_text()) if rep.stat().st_size else {}
    finally:
        rep.unlink(missing_ok=True)
    path = (data.get("paths") or [{}])[0]
    return path.get("status"), path.get("counterexample")


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
    for case in CASES:
        with_status, _ = discharge(model_for(case, True), z3_bin, sys.executable)
        without_status, cex = discharge(model_for(case, False), z3_bin, sys.executable)
        ok = with_status == "sound" and without_status == "UNSOUND" and bool(cex)
        results.append({"case": case["name"], "fact": case["fact"],
                        "with_guard": with_status, "without_guard": without_status,
                        "counterexample": cex, "ok": ok})

    ok = bool(results) and all(r["ok"] for r in results)
    report = {"cases": len(results), "ok": ok, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "ok": ok}, sort_keys=True))
    for r in results:
        teeth = f"  refuted-without-guard cex={r['counterexample']}" if r["ok"] else "  *** GATE FAILED ***"
        print(f"  [{r['with_guard']} | drop->{r['without_guard']}] {r['case']}{teeth}",
              file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
