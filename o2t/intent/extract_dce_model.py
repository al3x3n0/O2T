#!/usr/bin/env python3
"""Recover DCE dead-instruction erasures from pass SOURCE and discharge them.

This miner recognizes instruction-deletion folds and checks that the pass establishes an
auditable trivially-dead guard (`isInstructionTriviallyDead` or
`wouldInstructionBeTriviallyDead`). Guarded erasures prove; bare `eraseFromParent`-style
deletions are refuted because the erased instruction may still have a live use or side effect.
"""

from __future__ import annotations

import re

from o2t.mine.pass_scev import FUNC_RE, KEYWORDS, strip_comments
from o2t.validate import dce_model as dce

_ERASE_RE = re.compile(
    r"\beraseFromParent\s*\(|\bdeleteDeadInstruction\s*\(|"
    r"\bRecursivelyDeleteTriviallyDeadInstructions\s*\("
)
_GUARD_RE = re.compile(
    r"\bisInstructionTriviallyDead\s*\(|\bwouldInstructionBeTriviallyDead\s*\("
)
_DEAD_LOOP_GUARD_RE = re.compile(r"\bisDeadLoopInstruction\s*\(")
_TRUSTED_DEAD_DELETE_RE = re.compile(
    r"\bdeleteDeadInstruction\s*\(|\bRecursivelyDeleteTriviallyDeadInstructions\s*\("
)
_MEMORY_DSE_RE = re.compile(
    r"\bStoreInst\b|\bisOverwrite\b|\bfullyOverwrites\b|\bnoIntervening(?:Read|Store)\b|"
    r"\bMemorySSA\b|\bMemoryDef\b|\bMemoryLocation\b"
)
_ALLOCA_RE = re.compile(r"\bAllocaInst\b|\balloca\b", re.I)
_LOOP_RE = re.compile(r"\bLoop\b|\bloop\b", re.I)
_USE_EMPTY_RE = re.compile(
    r"\buse_empty\s*\(|\buser_empty\s*\(|\bhasNUses\s*\(\s*0\s*\)|"
    r"\busers\s*\(\s*\)\s*\.\s*empty\s*\("
)
_NEGATED_HAS_USES_OR_MORE_RE = re.compile(
    r"!\s*(?:\(\s*)?(?:[A-Za-z_]\w*\s*(?:->|\.)\s*)?hasNUsesOrMore\s*\(\s*1\s*\)"
)

DEAD_INSTRUCTION_MARKER = "probe.dce.dead-instruction"
DEAD_LOOP_INSTRUCTION_MARKER = "probe.dce.dead-loop-instruction"
UNUSED_ALLOCA_MARKER = "probe.cleanup.unused-alloca"


def split_function_texts(source_text: str) -> dict[str, str]:
    """Return name -> signature+body text for function-level source mining."""
    src = strip_comments(source_text)
    funcs: dict[str, str] = {}
    pos = 0
    for match in FUNC_RE.finditer(src):
        if match.start() < pos or match.group(1) in KEYWORDS:
            continue
        depth = 1
        index = match.end()
        while index < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[index], 0)
            index += 1
        funcs[match.group(1)] = src[match.start():index]
        pos = index
    return funcs


