#!/usr/bin/env python3
"""GEP / pointer arithmetic: memory-state TV through getelementptr, aliasing handled by array theory.

Extends the memory-state model (o2t/validate/mem_state.py) so a `getelementptr` is address arithmetic
on the opaque pointer address (`bvadd base idx`, element-addressed). Because memory is an SMT array, the
resulting aliasing is EXACT with no alias analysis: two geps alias iff they compute equal addresses.

  * `store %x, ptr p[i]; load p[i]` returns %x (same index) -> proved;
  * ALIASING -- `store %x, ptr p[i]; load p[j]` claiming to return %x is REFUTED (unsound when i != j);
  * gep REASSOCIATION -- `gep(gep(p, i), j)` addresses the same as `gep(p, i+j)`, so a store/load through
    either proves equivalent -- (p+i)+j == p+(i+j);
  * a real opt redundant-load elimination through a gep proves.
Scope: i32-element pointers/arrays, constant/scalar indices; struct/i8/other-type geps decline. Needs
z3 + opt 18.
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

SAME = ("define i32 @f(ptr %p, i64 %i, i32 %x) {\n"
        "  %q = getelementptr i32, ptr %p, i64 %i\n  store i32 %x, ptr %q\n"
        "  %v = load i32, ptr %q\n  ret i32 %v\n}\n")
ALIAS = ("define i32 @f(ptr %p, i64 %i, i64 %j, i32 %x) {\n"
         "  %pi = getelementptr i32, ptr %p, i64 %i\n  store i32 %x, ptr %pi\n"
         "  %pj = getelementptr i32, ptr %p, i64 %j\n  %v = load i32, ptr %pj\n  ret i32 %v\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("gep_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. store p[i]; load p[i] returns x (same address). Proved against opt's own redundant-load
    #    elimination (early-cse/gvn), and against the hand-written folded form.
    folded = ("define i32 @f(ptr %p, i64 %i, i32 %x) {\n"
              "  %q = getelementptr i32, ptr %p, i64 %i\n  store i32 %x, ptr %q\n  ret i32 %x\n}\n")
    assert mem_state_tv(z3, SAME, folded, "f")["status"] == "proved", "load p[i] == stored x"
    after = si.run_passes(SAME, "gvn", opt)
    assert mem_state_tv(z3, SAME, after, "f")["status"] == "proved", "opt redundant-load elim through gep"

    # 2. ALIASING (array-theory exact): store p[i]; load p[j] claiming to return x is UNSOUND when
    #    i != j -> refuted with a witness.
    alias_bad = ALIAS.replace("%v = load i32, ptr %pj\n  ret i32 %v", "ret i32 %x")
    v = mem_state_tv(z3, ALIAS, alias_bad, "f")
    assert v["status"] == "refuted" and v.get("witness"), ("alias-unsound gep load must refute", v)

    # 3. gep REASSOCIATION: (p+i)+j == p+(i+j), so store/load through either path is equivalent.
    re2 = ("define i32 @f(ptr %p, i64 %i, i64 %j, i32 %x) {\n"
           "  %a = getelementptr i32, ptr %p, i64 %i\n  %b = getelementptr i32, ptr %a, i64 %j\n"
           "  store i32 %x, ptr %b\n  %v = load i32, ptr %b\n  ret i32 %v\n}\n")
    re1 = ("define i32 @f(ptr %p, i64 %i, i64 %j, i32 %x) {\n  %s = add i64 %i, %j\n"
           "  %b = getelementptr i32, ptr %p, i64 %s\n  store i32 %x, ptr %b\n"
           "  %v = load i32, ptr %b\n  ret i32 %v\n}\n")
    assert mem_state_tv(z3, re2, re1, "f")["status"] == "proved", "gep(gep(p,i),j) == gep(p,i+j)"

    # 4. A different index really does alias-differ: store p[i], load p[i+1] returning x is refuted.
    off = ("define i32 @f(ptr %p, i64 %i, i32 %x) {\n"
           "  %pi = getelementptr i32, ptr %p, i64 %i\n  store i32 %x, ptr %pi\n"
           "  %j = add i64 %i, 1\n  %pj = getelementptr i32, ptr %p, i64 %j\n"
           "  %v = load i32, ptr %pj\n  ret i32 %v\n}\n")
    off_bad = ("define i32 @f(ptr %p, i64 %i, i32 %x) {\n"
               "  %pi = getelementptr i32, ptr %p, i64 %i\n  store i32 %x, ptr %pi\n  ret i32 %x\n}\n")
    assert mem_state_tv(z3, off, off_bad, "f")["status"] == "refuted", "p[i] and p[i+1] never alias"

    print("gep_tv_fixture OK: getelementptr is modeled as address arithmetic over the memory array, so "
          "aliasing is EXACT -- store p[i];load p[i] returns x (proved, incl. opt's redundant-load elim); "
          "store p[i];load p[j] claiming x REFUTES (unsound when i!=j); gep(gep(p,i),j)==gep(p,i+j) proves; "
          "and p[i] vs p[i+1] never alias. Pointer arithmetic, handled by the theory of arrays")
    return 0


if __name__ == "__main__":
    sys.exit(main())
