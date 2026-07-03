#!/usr/bin/env python3
"""Differential SMT proving: cross-check the formal corpus under a second solver.

A Z3 `unsat` is only as trustworthy as Z3. This re-runs every formal proof query
under an INDEPENDENT solver (default bitwuzla) and requires the two to AGREE. The
point is to catch:

  * solver disagreement -- z3 says unsat, the other says sat (or vice versa): a
    solver bug or a query that means different things to the two -> hard failure;
  * inconclusive results -- the second solver returns unknown/errors on a query
    the pipeline relies on (surfaced, not silently ignored).

Corpora (each record's `formal` block is lowered through the SAME equivalence_smt
path the gate uses, so this validates the real queries, not a paraphrase):
  * optimization_intents.json + extended_identities.json -> expected UNSAT (sound);
  * negative_intents.json                                 -> expected SAT (unsound).

Degrades gracefully: if no second solver is on PATH (or via CV_SECOND_SOLVER), the
tool reports `skipped` rather than failing -- same contract as the KLEE/alive-tv
wrappers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402

CORPORA = [
    ("optimization_intents", ROOT / "constraints" / "optimization_intents.json", "unsat"),
    ("extended_identities", ROOT / "constraints" / "extended_identities.json", "unsat"),
    ("negative_intents", ROOT / "constraints" / "negative_intents.json", "sat"),
]


def solver_cmd(path: str) -> list[str]:
    # z3 needs -in to read SMT-LIB from stdin; bitwuzla/boolector/cvc5 read it directly.
    return [path, "-in"] if "z3" in Path(path).name else [path]


def run_solver(path: str, smt: str, timeout: float) -> str:
    try:
        proc = subprocess.run(solver_cmd(path), input=smt, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout"
    out = proc.stdout.strip() or proc.stderr.strip()
    if not out:
        return "error"
    head = out.splitlines()[0].strip().lower()
    return head if head in ("sat", "unsat", "unknown") else f"error:{head[:40]}"


def queries():
    """Yield (corpus, label, expected, smt) for every formal proof query."""
    for corpus, path, expected in CORPORA:
        if not path.exists():
            continue
        for rec in json.loads(path.read_text()):
            if not isinstance(rec, dict) or not rec.get("formal"):
                continue
            marker = rec.get("marker") or rec.get("name") or "?"
            try:
                pairs = pair_instances_for_formal(rec["formal"])
            except FormalIrError:
                continue
            for idx, pair in pairs:
                label = marker if idx is None else f"{marker}#{idx}"
                yield corpus, label, expected, equivalence_smt(marker, "cross-solver", pair)


def resolve_second(name: str) -> str | None:
    return shutil.which(os.environ.get("CV_SECOND_SOLVER", name))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--second-bin", default="bitwuzla")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    second_bin = resolve_second(args.second_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0
    if second_bin is None:
        print(json.dumps({"status": "skipped", "reason": "no second solver",
                          "looked_for": args.second_bin}))
        return 0

    agree = 0
    disagreements: list[dict] = []
    second_inconclusive: list[dict] = []
    z3_wrong: list[dict] = []
    total = 0
    for corpus, label, expected, smt in queries():
        total += 1
        z3_v = run_solver(z3_bin, smt, args.timeout)
        second_v = run_solver(second_bin, smt, args.timeout)
        decisive = {"sat", "unsat"}
        if z3_v in decisive and second_v in decisive:
            if z3_v == second_v:
                agree += 1
                if z3_v != expected:
                    z3_wrong.append({"corpus": corpus, "case": label,
                                     "expected": expected, "both": z3_v})
            else:
                disagreements.append({"corpus": corpus, "case": label,
                                      "z3": z3_v, "second": second_v, "expected": expected})
        elif z3_v in decisive:
            second_inconclusive.append({"corpus": corpus, "case": label,
                                        "z3": z3_v, "second": second_v})
        else:
            z3_wrong.append({"corpus": corpus, "case": label, "z3": z3_v, "second": second_v})

    ok = not disagreements and not z3_wrong
    report = {"second_solver": Path(second_bin).name, "queries": total, "agree": agree,
              "disagreements": disagreements, "second_inconclusive": second_inconclusive,
              "z3_wrong_or_inconclusive": z3_wrong, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"second_solver": report["second_solver"], "queries": total,
                      "agree": agree, "disagreements": len(disagreements),
                      "second_inconclusive": len(second_inconclusive), "ok": ok},
                     sort_keys=True))
    for d in disagreements:
        print(f"  DISAGREE {d['corpus']}/{d['case']}: z3={d['z3']} second={d['second']}", file=sys.stderr)
    for s in second_inconclusive[:10]:
        print(f"  second-inconclusive {s['corpus']}/{s['case']}: second={s['second']}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
