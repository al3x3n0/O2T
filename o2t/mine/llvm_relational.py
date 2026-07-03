#!/usr/bin/env python3
"""Prove a loop TRANSFORM from REAL LLVM IR (before/after .ll) -- for all trip counts.

Combines the LLVM-IR loop parser (cv-mine-llvm-loop: PHI-recurrence extraction,
rotation-agnostic) with the two-loop relational prover (cv-mine-relational:
synthesize B's aux IVs, discover the output bijection). For each `<base>_before` /
`<base>_after` function pair it extracts both loops' multi-accumulator recurrences
from the actual IR and proves the outputs equal under a synthesized simulation
relation R(state_A, state_B).

SSA phi-loops are inherently PARALLEL (every `%x.next` reads the phi `%x`, the
iteration-start value), so the synth's parallel step semantics are exactly right --
no update-ordering subtlety. Discharged over Z (sound for every bitvector width).

  * strengthReduce: `acc += i*c`  vs  `acc += k; k += c`  ->  { k == c*i, acc == acc };
  * wrongStride: the running IV bumped by d != c  ->  no relation, refuted.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




from o2t.frontend import llvm_loop as llvm
from o2t.mine import relational as minerel

RET_RE = re.compile(r"\bret\s+\S+\s+(%[\w.]+)")


def llvm_loop_tuple(params, body):
    """Convert an LLVM-IR loop function to (accumulators, outputs, index) in the form
    cv-mine-relational.build_model expects: accumulators = [(name, init, delta)]."""
    res = llvm.recurrences(body)
    if res is None:
        return None
    iv, accs, defs = res
    acc_list = []
    for var, (init_tok, delta_tok) in accs.items():
        init = llvm.resolve(init_tok, defs, iv)
        delta = llvm.resolve(delta_tok, defs, iv)
        acc_list.append((llvm.sanitize(var), init, delta))
    ret = RET_RE.search(body)
    if ret is None:
        return None
    return acc_list, [llvm.sanitize(ret.group(1))], "i"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path,
                    default=ROOT / "tests" / "fixtures" / "llvm_loop_transforms.ll")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    funcs = llvm.split_functions(args.source.read_text())  # name -> (params, body)
    bases = sorted({n[:-len("_before")] for n in funcs if n.endswith("_before")})
    results = []
    for base in bases:
        if base + "_after" not in funcs:
            continue
        before = llvm_loop_tuple(*funcs[base + "_before"])
        after = llvm_loop_tuple(*funcs[base + "_after"])
        if before is None or after is None:
            results.append({"transform": base, "status": "no-recurrence"})
            continue
        consts = [llvm.sanitize(p.split()[-1]) for p in funcs[base + "_before"][0].split(",") if p.strip()]
        res = minerel.prove_mined(z3_bin, minerel.build_model(before, after, consts))
        results.append({"transform": base, "status": res["status"],
                        "pairing": res.get("pairing"), "relation": res.get("relation")})

    proved = [r for r in results if r["status"] == "proved"]
    definitive = {"proved", "output-not-preserved", "no-aux-invariant"}
    ok = bool(proved) and all(r["status"] in definitive for r in results)
    report = {"transforms": len(results), "proved": len(proved), "results": results, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"transforms": len(results), "proved": len(proved), "ok": ok}, sort_keys=True))
    for r in results:
        rel = " /\\ ".join(r["relation"]) if r.get("relation") else r["status"]
        print(f"  [{r['status']}] {r['transform']}: {rel}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