# WHAT IS BEING ERASED, and UNDER WHAT CONDITION. Both were unmodeled, and both produced FALSE
# REFUTATIONS the first time this miner met real third-party code (VeGen's GSLP vectorizer):
#
#   VectorPackSet.cpp:1060   for (auto *BB : OldBlocks) BB->eraseFromParent();
#       A BASIC BLOCK erase, after dropAllReferences() on every instruction in it -- ordinary CFG
#       teardown with nothing to do with dead-instruction elimination. The regex is just
#       `\beraseFromParent\s*\(`, so it read a block erase as an instruction erase and refuted it.
#       A category error, not a weak guard.
#
#   VectorPackSet.cpp:712    if (m_Intrinsic<lifetime_start>(m_Value()).match(I) || ...)
#                              I->eraseFromParent();
#       Erasing lifetime/noalias-scope intrinsics deliberately. The legitimacy argument is the
#       INTRINSIC KIND, not `isInstructionTriviallyDead`, and the model cannot express it. Dropping
#       those loses stack-colouring and alias information -- conservative, not a miscompile.
#
# The rule this project already applies twice (cascade `guard-unmodeled`, `opaque-const-expr`): a
# premise the model cannot express must produce a DECLINE, never a refutation. So an erase whose
# receiver is not evidently an `Instruction` is not mined at all, and one guarded by a condition
# outside the model is mined but marked `guard_unmodeled` so the discharge declines instead of
# accusing.
# Pointer OR REFERENCE: `void eraseUserEmptyAlloca(AllocaInst &AI)` is as much an instruction
# erasure as the pointer form, and requiring `*` silently dropped two sound folds from the fixture
# corpus -- a filter meant to stop false REFUTATIONS quietly costing real PROOFS instead.
_INSTRUCTION_EVIDENCE_RE = re.compile(
    r"\b(?:Instruction|Inst|StoreInst|LoadInst|CallInst|AllocaInst|PHINode|BinaryOperator|"
    r"CastInst|CmpInst|SelectInst|GetElementPtrInst|IntrinsicInst)\s*[*&]|"
    r"\b(?:dyn_cast|cast|isa)<\s*(?:Instruction|AllocaInst|PHINode|IntrinsicInst)\b|"
    # The DCE guard and deleter APIs are themselves Instruction-typed, so calling one on the
    # receiver establishes what it is just as firmly as a declaration does.
    r"\bisInstructionTriviallyDead\s*\(|\bwouldInstructionBeTriviallyDead\s*\(|"
    r"\bisDeadLoopInstruction\s*\(|\bdeleteDeadInstruction\s*\(|"
    r"\bRecursivelyDeleteTriviallyDeadInstructions\s*\("
)
_UNMODELED_ERASE_GUARD_RE = re.compile(r"\bm_Intrinsic\s*<|\bgetIntrinsicID\s*\(")
# `X->replaceAllUsesWith(Y); X->eraseFromParent();` is the canonical LLVM erase idiom and almost
# certainly the most common one in real passes -- after RAUW the instruction provably has no uses.
# The fixture corpus does not contain it (fixture folds reach for `isInstructionTriviallyDead`), so
# every real pass using it was REFUTED: VeGen's GSLP.cpp balanceReductionTree, Scalarizer.cpp visit.
#
# It is a PARTIAL guard, and that is what decides the verdict. RAUW establishes NO LIVE USE and says
# nothing about SIDE EFFECTS -- erasing a store or a call after RAUW is still wrong. One premise
# established and one unestablished is not a proof, and it is not a refutation either: it is a
# decline, the same shape as `guard-unmodeled`.
# The same partial-guard shape, reached two other ways in real code:
#   `if (Done && I->getType()->isVoidTy()) I->eraseFromParent();`   (VeGen Scalarizer.cpp visit)
# A VOID instruction provably has no uses, so no-live-use is established by the type alone -- but
# side-effect freedom still is not (a store is void). Every member of this family establishes half
# the obligation, which is a decline, never a refutation and never a proof.
_PARTIAL_ERASE_GUARD_RE = re.compile(
    r"\breplaceAllUsesWith\s*\(|\bisVoidTy\s*\(|\breplaceUsesOfWith\s*\(|"
    r"\breplaceAllUsesInside\s*\("
)


