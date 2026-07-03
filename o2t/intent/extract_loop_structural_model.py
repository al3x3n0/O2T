#!/usr/bin/env python3
"""Recover LICM-style hoist folds from pass SOURCE and discharge them (deep loop-structural model).

The deep model (`validate/loop_structural_model.py`) proves the CANONICAL hoist obligations. This
lifts them from a pass's C++: it finds each fold that hoists an instruction out of a loop
(`hoist...`, `moveBefore...Preheader`, `makeLoopInvariant`) and recovers the legality it
establishes:

  * `isLoopInvariant` / `makeLoopInvariant`        -> operands are loop-invariant (no stale value);
  * `isSafeToSpeculativelyExecute`                 -> the op cannot trap;
  * `isGuaranteedToExecute` / `mustExecute` / dominates-all-exits -> the op already ran each trip.

A fold is discharged as:

  * NOT invariant-guarded                          -> REFUTED (a stale value may be hoisted);
  * invariant but NEITHER speculatable NOR guaranteed -> REFUTED (a trapping op hoisted past a
    guard / out of a maybe-zero-trip loop introduces a new trap);
  * invariant AND (speculatable OR guaranteed)     -> proved.

So a fold that hoists on loop-invariance alone is refuted from its source with a witness.
"""

from __future__ import annotations

import re

from o2t.mine.pass_scev import split_functions
from o2t.validate import loop_structural_model as ls

# the fold hoists an instruction out of the loop.
_HOISTS_RE = re.compile(r"\bhoist\w*\s*\(|\bmoveBefore\w*\b|\bmakeLoopInvariant\b|\bToPreheader\b")
_INVARIANT_RE = re.compile(r"\bisLoopInvariant\b|\bmakeLoopInvariant\b|\bhasLoopInvariantOperands\b")
_SPECULATABLE_RE = re.compile(r"\bisSafeToSpeculativelyExecute\b|\bisSpeculatable\b")
_GUARANTEED_RE = re.compile(r"\bisGuaranteedToExecute\b|\bmustExecute\b|\bisDereferenceable\b"
                            r"|\bdominatesAllExits\b|\bmustExecuteInLoop\b")


def recognize_hoist_fold(body):
    """Recover {hoists, invariant, speculatable, guaranteed} for a hoist fold, or None."""
    if not _HOISTS_RE.search(body):
        return None
    return {"hoists": True,
            "invariant": bool(_INVARIANT_RE.search(body)),
            "speculatable": bool(_SPECULATABLE_RE.search(body)),
            "guaranteed": bool(_GUARANTEED_RE.search(body))}


def verify_source(z3_bin, source_text):
    """Mine each hoist fold and discharge it. Per-function verdicts:
    proved | refuted | not-a-transform."""
    results = []
    for name, body in split_functions(source_text).items():
        m = recognize_hoist_fold(body)
        if m is None:
            results.append({"function": name, "status": "not-a-transform"})
            continue
        entry = {"function": name, "invariant": m["invariant"],
                 "speculatable": m["speculatable"], "guaranteed": m["guaranteed"]}
        if not m["invariant"]:
            status, info = ls.prove_hoist_invariance(z3_bin, invariant=False)
            entry["reason"] = "operand-may-not-be-loop-invariant"
        elif not (m["speculatable"] or m["guaranteed"]):
            status, info = ls.prove_hoist_safety(z3_bin, guaranteed=False, speculatable=False)
            entry["reason"] = "trapping-op-not-guaranteed-or-speculatable"
        else:
            # invariant established; pick whichever safety fact the fold proved.
            status, info = ls.prove_hoist_safety(
                z3_bin, guaranteed=m["guaranteed"], speculatable=m["speculatable"])
            entry["reason"] = "invariant-and-safe"
        entry["status"] = status
        if status == "refuted":
            entry["witness"] = bool(info.get("model"))
        results.append(entry)
    return results
