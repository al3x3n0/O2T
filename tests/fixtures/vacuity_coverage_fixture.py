#!/usr/bin/env python3
"""A proof nobody probed for vacuity must not be counted as a proof that passed the probe.

WHY. Refinement holds trivially wherever the SOURCE is UB or poison on every input, so a vacuous
`proved` is valid but says nothing about the transform -- and it is the exact signature of an
OVER-APPROXIMATED UB model, which turns would-be refutations into proofs. Crucially it is the ONE
shape the external oracles cannot see: `lli` and reference Alive2 are consulted only on the PROVED
set, and they agree that a UB source refines to anything. If O2T's own probe does not catch it,
nothing does.

WHAT WAS WRONG. Only the SCALAR validator has that probe. `mem_state` and `vec_tv` have none -- the
word "vacuous" appeared in neither file. Their proofs therefore carried `vacuous: None`, which the
verdict schema documents as "the probe was inconclusive". Those are very different claims: one says
a guard ran and could not decide, the other says no guard exists. Measured over the pinned corpus,
521 of 1,826 proofs (28.5%) were the second case while the report showed only `vacuous: 1` --
inviting the reading that the other 1,825 had been checked. On `shift.ll` alone: the scalar
validator probed 117 proofs and DECIDED every one, while all 48 unprobed were `vec`/`svec`.

WHAT THIS PINS. That each validator DECLARES its vacuity coverage, and that the corpus layer counts
the unprobed separately instead of folding them into the clean majority. It does NOT pin that
vector/memory proofs are non-vacuous -- they are still unprobed. Extending the probe to those two
validators is the open work; this fixture makes the gap impossible to overlook while it is open,
and will fail (correctly) once it is closed, forcing the numbers to be restated.

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

    # 2) THE VECTOR PROOF IS NOT PROBED, and must ALSO say so rather than look inconclusive.
    #    This is the assertion that keeps 521 unprobed proofs from reading as checked.
    v = proved["vector_id"]
    assert v.get("vacuity_probe") == "absent", \
        ("vec_tv has no non-vacuity guard and the verdict must declare that, instead of leaving "
         "`vacuous: None` to be misread as an inconclusive probe", v)

    # 3) THE CORPUS LAYER COUNTS THEM SEPARATELY. Folding unprobed proofs into the clean count is
    #    the actual reporting bug; a bare `vacuous: 0` over this input would be true and misleading.
    assert res["vacuity_unprobed"] >= 1, \
        ("proofs with no vacuity guard must be counted and surfaced, not absorbed into the total",
         res.get("vacuity_unprobed"))
    assert res["vacuity_unprobed"] == sum(
        1 for f in proved.values() if f.get("vacuity_probe") == "absent"), res

    print("vacuity_coverage_fixture OK: the scalar validator declares its probe ran and decides "
          "vacuity; vec/mem proofs declare the probe ABSENT rather than presenting as inconclusive; "
          "the corpus layer counts unprobed proofs separately so they cannot be read as checked "
          "(521 of 1,826 corpus proofs are in that state, and vacuity is the one shape lli and "
          "Alive2 structurally cannot see)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
