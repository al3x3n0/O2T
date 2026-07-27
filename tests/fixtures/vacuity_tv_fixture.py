#!/usr/bin/env python3
"""Track B gets the two guards Track A always had: ANTI-VACUITY and a SECOND-SOLVER cross-check.

Track B's `validate_transform` is one hand-built SMT encoding decided by one z3 call. Two things were
unchecked -- and the first shares its failure shape with the two false proofs the 2026-07 review found
by hand: a model corner that silently converts a would-be refutation into a proof.

  1. ANTI-VACUITY. Refinement is vacuously true wherever the SOURCE is UB or poison, so a source that
     is UB on EVERY input refines to ANYTHING -- `udiv %x, 0` legitimately "proves" against
     `ret i32 12345`. Such a verdict is valid but says nothing about the transform, and it is exactly
     what an OVER-APPROXIMATED UB/poison model degrades into: claim UB where LLVM has none and a
     would-be refutation silently becomes a proof, invisible to every other oracle (lli and Alive2 are
     only consulted on the `proved` set, and they agree that a UB source refines to anything). Track A
     has had this guard since `mini_alive` (premises must be jointly SAT); Track B had none.
  2. SOLVER INDEPENDENCE. The three existing oracles (Track A reconcile, concrete_tv/lli, alive_diff)
     all check the ENCODING. None checks z3 itself. Since `validate_transform` already emits SMT-LIB2
     to the z3 binary, the identical script replays through an independently implemented solver.

Gated here:
  * vacuous UB source and vacuous POISON source are flagged `vacuous: True`;
  * a real fold is `vacuous: False` -- the flag is not trivially on;
  * THE HEADLINE -- inject an over-approximated UB model into scalar_ir (`add` claims UB) so a genuine
    value miscompile FALSELY PROVES; the vacuity probe CATCHES it (the proof rests on a source that is
    never defined). This is the failure mode no other oracle can see;
  * a `refuted` verdict claims no vacuity (the flag rides only on proofs);
  * the second-solver cross-check AGREES on a proved and on a refuted query, is caught DISAGREEING by
    a lying stub solver, and reports `skipped` (never a silent pass) when no second solver exists.
Needs z3; the second-solver half self-skips without bitwuzla/cvc5/cvc4 (the stub teeth always run).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FX = Path(__file__).resolve().parent
ROOT = FX.parents[1]
sys.path.insert(0, str(ROOT))
from o2t.meta import cross_check as cc  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402


def fn(body, params="i32 %x, i32 %y", ret="i32"):
    return f"define {ret} @f({params}) {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("vacuity_tv_fixture: z3 not found, skipped")
        return 0

    ub_src = fn("  %d = udiv i32 %x, 0\n  %r = add i32 %d, %y\n  ret i32 %r")
    poison_src = fn("  %s = shl nsw i32 %x, 33\n  ret i32 %s", params="i32 %x")
    junk = fn("  ret i32 12345")
    ident = fn("  ret i32 %x")
    add0 = fn("  %a = add i32 %x, 0\n  ret i32 %a")

    # 1) A source that is UB on every input refines to ANYTHING -- valid, and information-free.
    v = si.validate_transform(z3, ub_src, junk, "f", timeout=30)
    assert v["status"] == "proved" and v["vacuous"] is True, ("UB-everywhere source must be vacuous", v)

    # 2) Same for a source that is POISON on every input (an oversize `shl nsw`).
    v = si.validate_transform(z3, poison_src, fn("  ret i32 7", params="i32 %x"), "f", timeout=30)
    assert v["status"] == "proved" and v["vacuous"] is True, ("poison-everywhere source is vacuous", v)

    # 3) NOT trivially on: a real fold over a defined source is a NON-vacuous proof.
    v = si.validate_transform(z3, add0, ident, "f", timeout=30)
    assert v["status"] == "proved" and v["vacuous"] is False, ("a real fold must not be vacuous", v)

    # 4) THE HEADLINE: an over-approximated UB model turns a genuine miscompile into a FALSE PROOF,
    #    and only the vacuity probe can see it. Monkeypatch `_own_ub` to claim `add` is always UB --
    #    a different model corner than `udiv exact` (round 3) or mixed min/max (round 6), but the same
    #    failure shape: the model, not the transform, is what makes the obligation come out unsat.
    #    `add x,y -> add x,x` is a real miscompile; with the injected bug z3 proves it, because the
    #    source is "never defined". lli and Alive2 never see it (they only run on the proved set and
    #    would agree the transform is fine only if it were fine -- here O2T's own model is the liar).
    before = fn("  %r = add i32 %x, %y\n  ret i32 %r")
    miscompile = fn("  %r = add i32 %x, %x\n  ret i32 %r")
    assert si.validate_transform(z3, before, miscompile, "f", timeout=30)["status"] == "refuted", \
        "sanity: add x,y -> add x,x is a genuine miscompile"
    saved = si._own_ub
    try:
        si._own_ub = lambda name, a, b, w: "true" if name == "add" else saved(name, a, b, w)
        bad = si.validate_transform(z3, before, miscompile, "f", timeout=30)
        assert bad["status"] == "proved", \
            ("with an over-approximated UB model the miscompile should FALSELY prove", bad)
        assert bad["vacuous"] is True, \
            ("the anti-vacuity probe must CATCH the over-approximated-UB false proof", bad)
    finally:
        si._own_ub = saved
    # reverted: the miscompile is refuted again, and the guard reports no vacuity on a refutation.
    r = si.validate_transform(z3, before, miscompile, "f", timeout=30)
    assert r["status"] == "refuted" and "vacuous" not in r, ("vacuity rides only on proofs", r)

    # 5) `check_vacuity=False` opts out (the extra query is skippable for cost-sensitive sweeps).
    v = si.validate_transform(z3, add0, ident, "f", timeout=30, check_vacuity=False)
    assert v["status"] == "proved" and "vacuous" not in v, ("opt-out must not report vacuity", v)

    # 6) SECOND-SOLVER CROSS-CHECK: the identical script replayed through an independent solver.
    detected = [name for name, _ in cc.detect_solvers(z3) if name != "z3"]
    p = si.validate_transform(z3, add0, ident, "f", timeout=30, cross_check=True)
    d = si.validate_transform(z3, before, miscompile, "f", timeout=30, cross_check=True)
    if detected:
        assert p["cross_check"]["status"] == "agree" and p["cross_check"]["solvers"], \
            ("an independent solver must reproduce the proof", p["cross_check"])
        assert d["cross_check"]["status"] == "agree", \
            ("an independent solver must reproduce the refutation", d["cross_check"])
        assert set(p["cross_check"]["solvers"]) >= {detected[0]}, p["cross_check"]
    else:                       # honest: never claim a pass we did not run
        assert p["cross_check"]["status"] == "skipped", p["cross_check"]

    # 7) CROSS-CHECK TEETH: a stub solver that always answers `sat` must be caught disagreeing with a
    #    proved (unsat) query -- the harness is not vacuously agreeing with itself.
    stub = str((FX / "cross_check_sat_stub.sh").resolve())
    lying = si.validate_transform(z3, add0, ident, "f", timeout=30, cross_check=True,
                                  extra_solvers=[("fakesat", stub)])
    assert lying["cross_check"]["status"] == "disagree" and \
        lying["cross_check"]["solvers"]["fakesat"] == "sat", \
        ("a lying second solver must be caught", lying["cross_check"])
    # ...and z3 replayed as the "second solver" agrees (the multi-solver path itself works).
    twin = si.validate_transform(z3, add0, ident, "f", timeout=30, cross_check=True,
                                 extra_solvers=[("z3b", z3)])
    assert twin["cross_check"]["status"] == "agree", twin["cross_check"]

    # 8) The cross-check reaches the FALLBACK validators too -- and matters most for mem_state, the
    #    only QF_ABV (theory-of-arrays) encoding in Track B and the least-exercised corner of the
    #    solver stack. Vectors are QF_BV but a distinct lane encoding. Neither carries a vacuity flag:
    #    they compare VALUES, so there is no UB/poison term that could be over-approximated.
    from o2t.validate.mem_state import mem_state_tv  # noqa: E402
    from o2t.validate.vec_tv import vec_tv  # noqa: E402
    dse_b = ("define i32 @f(ptr %p) {\n  store i32 1, ptr %p\n  store i32 2, ptr %p\n"
             "  %v = load i32, ptr %p\n  ret i32 %v\n}\n")
    dse_a = "define i32 @f(ptr %p) {\n  store i32 2, ptr %p\n  ret i32 2\n}\n"
    m = mem_state_tv(z3, dse_b, dse_a, "f", cross_check=True)
    assert m["status"] == "proved" and "vacuous" not in m, ("dead-store elimination proves", m)
    vb = "define <4 x i32> @f(<4 x i32> %x) {\n  %r = add <4 x i32> %x, zeroinitializer\n  ret <4 x i32> %r\n}\n"
    va = "define <4 x i32> @f(<4 x i32> %x) {\n  ret <4 x i32> %x\n}\n"
    vv = vec_tv(z3, vb, va, "f", cross_check=True)
    assert vv["status"] == "proved" and "vacuous" not in vv, ("the vector fold proves", vv)
    for tag, res in (("mem_state/QF_ABV", m), ("vec/lane", vv)):
        want = "agree" if detected else "skipped"
        assert res["cross_check"]["status"] == want, (tag, res["cross_check"])
    # ...and the lying stub is caught on the array encoding too.
    bad_m = mem_state_tv(z3, dse_b, dse_a, "f", cross_check=True, extra_solvers=[("fakesat", stub)])
    assert bad_m["cross_check"]["status"] == "disagree", bad_m["cross_check"]

    solvers = ", ".join(detected) if detected else "none installed (stub teeth only)"
    print("vacuity_tv_fixture OK: Track B now has the two guards Track A always had. ANTI-VACUITY -- a "
          "UB-everywhere and a poison-everywhere source are flagged `vacuous`, a real fold is not, and "
          "(the headline) an injected OVER-APPROXIMATED UB model that turns a genuine miscompile into a "
          "FALSE PROOF is CAUGHT by the probe -- the one false-proof class no encoding oracle (lli, "
          f"Alive2) can see. SECOND-SOLVER CROSS-CHECK [{solvers}] -- the identical SMT-LIB2 replayed "
          "through an independent solver agrees on a proof and a refutation, a lying stub is caught, "
          "and an absent solver reports `skipped`, never a silent pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
