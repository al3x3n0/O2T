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

    proc = subprocess.run([sys.executable, str(TOOL), "--count", "12", "--seed", "20704", "--insns", "9"],
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, ("the differential fuzzer found a disagreement (false proof/refutation)", out)
    assert "DISAGREEMENTS: 0" in out, ("expected zero O2T-vs-Alive2 disagreements", out)
    # sanity: it actually generated + cross-checked (not a vacuous pass).
    assert "generated 12" in out and "O2T {" in out, ("the fuzzer did not run the pipeline", out)

    print("fuzz_differential_fixture OK: a small differential-fuzz batch (random IR -> real opt -> O2T "
          "Track B vs reference Alive2) found ZERO disagreements -- O2T's proofs agree with the "
          "ground-truth oracle on random inputs too. Regression net for false proofs (full campaign: "
          "tools/cv-fuzz-differential.py --count N)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
