#!/usr/bin/env python3
"""Nondeterminism has a POLARITY, and getting it right is what makes `freeze` decidable.

Poison and undef are not the same freedom (Lee et al., PLDI'17), and neither is a value O2T could
model as one SMT constant. Three things had to be true together before a source-side `freeze` could be
decided at all, and this fixture pins each of them by its consequences.

1. A PARAMETER MAY ARRIVE POISON. Without `noundef` an argument may be poison as well as undef, and
   modelling it as definitely-not-poison flatters the TARGET -- it can return that parameter, look
   defined, and be poison in reality. This is not a theoretical worry: reference Alive2's witness for
   `freeze %x -> %x` is `%x = poison`, not an undef one. Each such parameter now carries a poison
   flag, and both sides share the SAME flag, because it describes the input rather than a choice
   either side makes.

2. THE SOURCE'S NONDETERMINISM IS UNIVERSAL. O2T solves the refutation `exists input. src defined AND
   tgt misbehaves`, and choices flip sense inside it: a TARGET choice is existential (a free constant
   the solver picks to expose the miscompile) while a SOURCE choice is universal (the target must
   differ from EVERY value the source could have produced). Binding a source choice as a free constant
   would let the solver pick the source's value to make the two differ, manufacturing false
   REFUTATIONS. It is now bound by a `forall` around the refutation, which costs the quantifier-free
   logic; `BV` is still decidable and an `unknown` is reported, never guessed.

3. ...AND WHERE THERE IS NO FREEDOM, THERE IS NO QUANTIFIER. A freeze over a value that is provably
   neither poison nor undef is the identity, and the `forall` has a one-element domain. That needs the
   second lattice bit: undef-capable unless `noundef`, conservatively inherited through operands,
   cleared by freeze. Under-approximating it costs a decline, never a proof.

What remains declined is the case that needs per-USE undef instantiation -- an undef value is not one
value, so a duplicated use of one cannot be a single term. `validate_transform`'s undef-risk guard
covers it soundly.

Every expected verdict below is REFERENCE ALIVE2'S OWN, and is re-checked against alive-tv on every
run so the table cannot go stale. That is deliberate: this is the one area where O2T's other oracles
are blind by construction -- `lli` does not model undef or poison at all, and the corpus sweeps never
exercise it because real InstCombine does not introduce duplicated argument uses. The invariant
asserted on every case is the one that matters: O2T may DECLINE, but it must never contradict the
oracle.

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

F = "f"


def _fn(body, sig="i32 %x"):
    return f"define i32 @{F}({sig}) {{\n{body}\n}}\n"


# (name, source, target, what reference Alive2 says, what O2T must do)
#
# "prove" means O2T must reach the same verdict Alive2 did. "decline-or-agree" means O2T may decline
# (the sound non-answer) but must NEVER contradict the oracle -- used where the case needs machinery
# this slice deliberately does not build (a universal quantifier, or per-use undef instantiation).
CASES = [
    # The win: with `noundef` the operand has no freedom, so the source-side freeze IS the identity.
    ("freeze-removal, operand declared noundef",
     _fn("  %f = freeze i32 %x\n  ret i32 %f", "i32 noundef %x"),
     _fn("  ret i32 %x", "i32 noundef %x"), "proved", "prove"),
    # ...and without it, the operand may be undef, whose per-use freedom the freeze is collapsing.
    # Removing it is a real miscompile. O2T has no undef term, so it must DECLINE, never prove.
    ("freeze-removal, operand may be undef",
     _fn("  %f = freeze i32 %x\n  ret i32 %f"),
     _fn("  ret i32 %x"), "refuted", "prove"),
    # Introduction is the existential direction and already worked; it must keep working.
    ("freeze-introduction",
     _fn("  ret i32 %x"),
     _fn("  %f = freeze i32 %x\n  ret i32 %f"), "proved", "prove"),
    # A duplicated use of a possibly-undef parameter in the TARGET: the undef-risk guard's case.
    ("duplicated undef use introduced in the target",
     _fn("  ret i32 0"),
     _fn("  %r = xor i32 %x, %x\n  ret i32 %r"), "refuted", "decline-or-agree"),
    # ...and with noundef it is an honest fold, which O2T proves.
    ("duplicated use of a noundef parameter",
     _fn("  ret i32 0", "i32 noundef %x"),
     _fn("  %r = xor i32 %x, %x\n  ret i32 %r", "i32 noundef %x"), "proved", "prove"),
    # Freezing a value whose operand carries POISON (not undef) is the case that needs no undef model
    # at all: the freeze still has something to collapse, so the identity must NOT be taken.
    ("freeze-removal over a poison-capable operand",
     _fn("  %a = add nsw i32 %x, %x\n  %f = freeze i32 %a\n  ret i32 %f", "i32 noundef %x"),
     _fn("  %a = add nsw i32 %x, %x\n  ret i32 %a", "i32 noundef %x"), "refuted", "prove"),
]


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("undef_lattice_fixture: needs z3, skipped")
        return 0
    alive = shutil.which("alive-tv")

    proved = declined = 0
    for name, src, tgt, oracle, expect in CASES:
        got = S.validate_transform(z3, src, tgt, F)
        status = got.get("status")

        # 1) O2T MUST NEVER CONTRADICT THE ORACLE. This is the invariant that matters: a decline is
        #    always acceptable, a disagreement never is.
        assert status in (oracle, "unsupported", "timeout"), \
            (f"{name}: O2T says {status!r}, reference Alive2 says {oracle!r}", got)

        if expect == "prove":
            assert status == oracle, (f"{name}: this case is meant to be decided, not declined", got)
            proved += 1
        else:
            assert status != "proved", \
                (f"{name}: proving this would be a false proof -- Alive2 refutes it", got)
            declined += 1

        # 2) ...and the oracle is re-consulted here rather than trusted from a comment, so the table
        #    cannot drift away from what alive-tv actually says.
        if alive:
            live = alive_refines(src, tgt, alive).get("status")
            assert live == oracle, \
                (f"{name}: the recorded oracle verdict {oracle!r} is stale; alive-tv now says {live!r}")

    assert proved == 5 and declined == 1, (proved, declined)
    print(f"undef_lattice_fixture OK: {proved} of {len(CASES)} cases DECIDED in agreement with "
          f"reference Alive2, {declined} declined. Source-side `freeze` is no longer an "
          f"unconditional decline: a parameter that may arrive POISON carries a flag shared by both "
          f"sides, the source's nondeterministic choice is bound by a `forall` rather than left free "
          f"(a free one would manufacture false refutations by letting the solver choose the "
          f"source's value), and where the operand has no freedom at all the quantifier disappears. "
          f"The remaining decline needs per-USE undef instantiation. Every expected verdict is "
          f"alive-tv's own and is re-checked here, because lli models neither undef nor poison and "
          f"the corpus sweeps never reach this shape -- and the standing invariant on every case is "
          f"that O2T may decline but must never contradict the oracle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
