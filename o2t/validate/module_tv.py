#!/usr/bin/env python3
"""Module-level composition: verify a whole-MODULE transform, including function deletion.

Whole-function TV and pipeline composition are per-function. A module pass (globaldce, deadargelim,
globalopt, IPO) also DELETES, adds, or re-links functions -- effects no per-function proof sees. This
lifts TV to the module: a module transform `M -> M'` is a refinement iff

  * every SURVIVING function (defined in both) has its transform proved a refinement, AND
  * every DELETED function was provably DEAD -- it had internal/private linkage (not externally
    observable) AND is not referenced anywhere in M' (nothing live still needs it).

Deleting an EXTERNALLY-visible function, or one still referenced after, is `refuted` (observable
behavior may change / the result dangles). A survivor the scalar fragment cannot model, or a deleted
internal function still referenced only by other deleted functions (an unproven dead cluster), yields a
sound `inconclusive` -- never a false whole-module proof. Added functions are reported. Scope: soundness
of deletion + surviving-function refinement; signature changes and IPO value-flow are not yet modeled.
"""

from __future__ import annotations

import re

from o2t.validate import scalar_ir as si

_DEFINE_RE = re.compile(r"^define\s+(.*?)@([\w.$]+)\s*\(", re.M)


def _defined(ll_text: str) -> dict:
    """Defined functions -> True if internal/private linkage (not externally observable)."""
    out = {}
    for m in _DEFINE_RE.finditer(ll_text):
        attrs, name = m.group(1), m.group(2)
        out[name] = bool(re.search(r"\b(internal|private)\b", attrs))
    return out


def _referenced(name: str, ll_text: str) -> bool:
    """Is `@name` referenced (called or address-taken) anywhere in `ll_text`, other than its own
    `define` header? A reference in the AFTER module means the function is still live."""
    for m in re.finditer(rf"@{re.escape(name)}\b", ll_text):
        line = ll_text[ll_text.rfind("\n", 0, m.start()) + 1: ll_text.find("\n", m.start())]
        if not line.lstrip().startswith("define"):
            return True
    return False


def module_tv(z3_bin: str, before_ll: str, after_ll: str, timeout: int = 15) -> dict:
    """Verify a whole-module transform. Returns {survivors, deleted, added, steps, module}."""
    before, after = _defined(before_ll), _defined(after_ll)
    survivors = [n for n in before if n in after]
    deleted = [n for n in before if n not in after]
    added = [n for n in after if n not in before]
    steps, refuted, uncertain = [], False, False

    for n in survivors:
        v = si.validate_transform(z3_bin, before_ll, after_ll, n, timeout=timeout)
        steps.append({"function": n, "kind": "survivor", "status": v["status"]})
        if v["status"] == "refuted":
            refuted = True
        elif v["status"] != "proved":
            uncertain = True                              # unsupported/timeout -> can't complete

    for n in deleted:
        if not before[n]:                                 # external linkage: observable -> unsound to drop
            status = "external-removed"; refuted = True
        elif _referenced(n, after_ll):                    # still referenced after -> live, dangling
            status = "live-removed"; refuted = True
        else:
            status = "dead-removed"                        # internal + unreferenced -> sound removal
        steps.append({"function": n, "kind": "deleted", "status": status})

    module = "refuted" if refuted else ("inconclusive" if uncertain else "proved")
    return {"survivors": survivors, "deleted": deleted, "added": added, "steps": steps, "module": module}
