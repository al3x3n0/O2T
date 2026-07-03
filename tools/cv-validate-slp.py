#!/usr/bin/env python3
"""Deep SLP/(G)SLP vectorization verification: lane-mapping + reduction associativity.

Proves each canonical SLP contract: a consistent pack/extract lane mapping is value-equivalent
to the scalars (a mismatched one is REFUTED), and an integer reduction equals its vector (tree)
reduce (associative) while a FLOATING-POINT reduction without fast-math is REFUTED (the tree
reassociation changes the result). Needs z3 (with FP theory) only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import slp_model as slp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = []
    for cid, op, n, pack, ext, expect in slp.PACK_CONTRACTS:
        status, _ = slp.prove_pack_binop(z3, op, n, pack, ext)
        results.append({"contract": cid, "kind": "pack", "status": status,
                        "expected": expect, "as_expected": status == expect})
    for cid, op, n, fp, expect in slp.REDUCTION_CONTRACTS:
        status, _ = slp.prove_reduction(z3, op, n, fp)
        results.append({"contract": cid, "kind": "reduction", "status": status,
                        "expected": expect, "as_expected": status == expect})

    ok = all(r["as_expected"] for r in results)
    report = {"results": results, "ok": ok,
              "proved": sum(r["status"] == "proved" for r in results),
              "refuted": sum(r["status"] == "refuted" for r in results)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"contracts": len(results), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']:8}] {r['contract']} ({r['kind']}; expected {r['expected']})",
              file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
