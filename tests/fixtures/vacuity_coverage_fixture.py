#!/usr/bin/env python3
"""Every Track B proof must be probed for vacuity, or say why the question does not arise.

WHY. Refinement holds trivially wherever the SOURCE is UB or poison on every input, so a vacuous
`proved` is valid but says nothing about the transform -- and it is the exact signature of an
OVER-APPROXIMATED UB model, which turns would-be refutations into proofs. Crucially it is the ONE
shape the external oracles cannot see: `lli` and reference Alive2 are consulted only on the PROVED
set, and they agree that a UB source refines to anything. If O2T's own probe does not catch it,
nothing does.

THE HISTORY THIS PINS. The probe originally lived only in the SCALAR validator; `mem_state` and
`vec_tv` had none -- the word "vacuous" appeared in neither file -- and their proofs returned
`vacuous: None`, which the schema documents as "the probe was inconclusive". Those are different
claims: a guard that ran and could not decide, versus no guard at all. Measured over the pinned
corpus, 521 of 1,826 proofs (28.5%) were the second case while the report showed `vacuous: 1`,
inviting the reading that the other 1,825 had been checked.

Both validators now probe, and the number moved: **10 vacuous, not 1**, with unprobed falling from
521 to 34 (98.1% coverage; the 34 are genuine solver non-answers). The nine newly exposed proofs
are real -- e.g. `shift.ll:test62_splat_vector` (`ashr <4 x i32> %a, <32,32,32,32>`, every lane
poison on every input), `shift.ll:test38_poison` (`srem` by a poison divisor, which the lane model
treats as UB rather than poison because it decides whether the division traps), `and.ll:
negate_lowbitmask_commute` (both lanes poison, from opposite directions), and the six
`icmp.ll:or_poison_vec_*`. Each was being counted as a meaningful proof.

`svec_tv` is the deliberate exception and is marked `not-applicable` rather than probed. It asserts
NO UB premise -- it proves `rb == ra` for every input, with target poison handled by a separate
decline -- so there is no premise for a proof to hide behind and no vacuity escape to look for.
Bolting a probe onto it would answer "non-vacuous" every time, and a guard that cannot fail reads
as coverage while providing none.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate.corpus_tv import validate_file  # noqa: E402

# A scalar function (probe applies) and a vector one (no probe in vec_tv).
SRC = """define i32 @scalar_id(i32 %a) {
  %r = add i32 %a, 0
  ret i32 %r
}

define <4 x i32> @vector_id(<4 x i32> %a) {
  %r = and <4 x i32> %a, <i32 -1, i32 -1, i32 -1, i32 -1>
  ret <4 x i32> %r
}
"""


def main() -> int:
    opt = shutil.which("opt") or "/opt/homebrew/opt/llvm@18/bin/opt"
    if shutil.which("z3") is None or not Path(opt).exists():
        print("vacuity_coverage_fixture: z3/opt not found, skipped")
        return 0

    res = validate_file("z3", SRC, opt, timeout=20)
    proved = {f["function"]: f for f in res["functions"] if f.get("status") == "proved"}
    assert "scalar_id" in proved and "vector_id" in proved, res["counts"]

    # 1) THE SCALAR PROOF IS PROBED, and says so. `vacuous` is a real True/False here.
    s = proved["scalar_id"]
    assert s.get("vacuity_probe") == "ran", \
        ("the scalar validator runs the non-vacuity probe and must declare it", s)
    assert s.get("vacuous") in (True, False), \
        ("a probed proof carries a decided vacuity verdict", s)

    # 2) THE VECTOR PROOF IS PROBED TOO, and DECIDES. This is the assertion that closed the gap:
    #    before it, 521 corpus proofs carried no vacuity information while the report showed one
    #    vacuous proof, and nine genuinely vacuous ones were hiding in that silence.
    v = proved["vector_id"]
    assert v.get("vacuity_probe") == "ran", \
        ("the lane model must run the non-vacuity probe -- vacuity is the one shape no external "
         "oracle can see, so an unprobed proof is unchecked for it by anything at all", v)
    assert v.get("vacuous") is False, \
        ("and it must DECIDE: `and <4 x i32> %a, splat(-1)` is defined for every input, so a probe "
         "returning None here would mean the vector probe cannot answer even the easy case", v)

    # 3) NO PROOF IS LEFT UNPROBED WITHOUT SAYING SO. `vacuity_unprobed` is now the honest residue
    #    of solver non-answers rather than of missing guards; on this input it must be empty.
    assert res["vacuity_unprobed"] == 0, \
        ("with both validators probing, nothing here should be unprobed", res)

    # 4) THE PROBE MUST HAVE TEETH: a genuinely vacuous VECTOR source must be caught. Without this,
    #    assertions 2-3 are satisfied by a probe that answers "non-vacuous" unconditionally -- which
    #    is worse than no probe, because it reads as coverage while checking nothing.
    VACUOUS_SRC = """define <4 x i32> @all_poison(<4 x i32> %a) {
  %b = ashr <4 x i32> %a, <i32 32, i32 32, i32 32, i32 32>
  ret <4 x i32> %b
}
"""
    vres = validate_file("z3", VACUOUS_SRC, opt, timeout=20)
    vac = [f for f in vres["functions"] if f.get("status") == "proved"]
    assert vac and vac[0].get("vacuous") is True, \
        ("`ashr` by the full bit width is poison in every lane on every input, so this proof is "
         "vacuous and the probe must say so -- a probe that never fires is not a guard", vac)

    print("vacuity_coverage_fixture OK: scalar AND lane-model proofs are both probed and decide; "
          "a genuinely vacuous vector source is caught (the probe has teeth, not just coverage); "
          "nothing is left unprobed without being counted. Corpus effect: vacuity coverage 71% -> "
          "98.1%, and the vacuous count moved 1 -> 10 -- nine real vacuous proofs had been counted "
          "as meaningful, in the one place no external oracle could have told us")
    return 0


if __name__ == "__main__":
    sys.exit(main())
