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

    # 6) THE FOLD-SHAPE CONTRACT, which is the real defect the idiom cases above were symptoms of.
    #    Swept across 126 real LLVM Transforms passes, this miner produced 44 REFUTATIONS -- GVN,
    #    SROA, LoopRotation, ADCE, CodeGenPrepare -- and, worse, 32 PROOFS. Both directions were
    #    unfounded, because it mined ANY function containing an erase. Measured, the populations are
    #    disjoint: O2T's own fixture folds are 1-2 statements, while the real functions it was
    #    mining run 9-79. A mineable fold is BOUNDED (the whole argument fits in one small
    #    function), STRAIGHT-LINE (no worklist, so the deadness premise is local), and
    #    REMOVAL-ONLY (it deletes rather than constructing, so correctness does not rest on emitted
    #    code the model never reads). Each criterion was forced by a specific real false verdict.
    big = ("void f(Instruction *I){ " + "int x0 = 0; " * 8 + "I->eraseFromParent(); }")
    assert ed.recognize_dead_erase(big) is None, \
        ("a large function is not a fold -- its erase is one step of an algorithm, and GVN, SROA "
         "and CodeGenPrepare were all accused on exactly this shape")
    worklist = ("void f(){ while (!DeadInsts.empty()) { Instruction *I = DeadInsts.pop_back_val(); "
                "I->eraseFromParent(); } }")
    assert ed.recognize_dead_erase(worklist) is None, \
        ("an iterating function is not a fold -- SROA::DeleteDeadInstructions pops a worklist whose "
         "deadness was established by its CALLERS, so the premise is not local and refuting it "
         "accuses SROA of a bug that exists only in O2T's field of view")
    rewrite = ("void f(Instruction *I){ IRB.CreateCall3(MemsetFn, A, B, C); I->eraseFromParent(); }")
    assert ed.recognize_dead_erase(rewrite) is None, \
        ("a function that BUILDS then erases is a rewrite, not dead-code elimination -- "
         "ThreadSanitizer::instrumentMemIntrinsic is correct BECAUSE of the call it just emitted, "
         "which this model does not analyse")
    #    ...and the contract must not have swallowed the folds it exists to judge.
    still = {r["function"]: r for r in ed.verify_source(
        z3, "void f(Instruction *I){ I->eraseFromParent(); }")}["f"]
    assert still["status"] == "refuted", ("the fold-shaped unguarded erase must still refute", still)

    print("dce_real_pass_idioms_fixture OK: a BasicBlock erase is not mined as an instruction "
          "erase; RAUW-then-erase and a void result DECLINE as partial guards rather than accusing "
          "the commonest erase idiom in LLVM; the loop branch accepts the general deadness guards "
          "(a correctly-guarded erase near a loop now PROVES where it was refuted); and both "
          "unguarded shapes still refute, so none of it was bought by pulling the teeth. The "
          "fold-shape contract is pinned too -- bounded, straight-line, removal-only -- which took "
          "44 false refutations AND 32 false proofs against real LLVM passes down to 0 and 2, the "
          "two survivors being genuine guarded erasures in LoopIdiomRecognize and SimplifyCFG")
    return 0


if __name__ == "__main__":
    sys.exit(main())
