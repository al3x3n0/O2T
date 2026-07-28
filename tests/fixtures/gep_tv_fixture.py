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
Scope: whole-byte integer elements, arrays and integer structs (named, inline and packed), with
constant or scalar indices. The layout is read off LLVM's parsed type rather than regexed out of
the instruction text, so the NAMED form real IR uses (`%T = type {...}`) is covered -- it used to
decline, because the reader could only match a struct spelled inline. Needs z3 + opt 18.
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

    # 5. BYTE-LEVEL / TYPE PUNNING (byte-addressable memory): store an i32, load an i8 at the base --
    #    the low byte equals trunc(x). Proved; claiming it equals a different byte refutes.
    tp = ("define i8 @g(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  %v = load i8, ptr %p\n  ret i8 %v\n}\n")
    tp_ok = ("define i8 @g(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n"
             "  %t = trunc i32 %x to i8\n  ret i8 %t\n}\n")
    assert mem_state_tv(z3, tp, tp_ok, "g")["status"] == "proved", "load i8 == low byte of stored i32"
    tp_bad = ("define i8 @g(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n"
              "  %s = lshr i32 %x, 8\n  %t = trunc i32 %s to i8\n  ret i8 %t\n}\n")
    assert mem_state_tv(z3, tp, tp_bad, "g")["status"] == "refuted", "low byte != byte 1"

    # 6. STRUCT geps: field 1 of {i32, i32} is at byte offset 4. Store/load field 1 proves; and field 0
    #    and field 1 do NOT alias (offset 0 vs 4) -> a cross-field load-forwarding claim refutes.
    st = ("define i32 @h(ptr %p, i32 %x) {\n  %f = getelementptr {i32, i32}, ptr %p, i32 0, i32 1\n"
          "  store i32 %x, ptr %f\n  %v = load i32, ptr %f\n  ret i32 %v\n}\n")
    st_ok = ("define i32 @h(ptr %p, i32 %x) {\n  %f = getelementptr {i32, i32}, ptr %p, i32 0, i32 1\n"
             "  store i32 %x, ptr %f\n  ret i32 %x\n}\n")
    assert mem_state_tv(z3, st, st_ok, "h")["status"] == "proved", "struct field store/load"
    st_alias = ("define i32 @h(ptr %p, i32 %x) {\n"
                "  %f0 = getelementptr {i32, i32}, ptr %p, i32 0, i32 0\n  store i32 %x, ptr %f0\n"
                "  %f1 = getelementptr {i32, i32}, ptr %p, i32 0, i32 1\n"
                "  %v = load i32, ptr %f1\n  ret i32 %v\n}\n")
    st_bad = st_alias.replace("%v = load i32, ptr %f1\n  ret i32 %v", "ret i32 %x")
    assert mem_state_tv(z3, st_alias, st_bad, "h")["status"] == "refuted", "struct fields 0,1 don't alias"

    # 6b. NAMED and PACKED struct types -- the form real IR actually uses. Every case above spells the
    #     struct INLINE in the gep, and the text reader this model replaced could match nothing else:
    #     it regexed `{iA, iB, ...}` out of the instruction, so `%T = type {...}` + `getelementptr %T,
    #     ...` simply declined. The capability was documented and fixture-gated while the shape real
    #     code takes was never exercised -- the same pattern behind both false proofs in the 2026-07
    #     review. LLVM reports the layout, so both forms are now covered here.
    named = ("%T = type { i32, i64 }\n"
             "define i64 @h(ptr %p, i64 %x) {\n  %f = getelementptr %T, ptr %p, i32 0, i32 1\n"
             "  store i64 %x, ptr %f\n  %v = load i64, ptr %f\n  ret i64 %v\n}\n")
    named_ok = ("%T = type { i32, i64 }\n"
                "define i64 @h(ptr %p, i64 %x) {\n  %f = getelementptr %T, ptr %p, i32 0, i32 1\n"
                "  store i64 %x, ptr %f\n  ret i64 %x\n}\n")
    assert mem_state_tv(z3, named, named_ok, "h")["status"] == "proved", "named struct field store/load"

    #     A PACKED struct lays field 1 at byte 4 where the unpacked one aligns it to 8, so reading the
    #     alignment rule off the parsed type (rather than assuming) is load-bearing: a claim that the
    #     packed layout matches the unpacked one must REFUTE.
    packed = ("%P = type <{ i32, i64 }>\n"
              "define i64 @h(ptr %p, i64 %x) {\n  %f = getelementptr %P, ptr %p, i32 0, i32 1\n"
              "  store i64 %x, ptr %f\n  %v = load i64, ptr %f\n  ret i64 %v\n}\n")
    packed_ok = packed.replace("%v = load i64, ptr %f\n  ret i64 %v", "ret i64 %x")
    assert mem_state_tv(z3, packed, packed_ok, "h")["status"] == "proved", "packed struct field"
    #     ...and the two layouts are genuinely different: storing through the PACKED offset and
    #     loading through the UNPACKED one must not be provable.
    cross = ("%P = type <{ i32, i64 }>\n%U = type { i32, i64 }\n"
             "define i64 @h(ptr %p, i64 %x) {\n  %a = getelementptr %P, ptr %p, i32 0, i32 1\n"
             "  store i64 %x, ptr %a\n  %b = getelementptr %U, ptr %p, i32 0, i32 1\n"
             "  %v = load i64, ptr %b\n  ret i64 %v\n}\n")
    cross_bad = cross.replace("%v = load i64, ptr %b\n  ret i64 %v", "ret i64 %x")
    assert mem_state_tv(z3, cross, cross_bad, "h")["status"] == "refuted", \
        "packed and unpacked field 1 are at different offsets"

    print("gep_tv_fixture OK: getelementptr over BYTE-ADDRESSABLE memory (theory of arrays) -- "
          "store p[i];load p[i] returns x (incl. opt's redundant-load elim), store p[i];load p[j] "
          "claiming x REFUTES (i!=j), gep(gep(p,i),j)==gep(p,i+j) proves, p[i] vs p[i+1] never alias; "
          "and byte-level: TYPE PUNNING (store i32, load i8 low byte == trunc x) proves, struct field "
          "access proves, and distinct struct fields don't alias. Aliasing exact -- no alias analysis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
