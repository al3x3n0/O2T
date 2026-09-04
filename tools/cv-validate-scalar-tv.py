#!/usr/bin/env python3
"""Closed-loop translation validation for ANY value-preserving scalar pass.

Generalizes the InstCombine validator: runs `opt -passes=<PASS>` on a `.ll`, translates the before
and after of each single-BB integer function to an SMT term for its returned value, and proves
them equal for all inputs (QF_BV). Works for instcombine, reassociate, early-cse, gvn, instsimplify
and any other pass that preserves scalar function semantics. A function using an unmodeled
instruction is soundly declined (`unsupported`). A wrong transform is refuted with a witness.
Needs z3 and opt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "scalar_tv_cases.ll"


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--passes", default="reassociate", help="opt pass pipeline to validate")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--cross-check", action="store_true",
                    help="confirm every PROVED function against oracles that do not share O2T's "
                         "SMT encoding: lli execution, reference Alive2, and a second solver. A "
                         "disagreement is a possible FALSE PROOF.")
    ap.add_argument("--lli-bin")
    ap.add_argument("--alive-tv")
    ap.add_argument("--pass-plugin", help="built pass plugin (-load-pass-plugin): validates a pass "
                                          "O2T did not build")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = _resolve(args.z3_bin, "/opt/homebrew/bin/z3")
    opt = _resolve(args.opt_bin, "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print(json.dumps({"status": "skipped", "reason": "z3 or opt not found"}))
        return 0

    src = args.source.read_text()
    opt_text = scalar_ir.run_passes(src, args.passes, opt, args.pass_plugin)
    if opt_text is None:
        print(json.dumps({"status": "error", "reason": f"opt -passes={args.passes} failed"}))
        return 1

    # THE FULL TRACK B DISPATCHER, not the scalar translator alone. `validate_transform_ex` tries
    # scalar first and falls through to the theory-of-arrays memory model and the vector lane model
    # when the scalar one declines the SHAPE. Calling `scalar_ir.validate_transform` directly meant
    # a memory- or vector-heavy pass came back `unsupported` on most of its functions -- which is
    # precisely the kind of pass someone reaches for `--pass-plugin` to verify. The fallbacks are
    # each sound within their scope and decline out of it, so this widens reach without widening
    # what is claimed.
    from o2t.validate.corpus_tv import cross_check_file, validate_transform_ex
    results = [validate_transform_ex(z3, src, opt_text, fn)
               for fn in scalar_ir.function_names(src)]
    # INDEPENDENT CONFIRMATION, when asked for. Every verdict above rests on ONE solver and ONE
    # hand-written encoding; the oracles here share neither. This matters most for a pass O2T did
    # not build, where there is no corpus history to lean on -- and the same run that verifies the
    # pass can say whether anything outside O2T's own encoding contradicts it.
    disagreements, cross_checked, confirmed_by = [], None, []
    if args.cross_check:
        xc = cross_check_file(z3, src, opt, lli_bin=args.lli_bin, alive_bin=args.alive_tv,
                              passes=args.passes, pass_plugin=args.pass_plugin)
        disagreements = xc.get("disagreements", [])
        # HOW MANY PROOFS THE ORACLES ACTUALLY EXAMINED. `disagreements: 0` on its own says the same
        # thing whether every proof was confirmed or NO ORACLE RAN -- lli absent, alive-tv missing,
        # the plugin failing to load. That is the absence-of-evidence-as-evidence conflation this
        # project keeps finding elsewhere, and it would be worst here, where the number is read as
        # independent confirmation of a pass nobody has verified before.
        cross_checked = xc.get("independently_confirmed", 0)
        confirmed_by = xc.get("confirmed_by", [])
    proved = [r for r in results if r["status"] == "proved"]
    refuted = [r for r in results if r["status"] == "refuted"]
    unsupported = [r for r in results if r["status"] == "unsupported"]
    ok = not refuted and bool(proved)
    # HOW MANY FUNCTIONS THE PASS ACTUALLY CHANGED. A refinement proof over a function the pass left
    # untouched is trivially true -- identity refines identity -- and says nothing whatever about
    # the pass. Measured the hard way: a plugin pass with a planted `add x,x -> x` was reported
    # `proved` because O2T's default corpus contains no `add x,x` for it to break. Pass-level
    # vacuity, and the same shape as the per-proof vacuity Track B already guards.
    from o2t.validate.corpus_tv import _extract_define

    def _instructions(text):
        """A function's INSTRUCTIONS, with comments and blank lines dropped.

        Comparing the raw text counts `opt` stripping `; CHECK-...` lines as a transformation. Every
        real LLVM test file carries those, so on real IR this reported `changed: 207` of 207 for
        sroa, gvn AND early-cse alike -- none of which touched a single instruction. The vacuity
        guard would then never fire on the inputs it exists to protect, and a pass that does nothing
        would be certified. It only appeared to work because the toy corpus had no comments."""
        return [ln.split(";")[0].rstrip() for ln in (text or "").splitlines()
                if ln.split(";")[0].strip()]

    changed = sum(1 for fn in scalar_ir.function_names(src)
                  if _instructions(_extract_define(src, fn))
                  != _instructions(_extract_define(opt_text, fn)))
    report = {"passes": args.passes, "changed": changed,
              # SAME KEYS AS STDOUT. `_run_json` appends `--report` and parses the FILE, so a name
              # that differs between the two output paths reads back as None in the orchestrator --
              # which is exactly how `independently_confirmed` arrived empty while the tool was
              # printing it correctly on stdout.
              "disagreements": disagreements,
              "independently_confirmed": cross_checked,
              "confirmed_by": confirmed_by,
              "results": results, "proved": len(proved),
              "refuted": len(refuted), "unsupported": len(unsupported), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passes": args.passes, "changed": changed,
                      "disagreements": len(disagreements), "independently_confirmed": cross_checked,
                      "proved": len(proved), "refuted": len(refuted),
                      "unsupported": len(unsupported), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']:11}] {r['function']} {r.get('reason', '')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
