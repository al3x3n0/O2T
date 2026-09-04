#!/usr/bin/env python3
"""The DCE miner must survive the idioms REAL passes use, not just the corpus dialect.

O2T's DCE fixtures were written in one dialect: every fold declares its types, reaches for
`isInstructionTriviallyDead`, and is small enough that the function IS the fold. Pointed at VeGen's
GSLP -- a 24-file research vectorizer, the first code this tool has seen that was not written for
it -- the miner produced FOUR false refutations against working code in an afternoon, none of them
reachable from 1,937 corpus functions or 496 fixtures. This file pins each shape.

Every case here is taken from real source, and each defect made O2T ACCUSE a correct pass. That is
the failure mode this project treats as seriously as a false proof, and the one most likely to
destroy trust the first time someone points the tool at their own code.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import o2t.intent.extract_dce_model as ed  # noqa: E402


def main() -> int:
    if shutil.which("z3") is None:
        print("dce_real_pass_idioms_fixture: z3 not found, skipped")
        return 0
    z3 = "z3"

    # 1) A BASIC BLOCK ERASE IS NOT AN INSTRUCTION ERASE. VeGen VectorPackSet.cpp:1060 --
    #    `for (auto *BB : OldBlocks) BB->eraseFromParent();` after dropAllReferences(), ordinary CFG
    #    teardown. `\beraseFromParent\s*\(` has no type awareness and refuted it. The corpus could
    #    never show this: fixture folds always declare their types.
    assert ed.recognize_dead_erase(
        "void f(){ for (auto *BB : OldBlocks) BB->eraseFromParent(); }") is None, \
        "a BasicBlock erase must not be mined as a dead-INSTRUCTION erasure"

    # 2) RAUW IS A DEADNESS ARGUMENT -- and a PARTIAL one. VeGen GSLP.cpp balanceReductionTree:
    #    `I->replaceAllUsesWith(NewRoot); I->eraseFromParent();` is the canonical LLVM erase idiom,
    #    almost certainly the commonest in real passes, and every instance was refuted because the
    #    corpus only ever uses `isInstructionTriviallyDead`. RAUW establishes NO LIVE USE; it says
    #    nothing about SIDE EFFECTS (erasing a store after RAUW is still wrong). Half an obligation
    #    is a decline -- never a refutation, and never a proof either.
    body = ("void f(Instruction *I, Value *NewRoot){ I->replaceAllUsesWith(NewRoot); "
            "I->eraseFromParent(); }")
    m = ed.recognize_dead_erase(body)
    assert m and m.get("guard_partial") and not m["trivially_dead"], m
    res = {r["function"]: r for r in ed.verify_source(z3, body)}["f"]
    assert res["status"] == "declined" and res["guard"] == "erase-guard-partial", \
        ("RAUW-then-erase must DECLINE, not refute -- it is the commonest erase idiom in LLVM and "
         "refuting it accuses almost every real pass", res)

    # 3) A VOID RESULT IS THE SAME PARTIAL ARGUMENT. VeGen Scalarizer.cpp visit:
    #    `if (Done && I->getType()->isVoidTy()) I->eraseFromParent();` -- a void instruction
    #    provably has no uses, so no-live-use holds by the type alone; side effects still do not.
    void_body = ("void f(Instruction *I){ if (Done && I->getType()->isVoidTy()) "
                 "I->eraseFromParent(); }")
    res_v = {r["function"]: r for r in ed.verify_source(z3, void_body)}["f"]
    assert res_v["status"] == "declined" and res_v["guard"] == "erase-guard-partial", res_v

    # 4) THE LOOP BRANCH MUST ACCEPT THE GENERAL GUARDS. `_LOOP_RE` is `\bloop\b`, case-insensitive,
    #    so it routes on the mere MENTION of a loop -- in a loop pass, every function. Accepting
    #    only `isDeadLoopInstruction` refuted erases guarded perfectly well by the general API.
    #    VeGen LoopUnrolling.cpp simplifyLoopAfterUnroll2 was refuted while doing exactly
    #    `if (isInstructionTriviallyDead(Inst)) ...erase(Inst);` -- and now PROVES.
    loop_body = ("void simplifyLoopAfterUnroll(Loop *L, Instruction *Inst){ "
                 "if (isInstructionTriviallyDead(Inst)) Inst->eraseFromParent(); }")
    res_l = {r["function"]: r for r in ed.verify_source(z3, loop_body)}["simplifyLoopAfterUnroll"]
    assert res_l["status"] == "proved", \
        ("a trivially-dead instruction is dead whether or not a loop is nearby", res_l)

    # 5) THE TEETH SURVIVE ALL OF IT. Four declines were added above; if they had been bought by
    #    weakening the model, these would have gone quiet. An unguarded erase still refutes, and an
    #    unguarded erase in a LOOP function still refutes on its own branch.
    bad = {r["function"]: r for r in ed.verify_source(
        z3, "void f(Instruction *I){ I->eraseFromParent(); }")}["f"]
    assert bad["status"] == "refuted", ("an unguarded typed erase must still refute", bad)
    bad_loop = {r["function"]: r for r in ed.verify_source(
        z3, "void g(Loop *L, Instruction *I){ I->eraseFromParent(); }")}["g"]
    assert bad_loop["status"] == "refuted", \
        ("and the loop branch must keep ITS teeth -- accepting the general guards must not accept "
         "the absence of one", bad_loop)

    print("dce_real_pass_idioms_fixture OK: a BasicBlock erase is not mined as an instruction "
          "erase; RAUW-then-erase and a void result DECLINE as partial guards rather than accusing "
          "the commonest erase idiom in LLVM; the loop branch accepts the general deadness guards "
          "(a correctly-guarded erase near a loop now PROVES where it was refuted); and both "
          "unguarded shapes still refute, so none of it was bought by pulling the teeth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
