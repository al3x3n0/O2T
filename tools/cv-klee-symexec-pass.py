#!/usr/bin/env python3
"""KLEE-driven symbolic execution of real fold C++/C control flow, with per-path refinement.

KLEE makes the analysis queries and the input opcode symbolic, forks on every feasible branch
(including `&&` short-circuits and input-shape dispatch), and writes one test per path; replaying
each reproduces the path, and the driver proves every rewriting path refines the input under the
facts its branches established. KLEE finds the feasible paths automatically -- no hand-enumeration.
Requires KLEE and its matching clang (LLVM 16); reported skipped if absent (the enumeration path in
`cv-symexec-real-pass` remains the fallback). Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.symexec import klee_driver as K  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness", type=Path, default=K.HARNESS)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0
    if not K.available():
        print(json.dumps({"status": "skipped", "reason": "klee or matching clang not found"}))
        return 0

    rep = K.run_klee(z3, args.harness)
    if rep.get("status") != "ok":
        print(json.dumps(rep, sort_keys=True))
        return 1 if rep.get("status") == "error" else 0
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"paths": rep["paths"], "rewriting_paths": rep["rewriting_paths"],
                      "proved": rep["proved"], "refuted": rep["refuted"], "ok": rep["ok"]},
                     sort_keys=True))
    for r in rep["rows"]:
        tag = "REWRITE" if r["rewrote"] else "no-rw  "
        print(f"  op{r['opcode']} {tag} [{r['status']:10}] decisions={r['decisions']}", file=sys.stderr)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
