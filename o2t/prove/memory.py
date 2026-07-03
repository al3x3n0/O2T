#!/usr/bin/env python3
"""Prove loop transforms whose delta is a MEMORY load (no polynomial closed form).

`acc += p[i]` has no closed form -- it depends on the array. But a transform can
still be proved RELATIONALLY: model each load as an UNINTERPRETED function of its
address (deterministic -- the same address yields the same value, with no intervening
write), and prove the before/after accumulators stay equal by CONGRUENCE, without
knowing the loaded values. Discharged over Z with uninterpreted functions (Z3 UF).

  * LICM of a load: `for i: acc += *q`  ==  `t = *q; for i: acc += t`  (both add the
    same invariant value LQ);
  * GVN / redundant-load: `acc += p[i] + p[i]`  ==  `x = p[i]; acc += x + x`  (two
    loads of one address equal one load reused: ld(i) + ld(i) == 2*ld(i));
  * reading a DIFFERENT array (ld vs lq) is NOT equivalent -- refuted.

For a lockstep relation acc_A == acc_B, the step reduces to delta_A == delta_B with
the loads modeled as the SAME uninterpreted function across both loops (same memory).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def v(name):
    return {"op": "var", "name": name}


def add(*a):
    acc = a[0]
    for x in a[1:]:
        acc = {"op": "bvadd", "args": [acc, x]}
    return acc


def mul(a, b):
    return {"op": "bvmul", "args": [a, b]}


def ld(name, *args):
    """An uninterpreted load: name(args). 0-ary => a loop-invariant value."""
    return {"op": "opaque", "name": name, "args": list(args)}


INT_OP = {"bvadd": "+", "bvsub": "-", "bvmul": "*"}


def lower(expr):
    o = expr["op"]
    if o == "var":
        return expr["name"]
    if o == "bvconst":
        return str(expr["value"])
    if o == "opaque":
        return expr["name"] if not expr["args"] else "(" + expr["name"] + " " + " ".join(lower(a) for a in expr["args"]) + ")"
    return "(" + INT_OP[o] + " " + " ".join(lower(a) for a in expr["args"]) + ")"


def opaques(expr, out):
    if isinstance(expr, dict):
        if expr.get("op") == "opaque":
            out.add((expr["name"], len(expr["args"])))
        for a in expr.get("args", []) or []:
            opaques(a, out)


def valid(z3_bin, variables, before, after):
    """before == after valid over Z with uninterpreted loads (Z3 unsat of negation)."""
    funcs = set()
    opaques(before, funcs)
    opaques(after, funcs)
    decls = [f"(declare-const {name} Int)" for name in variables]
    for name, arity in sorted(funcs):
        decls.append(f"(declare-const {name} Int)" if arity == 0
                     else f"(declare-fun {name} ({' '.join(['Int'] * arity)}) Int)")
    smt = "\n".join(["(set-logic ALL)", *decls,
                     f"(assert (not (= {lower(before)} {lower(after)})))",
                     "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return bool(out) and out.splitlines()[0].strip() == "unsat"


ACC, I = v("ACC"), v("i")
ZERO = {"op": "bvconst", "value": 0}

# (label, init_A, delta_A, init_B, delta_B, expected). With acc_A==acc_B unified as ACC,
# BASE = (init_A == init_B), STEP = (ACC + delta_A == ACC + delta_B).
CASES = [
    ("licm-load-sound", ZERO, ld("LQ"), ZERO, ld("LQ"), True),
    ("gvn-redundant-load-sound", ZERO, add(ld("ld", I), ld("ld", I)), ZERO,
     mul({"op": "bvconst", "value": 2}, ld("ld", I)), True),
    ("identity-load-sound", ZERO, ld("ld", I), ZERO, ld("ld", I), True),
    ("different-array-unsound", ZERO, ld("ld", I), ZERO, ld("lq", I), False),
]


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

    variables = ["ACC", "i"]
    results = []
    for label, ia, da, ib, db, expect in CASES:
        base = valid(z3_bin, variables, ia, ib)
        step = valid(z3_bin, variables, add(ACC, da), add(ACC, db))
        proved = base and step
        results.append({"case": label, "expected_proved": expect,
                        "base": base, "step": step, "proved": proved,
                        "ok": proved == expect})

    failed = [r for r in results if not r["ok"]]
    report = {"cases": len(results), "failed": len(failed), "results": results, "ok": not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(results), "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['case']}: proved={r['proved']} (expect {r['expected_proved']})",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
