#!/usr/bin/env python3
"""Argument promotion: a whole-MODULE transform verified at the CALLERS, via memory-threaded inlining.

`argpromotion` rewrites an internal callee whose `ptr` parameter is only LOADED into a by-value scalar
parameter, hoisting the load to every call site:

    define internal i32 @g(ptr %p) {          define internal i32 @g(i32 %p.val) {
      %v = load i32, ptr %p          =>          ret i32 %p.val
      ret i32 %v                                }
    }
    ... %r = call i32 @g(ptr %q)               ... %q.val = load i32, ptr %q
                                                   %r = call i32 @g(i32 %q.val)

The callee's SIGNATURE changes, so it cannot be compared before-vs-after directly. But its behavior is
entirely observed through its CALLERS, and a caller's own signature is unchanged. So the obligation is:

  * every survivor whose signature is UNCHANGED (the callers, and untouched functions) is proved a
    refinement over the MEMORY STATE -- with the promoted callee INLINED on both sides (o2t/validate/
    mem_state.py threads memory through the call), so the before-caller inlines the `ptr`-param load and
    the after-caller's hoisted load must compute the identical value / memory; and
  * every survivor whose signature CHANGED (a promoted callee) must have INTERNAL/private linkage --
    not externally observable -- since an out-of-module caller we cannot see would break on the new ABI.

Proved iff all unchanged-signature survivors prove AND every changed-signature survivor is internal.
A promoted callee with external linkage is `refuted` (observable ABI change); a caller that passes the
WRONG value (or a callee whose body was altered) refutes through the memory-state proof; a caller the
fragment cannot model yields a sound `inconclusive` -- never a false whole-module proof. The
load-hoisting shares the memory track's documented null-deref-UB gap: sound where the load already
occurred in the before (a single-BB callee loads unconditionally), which is exactly when the pass fires.
"""

from __future__ import annotations

from o2t.validate.mem_state import _signature, mem_state_tv
from o2t.validate.module_tv import _defined, signature_tv


def argpromotion_tv(z3_bin: str, before_ll: str, after_ll: str, timeout: int = 15) -> dict:
    """Verify an argument-promotion transform (an internal callee's signature changed, its callers
    preserved) at the module level. Returns {promoted, steps, module}. Scope is deliberately narrow:
    it proves the SURVIVING functions with promoted callees inlined -- it does NOT check function
    DELETION or addition (compose that with module_tv when a transform also deletes)."""
    before, after = _defined(before_ll), _defined(after_ll)   # name -> internal?
    survivors = [n for n in before if n in after]
    steps, promoted, refuted, uncertain = [], [], False, False

    for n in survivors:
        if _signature(before_ll, n) != _signature(after_ll, n):     # a promoted / signature-changed callee
            promoted.append(n)
            if not before[n]:                                       # external linkage -> ABI is observable
                steps.append({"function": n, "kind": "promoted", "status": "external-promoted"})
                refuted = True
            else:
                steps.append({"function": n, "kind": "promoted", "status": "internal-inlined"})

    for n in survivors:                                             # callers + untouched functions
        if n in promoted:
            continue
        v = mem_state_tv(z3_bin, before_ll, after_ll, n, timeout=timeout)   # inlines the promoted callee
        if v["status"] == "unsupported":                           # non-memory / non-inlining function
            v = signature_tv(z3_bin, before_ll, after_ll, n, timeout=timeout)
        steps.append({"function": n, "kind": "caller", "status": v["status"]})
        if v["status"] == "refuted":
            refuted = True
        elif v["status"] != "proved":
            uncertain = True

    module = "refuted" if refuted else ("inconclusive" if uncertain else "proved")
    return {"promoted": promoted, "steps": steps, "module": module}
