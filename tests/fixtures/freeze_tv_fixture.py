#!/usr/bin/env python3
"""`freeze` in Track B -- the poison-laundering instruction InstCombine INTRODUCES.

Declining `freeze` blinded whole-function TV on exactly the poison-critical folds: `freeze` is how
InstCombine discharges `isGuaranteedNotToBePoison` (select->or/and, the `and`/`or` idiom rewrites),
so any function where it fired was `unsupported`.

SEMANTICS. If the operand is not poison, `freeze` is the identity; if it is, `freeze` yields ONE
arbitrary value, fixed for the execution -- so it is modeled as `ite(poison_v, fresh, v)` with a single
fresh constant per `freeze` instruction, and a result that is never poison.

The interesting part is the QUANTIFIER on that choice, which differs by side. Refinement is "every
TARGET behaviour is one the SOURCE could have produced", so in the refutation query the target's pick
is EXISTENTIAL -- a free constant the solver may choose -- while the source's pick is UNIVERSAL. A free
constant on the source side would let the solver pick the one differing value and report a FALSE
REFUTATION, and QF_BV cannot carry the quantifier, so a source-side `freeze` DECLINES.

It declines even when the operand is syntactically poison-free, and that refusal was NOT the first
design. Taking the obvious identity shortcut (`freeze` of a definite value is a no-op) makes
`freeze %x -> %x` PROVE -- and reference Alive2 REFUTES it: this model has no `undef` and treats
parameters as definite, but LLVM allows an argument to be `undef` unless `noundef`, and `freeze` is
precisely the instruction that observes the difference (target `%x` may be undef, source `%z` is one
fixed value). The shortcut was a false proof, caught by the oracle within minutes of being written.
REMOVING a freeze is therefore outside the fragment until `undef` is modeled; INTRODUCING one -- what
InstCombine actually does -- is inside it.

Gated here, with every decisive verdict CONFIRMED against reference Alive2 (the model was derived
against the oracle, not asserted):
  * introducing `freeze` over a poison-carrying value PROVES (it launders poison soundly);
  * introducing `freeze` over a parameter PROVES;
  * a wrong value under a `freeze` REFUTES;
  * TEETH -- a target that freezes NEWLY introduced poison where the source is definite REFUTES
    (the existential encoding is what makes this bite);
  * a source-side `freeze` DECLINES, including the `freeze %x -> %x` case Alive2 refutes;
  * `freeze` works on the multi-block path too.
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


def fn(body, params="i32 %x, i32 %y", ret="i32"):
    return f"define {ret} @f({params}) {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("freeze_tv_fixture: z3 not found, skipped")
        return 0
    alive = shutil.which("alive-tv")

    def check(name, before, after, want):
        v = si.validate_transform(z3, before, after, "f", timeout=30)
        assert v["status"] == want, (name, want, v)
        # Every decisive verdict must match the independent poison oracle. A decline needs no
        # confirmation (it claims nothing), but a proof or refutation does.
        if alive and want in ("proved", "refuted"):
            av = alive_refines(before, after, alive).get("status")
            assert av == want, (name, "Alive2 disagrees with O2T", want, av)
        return v

    poison = "  %a = add nsw i32 %x, %y\n"

    # 1) INTRODUCING freeze over a value that may be poison is sound -- it is how a pass discharges
    #    "this must not be poison" -- and proves.
    check("introduce over nsw", fn(poison + "  ret i32 %a"),
          fn(poison + "  %z = freeze i32 %a\n  ret i32 %z"), "proved")

    # 2) ...and over a parameter (poison-free in this model) it is the identity on the target side.
    check("introduce over param", fn("  ret i32 %x"),
          fn("  %z = freeze i32 %x\n  ret i32 %z"), "proved")

    # 3) A freeze does not launder a WRONG VALUE: the fold is still refuted.
    check("wrong value under freeze", fn(poison + "  ret i32 %a"),
          fn("  %a = sub nsw i32 %x, %y\n  %z = freeze i32 %a\n  ret i32 %z"), "refuted")

    # 4) TEETH for the EXISTENTIAL encoding. The source is definite (`add`, no flags); the target
    #    introduces poison (`add nsw`) and freezes it, so on overflow it returns an arbitrary value
    #    where the source returns a definite one -- unsound, and refuted. Model the target's choice
    #    universally instead and this would wrongly prove.
    v = check("freeze of NEW poison", fn("  %a = add i32 %x, %y\n  ret i32 %a"),
              fn("  %b = add nsw i32 %x, %y\n  %z = freeze i32 %b\n  ret i32 %z"), "refuted")
    assert v.get("witness"), ("a refutation must ship a witness", v)

    # 5) REMOVING a source-side freeze is now DECIDED, and refuted. The source's freeze makes the
    #    result definite; the target returns the `nsw` add itself, which is poison on overflow -- so
    #    the target is poison exactly where the source is defined. This used to decline: the source's
    #    choice is UNIVERSAL (the target must differ from every value the freeze could have picked),
    #    which QF_BV cannot state. It is now bound by a `forall` around the refutation, and `check`
    #    confirms the verdict against Alive2 rather than against this comment.
    d = check("source freeze of poison", fn(poison + "  %z = freeze i32 %a\n  ret i32 %z"),
              fn(poison + "  ret i32 %a"), "refuted")
    assert d.get("witness"), ("a refutation must ship a witness", d)

    # 6) ...including over a PARAMETER, where the identity shortcut looks safe and is not. An
    #    argument without `noundef` may arrive POISON -- that is Alive2's own witness for this
    #    transform, `%x = poison`, and not an undef one -- so the target returning `%x` is poison
    #    where the source's frozen `%z` is definite. Modelling a parameter as definitely-not-poison
    #    is what used to hide it; each such parameter now carries a poison flag shared by both sides.
    rm_before, rm_after = fn("  %z = freeze i32 %x\n  ret i32 %z"), fn("  ret i32 %x")
    check("remove a freeze over a parameter", rm_before, rm_after, "refuted")
    if alive:
        av = alive_refines(rm_before, rm_after, alive).get("status")
        assert av == "refuted", ("Alive2 should refute freeze-removal (undef argument)", av)

    # 7) The multi-block path shares `_instruction`, so freeze works there too.
    mb_src = ("define i32 @f(i32 %x, i32 %y) {\nentry:\n  %c = icmp sgt i32 %x, %y\n"
              "  br i1 %c, label %t, label %e\nt:\n  %a = add nsw i32 %x, %y\n  br label %m\n"
              "e:\n  br label %m\nm:\n  %p = phi i32 [ %a, %t ], [ %y, %e ]\n  ret i32 %p\n}\n")
    mb_tgt = mb_src.replace("  ret i32 %p", "  %z = freeze i32 %p\n  ret i32 %z")
    check("multiblock introduce", mb_src, mb_tgt, "proved")

    oracle = "confirmed against reference Alive2" if alive else "Alive2 absent (skipped)"
    print("freeze_tv_fixture OK: Track B models `freeze`, the poison-laundering instruction InstCombine "
          "INTRODUCES -- so the poison-critical folds are no longer blind declines. The nondeterministic "
          "choice is EXISTENTIAL on the target (a target that freezes newly introduced poison over a "
          "definite source is REFUTED with a witness) and UNIVERSAL on the source, which therefore "
          "DECLINES -- including `freeze %x -> %x`, where the tempting identity shortcut is a FALSE "
          f"PROOF that Alive2 refutes because an argument may be `undef`. Every verdict {oracle}; "
          "freeze-removal stays outside the fragment until undef is modeled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