# A DCE FOLD IS A FUNCTION WHOSE BODY *IS* THE ERASURE. That is the contract this model was built
# for, and it was never checked -- any function containing an erase anywhere was mined as a fold.
#
# The two populations are disjoint, measured over 126 real LLVM Transforms passes against O2T's own
# fixtures:
#
#     fixture folds        1-2 statements   (median 1, max 2)
#     real LLVM functions  9-79 statements  (median ~30)
#
# Real passes simply do not contain 2-statement erasure folds; their erases are one step inside an
# algorithm. Mining those produced 44 REFUTATIONS against GVN, SROA, LoopRotation, ADCE and
# CodeGenPrepare -- and, worse, 32 PROOFS, because `guarded` is "does isInstructionTriviallyDead
# appear anywhere in the body" and in a 74-statement function an unrelated call elsewhere makes the
# erase look guarded. Confident verdicts in both directions about functions the model cannot read.
#
# The threshold is not tuned to make those 44 disappear: it sits in the empty gap between 2 and 9,
# and either population would have to change shape entirely to reach it. Larger bodies are declined
# as `not-a-transform`, which is the honest answer -- O2T's source-mining contract is fold-shaped
# code, and declining to speak about `SROA::run` is correct where accusing it is not.
_MAX_FOLD_STATEMENTS = 6
# ...AND STRAIGHT-LINE. A fold tests a guard and erases ONE instruction; a loop means the function
# is walking a data structure, and then the deadness premise lives in whatever filled that
# structure -- a worklist, or the caller -- which this model never reads. `SROA::DeleteDeadInstructions`
# is the clean example: it pops from `DeadInsts` and erases, and everything on that worklist was
# established dead ELSEWHERE in SROA. From this function's text the erase is unguarded, so it was
# refuted; the premise is simply not local. Measured: every one of O2T's 16 fixture folds is
# loop-free and 61-127 chars, while the three real-pass functions that survived the statement
# threshold are 445-753 chars and all iterate.
_FOLD_LOOP_RE = re.compile(r"\b(?:for|while)\s*\(")
# ...AND IT REMOVES RATHER THAN CONSTRUCTS. A dead-instruction erasure deletes; it does not build
# replacement IR. When a body emits code and then erases, the erase is part of a REWRITE whose
# correctness rests on the emitted code being equivalent -- an argument this model never reads.
# ThreadSanitizer's `instrumentMemIntrinsic` is the case: it emits `IRB.CreateCall3(MemsetFn, ...)`
# and erases the original memset, which is correct precisely BECAUSE of the call it just built.
# Refuting it for lacking `isInstructionTriviallyDead` misreads an instrumentation rewrite as
# failed dead-code elimination.
_FOLD_BUILDS_IR_RE = re.compile(
    r"\b(?:IRB|IRBuilder|Builder|B)\w*\s*(?:\.|->)\s*Create\w+|\bnew\s+\w*Inst\b|"
    r"\bCreateCall\d?\s*\("
)


def recognize_dead_erase(body: str):
    """Recover {erases, trivially_dead} for a dead-instruction erasure fold, or None."""
    if not _ERASE_RE.search(body):
        return None
    if (body.count(";") > _MAX_FOLD_STATEMENTS or _FOLD_LOOP_RE.search(body)
            or _FOLD_BUILDS_IR_RE.search(body)):
        return None
    if _MEMORY_DSE_RE.search(body):
        return None
    if not _INSTRUCTION_EVIDENCE_RE.search(body):
        # Nothing here establishes that an INSTRUCTION is what gets erased. A BasicBlock, Function or
        # GlobalVariable erase is not a dead-instruction fold, and guessing from the call alone is
        # how a CFG teardown loop came back `refuted`.
        return None
    if _DEAD_LOOP_GUARD_RE.search(body) or _LOOP_RE.search(body):
        # `_LOOP_RE` is `\bloop\b`, case-insensitive: it routes here on the mere MENTION of a loop,
        # which in a loop pass is every function. Accepting only `isDeadLoopInstruction` then
        # refuted erases guarded perfectly well by the general API -- VeGen's
        # LoopUnrolling.cpp simplifyLoopAfterUnroll2 does
        #     Inst->replaceAllUsesWith(V);
        #     if (isInstructionTriviallyDead(Inst)) BB->getInstList().erase(Inst);
        # and also calls RecursivelyDeleteTriviallyDeadInstructions. Both are guards this file
        # already recognises everywhere else; only the loop branch refused them. A trivially-dead
        # instruction is dead whether or not a loop is nearby, so the general guards count here too.
        guarded = bool(_DEAD_LOOP_GUARD_RE.search(body) or _GUARD_RE.search(body)
                       or _TRUSTED_DEAD_DELETE_RE.search(body))
        return {
            "erases": True,
            "kind": "dead-loop-instruction",
            "marker": DEAD_LOOP_INSTRUCTION_MARKER,
            "dead_loop_instruction": guarded,
            "guard_unmodeled": (not guarded) and bool(_UNMODELED_ERASE_GUARD_RE.search(body)),
        }
    if _ALLOCA_RE.search(body):
        unused = bool(_USE_EMPTY_RE.search(body) or _NEGATED_HAS_USES_OR_MORE_RE.search(body))
        return {
            "erases": True,
            "kind": "unused-alloca",
            "marker": UNUSED_ALLOCA_MARKER,
            "unused_alloca": unused,
            "guard_unmodeled": (not unused) and bool(_UNMODELED_ERASE_GUARD_RE.search(body)),
        }
    guarded = bool(_GUARD_RE.search(body) or _TRUSTED_DEAD_DELETE_RE.search(body))
    return {
        "erases": True,
        "kind": "dead-instruction",
        "marker": DEAD_INSTRUCTION_MARKER,
        "trivially_dead": guarded,
        # An erase gated on the intrinsic KIND is legitimate for a reason this model has no way to
        # state. Unmodeled premise => the discharge must decline, never refute.
        "guard_unmodeled": (not guarded) and bool(_UNMODELED_ERASE_GUARD_RE.search(body)),
        # RAUW: no-live-use established, side-effect freedom NOT. Partial premise => decline.
        "guard_partial": (not guarded) and bool(_PARTIAL_ERASE_GUARD_RE.search(body)),
    }


