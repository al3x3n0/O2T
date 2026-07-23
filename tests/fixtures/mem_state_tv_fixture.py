#!/usr/bin/env python3
"""Pointer-side-effect memory: whole-function TV over the MEMORY STATE (SMT theory of arrays).

Functions that write to pointer ARGUMENTS have observable memory side effects no return-value proof
sees. o2t/validate/mem_state.py models memory as an SMT array (word-addressed by an opaque 64-bit
pointer); a transform is a refinement iff the return value AND the final memory state agree for all
initial memories and arguments. The array theory models ALIASING PRECISELY -- no alias analysis needed.

  * DSE removing a dead (overwritten) store PROVES (the final memory is unchanged);
  * TEETH -- dropping a LIVE store, or storing the wrong value, REFUTES (the memory state differs);
  * ALIASING -- a `store %x, ptr %p; load ptr %q` where p,q may alias: claiming the load equals %x is
    REFUTED (unsound when p != q), while a same-pointer store/load PROVES. The theory of arrays gets
    may-alias exactly right.
Scope: single-BB, i32 word store/load to opaque pointer arguments; pointer validity / null-deref UB is
not modeled, but ENFORCED via a new-dereference guard -- a transform whose target dereferences an
address the source does not is DECLINED, never proved. Needs z3 + opt 18.
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

DSE = ("define void @f(ptr %p, i32 %x) {\n"
       "  store i32 1, ptr %p\n  store i32 %x, ptr %p\n  ret void\n}\n")   # 1st store is dead
ALIAS = ("define i32 @g(ptr %p, ptr %q, i32 %x) {\n"
         "  store i32 %x, ptr %p\n  %v = load i32, ptr %q\n  ret i32 %v\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("mem_state_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. DSE removes the dead first store; the final memory state is unchanged -> proved.
    after = si.run_passes(DSE, "dse", opt)
    assert after is not None
    assert mem_state_tv(z3, DSE, after, "f")["status"] == "proved", "DSE of a dead store must prove"

    # 2. TEETH -- dropping the LIVE (surviving) store leaves the wrong memory -> refuted.
    drop_live = "define void @f(ptr %p, i32 %x) {\n  store i32 1, ptr %p\n  ret void\n}\n"
    assert mem_state_tv(z3, DSE, drop_live, "f")["status"] == "refuted", "dropping a live store must refute"

    # 3. TEETH -- storing a wrong value (x+1 instead of x) -> refuted.
    wrong_val = ("define void @f(ptr %p, i32 %x) {\n  %y = add i32 %x, 1\n"
                 "  store i32 %y, ptr %p\n  ret void\n}\n")
    assert mem_state_tv(z3, DSE, wrong_val, "f")["status"] == "refuted", "wrong stored value must refute"

    # 4. ALIASING (the array-theory highlight): store to %p then load %q. Claiming the load returns %x
    #    is UNSOUND when p != q -> refuted; a same-pointer store/load (load %p) is proved.
    alias_bad = ("define i32 @g(ptr %p, ptr %q, i32 %x) {\n  store i32 %x, ptr %p\n  ret i32 %x\n}\n")
    assert mem_state_tv(z3, ALIAS, alias_bad, "g")["status"] == "refuted", "alias-unsound load must refute"
    same_ptr = ("define i32 @g(ptr %p, ptr %q, i32 %x) {\n  store i32 %x, ptr %p\n"
                "  %v = load i32, ptr %p\n  ret i32 %v\n}\n")   # load the SAME pointer -> always %x
    same_ok = ("define i32 @g(ptr %p, ptr %q, i32 %x) {\n  store i32 %x, ptr %p\n  ret i32 %x\n}\n")
    assert mem_state_tv(z3, same_ptr, same_ok, "g")["status"] == "proved", "same-pointer load == x"

    # 5. NEW-DEREFERENCE guard: this model does not track pointer validity, so a transform whose TARGET
    #    dereferences an address the SOURCE does not (a load/store that could fault where the source is
    #    defined) is DECLINED, never proved -- the null-deref gap is enforced, not merely documented.
    no_deref = "define i32 @h(ptr %p, i32 %x) {\n  ret i32 %x\n}\n"
    new_load = "define i32 @h(ptr %p, i32 %x) {\n  %v = load i32, ptr %p\n  ret i32 %x\n}\n"
    r = mem_state_tv(z3, no_deref, new_load, "h")
    assert r["status"] == "unsupported" and "dereference" in r.get("reason", ""), \
        ("an introduced load through an untracked pointer must DECLINE, not prove", r)
    #    ...while a transform that only READS pointers the source already read still proves (guard is not
    #    over-eager): loading %p that the source also loads is fine.
    reads_p = "define i32 @h(ptr %p, i32 %x) {\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n"
    reads_ok = "define i32 @h(ptr %p, i32 %x) {\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n"
    assert mem_state_tv(z3, reads_p, reads_ok, "h")["status"] == "proved", \
        "a source-dereferenced pointer is fine to re-read (guard must not over-decline)"

    print("mem_state_tv_fixture OK: pointer-side-effect functions are TV'd over the MEMORY STATE via the "
          "SMT theory of arrays -- DSE removing a dead store PROVES (final memory unchanged); dropping a "
          "live store or storing a wrong value REFUTES; and ALIASING is handled exactly -- claiming a "
          "load of %q returns %x is refuted when p,q may alias, while a same-pointer load proves; and an "
          "INTRODUCED dereference (a load through an untracked pointer the source never touches) is "
          "DECLINED, not proved -- the null-deref gap is enforced. Closed for store removal/reordering")
    return 0


if __name__ == "__main__":
    sys.exit(main())
