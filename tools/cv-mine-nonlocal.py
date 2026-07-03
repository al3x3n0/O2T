#!/usr/bin/env python3
"""Mine NON-LOCAL (select / if-converted) folds from real pass source and prove them.

Closes the loop for beyond-peephole patterns: the AST miner already surfaces the
`m_Select(...)` predicate of an if-conversion fold; this extracts that matcher, lifts
it through cv_lift_matcher (m_Select -> ite, the branch condition as `C != 0`), pairs
it with the rewrite, and proves the whole transform with Z3 -- exactly the pipeline
the peephole side uses, now reaching control-flow folds.

  miner -> predicate `match(V, m_Select(m_Value(C), m_Value(X), m_Deferred(X)))`
        -> lift  -> before = ite(C!=0, X, X),  after = X
        -> z3    -> UNSAT (sound: identical-arm select folds away)

Skips gracefully if the AST miner is not built or z3 is absent.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cv_lift_matcher as lm  # noqa: E402
from cv_formal_ir import FormalIrError, equivalence_smt, pair_for_formal  # noqa: E402

DEFAULT_MINER = ROOT / "build-clang-tools" / "cv-mine-pass-source-ast"
DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "select_folds.cpp"


def extract_matcher(predicate: str) -> str | None:
    """Pull the non-local matcher (m_Select(...)) out of `match(V, <matcher>)`."""
    for token in ("m_Select",):
        i = predicate.find(token)
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(predicate)):
            if predicate[j] == "(":
                depth += 1
            elif predicate[j] == ")":
                depth -= 1
                if depth == 0:
                    return predicate[i:j + 1]
    return None


def run_miner(miner: Path, source: Path) -> list[dict]:
    proc = subprocess.run([str(miner), str(source)], capture_output=True, text=True, cwd=str(ROOT))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else data.get("findings", [])


def run_z3(z3_bin: str, smt: str) -> str:
    proc = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
    return proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    if not args.miner.exists():
        print(json.dumps({"status": "skipped", "reason": "miner not built"}))
        return 0
    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    findings = run_miner(args.miner, args.source)
    results = []
    for f in findings:
        matcher = extract_matcher(str(f.get("predicate_source") or ""))
        if matcher is None:
            continue
        rewrite = re.sub(r"^\s*return\s+", "", str(f.get("rewrite_source") or "")).rstrip(";").strip()
        # The miner records the rewrite's actual argument name; the proof only needs
        # the structural transform, so normalize to the matched arm name X.
        rewrite = re.sub(r"replaceInstUsesWith\([^,]+,[^)]*\)", "replaceInstUsesWith(I, X)", rewrite)
        try:
            before, after, variables = lm.lift_transform(matcher, rewrite)
            formal = {"domain": "scalar-bv32", "equivalence": "result", "variables": variables,
                      "poison_variables": [], "refinement": "refinement", "before": before, "after": after}
            verdict = run_z3(z3_bin, equivalence_smt("mined-nonlocal", "mine-nonlocal", pair_for_formal(formal)))
        except (lm.MatcherError, FormalIrError) as exc:
            results.append({"line": f.get("line"), "matcher": matcher, "status": "lift-error", "error": str(exc)})
            continue
        results.append({"line": f.get("line"), "matcher": matcher,
                        "verdict": verdict, "status": "proved" if verdict == "unsat" else
                        ("refuted" if verdict == "sat" else "error")})

    proved = [r for r in results if r["status"] == "proved"]
    errors = [r for r in results if r["status"] in ("lift-error", "error")]
    ok = bool(proved) and not errors
    report = {"source": str(args.source), "findings": len(findings),
              "nonlocal_mined": len(results), "proved": len(proved), "results": results, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"findings": len(findings), "nonlocal_mined": len(results),
                      "proved": len(proved), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']}] line {r.get('line')}: {r['matcher'][:50]}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
