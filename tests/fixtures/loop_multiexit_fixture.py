#!/usr/bin/env python3
"""Cover UNBOUNDED multi-exit loop equivalence (validate/loop_multiexit.py).

Asserts that a loop with several exit edges (a header guard `i >= n` plus an in-body break
`acc > lim`) is modeled as ordered exits + a continue-step, and two such loops are proved
equivalent for ALL trip counts by induction over (init, per-exit decision, per-exit result, step).
Two-sided teeth: a flipped exit condition fails `decision`, a swapped exit value fails `result`,
and a changed body step fails `step` -- each refuted with a witness. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_multiexit as M


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("loop_multiexit_fixture: z3 not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "loop_multiexit_cases.ll").read_text()

    # 1) the loop has TWO exits recovered (header guard + in-body break) and proves equivalent to
    #    itself for all trip counts, with every obligation discharged.
    model = M.extract_multiexit(src, "search")
    assert len(model["exits"]) == 2, ("expected two exits", len(model["exits"]))
    r = M.validate_multiexit(z3, src, "search", src, "search")
    assert r["status"] == "proved" and r["exits"] == 2, r
    assert r["parts"]["init"] == "proved" and r["parts"]["step"] == "proved"
    assert r["parts"]["decision0"] == "proved" and r["parts"]["decision1"] == "proved"
    assert r["parts"]["result0"] == "proved" and r["parts"]["result1"] == "proved"

    # 2) TEETH (decision): flip the break condition (sgt -> slt) -> the loops take different exits
    #    -> refuted at the in-body exit's DECISION obligation.
    cond = src.replace("%brk = icmp sgt i32 %acc, %lim", "%brk = icmp slt i32 %acc, %lim", 1)
    rc = M.validate_multiexit(z3, src, "search", cond, "search")
    assert rc["status"] == "refuted" and rc["failed"] == "decision1", rc

    # 3) TEETH (result): change the in-body exit's returned value (acc -> i) -> refuted at RESULT.
    res = src.replace("exitB:\n  ret i32 %acc", "exitB:\n  ret i32 %i", 1)
    rr = M.validate_multiexit(z3, src, "search", res, "search")
    assert rr["status"] == "refuted" and rr["failed"] == "result1", rr

    # 4) TEETH (step): change the body recurrence (acc + i -> acc + 1) -> refuted at STEP.
    st = src.replace("%acc.n = add i32 %acc, %i", "%acc.n = add i32 %acc, 1", 1)
    rs = M.validate_multiexit(z3, src, "search", st, "search")
    assert rs["status"] == "refuted" and rs["failed"] == "step", rs

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-loop-multiexit.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"refuted": 3' in proc.stdout, proc.stdout

    print("loop_multiexit_fixture OK: a multi-exit loop (header guard + in-body break) modeled as "
          "ordered exits + step and proved equivalent for ALL trip counts; a flipped exit "
          "condition, a swapped exit value, and a changed step each refuted at their obligation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
