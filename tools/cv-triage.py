#!/usr/bin/env python3
"""Triage classifier for refutations (autonomous-verify #4).

The dossier's `suspected-bug` lumps together several very different situations.
This classifier splits a refuted/suspect transform into an actionable verdict by
composing the pieces already built -- faithfulness (did we lift the right thing?),
CEGIS guard inference (is it sound under a precondition?), and the real-opt TV
result:

    lifter-issue        not faithful -> we lifted the wrong transform (OUR bug,
                        not LLVM's); re-mine / fix the lifter
    missing-precondition faithful + CEGIS finds a guard -> sound UNDER a condition
                        (conditional, NOT a bug); the guard is the discovered
                        precondition the pass must establish
    real-miscompile     faithful + no precondition + the real opt's TV REFUTED
                        -> a genuine miscompile, with a witness
    suspected-miscompile faithful + no precondition + no TV refutation yet
                        -> needs cross-validation to confirm/clear

Composes cv-infer-guard (CEGIS). Witness for real-miscompile is the lowered
before-tree (minimize further with cv-reduce-ir).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INFER_GUARD = HERE / "cv-infer-guard.py"


def cegis(before, after, variables, z3_bin: str) -> dict:
    formal = {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
              "variables": variables, "poison_variables": variables,
              "before": before, "after": after}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(formal, tmp)
        path = tmp.name
    try:
        proc = subprocess.run([sys.executable, str(INFER_GUARD), "--formal", path,
                               "--z3-bin", z3_bin], capture_output=True, text=True)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"status": "error"}
    finally:
        Path(path).unlink(missing_ok=True)


def triage_one(t: dict, z3_bin: str) -> dict:
    marker = t.get("marker", "?")
    out = {"marker": marker}
    if t.get("faithful") is False:
        out.update(verdict="lifter-issue",
                   reason="reconstructed transform does not match the registry; re-mine/fix lifter")
        return out
    # faithful (or unknown-but-treated-as-faithful): is it sound under a precondition?
    cg = cegis(t["before"], t["after"], t["variables"], z3_bin)
    status = cg.get("status")
    if status == "guard-found":
        out.update(verdict="missing-precondition", guards=cg.get("guards", []),
                   reason="sound under the inferred precondition (conditional, not a bug)")
    elif status == "already-sound":
        out.update(verdict="not-a-bug", reason="formal is actually sound (refutation was spurious)")
    elif status == "no-precondition":
        if t.get("tv") == "refuted":
            out.update(verdict="real-miscompile",
                       reason="faithful, no precondition, real-opt TV refuted -> witness")
        else:
            out.update(verdict="suspected-miscompile",
                       reason="faithful, no catalog precondition; cross-validate to confirm")
    else:
        out.update(verdict="triage-error", reason=str(status))
    return out


def selftest_cases():
    def v(n):
        return {"op": "var", "name": n}

    def bvc(x):
        return {"op": "bvconst", "bits": 32, "value": x}
    eq_ab = {"op": "eq", "args": [v("a"), v("b")]}
    return [
        # not faithful -> lifter-issue
        dict(marker="probe.x.mul-zero", faithful=False,
             before={"op": "bvmul", "args": [v("a"), bvc(0)]}, after=v("a"), variables=["a"],
             expect="lifter-issue"),
        # faithful, CEGIS finds a!=b -> missing-precondition
        dict(marker="probe.x.select-eq", faithful=True,
             before={"op": "ite", "args": [eq_ab, v("x"), v("y")]}, after=v("y"),
             variables=["a", "b", "x", "y"], expect="missing-precondition"),
        # faithful, no precondition (a+1==a is false for ALL a) -> suspected-miscompile
        dict(marker="probe.x.add-one", faithful=True,
             before={"op": "bvadd", "args": [v("a"), bvc(1)]}, after=v("a"),
             variables=["a"], expect="suspected-miscompile"),
        # same but the real-opt TV refuted -> real-miscompile (with witness)
        dict(marker="probe.x.add-one-tv", faithful=True, tv="refuted",
             before={"op": "bvadd", "args": [v("a"), bvc(1)]}, after=v("a"),
             variables=["a"], expect="real-miscompile"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--transforms", type=Path, help="json list of refuted transforms")
    src.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    if args.selftest:
        cases = selftest_cases()
    elif args.transforms:
        cases = json.loads(args.transforms.read_text())
    else:
        ap.error("provide --transforms or --selftest")

    results, failed = [], 0
    counts: dict[str, int] = {}
    for case in cases:
        verdict = triage_one(case, z3_bin)
        counts[verdict["verdict"]] = counts.get(verdict["verdict"], 0) + 1
        if "expect" in case and verdict["verdict"] != case["expect"]:
            verdict["UNEXPECTED"] = case["expect"]
            failed += 1
        results.append(verdict)

    summary = {"cases": len(results), "failed": failed, "verdicts": counts, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    for r in results:
        tag = "FAIL" if "UNEXPECTED" in r else "ok"
        guards = r.get("guards", "")
        print(f"  [{tag}] {r['marker']}: {r['verdict']} {guards}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
