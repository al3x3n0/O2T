#!/usr/bin/env python3
"""Whole-pass composition: verify a pass PIPELINE compositionally over a `.ll` file.

Run each `--stages` pass in sequence, translation-validate every step, and compose by refinement
transitivity: proved iff every step proves (f_n refines f_0), refuted if any step miscompiles
(localized to that pass), else inconclusive. See o2t/validate/compose_tv.py.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.compose_tv import compose_tv  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ll", type=Path)
    ap.add_argument("--stages", required=True, help="comma-separated opt passes, e.g. reassociate,instcombine")
    ap.add_argument("--function", help="function to verify (default: all)")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)

    z3 = shutil.which(args.z3_bin)
    opt = tv._resolve_opt(args.opt_bin)
    if z3 is None or opt is None:
        print("cv-compose-tv: z3 and opt(18) required", file=sys.stderr)
        return 2
    ll = args.ll.read_text()
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    funcs = [args.function] if args.function else si.function_names(ll)
    results = [compose_tv(z3, ll, fn, stages, opt) for fn in funcs]
    for r in results:
        print(f"{r['function']}: {r['composed']}  "
              + " -> ".join(f"{s['stage']}:{s['status']}" for s in r["steps"]))
    if args.report:
        args.report.write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
