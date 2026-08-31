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

    # 6. POISON REFINEMENT, through memory. This case was found by the differential fuzzer and used
    #    to DECLINE: the model compared values, so it could not tell a miscompile from a sound
    #    exploitation and had to refuse. `ashr x,x` is poison wherever the shift reaches the width,
    #    so folding it -- and the store of it -- to 0 refines, and reference Alive2 proves it. Every
    #    byte of memory now carries a poison bit, so this is PROVED rather than declined.
    pois_b = ("define i32 @k(ptr %q, i32 %x) {\n  %v = ashr i32 %x, %x\n  store i32 %v, ptr %q\n"
              "  ret i32 %v\n}\n")
    pois_a = ("define i32 @k(ptr %q, i32 %x) {\n  store i32 0, ptr %q\n  ret i32 0\n}\n")
    assert mem_state_tv(z3, pois_b, pois_a, "k")["status"] == "proved", \
        "a sound poison-exploiting fold through memory must now PROVE"

    # 6b. TEETH for the other direction, which only a poison term can catch: the target STORES a
    #     value that is poison where the source's is defined. Every stored byte is value-identical,
    #     so the old value comparison saw nothing at all -- and the guard it relied on erred toward
    #     declining, never toward catching this.
    st_b = ("define void @m(ptr %q, i32 %x, i32 %y) {\n  %v = lshr i32 %x, %y\n"
            "  store i32 %v, ptr %q\n  ret void\n}\n")
    st_a = ("define void @m(ptr %q, i32 %x, i32 %y) {\n  %v = lshr exact i32 %x, %y\n"
            "  store i32 %v, ptr %q\n  ret void\n}\n")
    v = mem_state_tv(z3, st_b, st_a, "m")
    assert v["status"] == "refuted" and v.get("witness"), \
        ("storing a value poison where the source's is defined must refute, values being identical", v)

    # 7. POINTERS ARE VALUES, not just named parameters. `addr` used to be keyed by register name
    #    and filled from the signature alone, so a pointer that came from anywhere else -- a
    #    `select` between two of them, one loaded out of memory -- had no address and the function
    #    declined. That single gap is what the decline census reported as four separate buckets
    #    (`getelementptr`, `ptrtoint`, "non-integer type ptr", `alloca`).
    sel = ("define i32 @f(i1 %c, ptr %p, ptr %q) {\n"
           "  %s = select i1 %c, ptr %p, ptr %q\n  %v = load i32, ptr %s\n  ret i32 %v\n}\n")
    assert mem_state_tv(z3, sel, sel, "f")["status"] == "proved", \
        "a pointer chosen by select must be a usable address"
    #    Swapping the arms makes the target load through an address the source need not touch, and
    #    the NEW-DEREFERENCE guard covers pointer-valued selects exactly as it covers named ones --
    #    it declines rather than proving. Asserted here because the guard reaching this new kind of
    #    address is the thing worth pinning, not merely that something non-proved came back.
    swapped = sel.replace("select i1 %c, ptr %p", "select i1 %c, ptr %q").replace(
        "ptr %q, ptr %q", "ptr %q, ptr %p")   # swap the ARMS only, not the signature
    v = mem_state_tv(z3, sel, swapped, "f")
    assert v["status"] == "unsupported" and v.get("guard") == "new-deref", \
        ("swapping a pointer select's arms derefs an address the source may not -- the guard must "
         "decline, never prove", v)
    #    A returned pointer is its address, compared like any other value; returning the other one
    #    refutes.
    rp = "define ptr @g(i1 %c, ptr %p, ptr %q) {\n  %s = select i1 %c, ptr %p, ptr %q\n  ret ptr %s\n}\n"
    assert mem_state_tv(z3, rp, rp, "g")["status"] == "proved", "a returned pointer must be decided"
    rp_swapped = rp.replace("select i1 %c, ptr %p, ptr %q", "select i1 %c, ptr %q, ptr %p")
    assert mem_state_tv(z3, rp, rp_swapped, "g")["status"] == "refuted", \
        "returning the other pointer must refute"
    #    Comparing pointers compares addresses; `icmp eq %p, %p` is true, `%p` vs `%q` is not.
    same = "define i1 @h(ptr %p) {\n  %r = icmp eq ptr %p, %p\n  ret i1 %r\n}\n"
    true_ = "define i1 @h(ptr %p) {\n  ret i1 true\n}\n"
    assert mem_state_tv(z3, same, true_, "h")["status"] == "proved", "icmp eq %p,%p is true"
    diff = "define i1 @h2(ptr %p, ptr %q) {\n  %r = icmp eq ptr %p, %q\n  ret i1 %r\n}\n"
    assert mem_state_tv(z3, diff, "define i1 @h2(ptr %p, ptr %q) {\n  ret i1 true\n}\n",
                        "h2")["status"] == "refuted", "distinct pointers need not be equal"

    # 7b. ADDRESS SPACE 0 ONLY, and this is a decline the pointer work CREATED the need for. `null`
    #     is address 0 in the model, which is a claim about address space 0: elsewhere null may be a
    #     perfectly good address. LLVM's own tests turn on it (`select.ll test16` folds, `test16_neg`
    #     with `addrspace(1)` must not). Until this landed the parse reported both as plain `ptr`,
    #     so the model could not have declined even if it wanted to.
    as1 = ("define i32 @k(i1 %c, ptr addrspace(1) %p) {\n"
           "  %s = select i1 %c, ptr addrspace(1) %p, ptr addrspace(1) null\n"
           "  %v = load i32, ptr addrspace(1) %s\n  ret i32 %v\n}\n")
    d = mem_state_tv(z3, as1, as1, "k")
    assert d["status"] == "unsupported" and "address space" in d.get("reason", ""), \
        ("a non-zero address space must DECLINE -- null is only known invalid in addrspace 0", d)

    # 7c. A POISON POINTER IS UB TO DEREFERENCE WHICHEVER WAY THE ACCESS GOES. The load path said
    #     so; the store path did not, which left a storing TARGET looking defined where it is not --
    #     and a target that is secretly UB is exactly the disjunct whose absence turns a refutation
    #     into a proof. Here the SOURCE is the UB one, so the pair must prove despite storing a
    #     different value: a UB source refines to anything. Without the store-side term the model
    #     compares the two values and refutes.
    ub_src = ("define void @p1(ptr %m) {\n  store i64 poison, ptr %m\n"
              "  %p = load ptr, ptr %m\n  store i32 1, ptr %p\n  ret void\n}\n")
    ub_tgt = ub_src.replace("store i32 1", "store i32 2")
    assert mem_state_tv(z3, ub_src, ub_tgt, "p1")["status"] == "proved", \
        "storing through a poison pointer is UB, and a UB source refines to anything"
    #     This pair also pins the `poison_<width>` DECLARATION: a literal `poison` operand is spelled
    #     `poison_64` by the semantics layer and nothing here declared it, so any function containing
    #     one used to come back a solver error -- or, through the new-deref probe, a spurious decline.

    # 7d. POINTER POISON MUST TRAVEL WITH A BOUND ARGUMENT WHEN A CALLEE IS INLINED. These two are
    #     the SAME program -- one writes the access inside a call, the other by hand -- so they must
    #     prove in both directions. With the callee's frame starting its pointer-poison map empty,
    #     every argument read as definitely-defined, the callee's poison-deref UB vanished and the
    #     pair REFUTED against itself.
    thru_call = ("define void @p2(ptr %m) {\n  store i64 poison, ptr %m\n"
                 "  %p = load ptr, ptr %m\n  call void @sink(ptr %p)\n  ret void\n}\n"
                 "define void @sink(ptr %q) {\n  store i32 1, ptr %q\n  ret void\n}\n")
    inlined = ("define void @p2(ptr %m) {\n  store i64 poison, ptr %m\n"
               "  %p = load ptr, ptr %m\n  store i32 1, ptr %p\n  ret void\n}\n"
               "define void @sink(ptr %q) {\n  store i32 1, ptr %q\n  ret void\n}\n")
    for a, b, why in ((thru_call, inlined, "call -> inlined"), (inlined, thru_call, "inlined -> call")):
        assert mem_state_tv(z3, a, b, "p2")["status"] == "proved", \
            f"a bound pointer argument keeps its poison across inlining ({why})"

    # 7e. THE RETURN TYPE IS PART OF THE SIGNATURE, and only became able to differ silently once a
    #     returned pointer stopped declining -- `ptr` and `i64` are both 64 bits, so the width check
    #     let such a pair through and two functions returning the SAME 64 bits under different types
    #     proved equivalent. Nothing in the corpus builds that pair; the model should still decline
    #     to answer a question it was not asked.
    as_int = "define i64 @r(ptr %m) {\n  %v = load i64, ptr %m\n  ret i64 %v\n}\n"
    as_ptr = "define ptr @r(ptr %m) {\n  %v = load ptr, ptr %m\n  ret ptr %v\n}\n"
    d = mem_state_tv(z3, as_int, as_ptr, "r")
    assert d["status"] == "unsupported" and d.get("reason") == "return type changed", \
        ("a pair whose sides return different types must decline, not prove", d)

    print("mem_state_tv_fixture OK: pointer-side-effect functions are TV'd over the MEMORY STATE via the "
          "SMT theory of arrays -- DSE removing a dead store PROVES (final memory unchanged); dropping a "
          "live store or storing a wrong value REFUTES; and ALIASING is handled exactly -- claiming a "
          "load of %q returns %x is refuted when p,q may alias, while a same-pointer load proves; and an "
          "INTRODUCED dereference (a load through an untracked pointer the source never touches) is "
          "DECLINED, not proved -- the null-deref gap is enforced. Closed for store removal/reordering")
    return 0


if __name__ == "__main__":
    sys.exit(main())
