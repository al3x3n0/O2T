#!/usr/bin/env python3
"""Module-level TV: verify a whole-module `opt` pass, including function deletion.

Run `--pass` (e.g. globaldce) on a module and verify the whole-module transform: surviving functions
must refine and deleted functions must be provably dead (internal + unreferenced). Deleting an
external or still-referenced function is refuted. See o2t/validate/module_tv.py.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.module_tv import module_tv  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ll", type=Path)
    ap.add_argument("--pass", dest="passes", required=True, help="opt pass(es), e.g. globaldce")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)

    z3 = shutil.which(args.z3_bin)
    opt = tv._resolve_opt(args.opt_bin)
    if z3 is None or opt is None:
        print("cv-module-tv: z3 and opt(18) required", file=sys.stderr)
        return 2
    before = args.ll.read_text()
    after = si.run_passes(before, args.passes, opt)
    if after is None:
        print("cv-module-tv: opt failed", file=sys.stderr)
        return 1
    r = module_tv(z3, before, after)
    print(f"module: {r['module']}  (survivors {len(r['survivors'])}, deleted {r['deleted']}, "
          f"added {r['added']})")
    for s in r["steps"]:
        print(f"  [{s['kind']}] {s['function']}: {s['status']}", file=sys.stderr)
    if args.report:
        args.report.write_text(json.dumps(r, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
