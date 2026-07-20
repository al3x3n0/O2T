#!/usr/bin/env python3
"""E3: the performance experiment -- measure the claims the paper makes about speed.

Three measurements, each gating a specific statement:
1. INTEGER-RING vs BV32 on the nonlinear Faulhaber STEP implication (the draft's headline: the
   integer discharge returns in milliseconds where bit-blasted nonlinear bv32 does not return) --
   the bv32 side runs under a hard timeout and its non-return is a RESULT, reported as
   `>= cap`, never silently truncated.
2. BATCH vs PER-CANDIDATE synthesis discharge: the same candidate set through one push/pop z3
   process (`poly.batch_check`) vs one process per candidate (`poly.valid`) -- the engineering
   claim behind interactive synthesis.
3. PER-OBLIGATION prove times over representative recovered fold families (identity, guarded,
   builder DFG, relational-precondition) -- the "cheap enough for a validation workflow" claim.

Wall-clock numbers are machine-specific: the FIXTURE asserts only robust facts (the integer side
succeeds well under the cap; bv32 exceeds it or is an order slower; batch does not lose to
per-candidate; obligations stay sub-second-ish with a generous bound), while the measured table
lands in docs/e3-timing.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from o2t import mini_alive as ma
from o2t.intent import pass_graph as pg
from o2t.synth import poly

# The Faulhaber STEP for acc += i*i with closed form 6*acc == 2i^3 - 3i^2 + i:
#   6*acc == 2i^3 - 3i^2 + i   ==>   6*(acc + i^2) == 2(i+1)^3 - 3(i+1)^2 + (i+1)
# -- a nonlinear implication; over Int it is a ring identity, over bv32 it forces bit-blasting.
_INT_SMT = """(set-logic ALL)
(declare-const acc Int) (declare-const i Int)
(assert (= (* 6 acc) (+ (- (* 2 i i i) (* 3 i i)) i)))
(assert (not (= (* 6 (+ acc (* i i)))
                (+ (- (* 2 (+ i 1) (+ i 1) (+ i 1)) (* 3 (+ i 1) (+ i 1))) (+ i 1)))))
