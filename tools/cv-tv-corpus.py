#!/usr/bin/env python3
"""Whole-function translation validation over a corpus of `.ll` files (Track B, end-to-end).

For every function in each input `.ll`, run the REAL `opt -passes=instcombine` and prove the WHOLE
function's transformation sound (Alive2-style refinement). Reports per-status counts -- the honest
end-to-end reach-vs-decline picture over real code. See o2t/validate/corpus_tv.py.
"""
import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate.corpus_tv import validate_file  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ll", type=Path, nargs="+", help="LLVM .ll file(s)")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--timeout", type=int, default=15, help="per-function z3 timeout (s)")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--show", choices=["refuted", "unsupported", "timeout", "error", "all"],
                    help="also list function names in this status")
    args = ap.parse_args(argv)

    z3 = shutil.which(args.z3_bin)
    opt = tv._resolve_opt(args.opt_bin)
    if z3 is None or opt is None:
        print("cv-tv-corpus: z3 and opt(18) required", file=sys.stderr)
        return 2
    total = Counter()
    files = []
    for path in args.ll:
        r = validate_file(z3, path.read_text(), opt, timeout=args.timeout)
        total.update(r["counts"])
        files.append({"file": str(path), "counts": r["counts"],
                      "functions": r["functions"] if args.show else None})
        listed = [f["function"] for f in r["functions"]
                  if args.show and (args.show == "all" or f["status"] == args.show)]
        print(f"{path.name}: {dict(r['counts'])}" + (f"  {listed}" if listed else ""))
    n = sum(total.values())
    proved = total.get("proved", 0)
    summary = {"functions": n, "counts": dict(total),
               "proved_pct": (100 * proved // n) if n else 0, "refuted": total.get("refuted", 0)}
    print(f"AGGREGATE: proved {proved}/{n} ({summary['proved_pct']}%), refuted {summary['refuted']}")
    if args.report:
        args.report.write_text(json.dumps({"summary": summary, "files": files}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
