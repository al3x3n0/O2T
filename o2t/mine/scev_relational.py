#!/usr/bin/env python3
"""Prove a loop TRANSFORM from real .ll using the SCEV frontend (no regex IR parsing).

Same job as cv-mine-llvm-relational -- prove `<base>_before` == `<base>_after` for all trip
counts -- but the loop recurrences come from LLVM's ScalarEvolution (cv-mine-scev-loop)
instead of hand-rolled PHI/latch regex. Because SCEV is rotation/block-layout/temporary/GEP
invariant, this survives rotated multi-block loops with LCSSA exit phis that stress the
line-regex miners. The prover (cv-mine-relational.prove_mined) is unchanged: the frontend
swap is isolated from the proof layer.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




from o2t.frontend import scev_loop as scev
from o2t.mine import relational as minerel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path,
                    default=ROOT / "tests" / "fixtures" / "scev_rotated_loops.ll")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0
    if scev.find_opt(args.opt_bin) is None:
        print(json.dumps({"status": "skipped", "reason": "opt (LLVM) not found"}))
        return 0

    ll_text = args.source.read_text()
    funcs = scev.function_names(ll_text)
    bases = sorted({n[:-len("_before")] for n in funcs if n.endswith("_before")})
    results = []
    for base in bases:
        if base + "_after" not in funcs:
            continue
        before = scev.scev_loop_tuple(ll_text, base + "_before", args.opt_bin)
        after = scev.scev_loop_tuple(ll_text, base + "_after", args.opt_bin)
        if before is None or after is None:
            results.append({"transform": base, "status": "no-recurrence"})
            continue
        # consts = the function's params (the prover only uses those appearing in init/delta).
        sig = re.search(r"define\b[^@]*@" + re.escape(base) + r"_before\s*\(([^)]*)\)", ll_text)
        consts = [scev.sanitize(p.split()[-1]) for p in sig.group(1).split(",") if p.strip()]
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
