#!/usr/bin/env python3
"""Concrete differential validation: tie the (untrusted) lifted intents to reality.

A Z3 `unsat` proves the lifted before/after trees equivalent -- but the LIFTER is
untrusted, and a deductive proof cannot catch a mis-lift or an encoder bug that
makes both legs agree on a wrong query. This adds two NON-deductive checks:

  Leg 1 -- empirical equivalence: evaluate the lifted before/after on many random
    (plus edge) i32 inputs with an independent scalar evaluator. Sound intents must
    agree on EVERY input (a disagreement is a real lifter/encoder/proof bug that
    z3 missed); negative intents must DISAGREE on at least one (teeth: the check is
    not vacuous).

  Leg 2 -- real-opt ground truth: lower the lifted `before` to LLVM IR, run it
    through real `opt`, parse the optimized IR back, and confirm opt preserved the
    value on every input (opt is sound -> the lowered IR is faithful and opt
    agrees). Also reports how often opt's output actually MATCHES the lifted
    `after` (i.e. real opt performs this rewrite). Skips gracefully when opt is
    absent or its output is outside the parseable subset.

Needs `opt` for Leg 2 (set CV_LLVM_BIN, default /opt/homebrew/opt/llvm@18/bin);
Leg 1 is self-contained.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent




sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t import mini_alive as ma

CORPORA = [
    ("optimization_intents", ROOT / "constraints" / "optimization_intents.json", True),
    ("extended_identities", ROOT / "constraints" / "extended_identities.json", True),
    ("negative_intents", ROOT / "constraints" / "negative_intents.json", False),
]
WIDTH = 32
MASK = (1 << WIDTH) - 1
EDGE = [0, 1, 2, 3, MASK, MASK - 1, 1 << (WIDTH - 1), (1 << (WIDTH - 1)) - 1, 7, 8, 16, 31, 32]
PASSES = "instcombine,instsimplify,reassociate,early-cse,sccp"


def default_opt():
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    cand = base / "opt"
    return str(cand) if cand.exists() else shutil.which("opt")


def sample_inputs(nvars, seed, n=200):
    rng = random.Random(seed)
    rows = []
    # edge combinations (cartesian over a small edge set, capped) + random fill
    from itertools import product, islice
    for combo in islice(product(EDGE, repeat=nvars), 256):
        rows.append(list(combo))
    for _ in range(n):
        rows.append([rng.randint(0, MASK) for _ in range(nvars)])
    return rows


def eval_all(tree, variables, rows):
    """Evaluate tree over every input row; return list of ints or None if any
    input hits an op the scalar evaluator does not model."""
    out = []
    for row in rows:
        env = dict(zip(variables, row))
        v = ma.evaluate(tree, env, WIDTH)
        if v is None:
            return None
        out.append(int(v) & MASK)
    return out


def run_opt(opt_bin, before_ll):
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "before.ll"
        src.write_text(before_ll)
        proc = subprocess.run([opt_bin, f"-passes={PASSES}", "-S", str(src), "-o", "-"],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        return proc.stdout


def leg2_opt(formal, variables, rows, before_vals, opt_bin):
    """Return ('preserved', matches_after) | ('skip', reason) | ('VIOLATION', detail)."""
    try:
        before_ll = ma.lower_pair_ll(formal, WIDTH)[0]
    except ma.Unsupported:
        return ("skip", "before not lowerable")
    opt_ll = run_opt(opt_bin, before_ll)
    if opt_ll is None:
        return ("skip", "opt failed")
    try:
        parsed = ma.parse_function(opt_ll)
    except Exception:  # noqa: BLE001 -- opt output outside the parseable subset
        return ("skip", "opt output unparseable")
    opt_vals = eval_all(parsed["result"], parsed["variables"], rows)
    if opt_vals is None:
        return ("skip", "opt output not evaluable")
    for i, (ov, bv) in enumerate(zip(opt_vals, before_vals)):
        if ov != bv:
            return ("VIOLATION", {"input": rows[i], "opt": ov, "before": bv})
    after_vals = eval_all(formal["after"], variables, rows)
    matches_after = after_vals is not None and opt_vals == after_vals
    return ("preserved", matches_after)


def differential(opt_bin, seed):
    results = []
    for corpus, path, sound in CORPORA:
        if not path.exists():
            continue
        for rec in json.loads(path.read_text()):
            formal = rec.get("formal") if isinstance(rec, dict) else None
            if not formal or formal.get("domain") != "scalar-bv32":
                continue
            if any(v != 32 for v in (formal.get("variable_bits") or {}).values()):
                continue
            marker = rec.get("marker") or rec.get("name") or "?"
            variables = formal["variables"]
            rows = sample_inputs(len(variables), seed)
            before_vals = eval_all(formal["before"], variables, rows)
            after_vals = eval_all(formal["after"], variables, rows)
            entry = {"corpus": corpus, "marker": marker, "sound": sound}
            if before_vals is None or after_vals is None:
                entry["leg1"] = "skip-unevaluable"
                results.append(entry)
                continue
            disagree = [i for i, (b, a) in enumerate(zip(before_vals, after_vals)) if b != a]
            if sound:
                entry["leg1"] = "ok" if not disagree else "EMPIRICAL-DISAGREE"
                if disagree:
                    entry["leg1_cex"] = {"input": rows[disagree[0]],
                                         "before": before_vals[disagree[0]],
                                         "after": after_vals[disagree[0]]}
            else:
                entry["leg1"] = "refuted" if disagree else "NOT-REFUTED"
            if opt_bin and sound:
                status, info = leg2_opt(formal, variables, rows, before_vals, opt_bin)
                entry["leg2"] = status
                if status == "preserved":
                    entry["opt_matches_after"] = bool(info)
                elif status == "VIOLATION":
                    entry["leg2_cex"] = info
                else:
                    entry["leg2_skip"] = info
            results.append(entry)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--seed", type=int, default=0xB)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    opt_bin = default_opt()
    results = differential(opt_bin, args.seed)

    leg1_disagree = [r for r in results if r.get("leg1") == "EMPIRICAL-DISAGREE"]
    not_refuted = [r for r in results if r.get("leg1") == "NOT-REFUTED"]
    leg2_violation = [r for r in results if r.get("leg2") == "VIOLATION"]
    leg2_preserved = [r for r in results if r.get("leg2") == "preserved"]
    opt_matches = [r for r in leg2_preserved if r.get("opt_matches_after")]
    ok = not leg1_disagree and not not_refuted and not leg2_violation

    report = {
        "opt_available": opt_bin is not None, "passes": PASSES,
        "intents": len(results),
        "leg1_empirical_disagreements": leg1_disagree,
        "negatives_not_refuted": not_refuted,
        "leg2_opt_preservation_violations": leg2_violation,
        "leg2_preserved": len(leg2_preserved),
        "leg2_opt_matches_after": len(opt_matches),
        "ok": ok, "results": results,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"intents": len(results), "opt_available": opt_bin is not None,
                      "leg1_disagreements": len(leg1_disagree),
                      "negatives_not_refuted": len(not_refuted),
                      "leg2_preserved": len(leg2_preserved),
                      "leg2_violations": len(leg2_violation),
                      "leg2_opt_matches_after": len(opt_matches), "ok": ok}, sort_keys=True))
    for r in leg1_disagree + not_refuted + leg2_violation:
        print(f"  PROBLEM {r['corpus']}/{r['marker']}: leg1={r.get('leg1')} leg2={r.get('leg2')}",
              file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