def verify_source(z3_bin: str, source_text: str):
    """Mine each instruction-erasure fold and discharge it.

    Per-function verdicts are: proved | refuted | not-a-transform.
    """
    results = []
    for name, body in split_function_texts(source_text).items():
        model = recognize_dead_erase(body)
        if model is None:
            results.append({"function": name, "status": "not-a-transform"})
            continue
        entry = {"function": name, "kind": model["kind"], "marker": model["marker"]}
        if model.get("guard_partial") and not model.get("guard_unmodeled"):
            entry.update({
                "status": "declined",
                "guard": "erase-guard-partial",
                "reason": "no-live-use is established (replaceAllUsesWith, or a void result type) "
                          "but side-effect freedom is not -- a partial premise may not refute",
            })
            results.append(entry)
            continue
        if model.get("guard_unmodeled"):
            # Applies whichever shape the body fell into. `emitLoop` in VeGen's VectorPackSet.cpp is
            # a 100-line code-generation routine that merely MENTIONS allocas, so it took the
            # unused-alloca branch and was refuted for lacking a `use_empty()` check it was never
            # going to have -- while its actual erase is gated on the intrinsic kind. On a real pass
            # an erase inside a large routine is usually incidental, and assuming otherwise accuses
            # working code.
            entry.update({
                "status": "declined",
                "guard": "erase-guard-unmodeled",
                "reason": "erase is gated on a condition this model cannot express "
                          "(intrinsic kind); an incomplete premise may not refute",
            })
            results.append(entry)
            continue
        if model["kind"] == "unused-alloca":
            guarded = bool(model["unused_alloca"])
            status, info = dce.prove_unused_alloca_erase(
                z3_bin,
                no_uses=guarded,
                no_escape=guarded,
                no_lifetime_effect=guarded,
            )
            entry.update({
                "unused_alloca": guarded,
                "reason": "use-empty" if guarded else "missing-use-empty-guard",
            })
        elif model["kind"] == "dead-loop-instruction":
            guarded = bool(model["dead_loop_instruction"])
            status, info = dce.prove_dead_loop_instruction_erase(
                z3_bin,
                no_loop_result_use=guarded,
                no_loop_control_effect=guarded,
                no_loop_side_effect=guarded,
            )
            entry.update({
                "dead_loop_instruction": guarded,
                "reason": "dead-loop-instruction" if guarded else "missing-dead-loop-guard",
            })
        else:
            guarded = bool(model["trivially_dead"])
            status, info = dce.prove_dead_erase(
                z3_bin,
                no_live_use=guarded,
                no_side_effect=guarded,
            )
            entry.update({
                "trivially_dead": guarded,
                "reason": "trivially-dead" if guarded else "missing-trivially-dead-guard",
            })
        entry["status"] = status
        if status == "refuted":
            entry["witness"] = bool(info.get("model"))
        results.append(entry)
    return results
