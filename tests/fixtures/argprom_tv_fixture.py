#!/usr/bin/env python3
"""Track B's last composition-axis edges: NON-SCALAR (pointer/void) callees and ARGUMENT PROMOTION.

Both reduce to one capability -- memory-threaded interprocedural inlining (o2t/validate/mem_state.py):
a `call` to a defined callee is inlined THROUGH the SMT memory array, pointer arguments bound to the
caller's addresses and scalar arguments to terms. Then:

  * NON-SCALAR CALLEE -- a caller of a `void`/pointer callee that stores through a pointer argument is
    translatable; a store-then-load-forward proves, and a callee that stores the WRONG value REFUTES;
  * ARGUMENT PROMOTION (o2t/validate/argprom_tv.py) -- a whole-module transform proved at the CALLERS:
    the before-caller inlines the callee's `ptr`-param load, the after-caller's hoisted load must
    compute the identical value. Proved against REAL `opt -passes=argpromotion` output; a caller
    passing the WRONG value REFUTES, and promoting an EXTERNAL (observable-ABI) callee REFUTES.
Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.mem_state import mem_state_tv  # noqa: E402
from o2t.validate.argprom_tv import argpromotion_tv  # noqa: E402

# A void, pointer-taking callee (a NON-SCALAR callee) that writes through its pointer argument.
NS_BEFORE = ("define void @writes(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  ret void\n}\n"
             "define i32 @caller(ptr %q, i32 %y) {\n  call void @writes(ptr %q, i32 %y)\n"
             "  %v = load i32, ptr %q\n  ret i32 %v\n}\n")
NS_FOLDED = ("define void @writes(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  ret void\n}\n"
             "define i32 @caller(ptr %q, i32 %y) {\n  call void @writes(ptr %q, i32 %y)\n"
             "  ret i32 %y\n}\n")

# Argument-promotion input: an internal callee whose pointer parameter is only loaded.
AP_BEFORE = ("define internal i32 @g(ptr %p) {\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n"
             "define i32 @caller(ptr %q) {\n  %r = call i32 @g(ptr %q)\n  ret i32 %r\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("argprom_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. NON-SCALAR (pointer/void) callee: the call threads memory, so store-through-pointer then
    #    load-forward proves the loaded value equals the stored one.
    assert mem_state_tv(z3, NS_BEFORE, NS_FOLDED, "caller")["status"] == "proved", \
        "void/pointer callee: store-through-ptr forwards"
    #    TEETH -- a callee that stores the WRONG value (x+1) breaks the forward -> refuted.
    ns_bad = NS_BEFORE.replace("store i32 %x, ptr %p",
                               "%x1 = add i32 %x, 1\n  store i32 %x1, ptr %p")
    v = mem_state_tv(z3, ns_bad, NS_FOLDED, "caller")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong pointer-callee store must refute", v)

    # 2. ARGUMENT PROMOTION against REAL opt output: opt rewrites @g(ptr) -> @g(i32) and hoists the
    #    load to the caller. The module transform is proved at the caller, with @g inlined on both
    #    sides -- the before's internal load and the after's hoisted load compute the same value.
    ap_after = si.run_passes(AP_BEFORE, "argpromotion", opt)
    assert "i32 @g(i32" in ap_after, ("opt did not promote the argument", ap_after)
    r = argpromotion_tv(z3, AP_BEFORE, ap_after)
    assert r["module"] == "proved", ("real argpromotion output must prove", r)
    assert "g" in r["promoted"], ("g must be recognized as the promoted callee", r)

    # 3. TEETH -- a caller that passes the WRONG hoisted value (q.val + 1, not q.val) REFUTES.
    ap_bad = ("define internal i32 @g(i32 %p.val) {\n  ret i32 %p.val\n}\n"
              "define i32 @caller(ptr %q) {\n  %q.val = load i32, ptr %q\n"
              "  %bad = add i32 %q.val, 1\n  %r = call i32 @g(i32 %bad)\n  ret i32 %r\n}\n")
    assert argpromotion_tv(z3, AP_BEFORE, ap_bad)["module"] == "refuted", \
        "a wrong hoisted call value must refute"

    # 4. TEETH -- promoting an EXTERNAL (observable-ABI) callee must REFUTE: an out-of-module caller
    #    we cannot see would break on the new signature.
    ap_ext_before = AP_BEFORE.replace("internal i32 @g", "i32 @g")
    ap_ext_after = ap_after.replace("internal i32 @g", "i32 @g")
    assert argpromotion_tv(z3, ap_ext_before, ap_ext_after)["module"] == "refuted", \
        "promoting an externally-visible callee must refute"

    # 5. ALIASING through a callee store (array theory, exact): @w stores through %a; a caller that
    #    then loads %b and claims the result equals the stored value is UNSOUND when a aliases b, so
    #    it must REFUTE -- not decline. (Regression: signature reading must anchor on the callee's
    #    `define`, not a forward-reference call site, or the callee params are misread and this
    #    silently declines instead of refuting.)
    al = ("define i32 @f(ptr %a, ptr %b, i32 %x) {\n  call void @w(ptr %a, i32 %x)\n"
          "  %v = load i32, ptr %b\n  ret i32 %v\n}\n"
          "define void @w(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  ret void\n}\n")
    al_bad = al.replace("%v = load i32, ptr %b\n  ret i32 %v", "ret i32 %x")
    v = mem_state_tv(z3, al, al_bad, "f")
    assert v["status"] == "refuted" and v.get("witness"), ("alias-unsound callee store must refute", v)

    # 6. FORWARD REFERENCE (order independence): the CALLER is defined BEFORE the callee, so the only
    #    `@w(...)` above the definition is the call site. The signature reader must still find @w's
    #    real parameters -- the store-forward must PROVE, and a wrong version REFUTE, never decline.
    fwd = ("define i32 @caller(ptr %q, i32 %y) {\n  call void @w(ptr %q, i32 %y)\n"
           "  %v = load i32, ptr %q\n  ret i32 %v\n}\n"
           "define void @w(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  ret void\n}\n")
    fwd_ok = fwd.replace("%v = load i32, ptr %q\n  ret i32 %v", "ret i32 %y")
    assert mem_state_tv(z3, fwd, fwd_ok, "caller")["status"] == "proved", \
        "forward-referenced callee must translate, not decline"
    fwd_bad = fwd.replace("%v = load i32, ptr %q\n  ret i32 %v", "%z = add i32 %y, 1\n  ret i32 %z")
    assert mem_state_tv(z3, fwd, fwd_bad, "caller")["status"] == "refuted", \
        "a wrong forward-referenced transform must refute"

    print("argprom_tv_fixture OK: memory-threaded interprocedural inlining closes Track B's last two "
          "edges -- NON-SCALAR (void/pointer) callees (store-through-ptr forwards; a wrong store "
          "REFUTES) and ARGUMENT PROMOTION proved at the callers against REAL opt output (@g(ptr)->"
          "@g(i32) with the load hoisted; a wrong hoisted value REFUTES, an external-ABI promotion "
          "REFUTES). The composition axis -- pipeline, module, interproc, signature, promotion -- closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
