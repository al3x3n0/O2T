#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess

from common import context
ctx = context()
HERE = H = ctx.out
ROOT = ctx.root
RV = ctx.rv
LLVM = LLVM16 = ctx.llvm16 / 'bin'
LLVM18 = ctx.llvm18 / 'bin'
from rv_cases import CASES, TARGETED

manifest = []
for name, filename, kind, shapes in CASES:
    if name not in getattr(ctx, 'cases', [name]):
        continue
    manifest.append({"name": name, "source": str(RV / "test/suite" / filename),
                     "origin": "upstream-unmodified", "kind": kind, "shapes": shapes})
for name, code in TARGETED.items():
    if name not in getattr(ctx, 'cases', [name]):
        continue
    d = HERE / name
    d.mkdir(exist_ok=True)
    src = d / "input.cpp"
    src.write_text(code)
    manifest.append({"name": name, "source": str(src), "origin": "targeted-input",
                     "kind": "integer", "shapes": "T_TrT"})
for case in manifest:
    d = HERE / case["name"]
    d.mkdir(exist_ok=True)
    cmd = [str(LLVM / "clang++"), "-std=c++17", "-O1", "-ffp-contract=off",
           "-fno-vectorize", "-fno-slp-vectorize", "-fno-unroll-loops", "-S", "-emit-llvm",
           case["source"], "-o", str(d / "scalar.ll")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    (d / "compile.log").write_text(proc.stderr)
    case["prepare_exit"] = proc.returncode
    case["prepare_command"] = cmd
    print(case["name"], proc.returncode, flush=True)
(HERE / "cases.json").write_text(json.dumps(manifest, indent=2) + "\n")
