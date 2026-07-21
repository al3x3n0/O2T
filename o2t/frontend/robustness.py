#!/usr/bin/env python3
"""E4: frontend robustness -- the SCEV frontend recovers loops the line-regex frontend cannot.

O2T has two loop-recurrence frontends. The legacy REGEX frontend (`llvm_loop.recurrences`) chases
phi chains line by line; it works on the textbook single-block loop but breaks on the canonical
shape `clang -O1` actually emits -- rotated, multi-block, with the live-out an LCSSA phi rather
than the loop phi. The SCEV frontend (`scev_loop.scev_recurrences`) instead asks LLVM's OWN
scalar-evolution analysis, so block layout is irrelevant.

This measures the differential: over a rotated/LCSSA benchmark, the regex frontend should recover
NONE and SCEV ALL (strict domination on the real-world shape); over a simple-loop control, the
regex frontend recovers -- proving its rotated failures are a property of loop SHAPE, not a dead
parser. Recovery here means "extracts a loop recurrence"; a frontend that returns nothing / errors
`failed`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from o2t.frontend import llvm_loop, scev_loop

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
ROTATED = FIX / "scev_rotated_loops.ll"       # rotated + multi-block + LCSSA (clang -O1 shape)
SIMPLE = FIX / "llvm_loops.ll"                 # single-block textbook loops (the control)
_HOMEBREW_OPT = Path("/opt/homebrew/opt/llvm@18/bin/opt")


def _regex_recovers(body: str) -> bool:
    try:
        return bool(llvm_loop.recurrences(body))
    except Exception:
        return False


def _scev_recovers(text: str, name: str, opt: str) -> bool:
    try:
        return bool(scev_loop.scev_recurrences(text, name, opt_bin=opt))
    except Exception:
        return False


def _differential(ll_path: Path, opt: str) -> dict:
    text = ll_path.read_text()
    rows = {}
    for name, (_params, body) in llvm_loop.split_functions(text).items():
        rows[name] = {"regex": _regex_recovers(body),
                      "scev": _scev_recovers(text, name, opt)}
    regex_ok = sum(1 for v in rows.values() if v["regex"])
    scev_ok = sum(1 for v in rows.values() if v["scev"])
    return {"functions": rows, "count": len(rows), "regex_recovered": regex_ok,
            "scev_recovered": scev_ok}


def run(opt: str) -> dict:
    rotated = _differential(ROTATED, opt)
    simple = _differential(SIMPLE, opt)
    # The headline claim: on the rotated shape SCEV strictly dominates the regex frontend.
    scev_only = [n for n, v in rotated["functions"].items() if v["scev"] and not v["regex"]]
    return {"rotated": rotated, "simple": simple, "scev_only_on_rotated": scev_only,
            "strict_domination_on_rotated":
                rotated["regex_recovered"] == 0 and rotated["scev_recovered"] == rotated["count"],
            "regex_works_on_simple": simple["regex_recovered"] > 0}


def render(r: dict) -> str:
    def tbl(d):
        return (f"regex {d['regex_recovered']}/{d['count']}, "
                f"scev {d['scev_recovered']}/{d['count']}")
    lines = ["== E4: frontend robustness (SCEV vs line-regex) ==",
             f"rotated / multi-block / LCSSA loops: {tbl(r['rotated'])}"]
    for n, v in r["rotated"]["functions"].items():
        lines.append(f"    {n:22s} regex={'ok' if v['regex'] else 'FAIL':4s} "
                     f"scev={'ok' if v['scev'] else 'FAIL'}")
    lines.append(f"simple single-block control:         {tbl(r['simple'])}")
    lines.append(f"SCEV recovers where regex fails (rotated): {r['scev_only_on_rotated']}")
    lines.append(f"strict domination on rotated: {r['strict_domination_on_rotated']}   "
                 f"regex works on simple: {r['regex_works_on_simple']}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="E4: SCEV-vs-regex frontend robustness differential")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    opt = shutil.which(args.opt_bin) or (str(_HOMEBREW_OPT) if _HOMEBREW_OPT.exists() else None)
    if opt is None:
        print("cv-frontend-robustness: opt (18) required", file=sys.stderr)
        return 2
    r = run(opt)
    if args.report:
        args.report.write_text(json.dumps(r, indent=2) + "\n")
    print(render(r), end="")
    return 0 if r["strict_domination_on_rotated"] and r["regex_works_on_simple"] else 1


if __name__ == "__main__":
    sys.exit(main())
