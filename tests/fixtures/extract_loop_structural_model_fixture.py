#!/usr/bin/env python3
"""Cover source-recovered LICM verification (intent/extract_loop_structural_model.py).

Asserts the miner recovers each hoist fold's legality (loop-invariance / speculatable /
guaranteed-to-execute), proves the folds that establish invariance AND a safety condition, and
REFUTES one that hoists on loop-invariance alone -- catching an unsound LICM-like pass (a hoisted
trapping op) from its source. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import extract_loop_structural_model as el

FX = ROOT / "tests" / "fixtures"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("extract_loop_structural_model_fixture: z3 not found, skipped")
        return 0

    by = {r["function"]: r for r in el.verify_source(
        z3, (FX / "licm_hoist_folds.cpp").read_text())}

    # 1) the sound folds (invariant + speculatable / guaranteed) prove.
    assert by["hoistInvariantSpeculatable"]["status"] == "proved", by
    assert by["hoistInvariantSpeculatable"]["speculatable"] is True
    assert by["hoistInvariantGuaranteed"]["status"] == "proved", by
    assert by["hoistInvariantGuaranteed"]["guaranteed"] is True

    # 2) the unsafe fold (loop-invariance only, no safety check) is REFUTED with a witness.
    bad = by["hoistInvariantOnly"]
    assert bad["invariant"] and not bad["speculatable"] and not bad["guaranteed"], bad
    assert bad["status"] == "refuted" and bad.get("witness"), ("hoisted trapping op not caught", bad)

    # 3) the all-sound source proves every fold.
    sound = el.verify_source(z3, (FX / "licm_hoist_sound.cpp").read_text())
    folds = [r for r in sound if r["status"] != "not-a-transform"]
    assert folds and all(r["status"] == "proved" for r in folds), sound

    # 4) recognition helper directly: a non-hoisting fold is not a transform; missing the safety
    #    fact alone makes an invariant-only hoist unsafe.
    assert el.recognize_hoist_fold("void f(){ I->eraseFromParent(); }") is None
    m = el.recognize_hoist_fold("void f(){ if (isLoopInvariant(L,I)) hoistToPreheader(I,L); }")
    assert m and m["invariant"] and not m["speculatable"] and not m["guaranteed"], m

    # 5) the CLIs agree.
    mine = ROOT / "tools" / "cv-mine-licm-pass.py"
    p1 = subprocess.run([sys.executable, str(mine)], capture_output=True, text=True)
    assert p1.returncode == 0 and '"refuted": 1' in p1.stdout and '"proved": 2' in p1.stdout, p1.stdout
    p2 = subprocess.run([sys.executable, str(mine), "--source", str(FX / "licm_hoist_sound.cpp")],
                        capture_output=True, text=True)
    assert p2.returncode == 0 and '"proved": 2' in p2.stdout and '"refuted": 0' in p2.stdout, p2.stdout

    print("extract_loop_structural_model_fixture OK: LICM hoist folds recovered from source and "
          "discharged; a hoist guarded only by loop-invariance (trapping op) refuted with a witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
