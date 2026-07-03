#!/usr/bin/env python3
"""KLEE-driven symbolic execution of real fold control flow, then per-path refinement discharge.

Where `real_pass.py` hand-enumerates query outcomes, this uses KLEE to do TRUE symbolic execution of
the fold's C harness: the analysis queries AND the input opcode are made symbolic, KLEE forks on
every feasible branch (including `&&` short-circuits and input-shape dispatch) and writes one test
case per path. We replay each test (libkleeRuntest) to reproduce the path concretely -- yielding
{opcode, input, output, decisions} -- and discharge `(facts the branches established) => out == in`
for each rewriting path (reusing the same per-path refinement check). KLEE finds the feasible paths
automatically, so an under-guarded path is discovered and refuted without enumerating anything.

cvc5-style graceful degradation: KLEE + its matching clang are auto-detected; absent, the run is
reported skipped (the hand-enumeration path in `real_pass.py` remains the always-available fallback).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from o2t.symexec.real_pass import discharge_path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "o2t" / "symexec" / "klee_fold.c"

# KLEE needs bitcode from the LLVM it was built against (16); auto-detect the toolchain.
_KLEE = "/opt/homebrew/bin/klee"
_CLANG16 = "/opt/homebrew/opt/llvm@16/bin/clang"
_INCLUDE = "/opt/homebrew/include"
_LIB = "/opt/homebrew/lib"


def available():
    return all(Path(p).exists() for p in (_KLEE, _CLANG16, _INCLUDE,
                                          Path(_LIB) / "libkleeRuntest.dylib"))


_solver_args_cache: list[str] | None = None


def _solver_args():
    """Prefer KLEE's Z3 core-solver backend when this KLEE was built with it: several packaged KLEE
    builds ship a default STP backend that aborts at startup (STPSolverImpl fatal error), while the
    Z3 backend works. Detected once from `klee --help`; an STP-only build keeps the default."""
    global _solver_args_cache
    if _solver_args_cache is None:
        try:
            help_text = subprocess.run([_KLEE, "--help"], capture_output=True, text=True).stdout
        except OSError:
            help_text = ""
        _solver_args_cache = ["--solver-backend=z3"] if "=z3" in help_text else []
    return _solver_args_cache


def _explore(c_path, workdir):
    """Compile to bitcode, run KLEE, return the ktest paths (or None on failure)."""
    bc = workdir / "fold.bc"
    r = subprocess.run([_CLANG16, "-emit-llvm", "-c", "-g", "-O0", "-I", _INCLUDE,
                        str(c_path), "-o", str(bc)], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = workdir / "klee-out"
    subprocess.run([_KLEE, *_solver_args(), f"--output-dir={out}", str(bc)],
                   capture_output=True, text=True)
    return sorted(out.glob("*.ktest")) or None


def _replay_binary(c_path, workdir):
    exe = workdir / "replay"
    r = subprocess.run([_CLANG16, str(c_path), "-I", _INCLUDE, "-L", _LIB, "-lkleeRuntest",
                        "-o", str(exe)], capture_output=True, text=True)
    return str(exe) if r.returncode == 0 else None


def _replay(exe, ktest):
    r = subprocess.run([exe], capture_output=True, text=True,
                       env={"KTEST_FILE": str(ktest), "PATH": "/usr/bin:/bin"})
    line = r.stdout.strip().splitlines()
    try:
        return json.loads(line[-1]) if line else None
    except json.JSONDecodeError:
        return None


def run_klee(z3_bin, c_path=None):
    """Explore the harness with KLEE and discharge every rewriting path. Returns a report."""
    c_path = c_path or HARNESS
    if not available():
        return {"status": "skipped", "reason": "klee or matching clang not found"}
    with tempfile.TemporaryDirectory() as d:
        wd = Path(d)
        ktests = _explore(c_path, wd)
        exe = _replay_binary(c_path, wd)
        if ktests is None or exe is None:
            return {"status": "error", "reason": "klee/compile failed"}
        paths = [p for p in (_replay(exe, t) for t in ktests) if p]
        rows = []
        for p in paths:
            v = discharge_path(z3_bin, p)
            rows.append({"opcode": p.get("opcode"), "input": p["input"],
                         "rewrote": v["rewrote"], "status": v["status"],
                         "facts": v.get("facts"),
                         "decisions": [d["q"] + ("" if d["v"] else "!") for d in p["decisions"]],
                         "witness": bool(v.get("witness"))})
    rewriting = [r for r in rows if r["rewrote"]]
    refuted = [r for r in rewriting if r["status"] == "refuted"]
    proved = [r for r in rewriting if r["status"] == "proved"]
    return {"status": "ok", "paths": len(rows), "rewriting_paths": len(rewriting),
            "proved": len(proved), "refuted": len(refuted),
            "ok": bool(rewriting) and not refuted, "rows": rows}
