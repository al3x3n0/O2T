#!/usr/bin/env python3
"""Prove memory-transform soundness (DSE / store-load forwarding) over an SMT array.

The registry's `memory-bv32` intents are abstract stand-ins (before == after over a
scalar) -- they never model the store sequence or aliasing that a dead-store or
load-forwarding transform actually depends on. This proves the REAL obligations on
the formal IR's memory ops (memvar -> Array, mem_store -> store, mem_load -> select)
with Z3's array theory (QF_AUFBV):

  * dead store elimination is sound IFF the later store is to the SAME address
    (must-alias); to a different address the earlier store is live -> UNSOUND;
  * store-to-load forwarding: load(store(M,p,a), p) == a;
  * a store to p leaves every non-aliasing address q (p != q) unchanged;
  * a fully-overwritten value is unobservable.

Each case lowers its before/after through typed_expr_to_smt (the SAME lowering the
proof pipeline uses), then asks Z3 whether the two can differ. UNSAT == sound;
a deliberately-unsound case expects SAT (a concrete aliasing counterexample).
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
from cv_formal_ir import FormalContext, typed_expr_to_smt  # noqa: E402

M = {"op": "memvar", "name": "M"}


def v(name):
    return {"op": "var", "name": name}


def load(mem, addr):
    return {"op": "mem_load", "args": [mem, addr]}


def store(mem, addr, val):
    return {"op": "mem_store", "args": [mem, addr, val]}


# (label, before, after, assumptions, expected). expected is the Z3 verdict on
# "can before and after differ": unsat == sound, sat == unsound (counterexample).
CASES = [
    ("dse-dead-store-same-addr-sound",
     store(store(M, v("p"), v("a")), v("p"), v("b")), store(M, v("p"), v("b")),
     [], "unsat"),
    ("dse-dead-store-diff-addr-unsound",
     store(store(M, v("p"), v("a")), v("q"), v("b")), store(M, v("q"), v("b")),
     [], "sat"),
    ("store-load-forward-sound",
     load(store(M, v("p"), v("a")), v("p")), v("a"),
     [], "unsat"),
    ("load-noalias-preserved-sound",
     load(store(M, v("p"), v("a")), v("q")), load(M, v("q")),
     ["(not (= p q))"], "unsat"),
    ("overwritten-store-fully-killed-sound",
     load(store(store(M, v("p"), v("a")), v("p"), v("b")), v("p")), v("b"),
     [], "unsat"),
    ("store-forward-wrong-value-unsound",
     load(store(M, v("p"), v("a")), v("p")), v("b"),
     [], "sat"),
]

BV_VARS = ["p", "q", "a", "b", "c"]


def collect_vars(node, out):
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["name"])
        for arg in node.get("args", []) or []:
            collect_vars(arg, out)


def build_smt(before, after, assumptions):
    used = set()
    collect_vars(before, used)
    collect_vars(after, used)
    context = FormalContext(set(), variable_bits={name: 32 for name in BV_VARS})
    variables = set(BV_VARS) | {"M"}
    btyped = typed_expr_to_smt(before, variables, context)
    atyped = typed_expr_to_smt(after, variables, context)
    if btyped.sort != atyped.sort:
        raise ValueError(f"sort mismatch {btyped.sort} vs {atyped.sort}")
    decls = ["(declare-const M (Array (_ BitVec 32) (_ BitVec 32)))"]
    decls += [f"(declare-const {name} (_ BitVec 32))" for name in sorted(used)]
    lines = ["(set-logic QF_AUFBV)", *decls]
    lines += [f"(assert {a})" for a in assumptions]
    lines.append(f"(assert (not (= {btyped.smt} {atyped.smt})))")
    lines.append("(check-sat)")
    return "\n".join(lines) + "\n"


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
    for label, before, after, assumptions, expected in CASES:
        smt = build_smt(before, after, assumptions)
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
