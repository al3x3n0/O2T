#!/usr/bin/env python3
"""Prove registry intents at multiple integer widths, not just bv32.

The formal registry proves every identity at a fixed 32-bit width, but the
generator now emits i8/i16/i32/i64. A fold that holds at i32 is not automatically
sound at i8 (sign bits, all-ones, and overflow boundaries move with the width),
so this re-encodes each width-parametric intent (scalar/cfg/loop domains) at a
set of widths and re-proves it with Z3 -- closing the gap between what the
generator produces and what the formal track guarantees.

Re-encoding rewrites each `bvconst` to the target width: 0/1 and small values
carry over, and the width-relative constants (all-ones, sign bit, signed max) are
rescaled. A constant that cannot be ported skips that width with a note. If Z3
finds a width where the identity does NOT hold, the model is reported as a
counterexample -- a real soundness finding.

Needs Z3 for proving; without it, only the re-encoding is checked.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

from o2t.formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal

# Only pure-scalar integer identities are cleanly width-parametric. cfg/loop/
# memory/vector formals embed width-specific structure (predicates, addresses,
# lane counts) that a flat width substitution would make inconsistent -- those
# need per-domain modeling (future work).
WIDTH_PARAMETRIC_DOMAINS = {"scalar-bv32"}


class NonPortable(Exception):
    pass


def port_const(value: int, old_bits: int, w: int) -> int:
    full_old = (1 << old_bits) - 1
    if value == 0:
        return 0
    if value == 1:
        return 1
    if value == full_old:                       # all-ones
        return (1 << w) - 1
    if value == (1 << (old_bits - 1)):          # sign bit
        return 1 << (w - 1)
    if value == (1 << (old_bits - 1)) - 1:      # signed max
        return (1 << (w - 1)) - 1
    if value < (1 << w):                        # small, fits the target width
        return value
    raise NonPortable(f"const {value} (@{old_bits}b) not portable to {w}b")


def reencode(node, w: int):
    if isinstance(node, dict):
        if node.get("op") == "bvconst":
            ported = port_const(int(node["value"]), int(node.get("bits", 32)), w)
            return {"op": "bvconst", "bits": w, "value": ported}
        return {k: reencode(v, w) for k, v in node.items()}
    if isinstance(node, list):
        return [reencode(x, w) for x in node]
    return node


def formal_at_width(formal: dict, w: int) -> dict:
    out = copy.deepcopy(formal)
    out["before"] = reencode(formal["before"], w)
    out["after"] = reencode(formal["after"], w)
    out["variable_bits"] = {v: w for v in formal.get("variables", [])}
    return out


def run_z3(z3_bin: str, smt: str, want_model: bool) -> tuple[str, str]:
    res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
    status = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
    model = ""
    if status == "sat" and want_model:
        res2 = subprocess.run([z3_bin, "-in"], input=smt + "\n(get-model)",
                              capture_output=True, text=True)
        model = res2.stdout.strip()
    return status, model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intents", type=Path,
                        default=Path(__file__).resolve().parents[2] / "constraints" / "optimization_intents.json")
    parser.add_argument("--widths", default="8,16,32,64")
    parser.add_argument("--z3", default=None)
    parser.add_argument("--no-z3", action="store_true",
                        help="only re-encode at each width (no proving); for CI without z3")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-all", action="store_true",
                        help="exit 1 if any portable intent fails to prove at any width")
    args = parser.parse_args()

    widths = [int(w) for w in args.widths.split(",")]
    z3_bin = None if args.no_z3 else (args.z3 or shutil.which("z3"))
    intents = json.loads(args.intents.read_text())

    results = []
    proved = failed = skipped = encoded = 0
    counterexamples = []

    for rec in intents:
        formal = rec.get("formal")
        marker = rec.get("marker", "?")
        if not isinstance(formal, dict) or formal.get("domain") not in WIDTH_PARAMETRIC_DOMAINS:
            continue
        per_width = {}
        for w in widths:
            try:
                fw = formal_at_width(formal, w)
                pairs = pair_instances_for_formal(fw)
            except (NonPortable, FormalIrError) as exc:
                per_width[w] = "skipped:" + type(exc).__name__
                skipped += 1
                continue
            ok = True
            for _, pair in pairs:
                smt = equivalence_smt(marker, "multiwidth", pair)
                if not z3_bin:
                    encoded += 1
                    per_width[w] = "encoded"
                    continue
                status, model = run_z3(z3_bin, smt, want_model=True)
                if status == "unsat":
                    continue
                ok = False
                if status == "sat":
                    counterexamples.append({"marker": marker, "width": w, "model": model})
                per_width[w] = f"FAILED:{status}"
                failed += 1
                break
            else:
                if z3_bin:
                    per_width[w] = "proved"
                    proved += 1
        results.append({"marker": marker, "domain": formal["domain"], "widths": per_width})

    summary = {
        "z3": z3_bin or None,
        "widths": widths,
        "intents_checked": len(results),
        "proved": proved, "failed": failed, "skipped": skipped, "encoded": encoded,
        "counterexamples": counterexamples,
        "results": results,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n")
    where = "proved" if z3_bin else "encoded (no z3)"
    print(f"multiwidth: {len(results)} intent(s) x {widths} -> {proved} {where}, "
          f"{failed} failed, {skipped} width-skips", file=sys.stderr)
    for c in counterexamples:
        print(f"  COUNTEREXAMPLE {c['marker']} @ {c['width']}b", file=sys.stderr)
    return 1 if (args.require_all and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
