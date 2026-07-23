#!/usr/bin/env python3
"""Differential against reference Alive2 -- the ground-truth POISON oracle O2T otherwise lacks.

O2T's internal cross-checks share its hand-written SMT encoding, and its execution oracle (concrete_tv,
via lli) is VALUE-only -- it cannot see a purely-poison difference. Reference Alive2 independently
models poison/undef/UB refinement, so it catches a poison-encoding bug that both z3 (same encoding) and
lli (values agree) miss. o2t/validate/alive_diff.py runs `alive-tv before after` and compares its
verdict to O2T's whole-function TV.

Gated here:
  * agreement on a real sound fold (add x,0 -> x): O2T proves, Alive2 proves;
  * the POISON case (add -> add nsw): O2T and Alive2 both REFUTE -- and (the point) concrete_tv/lli
    AGREE on it (values match), so Alive2 covers exactly concrete_tv's blind spot;
  * THE HEADLINE -- inject a poison-encoding bug (drop nsw from the model); O2T then FALSELY proves
    add -> add nsw, and the Alive2 differential CATCHES it (`o2t-false-proof`). This is the poison-class
    false proof that neither z3's shared encoding nor lli's value oracle can see;
  * a value miscompile (add -> sub): both refute.
Needs z3 + alive-tv (Alive2).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.alive_diff import alive_refines, differential  # noqa: E402
from o2t.validate.concrete_tv import concrete_tv  # noqa: E402


def f(body):
    return f"define i32 @f(i32 %x, i32 %y) {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    alive = shutil.which("alive-tv")
    if z3 is None or alive is None:
        print("alive_diff_fixture: z3 or alive-tv not found, skipped")
        return 0

    # 1. AGREEMENT on a real sound fold: add x,0 -> x. Both O2T and Alive2 prove it.
    add0 = f("  %a = add i32 %x, 0\n  ret i32 %a")
    idx = f("  ret i32 %x")
    d1 = differential(z3, alive, add0, idx, "f")
    assert d1["verdict"] == "agree" and d1["o2t"] == "proved" and d1["alive2"] == "proved", \
        ("O2T and Alive2 must agree the fold is sound", d1)

    # 2. The POISON case: add -> add nsw. O2T and Alive2 both REFUTE (poison introduction). Critically,
    #    the lli VALUE oracle AGREES on it (values match), so Alive2 covers concrete_tv's blind spot.
    add = f("  %r = add i32 %x, %y\n  ret i32 %r")
    nsw = f("  %r = add nsw i32 %x, %y\n  ret i32 %r")
    d2 = differential(z3, alive, add, nsw, "f")
    assert d2["verdict"] == "agree" and d2["alive2"] == "refuted", ("Alive2 must refute add->add nsw", d2)
    lli = toolchain.resolve_lli()
    if lli:
        blind = concrete_tv(lli, add, nsw, "f")["status"]
        assert blind == "agree", ("the poison diff is INVISIBLE to the lli value oracle -- Alive2's job", blind)

    # 3. THE HEADLINE: inject a poison-encoding bug (nsw un-modeled). O2T then FALSELY proves add->add
    #    nsw; the Alive2 differential CATCHES it. This is the poison-class false proof z3 (same encoding)
    #    and lli (value oracle) both miss.
    saved = si.VALID_FLAGS["bvadd"]
    try:
        si.VALID_FLAGS["bvadd"] = {"nuw"}              # nsw poison no longer modeled -> a false proof
        assert si.validate_transform(z3, add, nsw, "f")["status"] == "proved", \
            "with the bug, O2T should falsely prove add->add nsw"
        caught = differential(z3, alive, add, nsw, "f")
        assert caught["verdict"] == "o2t-false-proof", \
            ("the Alive2 differential must catch the poison-encoding false proof", caught)
    finally:
        si.VALID_FLAGS["bvadd"] = saved

    # 4. A value miscompile: add -> sub. Both refute (a sanity check the differential is not vacuous).
    sub = f("  %r = sub i32 %x, %y\n  ret i32 %r")
    assert alive_refines(add, sub, alive)["status"] == "refuted", "Alive2 must refute add->sub"

    print("alive_diff_fixture OK: reference Alive2 is wired as an INDEPENDENT ground-truth oracle -- it "
          "agrees with O2T on a sound fold, refutes add->add nsw (a POISON difference the lli value "
          "oracle is blind to), and (the headline) CATCHES an injected poison-encoding FALSE PROOF that "
          "both z3's shared encoding and lli miss. Track B's poison blind spot is now independently checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
