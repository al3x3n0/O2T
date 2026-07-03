#!/usr/bin/env python3
"""Drive the symbolic-IR fold harness (KLEE-on-bitcode, code-lift B faithful path).

Compiles harnesses/fold_symbolic_harness.cpp -- a peephole fold over a MOCK LLVM
IR with symbolic operands -- and verifies its soundness. The fold's value (what it
replaces the instruction with) must equal the instruction's true value on every
input; a violation is a real miscompile.

  * If `klee` is installed: compile the harness to bitcode with
    -DO2T_WITH_KLEE and run KLEE -> exhaustive symbolic exploration of
    all operand values (the true KLEE-on-bitcode path).
  * Otherwise (KLEE not available here): compile natively (clang, llvm@18) and run
    the harness, which enumerates concrete operands over a range that includes any
    bug trigger -- the KleeCompat native fallback. This VALIDATES the symbolic-IR
    model end to end without KLEE.

Teeth: the planted-bug variant (-DPLANT_BUG: add x,x -> x) must be caught; the
sound variant must pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "harnesses" / "fold_symbolic_harness.cpp"
INCLUDE = ROOT / "include"


def default_clang() -> str | None:
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    for name in ("clang++", "clang"):
        cand = base / name
        if cand.exists():
            return str(cand)
    return shutil.which("clang++") or shutil.which("clang")


def klee_clang() -> str | None:
    """A clang whose bitcode version matches KLEE's LLVM (klee 3.2 -> llvm@16).
    Mismatched bitcode fails to load, so prefer the KLEE-matched toolchain."""
    if os.environ.get("CV_KLEE_CLANG"):
        return os.environ["CV_KLEE_CLANG"]
    for base in ("/opt/homebrew/opt/llvm@16/bin", "/opt/homebrew/opt/llvm@17/bin"):
        cand = Path(base) / "clang++"
        if cand.exists():
            return str(cand)
    return default_clang()


def klee_include() -> str | None:
    """Directory containing klee/klee.h (for klee_make_symbolic/klee_assert)."""
    if os.environ.get("CV_KLEE_INCLUDE"):
        return os.environ["CV_KLEE_INCLUDE"]
    cand = Path("/opt/homebrew/opt/klee/include")
    if (cand / "klee" / "klee.h").exists():
        return str(cand)
    klee = shutil.which("klee")
    if klee:
        inc = Path(klee).resolve().parent.parent / "include"
        if (inc / "klee" / "klee.h").exists():
            return str(inc)
    return None


def klee_solver_args() -> list[str]:
    solver = os.environ.get("O2T_KLEE_SOLVER") or os.environ.get("CV_KLEE_SOLVER")
    if solver:
        return [f"--solver-backend={solver}"]
    if shutil.which("z3"):
        return ["--solver-backend=z3"]
    return []


def run_native(clang: str, plant_bug: bool) -> dict:
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / "harness"
        cmd = [clang, "-std=c++17", "-I", str(INCLUDE), str(HARNESS), "-o", str(exe)]
        if plant_bug:
            cmd.insert(1, "-DPLANT_BUG")
        compile_proc = subprocess.run(cmd, capture_output=True, text=True)
        if compile_proc.returncode != 0:
            return {"status": "compile-error", "stderr": compile_proc.stderr[-400:]}
        run_proc = subprocess.run([str(exe)], capture_output=True, text=True)
        sound = run_proc.returncode == 0
        return {"status": "sound" if sound else "miscompile",
                "output": run_proc.stdout.strip(), "backend": "native-enumeration"}


def run_klee(klee: str, clang: str, plant_bug: bool) -> dict:
    bc_clang = klee_clang() or clang
    inc = klee_include()
    with tempfile.TemporaryDirectory() as d:
        bc = Path(d) / "harness.bc"
        cmd = [bc_clang, "-std=c++17", "-DO2T_WITH_KLEE", "-I", str(INCLUDE),
               "-emit-llvm", "-c", "-g", "-O0", str(HARNESS), "-o", str(bc)]
        if inc:
            cmd[1:1] = ["-I", inc]
        if plant_bug:
            cmd.insert(1, "-DPLANT_BUG")
        cc = subprocess.run(cmd, capture_output=True, text=True)
        if cc.returncode != 0:
            return {"status": "compile-error", "backend": "klee", "stderr": cc.stderr[-400:]}
        solver_args = klee_solver_args()
        proc = subprocess.run([klee, *solver_args, "--exit-on-error-type=Assert", str(bc)],
                              capture_output=True, text=True, cwd=d)
        out = proc.stdout + proc.stderr
        # KLEE prints "ASSERTION FAIL" and emits a .err file on the miscompile path.
        found = "ASSERTION FAIL" in out
        sound = "KLEE: done:" in out and not found
        status = "miscompile" if found else ("sound" if sound else "error")
        result = {
            "status": status,
            "backend": "klee",
            "bitcode_clang": bc_clang,
            "returncode": proc.returncode,
        }
        if solver_args:
            result["solver_args"] = solver_args
        if status == "error":
            result["output_tail"] = out[-1200:]
        return result


def verify(plant_bug: bool, klee: str | None, clang: str) -> dict:
    if klee is not None:
        return run_klee(klee, clang, plant_bug)
    return run_native(clang, plant_bug)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="verify sound + planted-bug variants")
    ap.add_argument("--plant-bug", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    clang = default_clang()
    if clang is None:
        print(json.dumps({"status": "skipped", "reason": "clang not found"}))
        return 0
    klee = shutil.which("klee")  # None here; the symbolic path is wired for when it exists

    if args.selftest:
        sound = verify(plant_bug=False, klee=klee, clang=clang)
        buggy = verify(plant_bug=True, klee=klee, clang=clang)
        report = {"backend": sound.get("backend"), "klee_available": klee is not None,
                  "sound_variant": sound, "buggy_variant": buggy,
                  "ok": sound.get("status") == "sound" and buggy.get("status") == "miscompile"}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({k: v for k, v in report.items()
                          if k in ("backend", "klee_available", "ok")}, sort_keys=True))
        print(f"  sound variant: {sound['status']}", file=sys.stderr)
        print(f"  planted-bug variant: {buggy['status']} ({buggy.get('output', '')})", file=sys.stderr)
        return 0 if report["ok"] else 1

    result = verify(plant_bug=args.plant_bug, klee=klee, clang=clang)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") in ("sound", "miscompile") else 1


if __name__ == "__main__":
    sys.exit(main())
