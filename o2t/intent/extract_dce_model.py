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


def recognize_dead_erase(body: str):
    """Recover {erases, trivially_dead} for a dead-instruction erasure fold, or None."""
    if not _ERASE_RE.search(body):
        return None
    if _MEMORY_DSE_RE.search(body):
        return None
    if not _INSTRUCTION_EVIDENCE_RE.search(body):
        # Nothing here establishes that an INSTRUCTION is what gets erased. A BasicBlock, Function or
        # GlobalVariable erase is not a dead-instruction fold, and guessing from the call alone is
        # how a CFG teardown loop came back `refuted`.
        return None
    if _DEAD_LOOP_GUARD_RE.search(body) or _LOOP_RE.search(body):
        guarded = bool(_DEAD_LOOP_GUARD_RE.search(body))
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
