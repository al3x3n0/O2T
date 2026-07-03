#!/usr/bin/env python3
"""Recover GlobalOpt dead-initializer folds from pass SOURCE and discharge them (deep model).

The deep model (`validate/globalopt_model.py`) proves the SEMANTIC obligation of defaulting a
global's initializer to null. This lifts that obligation from a pass's C++: it finds each fold
that defaults an initializer (`setInitializer(... getNullValue ...)` / `zeroinitializer`) and
recovers the AUDITABLE legality facts the fold establishes:

  * `hasLocalLinkage` / internal linkage  -> the initializer is not observable by external code;
  * `use_empty` (no uses)                 -> no load observes the initial value.

`isGlobalInitializerDead` is treated as an OPAQUE upstream predicate (the very claim we are
re-checking), so it does NOT by itself establish unobservability. A fold is discharged as:

  * internal AND use-empty            -> proved (the real `hasLocalLinkage() && use_empty()` guard);
  * NOT internal (external visible)   -> REFUTED -- an external reader observes the initializer;
  * internal but NOT use-empty        -> REFUTED -- a load may observe the initializer before any
    store (read-before-store).

So a fold that defaults the initializer guarded only by `isGlobalInitializerDead` is refuted
from its source with a concrete `init != 0` witness.
"""

from __future__ import annotations

import re

from o2t.mine.pass_scev import split_functions
from o2t.validate import globalopt_model as g

# the fold defaults a global's initializer to the null/zero value.
_DEFAULTS_INIT_RE = re.compile(r"setInitializer\s*\([^;]*(?:getNullValue|zeroinitializer|ConstantAggregateZero)")
# auditable legality facts.
_LOCAL_LINKAGE_RE = re.compile(r"\bhasLocalLinkage\b|\bhasInternalLinkage\b|\bisLocalLinkage\b")
_USE_EMPTY_RE = re.compile(r"\buse_empty\b|\bhasNUses\s*\(\s*0\s*\)|\buser_empty\b")


def recognize_initializer_default(body):
    """Recover {defaults_init, local_linkage, use_empty} for an initializer-defaulting fold, or
    None if the fold does not default a global initializer."""
    if not _DEFAULTS_INIT_RE.search(body):
        return None
    return {"defaults_init": True,
            "local_linkage": bool(_LOCAL_LINKAGE_RE.search(body)),
            "use_empty": bool(_USE_EMPTY_RE.search(body))}


def verify_source(z3_bin, source_text):
    """Mine each initializer-defaulting fold and discharge it. Per-function verdicts:
    proved | refuted | not-a-transform."""
    results = []
    for name, body in split_functions(source_text).items():
        m = recognize_initializer_default(body)
        if m is None:
            results.append({"function": name, "status": "not-a-transform"})
            continue
        entry = {"function": name, "local_linkage": m["local_linkage"],
                 "use_empty": m["use_empty"]}
        if not m["local_linkage"]:
            status, info = g.prove_initializer_default(z3_bin, [], external=True)
            entry["reason"] = "external-linkage-observable"
        elif not m["use_empty"]:
            status, info = g.prove_initializer_default(
                z3_bin, [("load",), ("store", None)], external=False)
            entry["reason"] = "load-may-observe-initializer"
        else:
            status, info = g.prove_initializer_default(z3_bin, [], external=False)
            entry["reason"] = "internal-and-use-empty"
        entry["status"] = status
        if status == "refuted":
            entry["witness"] = bool(info.get("model"))
        results.append(entry)
    return results
