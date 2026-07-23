#!/usr/bin/env python3
"""Whole-PASS composition: verify a pass PIPELINE by composing per-pass TVs via refinement transitivity.

Whole-function TV validates ONE pass's net effect. This validates a whole PIPELINE compositionally
(o2t/validate/compose_tv.py): run each pass stage in sequence, TV each step (f_{i+1} refines f_i), and
-- because refinement is a preorder -- conclude f_n refines f_0 by TRANSITIVITY when every step proves.
No direct f0->fn proof is needed; a miscompiling pass is LOCALIZED to its step; a step outside the
scalar fragment makes the chain a sound `inconclusive`, never a false whole-pipeline proof.

Pinned here on `reassociate,instcombine` (3a via reassociate, left by instcombine):
  * both steps prove -> composed PROVED (transitivity), consistent with the direct f0->fn TV;
  * TEETH -- a miscompiling instcombine step (injected 4a for 3a) REFUTES, localized to instcombine
    while reassociate still proves -> composed REFUTED;
  * an intermediate outside the fragment (multi-block) makes a step unsupported -> composed
    INCONCLUSIVE, not a false proof.
Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.compose_tv import compose_tv, pipeline_irs  # noqa: E402

LL = ("define i32 @f(i32 %a) {\n"
      "  %1 = add i32 %a, %a\n  %2 = add i32 %1, %a\n  %3 = mul i32 %2, 1\n  ret i32 %3\n}\n")
STAGES = ["reassociate", "instcombine"]


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("compose_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. Compositional proof: every pass step is a refinement -> the whole pipeline is proved by
    #    transitivity, and each step names the pass responsible.
    r = compose_tv(z3, LL, "f", STAGES, opt)
    assert r["composed"] == "proved", ("pipeline must compose to proved", r)
    assert [s["status"] for s in r["steps"]] == ["proved", "proved"], r

    # 2. Consistency: the composed verdict matches the direct f0->fn TV (transitivity is not lying).
    irs = pipeline_irs(LL, STAGES, opt)
    assert si.validate_transform(z3, irs[0], irs[-1], "f")["status"] == "proved"

    # 3. TEETH -- a miscompiling instcombine step (4a substituted for the real 3a) refutes, LOCALIZED:
    #    reassociate still proves, instcombine refutes, the pipeline is refuted (not silently proved).
    bad = list(irs)
    bad[2] = "define i32 @f(i32 %a) {\n  %1 = mul i32 %a, 4\n  ret i32 %1\n}\n"
    rb = compose_tv(z3, LL, "f", STAGES, opt, irs=bad)
    assert rb["composed"] == "refuted", rb
    assert rb["steps"][0]["status"] == "proved" and rb["steps"][1]["status"] == "refuted", \
        ("the miscompile must localize to instcombine", rb["steps"])

    # 4. HONEST inconclusive: an intermediate outside the scalar fragment (a call to an external
    #    function) makes that step unsupported -> the chain is inconclusive, never a false proof.
    uns = list(irs)
    uns[2] = ("declare i32 @ext(i32)\n"
              "define i32 @f(i32 %a) {\n  %v = call i32 @ext(i32 %a)\n  ret i32 %v\n}\n")
    ru = compose_tv(z3, LL, "f", STAGES, opt, irs=uns)
    assert ru["composed"] == "inconclusive", ru
    assert ru["net"] in ("unsupported", "inconclusive", "timeout"), ru

    # 5. MASKED MISCOMPILE (zero-false-refutation): a pass introduces an unsound `nsw`, the NEXT pass
    #    removes it, so the net f0->fn is a sound refinement even though an intermediate step refutes.
    #    The per-step chain must NOT falsely refute the whole pipeline: `composed` follows the direct
    #    net verdict (proved), while `steps` still LOCALIZES the buggy first pass (refuted).
    f0 = "define i32 @f(i32 %a) {\n  %r = add i32 %a, %a\n  ret i32 %r\n}\n"
    f1 = "define i32 @f(i32 %a) {\n  %r = add nsw i32 %a, %a\n  ret i32 %r\n}\n"   # unsound nsw introduced
    masked = compose_tv(z3, f0, "f", STAGES, opt, irs=[f0, f1, f0])                # ...then removed
    assert masked["steps"][0]["status"] == "refuted", ("the buggy pass must still localize", masked)
    assert masked["steps"][1]["status"] == "proved", masked
    assert masked["net"] == "proved" and masked["composed"] == "proved", \
        ("a masked miscompile must NOT falsely refute a net-sound pipeline", masked)

    print("compose_tv_fixture OK: a pass PIPELINE (reassociate,instcombine) is verified compositionally "
          "-- each step translation-validated, the whole pipeline PROVED by refinement transitivity "
          "(consistent with the direct end-to-end TV); a miscompiling instcombine step is caught and "
          "LOCALIZED (reassociate proves, instcombine refutes -> refuted); an out-of-fragment step is a "
          "sound inconclusive; and a MASKED miscompile (an unsound nsw a later pass removes) is not "
          "falsely refuted -- composed follows the direct net verdict (proved) while steps still "
          "localize the buggy pass. Never a false whole-pipeline proof OR a false whole-pipeline refutation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
