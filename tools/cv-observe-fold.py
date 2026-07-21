#!/usr/bin/env python3
"""Observe a recovered fold against the REAL optimizer: source-intent <-> actual-behavior.

Recovers a fold obligation from a C++ fold-function source file (regex path), then emits its `before`
as LLVM IR, runs the actual `opt -passes=instcombine`, and classifies the optimizer's output against
the recovered `after` (confirmed | not-fired | divergent | unsupported). See o2t/validate/observe.py.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.intent import pass_graph as pg  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate.observe import observe_fold  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="a C++ fold-function source file")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)

    z3 = shutil.which(args.z3_bin)
    opt = tv._resolve_opt(args.opt_bin)
    if z3 is None or opt is None:
        print("cv-observe-fold: z3 and opt(18) required", file=sys.stderr)
        return 2
    arms = pg.recover_folds_from_function(args.source.read_text())
    if not arms:
        print(json.dumps({"recovered": 0, "results": []}))
        return 0
    results = [{"arm": a.get("arm"), "case": a.get("case"), **observe_fold(a, z3, opt)} for a in arms]
    for r in results:
        r.pop("observed", None)                       # keep the summary compact
    out = {"recovered": len(arms), "results": results}
    if args.report:
        args.report.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
