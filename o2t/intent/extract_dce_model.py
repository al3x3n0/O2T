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


def recognize_dead_erase(body: str):
    """Recover {erases, trivially_dead} for a dead-instruction erasure fold, or None."""
    if not _ERASE_RE.search(body):
        return None
    if _MEMORY_DSE_RE.search(body):
        return None
    if _DEAD_LOOP_GUARD_RE.search(body) or _LOOP_RE.search(body):
        guarded = bool(_DEAD_LOOP_GUARD_RE.search(body))
        return {
            "erases": True,
            "kind": "dead-loop-instruction",
            "marker": DEAD_LOOP_INSTRUCTION_MARKER,
            "dead_loop_instruction": guarded,
        }
    if _ALLOCA_RE.search(body):
        unused = bool(_USE_EMPTY_RE.search(body) or _NEGATED_HAS_USES_OR_MORE_RE.search(body))
        return {
            "erases": True,
            "kind": "unused-alloca",
            "marker": UNUSED_ALLOCA_MARKER,
            "unused_alloca": unused,
        }
    guarded = bool(_GUARD_RE.search(body) or _TRUSTED_DEAD_DELETE_RE.search(body))
    return {
        "erases": True,
        "kind": "dead-instruction",
        "marker": DEAD_INSTRUCTION_MARKER,
        "trivially_dead": guarded,
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
