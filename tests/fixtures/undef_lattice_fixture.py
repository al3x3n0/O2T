#!/usr/bin/env python3
"""`undef` is a SECOND LATTICE LEVEL, and the first thing it buys is source-side `freeze`.

Poison and undef are not the same freedom (Lee et al., PLDI'17). Poison is a single value that
taints; `undef` is not one value at all -- **each USE of it may observe a different one**. O2T models
a single poison bit, so an undef-capable value has no representation, and the consequences are
visible in exactly two places:

  * `validate_transform`'s UNDEF-RISK GUARD, which declines when the TARGET's result depends on a
    possibly-undef parameter the source's does not (`ret i32 0 -> xor %x, %x` is the canonical case:
    it proves under a one-constant model and reference Alive2 refutes it);
  * `freeze` on the SOURCE side, which declined unconditionally.

The second of those is a REACH loss rather than a soundness one, and this fixture closes the part of
it that needs no new machinery. The refutation query O2T solves is `exists input. src defined and tgt
misbehaves`, and nondeterminism flips polarity inside it: a TARGET choice is existential (a free
constant the solver picks, which is why target-side freeze has always worked) while a SOURCE choice
is universal (the target must differ from EVERY source choice). QF_BV cannot express the universal
one, so source-side freeze declined.

But it does not always need to. `freeze V` is the IDENTITY exactly when V has no freedom to collapse
-- when V is neither poison nor undef -- and then no quantifier is required. That condition is what is
modelled here, as a second lattice bit carried beside the poison term:

  * a parameter is undef-capable unless declared `noundef`; constants are not;
  * an operation's result is undef-capable if any operand is (deliberately CONSERVATIVE -- `and %x, 0`
    is really defined whatever `%x` is, and calling it undef-capable only declines);
  * `freeze` clears it, which is what freeze is for.

Erring toward "may be undef" can only cost reach, never soundness: the identity is taken only when the
operand is provably free of both kinds of freedom.

Every expected verdict below is REFERENCE ALIVE2'S, taken from the oracle rather than from reasoning
about the semantics -- deliberately, because this is the one area where O2T's other oracles are blind
by construction. `lli` does not model undef at all, and the corpus sweeps never exercise it because
real InstCombine does not introduce duplicated argument uses. Alive2 is the only oracle here, so the
battery is built from what it says and the fixture asserts O2T agrees or declines, never that it
proves something Alive2 has not confirmed.

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
     _fn("  ret i32 %x"), "refuted", "decline-or-agree"),
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
     _fn("  %a = add nsw i32 %x, %x\n  ret i32 %a", "i32 noundef %x"), "refuted", "decline-or-agree"),
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

    assert proved == 3 and declined == 3, (proved, declined)
    print(f"undef_lattice_fixture OK: source-side `freeze` is no longer an unconditional decline -- it "
          f"is the identity exactly where the operand has no freedom to collapse, which is a second "
          f"lattice bit (undef-capable unless `noundef`, conservatively inherited through operands) "
          f"carried beside the poison term. {proved} cases now DECIDED in agreement with reference "
          f"Alive2, {declined} still declined because they need a universal quantifier or per-use "
          f"undef -- and the standing invariant is the one asserted on every case: O2T may decline, "
          f"but it must never contradict the oracle. Every expected verdict is alive-tv's own, "
          f"re-checked here so the table cannot go stale.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
