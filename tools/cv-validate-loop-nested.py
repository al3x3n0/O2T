#!/usr/bin/env python3
"""UNBOUNDED equivalence for NESTED loops, compositionally (summarize the inner loop).

Proves two nested loops equivalent by (1) proving the inner loops define the same transition over
the enclosing-loop variables, then (2) abstracting the inner loop as one uninterpreted function
INNER and proving the outer loops equivalent (a QF_UFBV query). Validates the bundled nested
contracts: the loop is proved equivalent to itself and to an inner-body transform that preserves
the inner transition; an inconsistent inner change fails the inner check; an outer change fails the
outer check. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_nested as N  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "loop_nested_cases.ll"


def _contracts(z3, src):
    inner_eq = src.replace("%acc.i = add i32 %accn, %j",
                           "%t0 = add i32 %j, 0\n  %acc.i = add i32 %accn, %t0")
    inner_bad = src.replace("%acc.i = add i32 %accn, %j", "%acc.i = add i32 %accn, 1")
    outer_bad = src.replace("%i.n = add i32 %i, 1", "%i.n = add i32 %i, 2")
    return [
        ("nested-identity", N.validate_nested(z3, src, src, "nested"), "proved"),
        ("nested-inner-transform", N.validate_nested(z3, src, inner_eq, "nested"), "proved"),
        ("teeth-inner-body", N.validate_nested(z3, src, inner_bad, "nested"), "refuted"),
        ("teeth-outer-body", N.validate_nested(z3, src, outer_bad, "nested"), "refuted"),
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
