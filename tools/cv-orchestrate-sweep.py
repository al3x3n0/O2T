#!/usr/bin/env python3
"""Sweep a broad, multi-family LLVM pass-set through O2T's front door and print a coverage matrix.

Where `cv-orchestrate` routes one pass, this routes the whole curated set (one+ source per
modeled family -- sound, planted/under-guarded unsound, and a known-gap advisory), separates
each source's PRIMARY-family verdict (authoritative) from cross-family dispatches, and rolls up:
families exercised, deep verifiers dispatched, where the teeth fire, and where coverage is still
advisory. Exit 0 iff every case routes to its expected family and reaches its expected headline.

  cv-orchestrate-sweep.py                # run the built-in manifest, print the matrix
  cv-orchestrate-sweep.py --report out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from o2t.orchestrate.run import resolve_context  # noqa: E402
from o2t.orchestrate.sweep import run_sweep  # noqa: E402

_MARK = {"proved": "proved ", "refuted": "REFUTED", "advisory": "advisory"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--clang-bin", default="clang")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    ctx = resolve_context(args.z3_bin, args.opt_bin, args.clang_bin)
    if not ctx.get("z3"):
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    rep = run_sweep(ctx)
    s = rep["summary"]

    # Human-readable matrix to stderr.
    for r in rep["rows"]:
        flag = "ok " if (r["ok"] and r["family_ok"]) else "!! "
        prim = " ".join(f"{c['strategy']}={c['verdict']}" for c in r["primary"])
        print(f"{flag}[{_MARK.get(r['observed'], r['observed']):8}] "
              f"{r['primary_family'] or '?':22} {r['source']}", file=sys.stderr)
        print(f"      primary: {prim}", file=sys.stderr)
        print(f"      {r['note']}", file=sys.stderr)
    print(f"\nfamilies exercised : {', '.join(s['families_exercised'])}", file=sys.stderr)
    print(f"deep verifiers     : {', '.join(s['deep_verifiers_dispatched'])}", file=sys.stderr)
    print(f"teeth fired        : {', '.join(s['teeth_fired'])}", file=sys.stderr)
    print(f"advisory gaps      : {', '.join(s['advisory_gaps'])}", file=sys.stderr)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")

    print(json.dumps({"cases": s["cases"], "families": len(s["families_exercised"]),
                      "deep_verifiers": len(s["deep_verifiers_dispatched"]),
                      "teeth": len(s["teeth_fired"]), "gaps": len(s["advisory_gaps"]),
                      "all_ok": s["all_ok"]}, sort_keys=True))
    return 0 if s["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
