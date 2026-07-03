#!/usr/bin/env python3
"""Cover source-recovered DCE dead-instruction erasure verification."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import extract_dce_model as ed

FX = ROOT / "tests" / "fixtures"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("extract_dce_model_fixture: z3 not found, skipped")
        return 0

    rows = {result["function"]: result for result in ed.verify_source(
        z3, (FX / "dce_dead_instruction_folds.cpp").read_text(encoding="utf-8"))}
    for name in ("eraseTriviallyDead", "eraseWouldBeDead", "eraseRecursiveDead"):
        assert rows[name]["trivially_dead"] and rows[name]["status"] == "proved", rows[name]
    unsafe = rows["eraseWithoutGuard"]
    assert not unsafe["trivially_dead"], unsafe
    assert unsafe["status"] == "refuted" and unsafe.get("witness"), unsafe
    assert rows["notDeletion"]["status"] == "not-a-transform", rows["notDeletion"]

    sound = {result["function"]: result for result in ed.verify_source(
        z3, (FX / "dce_dead_instruction_sound.cpp").read_text(encoding="utf-8"))}
    assert all(result["status"] == "proved" for result in sound.values()), sound

    allocas = {result["function"]: result for result in ed.verify_source(
        z3, (FX / "dce_unused_alloca_folds.cpp").read_text(encoding="utf-8"))}
    for name in (
        "eraseUnusedAlloca",
        "eraseUserEmptyAlloca",
        "eraseHasNUsesZeroAlloca",
        "eraseUsersEmptyAlloca",
        "eraseNotHasNUsesOrMoreAlloca",
    ):
        assert allocas[name]["unused_alloca"] and allocas[name]["status"] == "proved", allocas[name]
        assert allocas[name]["marker"] == "probe.cleanup.unused-alloca", allocas[name]
    positive_use_alloca = allocas["erasePositiveHasNUsesOrMoreAlloca"]
    assert not positive_use_alloca["unused_alloca"], positive_use_alloca
    assert positive_use_alloca["status"] == "refuted" and positive_use_alloca.get("witness"), positive_use_alloca
    unsafe_alloca = allocas["eraseAllocaWithoutGuard"]
    assert not unsafe_alloca["unused_alloca"], unsafe_alloca
    assert unsafe_alloca["status"] == "refuted" and unsafe_alloca.get("witness"), unsafe_alloca
    assert allocas["notAllocaCleanup"]["status"] == "not-a-transform", allocas["notAllocaCleanup"]

    loops = {result["function"]: result for result in ed.verify_source(
        z3, (FX / "dce_dead_loop_instruction_folds.cpp").read_text(encoding="utf-8"))}
    for name in ("eraseDeadLoopInstruction", "deleteDeadLoopInstruction"):
        assert loops[name]["dead_loop_instruction"] and loops[name]["status"] == "proved", loops[name]
        assert loops[name]["marker"] == "probe.dce.dead-loop-instruction", loops[name]
    unsafe_loop = loops["eraseLoopInstructionWithoutGuard"]
    assert not unsafe_loop["dead_loop_instruction"], unsafe_loop
    assert unsafe_loop["status"] == "refuted" and unsafe_loop.get("witness"), unsafe_loop
    assert loops["notLoopDeletion"]["status"] == "not-a-transform", loops["notLoopDeletion"]

    assert ed.recognize_dead_erase("void f(){ Value *V = I; }") is None
    m = ed.recognize_dead_erase("void f(){ if (isInstructionTriviallyDead(I,nullptr)) I->eraseFromParent(); }")
    assert m and m["trivially_dead"], m
    m = ed.recognize_dead_erase("void f(){ I->eraseFromParent(); }")
    assert m and not m["trivially_dead"], m
    m = ed.recognize_dead_erase("void f(AllocaInst *AI){ if (AI->use_empty()) AI->eraseFromParent(); }")
    assert m and m["unused_alloca"] and m["marker"] == "probe.cleanup.unused-alloca", m
    m = ed.recognize_dead_erase("void f(AllocaInst *AI){ if (AI->users().empty()) AI->eraseFromParent(); }")
    assert m and m["unused_alloca"], m
    m = ed.recognize_dead_erase("void f(AllocaInst *AI){ if (!AI->hasNUsesOrMore(1)) AI->eraseFromParent(); }")
    assert m and m["unused_alloca"], m
    m = ed.recognize_dead_erase("void f(AllocaInst *AI){ if (AI->hasNUsesOrMore(1)) AI->eraseFromParent(); }")
    assert m and not m["unused_alloca"], m
    m = ed.recognize_dead_erase("void f(Loop &L, Instruction &I){ if (isDeadLoopInstruction(&I)) I.eraseFromParent(); }")
    assert m and m["dead_loop_instruction"] and m["marker"] == "probe.dce.dead-loop-instruction", m

    mine = ROOT / "tools" / "cv-mine-dce-pass.py"
    proc = subprocess.run([sys.executable, str(mine)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"proved": 3' in proc.stdout, proc.stdout
    assert '"refuted": 1' in proc.stdout, proc.stdout
    proc = subprocess.run([sys.executable, str(mine), "--source", str(FX / "dce_dead_instruction_sound.cpp")],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and '"proved": 2' in proc.stdout and '"refuted": 0' in proc.stdout, proc.stdout
    proc = subprocess.run([sys.executable, str(mine), "--source", str(FX / "dce_unused_alloca_folds.cpp")],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and '"proved": 5' in proc.stdout and '"refuted": 2' in proc.stdout, proc.stdout
    proc = subprocess.run([sys.executable, str(mine), "--source", str(FX / "dce_dead_loop_instruction_folds.cpp")],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and '"proved": 2' in proc.stdout and '"refuted": 1' in proc.stdout, proc.stdout

    print("extract_dce_model_fixture OK: dead-instruction, dead-loop-instruction, and "
          "unused-alloca erasures recovered from source; unguarded erases refute with witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
