#!/usr/bin/env python3
"""Cover UNBOUNDED loop equivalence by induction (validate/loop_induction.py).

Asserts that a structure-preserving body fold (real `opt -passes=instcombine`) is proved to keep a
loop's returned value for ALL trip counts -- via init/guard/step/result induction over the loop-
carried state, with no unrolling -- across a single-state and a three-state loop, with two-sided
teeth: a body whose step is altered is REFUTED (failed obligation `step`) with a state witness, and
a wrong exit value fails `result`. A non-isomorphic loop (state vector changed) is declined. Needs
z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_induction as LI
from o2t.validate import scalar_ir as si


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("loop_induction_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "loop_induction_cases.ll").read_text()
    after = si.run_passes(src, "instcombine", opt)
    assert after is not None, "opt -passes=instcombine failed"
    # the fold really happened (non-vacuous): the `+0`/`*1` identities are gone from the loop body.
    assert "add i32 %z, 0" not in after and "mul i32" not in after.split("@loopfold")[1].split("}")[0]

    # 1) the real instcombine'd loops are proved equivalent for ALL trip counts (all 4 obligations).
    for fn in ("loopfold", "twostate"):
        r = LI.validate_loop_equiv(z3, src, after, fn)
        assert r["status"] == "proved", ("unbounded loop equiv not proved", fn, r)
        assert r["parts"] == {"init": "proved", "guard": "proved",
                              "step": "proved", "result": "proved"}, r

    # 2) TEETH (step): alter the body's recurrence in the after -> the STEP obligation must fail.
    bad_step = after.replace("%acc.next = add i32 %acc, %i",
                             "%acc.next = add i32 %acc, 7", 1)
    assert bad_step != after, "could not find the loopfold step to corrupt"
    rs = LI.validate_loop_equiv(z3, src, bad_step, "loopfold")
    assert rs["status"] == "refuted" and rs.get("failed") == "step" and rs.get("witness"), rs

    # 3) the induction prover directly: equal-step loops prove, a wrong exit value fails `result`.
    base = ("define i32 @g(i32 %x, i32 %n){\nentry:\n  br label %h\nh:\n"
            "  %i = phi i32 [0,%entry],[%i.n,%b]\n  %a = phi i32 [%x,%entry],[%a.n,%b]\n"
            "  %c = icmp slt i32 %i, %n\n  br i1 %c, label %b, label %e\nb:\n"
            "  %a.n = add i32 %a, %i\n  %i.n = add i32 %i, 1\n  br label %h\n"
            "e:\n  ret i32 %a\n}\n")
    assert LI.validate_loop_equiv(z3, base, base, "g")["status"] == "proved"
    wrong_res = base.replace("e:\n  ret i32 %a", "e:\n  %w = add i32 %a, 1\n  ret i32 %w")
    rr = LI.validate_loop_equiv(z3, base, wrong_res, "g")
    assert rr["status"] == "refuted" and rr.get("failed") == "result", rr

    # 3b) POISON-REFINEMENT teeth: the STEP/RESULT obligations are Alive2 refinement, so a body fold
    #     that ADDS an unjustified nsw (poison) on otherwise-equal values fails STEP, while dropping a
    #     flag still proves. (instcombine-in-loop is exactly a flag-rewriting pass.)
    flagged = base.replace("  %a.n = add i32 %a, %i\n", "  %a.n = add nsw i32 %a, %i\n")
    rp = LI.validate_loop_equiv(z3, base, flagged, "g")
    assert rp["status"] == "refuted" and rp.get("failed") == "step", ("nsw introduction not caught", rp)
    assert LI.validate_loop_equiv(z3, flagged, base, "g")["status"] == "proved", "flag drop should still prove"

    # 4) SOUNDNESS boundary: a loop whose state vector changes is declined (not mis-proved).
    nz = base.replace("  %a = phi i32 [%x,%entry],[%a.n,%b]\n", "")  # malformed/changed state
    assert LI.validate_loop_equiv(z3, base, nz, "g")["status"] in ("unsupported", "error")

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-loop-induction.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 2' in proc.stdout, proc.stdout

    print("loop_induction_fixture OK: instcombine body folds proved to preserve the loop value for "
          "ALL trip counts by init/guard/step/result induction (single + three-state loops); an "
          "altered step and a wrong exit value refuted with witnesses; non-isomorphic loops declined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
