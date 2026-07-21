#!/usr/bin/env python3
"""E1: closed-loop translation-validation coverage matrix -- real opt passes x benchmark loops.

The paper's headline soundness experiment. For every (pass, loop function) cell, run the ACTUAL
`opt -passes=<X>` and prove its real output equivalent to the input for all trip counts (the SCEV
frontend + relational prover, driven by cv-translation-validate). Each cell is:
  proved              -- loop->loop, outputs proved equal
  proved-closed-form  -- loop->closed-form (indvars), the closed form proved equal
  loop-eliminated     -- the accumulator was deleted and the closed-form validator does not cover
                         this shape yet; reported honestly, NEVER silently passed
  output-not-preserved -- outputs differ (a miscompile) -- must NOT occur on a sound pass
  error               -- opt failed / no recurrence extracted

Headline invariant: ZERO refutations across sound passes (no false alarm on correct LLVM). The
teeth are separate and load-bearing: `mutate=True` corrupts one phi initial value in opt's output
(simulating a recurrence miscompile), and the validator must then refute -- the proof that a REAL
miscompile is caught, not rubber-stamped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
BENCH = ROOT / "tests" / "fixtures" / "translation_validation.ll"
_HOMEBREW_OPT = Path("/opt/homebrew/opt/llvm@18/bin/opt")

# Sound loop passes whose output this prover covers (loop->loop) or reports (loop->closed-form).
DEFAULT_PASSES = ("licm", "loop-rotate", "simple-loop-unswitch", "loop-instsimplify", "indvars")
POSITIVE = {"proved", "proved-closed-form"}
NEGATIVE = "output-not-preserved"        # cv-translation-validate's refuted token


def _resolve_opt(opt_bin: str) -> str | None:
    return shutil.which(opt_bin) or (str(_HOMEBREW_OPT) if _HOMEBREW_OPT.exists() else None)


def _run_cell(passes: str, source: Path, opt: str, z3: str, mutate: bool = False) -> list[dict]:
    tool = str(TOOLS / "cv-translation-validate.py")
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rep = Path(tf.name)
    argv = [sys.executable, tool, "--source", str(source), "--passes", passes,
            "--opt-bin", opt, "--z3-bin", z3, "--report", str(rep)]
    if mutate:
        argv.append("--mutate")
    try:
        subprocess.run(argv, capture_output=True, text=True)
        data = json.loads(rep.read_text()) if rep.stat().st_size else {}
    except (OSError, json.JSONDecodeError):
        return []
    finally:
        rep.unlink(missing_ok=True)
    return data.get("results", [])


def run(opt: str, z3: str, passes=DEFAULT_PASSES, source: Path = BENCH) -> dict:
    matrix: dict[str, dict[str, str]] = {}
    tally: dict[str, int] = {}
    functions: set[str] = set()
    for p in passes:
        row = {}
        for r in _run_cell(p, source, opt, z3):
            row[r["function"]] = r["status"]
            functions.add(r["function"])
            tally[r["status"]] = tally.get(r["status"], 0) + 1
        matrix[p] = row
    refutations = [(p, f) for p, row in matrix.items() for f, s in row.items() if s == NEGATIVE]
    positives = sum(v for k, v in tally.items() if k in POSITIVE)
    return {"passes": list(passes), "functions": sorted(functions), "matrix": matrix,
            "tally": tally, "positive_verdicts": positives, "false_refutations": refutations,
            "cells": sum(len(r) for r in matrix.values())}


def teeth(opt: str, z3: str, source: Path = BENCH) -> dict:
    """The miscompile-catch proof: a mutated opt output must be refuted somewhere."""
    results = _run_cell("licm", source, opt, z3, mutate=True)
    refuted = [r for r in results if r["status"] == NEGATIVE]
    return {"mutated_cells": len(results), "refuted": len(refuted),
            "caught": bool(refuted), "witness": bool(refuted and refuted[0].get("witness"))}


def render(r: dict, t: dict) -> str:
    lines = [f"== E1: translation-validation coverage ({len(r['passes'])} passes x "
             f"{len(r['functions'])} loops = {r['cells']} cells) =="]
    for p in r["passes"]:
        row = r["matrix"][p]
        summ = {}
        for s in row.values():
            summ[s] = summ.get(s, 0) + 1
        lines.append(f"  {p:22s} {summ}")
    lines.append(f"tally: {r['tally']}")
    lines.append(f"positive verdicts: {r['positive_verdicts']}   "
                 f"false refutations on sound passes: {len(r['false_refutations'])}")
    lines.append(f"teeth (mutated recurrence): {'REFUTED (caught)' if t['caught'] else 'ESCAPED'}"
                 f"{' with witness' if t['witness'] else ''}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="E1: closed-loop TV coverage matrix")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    opt = _resolve_opt(args.opt_bin)
    z3 = shutil.which(args.z3_bin)
    if opt is None or z3 is None:
        print(f"cv-tv-matrix: need opt and z3 (opt={opt}, z3={z3})", file=sys.stderr)
        return 2
    r = run(opt, z3)
    t = teeth(opt, z3)
    if args.report:
        args.report.write_text(json.dumps({"coverage": r, "teeth": t}, indent=2) + "\n")
    print(render(r, t), end="")
    return 1 if r["false_refutations"] or not t["caught"] else 0


if __name__ == "__main__":
    sys.exit(main())
