#!/usr/bin/env python3
"""Module-level composition: verify a whole-MODULE transform, including function deletion.

Whole-function TV and pipeline composition are per-function; a module pass also DELETES functions --
an effect no per-function proof sees. This pins module-level TV (o2t/validate/module_tv.py): a module
transform is a refinement iff every surviving function refines AND every deleted function was provably
DEAD (internal linkage + unreferenced in the result).

On real `globaldce` (removes a dead internal function):
  * survivor proved + dead function dead-removed -> module PROVED;
  * TEETH -- deleting an EXTERNAL (observable) function -> REFUTED (external-removed);
  * TEETH -- deleting a function still REFERENCED after -> REFUTED (live-removed / dangling).
So deleting dead code is verified sound, and deleting live/observable code is caught. Signature changes
and IPO value-flow are not yet modeled (a sound decline, not a false proof). Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.module_tv import module_tv  # noqa: E402

MOD = ("define i32 @keep(i32 %x) {\n  %r = and i32 %x, %x\n  ret i32 %r\n}\n"
       "define internal i32 @dead(i32 %x) {\n  %r = mul i32 %x, %x\n  ret i32 %r\n}\n")


def _status(steps, fn):
    return next(s["status"] for s in steps if s["function"] == fn)


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("module_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. SOUND: globaldce removes the dead internal @dead; @keep survives. The module transform is
    #    proved -- @keep refines, @dead is a justified dead removal.
    after = si.run_passes(MOD, "globaldce", opt)
    assert after is not None
    r = module_tv(z3, MOD, after)
    assert r["module"] == "proved", ("globaldce of a dead internal fn must prove", r)
    assert r["deleted"] == ["dead"] and _status(r["steps"], "dead") == "dead-removed", r
    assert _status(r["steps"], "keep") == "proved", r

    # 2. TEETH -- deleting the EXTERNAL @keep is unsound (its behavior is observable): refuted.
    drop_external = "define internal i32 @dead(i32 %x) {\n  %r = mul i32 %x, %x\n  ret i32 %r\n}\n"
    r1 = module_tv(z3, MOD, drop_external)
    assert r1["module"] == "refuted" and _status(r1["steps"], "keep") == "external-removed", r1

    # 3. TEETH -- deleting @dead while a surviving function still REFERENCES it (dangling / still live):
    #    refuted (live-removed), never a false proof.
    drop_live = "define i32 @keep(i32 %x) {\n  %c = call i32 @dead(i32 %x)\n  ret i32 %c\n}\n"
    r2 = module_tv(z3, MOD, drop_live)
    assert r2["module"] == "refuted" and _status(r2["steps"], "dead") == "live-removed", r2

    print("module_tv_fixture OK: whole-MODULE composition verified -- globaldce removing a dead internal "
          "function PROVES (surviving @keep refines, @dead is a justified dead removal); deleting an "
          "EXTERNAL observable function is REFUTED (external-removed); deleting a still-REFERENCED "
          "function is REFUTED (live-removed). Deleting dead code is verified sound; deleting live or "
          "observable code is caught -- the cross-function composition gap, closed for deletion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