(check-sat)
"""

_BV_SMT = """(set-logic QF_BV)
(declare-const acc (_ BitVec 32)) (declare-const i (_ BitVec 32))
(define-fun six () (_ BitVec 32) #x00000006)
(define-fun one () (_ BitVec 32) #x00000001)
(define-fun i1 () (_ BitVec 32) (bvadd i one))
(assert (= (bvmul six acc)
           (bvadd (bvsub (bvmul #x00000002 (bvmul i (bvmul i i))) (bvmul #x00000003 (bvmul i i))) i)))
(assert (not (= (bvmul six (bvadd acc (bvmul i i)))
                (bvadd (bvsub (bvmul #x00000002 (bvmul i1 (bvmul i1 i1)))
                              (bvmul #x00000003 (bvmul i1 i1))) i1))))
(check-sat)
"""

# Representative recovered folds for the per-obligation timing (the peephole side).
FOLD_FAMILIES = [
    ("nested-identity", "match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))",
     "return replaceInstUsesWith(I, X);"),
    ("guarded-sdiv-udiv", "match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) && "
     "isKnownNonNegative(Y)", "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"),
    ("builder-dfg", "match(&I, m_Sub(m_Value(A), m_Value(B)))",
     "return replaceInstUsesWith(I, Builder.CreateSub(A, B));"),
    ("relational-disjoint", "match(&I, m_Add(m_Value(X), m_Value(Y))) && haveNoCommonBitsSet(X, Y)",
     "return replaceInstUsesWith(I, Builder.CreateOr(X, Y));"),
]


def _run_z3(z3: str, smt: str, timeout_s: float) -> tuple[str, float]:
    """(head, seconds); head is 'timeout' when the cap is hit -- reported, never hidden."""
    t0 = time.monotonic()
    try:
        out = subprocess.run([z3, "-in"], input=smt, capture_output=True, text=True,
                             timeout=timeout_s)
        head = (out.stdout.strip().splitlines() or ["error"])[0]
    except subprocess.TimeoutExpired:
        return "timeout", time.monotonic() - t0
    return head, time.monotonic() - t0


def measure_ring_vs_bv(z3: str, cap_s: float = 10.0) -> dict:
    int_head, int_s = _run_z3(z3, _INT_SMT, cap_s)
    bv_head, bv_s = _run_z3(z3, _BV_SMT, cap_s)
    return {"integer": {"verdict": int_head, "seconds": round(int_s, 4)},
            "bv32": {"verdict": bv_head, "seconds": round(bv_s, 4)},
            "cap_seconds": cap_s}


def _candidates(n: int):
    """A synthesis-shaped candidate set: n coefficient guesses for the ring identity
    (i+1)^2 == i^2 + k*i + 1 -- exactly one (k = 2) is a theorem, the rest refute; the workload
    batch_check exists for."""
    def c(v):
        return {"op": "bvconst", "bits": 32, "value": v}

    def var(name):
        return {"op": "var", "name": name}

    def mul(a, b):
        return {"op": "bvmul", "args": [a, b]}

    def add(a, b):
        return {"op": "bvadd", "args": [a, b]}
    i = var("i")
    i1 = add(i, c(1))
    before = mul(i1, i1)                              # (i+1)^2
    return [([], before, add(add(mul(i, i), mul(c(k), i)), c(1)))   # i^2 + k*i + 1
            for k in range(n)]                        # only k == 2 is valid


def measure_batch_vs_percand(z3: str, n: int = 24) -> dict:
    queries = _candidates(n)
    t0 = time.monotonic()
    per = [poly.valid(z3, ["acc", "i"], a, b, c) for a, b, c in queries]
    per_s = time.monotonic() - t0
    t0 = time.monotonic()
    batch = poly.batch_check(z3, ["acc", "i"], queries)
    batch_s = time.monotonic() - t0
    assert per == batch, "the two discharge paths must agree candidate-by-candidate"
    return {"candidates": n, "valid_found": sum(batch),
            "per_candidate_seconds": round(per_s, 4), "batch_seconds": round(batch_s, 4),
            "speedup": round(per_s / batch_s, 2) if batch_s > 0 else None}


def measure_obligations(z3: str) -> dict:
    out = {}
    for name, pred, rw in FOLD_FAMILIES:
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, name
        t0 = time.monotonic()
        status, _ = ma.prove(pair, z3)
        out[name] = {"verdict": status, "seconds": round(time.monotonic() - t0, 4)}
    return out


def run(z3: str, cap_s: float = 10.0) -> dict:
    return {"ring_vs_bv32": measure_ring_vs_bv(z3, cap_s),
            "batch_vs_per_candidate": measure_batch_vs_percand(z3),
            "per_obligation": measure_obligations(z3)}


def render(r: dict) -> str:
    rb = r["ring_vs_bv32"]
    bp = r["batch_vs_per_candidate"]
    lines = ["== E3: performance (measured on this machine) ==",
             f"nonlinear Faulhaber STEP: Int {rb['integer']['verdict']} in "
             f"{rb['integer']['seconds']}s | bv32 {rb['bv32']['verdict']} at "
             f"{rb['bv32']['seconds']}s (cap {rb['cap_seconds']}s)",
             f"synthesis discharge ({bp['candidates']} candidates): per-candidate "
             f"{bp['per_candidate_seconds']}s vs batch {bp['batch_seconds']}s "
             f"(speedup {bp['speedup']}x)",
             "per-obligation prove times:"]
    for name, v in r["per_obligation"].items():
        lines.append(f"  {name:22s} {v['verdict']:8s} {v['seconds']}s")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    import shutil
    ap = argparse.ArgumentParser(description="E3: measure the paper's performance claims")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--bv-cap-seconds", type=float, default=10.0)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print("cv-prove-timing: z3 required", file=sys.stderr)
        return 2
    r = run(z3, args.bv_cap_seconds)
    if args.report:
        args.report.write_text(json.dumps(r, indent=2) + "\n")
    print(render(r), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
