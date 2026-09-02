#!/usr/bin/env python3
"""A call this model cannot look inside is still OBSERVABLE, and the target has to make it too.

LLVM's own InstCombine tests are full of `call void @use(i32 %x)`. The callee is a bare declaration
with no body and the call exists purely so DCE cannot delete the value the fold under test is about.
Whole-function TV declined every function containing one -- the single largest tractable decline
class measured across LLVM 18's test files (124 of 540 declines, against 19 for `undef`).

Nothing about the callee is known, so nothing about it is assumed. What is used instead is what the
CALL SITE already tells us:

  * it returns VOID, so it cannot contribute to the value this function returns;
  * the scalar fragment has no pointer for it to write through, so it cannot change the values that
    do (a function taking a pointer declines on the pointer, elsewhere and for its own reasons);
  * but it IS observable, so it is not simply skipped. Each one is recorded as an effect, and the
    target must make the SAME calls with the SAME argument values.

The split between what is checked syntactically and what is checked by the solver is the whole
design. The SEQUENCE of callees must match as written -- dropping, adding or reordering an observable
call is a change in behaviour this model does not reason about, so it DECLINES rather than guessing.
The ARGUMENTS are compared in the solver, because rewriting them into different-looking equal terms
is exactly what the pass under test does: `call void @use32(i32 %a)` where `%a` was `ashr %x, 1` in
the source and something else in the target is fine iff the two agree on every input.

A call whose RESULT is used still declines. That would need the callee to be a function of its
arguments, and a bodiless declaration promises no such thing -- two calls with equal arguments may
return different values, so modelling it as pure would assume something LLVM does not give.

Needs z3; the Alive2 half self-skips without `alive-tv`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir as S  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402

DECL = "declare void @use32(i32)\ndeclare void @use8(i8)\ndeclare i32 @get()\n"


def fn(body, sig="i32 %x"):
    return DECL + f"define i32 @f({sig}) {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("observable_call_fixture: needs z3, skipped")
        return 0
    alive = shutil.which("alive-tv")

    def check(name, before, after, want):
        v = S.validate_transform(z3, before, after, "f", timeout=30)
        assert v["status"] == want, (name, want, v)
        # A decisive verdict is confirmed against the independent oracle; a decline claims nothing.
        if alive and want in ("proved", "refuted"):
            av = alive_refines(before, after, alive).get("status")
            assert av == want, (name, "Alive2 disagrees with O2T", want, av)
        return v

    # 1) THE CASE THIS EXISTS FOR. The keep-alive call survives the fold, and the fold is proved --
    #    where before, the presence of the call alone made the whole function undecidable.
    keep = fn("  %a = ashr i32 %x, 1\n  call void @use32(i32 %a)\n"
              "  %r = ashr i32 %x, 1\n  ret i32 %r")
    folded = fn("  %a = ashr i32 %x, 1\n  call void @use32(i32 %a)\n  ret i32 %a")
    check("fold under a keep-alive call", keep, folded, "proved")

    # 2) ...and the argument is checked by the SOLVER, not by spelling: a target that rewrites it into
    #    a different-looking but equal term still proves.
    equal_arg = fn("  %a = ashr i32 %x, 1\n  %b = sdiv i32 %a, 1\n  call void @use32(i32 %b)\n"
                   "  ret i32 %a")
    check("argument rewritten to an equal term", keep, equal_arg, "proved")

    # 3) TEETH. Passing the call something DIFFERENT is a change in observable behaviour and is
    #    refuted -- this is what stops "ignore calls we cannot model" from being the rule.
    wrong = fn("  %a = ashr i32 %x, 1\n  call void @use32(i32 %x)\n  ret i32 %a")
    v = check("observable call given a different value", keep, wrong, "refuted")
    assert v.get("witness"), ("a refutation must ship a witness", v)

    # 4) DROPPING one declines rather than proving. Whether deleting an observable call is sound is a
    #    question about the callee, which is exactly what is not known here.
    dropped = fn("  %a = ashr i32 %x, 1\n  ret i32 %a")
    d = S.validate_transform(z3, keep, dropped, "f", timeout=30)
    assert d["status"] == "unsupported", ("dropping an observable call must decline", d)
    assert "observable calls differ" in d["reason"], d

    # 5) ...as does ADDING one, by the same rule and the same code path.
    added = fn("  %a = ashr i32 %x, 1\n  call void @use32(i32 %a)\n"
               "  call void @use32(i32 %a)\n  ret i32 %a")
    d = S.validate_transform(z3, keep, added, "f", timeout=30)
    assert d["status"] == "unsupported", ("adding an observable call must decline", d)

    # 5b) A POISON ARGUMENT IS NOT A DIFFERENCE. Where the source already passes poison, the callee
    #     may observe anything, so the target passing something else REFINES it. Comparing argument
    #     values unconditionally called that a miscompile -- a false refutation, and LLVM's own tests
    #     produced one immediately. The argument now gets exactly the rule the returned value gets.
    poison_arg = fn("  call void @use32(i32 poison)\n  ret i32 %x")
    other_arg = fn("  call void @use32(i32 %x)\n  ret i32 %x")
    check("target replaces a poison argument", poison_arg, other_arg, "proved")

    # 5c) AN UNMODELLED `@llvm.*` INTRINSIC IS NOT AN OPAQUE EXTERNAL CALL. It has semantics LLVM
    #     defines, so it must decline on its NAME rather than be waved through as "some effect".
    #     `llvm.lifetime.start` is such a name here: void, intrinsic, and unmodelled.
    #
    #     THE CASE THAT ORIGINALLY PROVED THIS WAS `llvm.assume`, which is now MODELLED -- so the
    #     example moved, and both halves are pinned below. Treating assume as opaque dropped the
    #     fact it ESTABLISHES, and a target simplified USING the assumption was refuted on exactly
    #     the inputs the assumption excluded (three false refutations in LLVM's own tests).
    life_src = ("declare void @llvm.lifetime.start.p0(i64, ptr)\n"
                "define i8 @f(ptr %p, i8 %x, i8 %y) {\n"
                "  call void @llvm.lifetime.start.p0(i64 8, ptr %p)\n  ret i8 %x\n}\n")
    d = S.validate_transform(z3, life_src, life_src, "f", timeout=30)
    assert d["status"] == "unsupported", \
        ("an unmodelled llvm.* intrinsic must DECLINE, not be treated as an opaque observable call "
         "-- it has semantics LLVM defines, and guessing at them is how a fact gets dropped", d)
    #     ...and the other half: once an intrinsic IS modelled, the fold that needs its meaning is
    #     DECIDED rather than declined. `assume` establishes its argument, so simplifying under it
    #     proves -- the outcome that was impossible while it was treated as an opaque effect.
    asm_src = ("declare void @llvm.assume(i1)\n"
               "define i8 @f(i1 %cond, i8 %x, i8 %y) {\n"
               "  call void @llvm.assume(i1 %cond)\n"
               "  %sel = select i1 %cond, i8 %x, i8 %y\n  ret i8 %sel\n}\n")
    asm_tgt = ("declare void @llvm.assume(i1)\n"
               "define i8 @f(i1 %cond, i8 %x, i8 %y) {\n"
               "  call void @llvm.assume(i1 %cond)\n  ret i8 %x\n}\n")
    d = S.validate_transform(z3, asm_src, asm_tgt, "f", timeout=30)
    assert d["status"] == "proved", \
        ("a target simplified USING an assumption must prove now that `assume` is modelled", d)

    # 6) A call whose RESULT IS USED still declines: a bodiless declaration is not a function of its
    #    arguments, and treating it as one would assume a purity LLVM does not promise.
    impure = fn("  %v = call i32 @get()\n  ret i32 %v")
    d = S.validate_transform(z3, impure, impure, "f", timeout=30)
    assert d["status"] == "unsupported", ("a call with a used result must decline", d)
    assert "call to" in d["reason"], d

    # 7) AN OBSERVABLE CALL IS OBSERVABLE WHATEVER THE FUNCTION RETURNS -- and getting that wrong was
    #    a live FALSE PROOF here, not a missed nicety. The effect terms used to sit INSIDE the guard
    #    on the returned value's poison, so once the source's result was poison the solver never
    #    looked at the calls at all. `shl i32 %x, 33` is poison for every input while the function
    #    itself has no UB, which isolates exactly that: the source hands the callee `%x`, the target
    #    hands it `0`, nothing else differs, and this PROVED while Alive2 refutes it (witness
    #    `%x = 1` -- the callee sees 1 against 0). `check` confirms the verdict against Alive2, so
    #    this case is pinned by the oracle rather than by our own reading.
    poison_ret = fn("  %p = shl i32 %x, 33\n  call void @use32(i32 %x)\n  ret i32 %p")
    poison_ret_bad = fn("  %p = shl i32 %x, 33\n  call void @use32(i32 0)\n  ret i32 %p")
    check("observable difference under a poison result", poison_ret, poison_ret_bad, "refuted")
    #    ...and the same shape with the argument PRESERVED must still prove, so the fix above is not
    #    simply refusing everything whose result is poison.
    check("poison result, call preserved", poison_ret, poison_ret, "proved")

    print("observable_call_fixture OK: a void call to a bodiless declaration no longer makes a whole "
          "function undecidable. It cannot change the returned value -- it returns nothing, and the "
          "scalar fragment gives it no pointer to write through -- but it IS observable, so the "
          "callee SEQUENCE must match as written (dropping, adding or reordering one declines) while "
          "the ARGUMENTS are proved equal in the solver, since rewriting them into equal-looking "
          "different terms is what the pass under test does. Passing a different value refutes with a "
          "witness, and a call whose result is used still declines rather than assuming a purity "
          "LLVM does not promise. The call's obligation sits BESIDE the returned value's rather than "
          "inside it -- an observable call is observable whatever the function returns -- and while "
          "those terms were nested under the result's poison guard, a source whose result is poison "
          "hid the difference completely: a false proof reference Alive2 refutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
