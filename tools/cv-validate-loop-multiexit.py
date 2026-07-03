#!/usr/bin/env python3
"""UNBOUNDED equivalence for MULTI-EXIT loops (header guard + in-body breaks).

Models a loop with several exit edges as an ordered list of exits `(fire, result)` plus the
continue-step, and proves two such loops equivalent for ALL trip counts by induction (init,
per-exit decision, per-exit result, step). This validates the bundled multi-exit contracts: the
loop is proved equivalent to itself, and three corruptions -- a flipped exit condition, a swapped
exit value, and a changed body step -- are each refuted at the corresponding obligation. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_multiexit as M  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "loop_multiexit_cases.ll"


def _contracts(z3, src):
    cond = src.replace("%brk = icmp sgt i32 %acc, %lim", "%brk = icmp slt i32 %acc, %lim")
    res = src.replace("exitB:\n  ret i32 %acc", "exitB:\n  ret i32 %i")
    step = src.replace("%acc.n = add i32 %acc, %i", "%acc.n = add i32 %acc, 1")
    return [
        ("multiexit-identity", M.validate_multiexit(z3, src, "search", src, "search"), "proved"),
        ("teeth-exit-condition", M.validate_multiexit(z3, src, "search", cond, "search"), "refuted"),
        ("teeth-exit-result", M.validate_multiexit(z3, src, "search", res, "search"), "refuted"),
        ("teeth-body-step", M.validate_multiexit(z3, src, "search", step, "search"), "refuted"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    src = args.source.read_text()
    results = [{"contract": n, "status": r["status"], "expect": e,
                "ok": r["status"] == e, "failed": r.get("failed")}
               for n, r, e in _contracts(z3, src)]
    proved = [r for r in results if r["status"] == "proved"]
    ok = all(r["ok"] for r in results)
    report = {"results": results, "proved": len(proved),
              "refuted": sum(1 for r in results if r["status"] == "refuted"), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"contracts": len(results), "proved": len(proved),
                      "refuted": report["refuted"], "ok": ok}, sort_keys=True))
    for r in results:
        mark = "ok " if r["ok"] else "!! "
        print(f"  {mark}[{r['status']:8}] {r['contract']} {r.get('failed') or ''}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
