#!/usr/bin/env python3
"""Cover source-recovered SimplifyCFG if-conversion verification (intent/extract_cfg_model.py).

Asserts the miner recovers how each `CreateSelect` fold binds the branch condition and the
then/else block values, proves the identity and negate-and-swap bindings, and REFUTES a fold that
swaps the value operands without negating the condition -- catching an unsound if-conversion from
its source, discharged by the same prover the IR-level diamond->select contract uses. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import extract_cfg_model as ec

FX = ROOT / "tests" / "fixtures"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("extract_cfg_model_fixture: z3 not found, skipped")
        return 0

    by = {r["function"]: r for r in ec.verify_source(z3, (FX / "cfg_ifconv_folds.cpp").read_text())}

    # 1) the identity binding proves; 2) negate-and-swap proves; 3) swap-only is REFUTED.
    ident = by["foldDiamondToSelect"]
    assert ident["status"] == "proved" and not ident["cond_negated"], ident
    assert ident["true_src"] == "then" and ident["false_src"] == "else", ident
    neg = by["foldDiamondNegatedSwapped"]
    assert neg["status"] == "proved" and neg["cond_negated"], neg
    assert neg["true_src"] == "else" and neg["false_src"] == "then", neg
    bad = by["foldDiamondSwappedOperands"]
    assert bad["status"] == "refuted" and bad.get("witness"), ("swapped if-conversion not caught", bad)
    assert not bad["cond_negated"] and bad["true_src"] == "else", bad

    # 4) the all-sound source proves every fold.
    sound = ec.verify_source(z3, (FX / "cfg_ifconv_sound.cpp").read_text())
    folds = [r for r in sound if r["status"] != "not-a-transform"]
    assert folds and all(r["status"] == "proved" for r in folds), sound

    # 5) recognition helper directly: resolves operand roles; a non-select fold is not a transform.
    m = ec.recognize_ifconversion_fold(
        "Value *f(IRBuilder &B, BranchInst *BI, PHINode *PN, BasicBlock *ThenBB, "
        "BasicBlock *ElseBB){ Value *C = BI->getCondition(); "
        "Value *T = PN->getIncomingValueForBlock(ThenBB); "
        "Value *F = PN->getIncomingValueForBlock(ElseBB); return B.CreateSelect(C, T, F); }")
    assert m and not m["cond_negated"] and m["true_src"] == "then" and m["false_src"] == "else", m
    assert ec.recognize_ifconversion_fold("void f(){ MergeBlockIntoPredecessor(BB); }") is None

    # 6) the CLIs agree.
    mine = ROOT / "tools" / "cv-mine-cfg-pass.py"
    p1 = subprocess.run([sys.executable, str(mine)], capture_output=True, text=True)
    assert p1.returncode == 0 and '"refuted": 1' in p1.stdout and '"proved": 2' in p1.stdout, p1.stdout
    p2 = subprocess.run([sys.executable, str(mine), "--source", str(FX / "cfg_ifconv_sound.cpp")],
                        capture_output=True, text=True)
    assert p2.returncode == 0 and '"proved": 2' in p2.stdout and '"refuted": 0' in p2.stdout, p2.stdout

    print("extract_cfg_model_fixture OK: if-conversion folds recovered from source and discharged; "
          "a select with swapped operands (no condition negation) refuted with a witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
