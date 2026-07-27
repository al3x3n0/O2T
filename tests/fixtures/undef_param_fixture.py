#!/usr/bin/env python3
"""The `noundef` assumption Track B was making silently -- and the false proofs it produced.

Track B models each parameter as ONE definite SMT constant. LLVM does not: unless a parameter is
declared `noundef`, an argument may be `undef`, and an `undef` value is not one value -- each USE of
it may observe a different one. So the model was silently assuming `noundef` on every argument, and
that assumption was LOAD-BEARING for a whole class of transforms:

    define i32 @f(i32 %x) { ret i32 0 }   ->   { %r = xor i32 %x, %x   ret i32 %r }

PROVED (both sides are 0 when `%x` is a single constant) while reference Alive2 REFUTES it (with an
undef argument the two uses may differ, so the target can return anything). Adding `noundef %x` makes
Alive2 prove the very same transform, which pins the mechanism exactly: the verdict was right under an
assumption that was never declared.

Why no oracle caught it. `lli` and Alive2 run only over the corpus sweeps, and real InstCombine never
INTRODUCES a duplicated argument use -- it folds in the other direction. The class is reachable through
the `validate_transform` API, which `compose_tv`, `module_tv`, `argprom_tv` and anyone validating their
own pass all go through. Hand-built adversarial probes found it; the campaigns could not.

THE GUARD (`validate_transform`): decline when the TARGET's returned value or its poison depends on a
non-`noundef` parameter that the SOURCE's does not. That is exactly the shape where the single-constant
model is load-bearing -- the source is determined where the target is not, so the target has behaviours
the source lacks. Deliberately NOT keyed on UB: UB is checked existentially over the parameter's whole
range either way, so including it only over-declines (it wrongly declined the introduce-a-dead-
div-by-zero teeth). Measured cost: 0 of 447 end-to-end-translatable functions on LLVM 18's
and/or/xor/add/select/freeze.ll -- the 10 there whose target multiplies a parameter use all have a
source that already depends on it, and Alive2 confirms all 10 sound.

`noundef` is now also PARSED (`_noundef_params`), so the assumption can be declared and the guard
skipped; parameter attributes in general no longer make a function decline on an unresolvable operand.

Gated here, with every decisive verdict confirmed against Alive2:
  * the three known false proofs (`xor`, `sub`, `icmp eq` of a parameter with itself) now DECLINE;
  * `noundef` on the parameter makes the same transform PROVE (the assumption, declared);
  * a source that only mentions the parameter in DEAD code is still guarded -- the test is on the
    result term, not on textual occurrence, and Alive2 confirms that case is genuinely unsound;
  * sound transforms are untouched (`%x -> and %x, %x`, ordinary folds);
  * the introduce-a-dead-div-by-zero teeth still REFUTE (the guard must not swallow UB refutations).
Needs z3; the Alive2 confirmations self-skip without `alive-tv`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402


def f(sig, body):
    return f"define {sig} {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("undef_param_fixture: z3 not found, skipped")
        return 0
    alive = shutil.which("alive-tv")

    def check(name, sig, before_body, after_body, want, want_alive=None):
        before, after = f(sig, before_body), f(sig, after_body)
        v = si.validate_transform(z3, before, after, "f", timeout=30)
        assert v["status"] == want, (name, want, v)
        if alive and want_alive:
            av = alive_refines(before, after, alive).get("status")
            assert av == want_alive, (name, "Alive2", want_alive, av)
        return v

    # 1) THE FALSE PROOFS. Each of these PROVED before the guard; Alive2 refutes all three.
    for op, ret, sig in (("xor", "i32", "i32 @f(i32 %x)"),
                         ("sub", "i32", "i32 @f(i32 %x)")):
        v = check(f"{op} x,x", sig, "  ret i32 0",
                  f"  %r = {op} i32 %x, %x\n  ret i32 %r", "unsupported", "refuted")
        assert "%x" in v["reason"] and "noundef" in v["reason"], ("name the parameter and the cure", v)
    check("icmp eq x,x", "i1 @f(i32 %x)", "  ret i1 true",
          "  %r = icmp eq i32 %x, %x\n  ret i1 %r", "unsupported", "refuted")

    # 2) DECLARED, therefore provable: with `noundef` the model's assumption is the function's
    #    contract, and Alive2 agrees. (This also exercises attribute parsing -- before, an attributed
    #    parameter was not matched at all and the function declined on an unresolvable operand.)
    check("noundef xor x,x", "i32 @f(i32 noundef %x)", "  ret i32 0",
          "  %r = xor i32 %x, %x\n  ret i32 %r", "proved", "proved")
    assert si._noundef_params(f("i32 @f(i32 noundef %x, i32 %y)", "  ret i32 0"), "f") == {"%x"}
    # An attribute may itself contain a comma and parentheses -- `byval({ i32, i64 })` is valid
    # LLVM 18 -- so the signature is captured to the MATCHING paren and split at paren depth zero.
    # Naively, the capture stopped at the first `)` and the split severed the type from the name,
    # silently dropping every later parameter (they then declined as unresolvable operands).
    attributed = f("i32 @f(ptr byval({ i32, i64 }) align 8 %s, i32 noundef %y)", "  ret i32 %y")
    assert si._params(attributed, "f") == {"%y": 32}, "the integer parameter must survive the attribute"
    assert si._noundef_params(attributed, "f") == {"%y"}, "and its noundef must be seen"

    # 3) The test is on the RESULT TERM, not textual occurrence: a source that mentions the parameter
    #    only in dead code is still guarded -- and Alive2 confirms that transform really is unsound,
    #    so this is not an over-decline.
    check("dead source use", "i32 @f(i32 %x)", "  %d = add i32 %x, 1\n  ret i32 0",
          "  %r = xor i32 %x, %x\n  ret i32 %r", "unsupported", "refuted")

    # 4) NOT trivially declining. Where the source's result already depends on the parameter, the
    #    target is free to use it as often as it likes -- these still prove, as they must.
    check("x -> and x,x", "i32 @f(i32 %x)", "  ret i32 %x",
          "  %r = and i32 %x, %x\n  ret i32 %r", "proved", "proved")
    check("x&1 -> mul(a,a)", "i32 @f(i32 %x)", "  %a = and i32 %x, 1\n  ret i32 %a",
          "  %a = and i32 %x, 1\n  %r = mul i32 %a, %a\n  ret i32 %r", "proved", "proved")
    check("ordinary fold", "i32 @f(i32 %x, i32 %y)", "  %a = add i32 %x, 0\n  ret i32 %a",
          "  ret i32 %x", "proved", "proved")

    # 5) The guard must not swallow UB refutations. A parameter reaching only the target's UB term is
    #    not a determinism problem -- introducing a dead div-by-zero must still REFUTE with a witness.
    v = check("introduce dead div-by-zero", "i32 @f(i32 %a, i32 %b)", "  ret i32 %a",
              "  %bad = udiv i32 %a, %b\n  ret i32 %a", "refuted", "refuted")
    assert v.get("witness"), ("the UB refutation must still ship a witness", v)

    oracle = "confirmed against reference Alive2" if alive else "Alive2 absent (skipped)"
    print("undef_param_fixture OK: the undeclared `noundef` assumption is closed. Modeling each "
          "parameter as ONE definite constant made `ret 0 -> xor %x,%x` (and `sub`, `icmp eq`) PROVE "
          "while Alive2 refutes them -- an undef argument may read differently at each use, and adding "
          "`noundef` makes Alive2 prove the same transform, pinning the mechanism. No oracle caught it: "
          "real InstCombine never introduces a duplicated argument use, so only the API reaches the "
          "class. The guard declines exactly where the assumption is load-bearing (target result "
          "depends on a non-`noundef` parameter the source result does not), leaves sound transforms "
          f"and UB refutations alone, and costs 0 of 447 corpus proofs. Every verdict {oracle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
