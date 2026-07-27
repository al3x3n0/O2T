#!/usr/bin/env python3
"""A small standing differential-fuzz: random IR, real opt, O2T Track B vs reference Alive2.

The soundness review found false proofs by hand. This gates a tiny automated version of that hunt so a
future change that introduces a false proof on random inputs is caught: `cv-fuzz-differential` generates
random scalar functions (with random poison flags), runs `opt -passes=instcombine`, and cross-checks
O2T's whole-function TV against Alive2. Zero disagreements is the invariant; the tool exits non-zero on
any. A deterministic seed keeps it reproducible. Small `--count` here (the full campaign is a manual
run); needs z3 + opt 18 + alive-tv.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402

TOOL = ROOT / "tools" / "cv-fuzz-differential.py"


def main() -> int:
    if not (shutil.which("z3") and toolchain.resolve_opt("opt") and shutil.which("alive-tv")):
        print("fuzz_differential_fixture: needs z3 + opt(18) + alive-tv, skipped")
        return 0

    # A small batch of EACH Track B shape -- scalar (poison flags), the modeled intrinsics, the
    # theory-of-arrays memory model, the vector lane model, the multi-block CFG path, and the freeze
    # encoding (whose target is synthesized, since InstCombine essentially never emits `freeze` on
    # random IR -- so this is the only way that model gets fuzzed). Each cross-checked against Alive2.
    for extra, label in ((["--intrinsics"], "scalar+intrinsics"),
                         (["--shape", "memory"], "memory"),
                         (["--shape", "vector"], "vector"),
                         (["--shape", "cfg"], "cfg"),
                         (["--shape", "freeze"], "freeze")):
        proc = subprocess.run([sys.executable, str(TOOL), "--count", "8", "--seed", "20704",
                               "--insns", "8", *extra], capture_output=True, text=True, timeout=300)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, (f"differential fuzzer found a disagreement on {label}", out)
        assert "DISAGREEMENTS: 0" in out and "generated 8" in out, (label, out)

    print("fuzz_differential_fixture OK: small differential-fuzz batches across all Track B shapes -- "
          "scalar+intrinsics, pointer-memory (theory of arrays), fixed vectors (lane model), the "
          "multi-block CFG path, and the freeze encoding -- each O2T vs reference Alive2, found ZERO "
          "disagreements. A standing regression net for false proofs over the whole fragment (full "
          "campaign: cv-fuzz-differential --shape {scalar,memory,vector,cfg,freeze} [--intrinsics] "
          "--count N)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
