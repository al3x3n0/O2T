#!/usr/bin/env python3
"""Signature changes: verify a transform that removes a function parameter (deadargelim).

The last level of the composition axis. A pass like deadargelim changes a function's SIGNATURE, which
per-function TV cannot compare (mismatched params). o2t/validate/module_tv.signature_tv verifies it
soundly: the change is a refinement iff every REMOVED parameter was DEAD (unused in the body) AND the
function -- as a function of the SURVIVING parameters -- refines. Wired into module_tv, so a module
pass that both deletes functions and changes signatures is verified end-to-end.

  * deadargelim removing a dead argument -> PROVED (the arg was unused; behavior over surviving args
    unchanged), and the whole module proves;
  * TEETH -- removing a LIVE argument (still used in the body) is REFUTED;
  * added/promoted parameters are a sound decline (not modeled). Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.module_tv import signature_tv, module_tv  # noqa: E402

# @f has a dead second arg; @main calls it (so its return stays live -- only the arg is removed).
MOD = ("define internal i32 @f(i32 %x, i32 %dead) {\n  %r = add i32 %x, 1\n  ret i32 %r\n}\n"
       "define i32 @main() {\n  %a = call i32 @f(i32 5, i32 99)\n  ret i32 %a\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("signature_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. deadargelim removes the dead %dead from @f. The signature change is verified sound: %dead was
    #    unused and @f over its surviving arg is unchanged. The whole module proves.
    after = si.run_passes(MOD, "deadargelim", opt)
    assert after is not None
    sv = signature_tv(z3, MOD, after, "f")
    assert sv["status"] == "proved" and sv.get("removed") == ["%dead"], ("dead-arg removal must prove", sv)
    assert module_tv(z3, MOD, after)["module"] == "proved", "the whole module must prove"

    # 2. TEETH -- removing a LIVE argument (%x, still used in the body) is refuted.
    bad = ("define internal i32 @f(i32 %dead) {\n  %r = add i32 %x, 1\n  ret i32 %r\n}\n"
           "define i32 @main() {\n  %a = call i32 @f(i32 5, i32 99)\n  ret i32 %a\n}\n")
    tv_bad = signature_tv(z3, MOD, bad, "f")
    assert tv_bad["status"] == "refuted", ("removing a live argument must refute", tv_bad)

    # 3. No signature change -> ordinary whole-function TV (signature_tv is a transparent superset).
    same = signature_tv(z3, MOD, MOD, "f")
    assert same["status"] == "proved", same

    print("signature_tv_fixture OK: a parameter-removing transform (deadargelim) is verified sound -- a "
          "dead argument removed PROVES (the arg was unused; behavior over the surviving args unchanged) "
          "and the whole module proves; removing a LIVE argument is REFUTED. Wired into module_tv, so a "
          "pass that deletes functions AND changes signatures is checked end-to-end. The composition "
          "axis -- fixpoint, pipeline, module deletion, interprocedural, signature changes -- is closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
