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

    # 7f. `!noundef` ON A LOAD, and the UB that must come with it. The promise is that the loaded
    #     value is neither undef nor poison -- and that it is UB if it ever is. Taking the result as
    #     definite WITHOUT the UB term would leave a target that VIOLATES the promise looking
    #     defined, which is the direction a false proof comes from, so both halves land together.
    #     `!dereferenceable` and `!dereferenceable_or_null` carry the same promise here: a poison
    #     pointer is neither dereferenceable nor null. Alive2 was asked before this was modeled and
    #     agrees the freeze folds under all three.
    for md in ("!noundef !0", "!dereferenceable !1", "!dereferenceable_or_null !1"):
        src = (f"define ptr @fz(ptr %ptr) {{\n  %p = load ptr, ptr %ptr, {md}\n"
               "  %f = freeze ptr %p\n  ret ptr %f\n}\n!0 = !{}\n!1 = !{i64 4}\n")
        tgt = (f"define ptr @fz(ptr %ptr) {{\n  %p = load ptr, ptr %ptr, {md}\n"
               "  ret ptr %p\n}\n!0 = !{}\n!1 = !{i64 4}\n")
        assert mem_state_tv(z3, src, tgt, "fz")["status"] == "proved", \
            f"freeze over a load promising a definite value must fold ({md})"
    #     ...and WITHOUT the metadata the very same pair must DECLINE, not prove: there the freeze
    #     has real freedom to collapse and this validator cannot tell which side it is translating.
    #     This is what stops the assertions above from passing for the wrong reason.
    bare_s = ("define ptr @fz(ptr %ptr) {\n  %p = load ptr, ptr %ptr\n"
              "  %f = freeze ptr %p\n  ret ptr %f\n}\n")
    bare_t = "define ptr @fz(ptr %ptr) {\n  %p = load ptr, ptr %ptr\n  ret ptr %p\n}\n"
    d = mem_state_tv(z3, bare_s, bare_t, "fz")
    assert d["status"] == "unsupported" and "freeze" in d.get("reason", ""), \
        ("without the promise the freeze has freedom to collapse and must decline", d)
    #     THE UB HALF NEEDS ITS OWN TOOTH -- the assertions above pass with it removed, because
    #     they only ever need the result to be DEFINITE. Here the load's value IS poison, so the
    #     promise is violated and the source is UB, which refines to anything: the pair proves
    #     despite the two sides storing different values. Drop `ub.append(...)` and the model sees
    #     a definite pointer, no UB on either side, two different stores -- and refutes.
    nd_s = ("define void @nd(ptr %m) {\n  store i64 poison, ptr %m\n"
            "  %p = load ptr, ptr %m, !noundef !0\n  store i32 1, ptr %p\n  ret void\n}\n"
            "!0 = !{}\n")
    assert mem_state_tv(z3, nd_s, nd_s.replace("store i32 1", "store i32 2"), "nd")["status"] \
        == "proved", "violating a !noundef promise is UB, and a UB source refines to anything"

    # 7g. `ptrtoint` IS NEARLY AN IDENTITY once an address is a 64-bit term -- the instruction the
    #     pointer-values work never connected to the capability it had just built.
    pti = ("define i64 @pi(ptr %p, ptr %q) {\n  %a = ptrtoint ptr %p to i64\n"
           "  %b = ptrtoint ptr %q to i64\n  %d = sub i64 %a, %b\n  ret i64 %d\n}\n")
    assert mem_state_tv(z3, pti, pti, "pi")["status"] == "proved", "ptrtoint must be decidable"
    #     Comparing a pointer and comparing its ptrtoint are the same question, so this proves...
    via_int = ("define i1 @pe(ptr %p, ptr %q) {\n  %a = ptrtoint ptr %p to i64\n"
               "  %b = ptrtoint ptr %q to i64\n  %c = icmp eq i64 %a, %b\n  ret i1 %c\n}\n")
    via_ptr = "define i1 @pe(ptr %p, ptr %q) {\n  %c = icmp eq ptr %p, %q\n  ret i1 %c\n}\n"
    assert mem_state_tv(z3, via_int, via_ptr, "pe")["status"] == "proved", \
        "icmp on addresses and icmp on their ptrtoint are the same comparison"
    #     ...while a WIDER target type would have to invent high bits, and declines.
    wide = ("define i128 @pw(ptr %p) {\n  %a = ptrtoint ptr %p to i128\n  ret i128 %a\n}\n")
    assert mem_state_tv(z3, wide, wide, "pw")["status"] == "unsupported", \
        "ptrtoint to a type wider than an address must decline"

    # 7h. THE DATALAYOUT DECIDES WHETHER `ptrtoint ptr to i32` IS A TRUNCATION AT ALL, and this
    #     is the pair that shows the model reads it. LLVM's own `or.ll` declares `p:32:32:32` and
    #     contains exactly this fold (test27): `(ptrtoint A | ptrtoint B) == 0` becomes
    #     `A == null && B == null`. At 32-BIT pointers the ptrtoint is EXACT and the fold is
    #     CORRECT. At 64-bit pointers the very same text truncates, and a pointer like
    #     0x1_0000_0000 makes the source true where the target is false -- so it is genuinely
    #     UNSOUND and must refute. Identical instructions, opposite verdicts, and nothing
    #     distinguishes them but the datalayout line.
    #
    #     Before the width was read from the module, addresses were 64 bits everywhere and this
    #     sound transform REFUTED -- it would have been the corpus's first refutation, and a false
    #     one. The reverse direction is the dangerous one: address arithmetic wraps at 2**pw, so
    #     computing gep offsets wider keeps two addresses distinct that the real target makes
    #     equal, under-approximating aliasing -- where false proofs come from.
    ptoi = ("define i1 @dl(ptr %A, ptr %B) {\n  %C1 = ptrtoint ptr %A to i32\n"
            "  %C2 = ptrtoint ptr %B to i32\n  %D = or i32 %C1, %C2\n"
            "  %E = icmp eq i32 %D, 0\n  ret i1 %E\n}\n")
    folded = ("define i1 @dl(ptr %A, ptr %B) {\n  %1 = icmp eq ptr %A, null\n"
              "  %2 = icmp eq ptr %B, null\n  %E = and i1 %1, %2\n  ret i1 %E\n}\n")
    dl32 = 'target datalayout = "e-p:32:32:32"\n'
    assert mem_state_tv(z3, dl32 + ptoi, dl32 + folded, "dl")["status"] == "proved", \
        "at 32-bit pointers ptrtoint to i32 is exact and this real InstCombine fold is sound"
    v = mem_state_tv(z3, ptoi, folded, "dl")
    assert v["status"] == "refuted" and v.get("witness"), \
        ("at 64-bit pointers the same text TRUNCATES and the fold is unsound -- it must refute "
         "with a witness, or the width is not reaching the model", v)
    #     A NARROW width is modelled, not merely tolerated: a gep at 16-bit pointers decides, and
    #     its offsets wrap at 2**16 the way the real target's do.
    gep = "define ptr @g(ptr %p, i64 %i) {\n  %q = getelementptr i8, ptr %p, i64 %i\n  ret ptr %q\n}\n"
    dl16 = 'target datalayout = "e-p:16:16:16"\n'
    assert mem_state_tv(z3, dl16 + gep, dl16 + gep, "g")["status"] == "proved", \
        "a 16-bit pointer module must be modelled at 16 bits, not declined and not widened"
    #     ...while a width this model does not handle declines rather than rounding to one it does.
    #     (LLVM's parser rejects a non-byte-multiple width outright, so only the wide case is
    #     reachable from real IR.)
    dl128 = 'target datalayout = "e-p:128:128:128"\n'
    d = mem_state_tv(z3, dl128 + gep, dl128 + gep, "g")
    assert d["status"] == "unsupported" and "pointer width" in d.get("reason", ""), \
        ("a pointer width past what the gep index handling covers must decline", d)

    # 7i. A NON-BYTE-WIDTH ACCESS MUST DECLINE, and this pins a decline rather than a capability.
    #     `i1` memory reads as the cheapest entry in the decline census and is not available to a
    #     byte array at all: Alive2 tracks how many BITS of a byte were written, and after
    #     `store i1` a `load i8` of that byte is POISON ("written with 1 bits"). Modelling the byte
    #     as zero-padded would make `store i8 0` and `store i1 false` compare equal -- a false
    #     proof. Taking the low bit on the load side fails too: `load i1` and `trunc (load i8)` do
    #     not verify in EITHER direction. Asserted so a later "obvious" fix has to confront this.
    for f, why in ((("define void @w(i1 %c, ptr %p) {\n  store i1 %c, ptr %p\n  ret void\n}\n"),
                    "store i1"),
                   (("define i1 @w2(ptr %p) {\n  %v = load i1, ptr %p\n  ret i1 %v\n}\n"),
                    "load i1"),
                   (("define void @w3(i4 %c, ptr %p) {\n  store i4 %c, ptr %p\n  ret void\n}\n"),
                    "store i4")):
        fname = f.split("@")[1].split("(")[0]
        d = mem_state_tv(z3, f, f, fname)
        assert d["status"] == "unsupported", \
            (f"a non-byte-width access ({why}) must decline -- a byte array cannot represent a "
             "partially-written byte", d)

    # 7j. ALLOCAS GET ADDRESSES, and the two facts asserted about them are simply TRUE of a fresh
    #     object: never null, and never overlapping another one. Asserting a true fact is not an
    #     approximation -- it moves the model TOWARDS reality, so the proofs it enables are right.
    nn = ("define i1 @a1() {\n  %a = alloca i32\n  %r = icmp eq ptr %a, null\n  ret i1 %r\n}\n")
    assert mem_state_tv(z3, nn, "define i1 @a1() {\n  ret i1 false\n}\n", "a1")["status"] \
        == "proved", "an alloca is never null"
    two = ("define i1 @a2() {\n  %a = alloca i32\n  %b = alloca i32\n"
           "  %r = icmp eq ptr %a, %b\n  ret i1 %r\n}\n")
    assert mem_state_tv(z3, two, "define i1 @a2() {\n  ret i1 false\n}\n", "a2")["status"] \
        == "proved", "two allocas never overlap"
    #     THE NON-WRAPPING FACT IS LOad-BEARING and was found by this pair refuting. Disjointness
    #     written only as `a + size <= b OR b + size <= a` is SATISFIABLE WITH a == b: near the top
    #     of the address space `a + 4` wraps below `a`, so the solver produced an "overlap-free"
    #     model in which two allocas were the same address. A real object never wraps.
    #
    #     What is NOT asserted matters as much. Allocas are not ORDERED, so this must not prove:
    ordd = ("define i1 @a3() {\n  %a = alloca i32\n  %b = alloca i32\n"
            "  %r = icmp ult ptr %a, %b\n  ret i1 %r\n}\n")
    assert mem_state_tv(z3, ordd, "define i1 @a3() {\n  ret i1 true\n}\n", "a3")["status"] \
        != "proved", "nothing orders two allocas -- claiming one is below the other must not prove"
    #     ...and an alloca compared with a pointer of UNKNOWN PROVENANCE DECLINES. They really are
    #     distinct, but the caller's object extents are unknown so the fact cannot be stated; left
    #     alone the solver aliases them and the pair refutes. LLVM folds exactly this comparison
    #     using alias analysis, so refuting would be a FALSE REFUTATION on real code, not a missed
    #     proof -- the one outcome this corpus has never produced.
    vs_p = ("define i1 @a4(ptr %p) {\n  %a = alloca i32\n  %r = icmp eq ptr %a, %p\n"
            "  ret i1 %r\n}\n")
    d = mem_state_tv(z3, vs_p, "define i1 @a4(ptr %p) {\n  ret i1 false\n}\n", "a4")
    assert d["status"] == "unsupported", \
        ("an alloca vs an unknown pointer must decline, never refute a sound fold", d)
    #     ACCESS through an alloca declines, including through a gep off one. Alloca memory is dead
    #     at return, so it must not join the final-memory comparison -- and EXCLUDING bytes from
    #     that comparison is the shape that hides a real difference. Keeping alloca bytes out of
    #     memory entirely leaves nothing to exclude.
    for f, why in (("define void @a5() {\n  %a = alloca i32\n  store i32 1, ptr %a\n  ret void\n}\n",
                    "store"),
                   ("define i32 @a6() {\n  %a = alloca i32\n  %v = load i32, ptr %a\n  ret i32 %v\n}\n",
                    "load"),
                   ("define void @a7() {\n  %a = alloca [4 x i32]\n"
                    "  %g = getelementptr [4 x i32], ptr %a, i64 0, i64 1\n"
                    "  store i32 1, ptr %g\n  ret void\n}\n", "store via a gep off one")):
        fname = f.split("@")[1].split("(")[0]
        d = mem_state_tv(z3, f, f, fname)
        assert d["status"] == "unsupported" and "alloca" in d.get("reason", ""), \
            (f"{why} through an alloca must decline -- its bytes are not modelled", d)

    print("mem_state_tv_fixture OK: pointer-side-effect functions are TV'd over the MEMORY STATE via the "
          "SMT theory of arrays -- DSE removing a dead store PROVES (final memory unchanged); dropping a "
          "live store or storing a wrong value REFUTES; and ALIASING is handled exactly -- claiming a "
          "load of %q returns %x is refuted when p,q may alias, while a same-pointer load proves; and an "
          "INTRODUCED dereference (a load through an untracked pointer the source never touches) is "
          "DECLINED, not proved -- the null-deref gap is enforced. Closed for store removal/reordering")
    return 0


if __name__ == "__main__":
    sys.exit(main())
