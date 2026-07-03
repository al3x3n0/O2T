#!/usr/bin/env python3
"""Cover UNBOUNDED nested-loop equivalence (validate/loop_nested.py).

Asserts that two nested loops are proved equivalent COMPOSITIONALLY -- the inner loops are shown to
define the same transition (equal init/guard/step over the enclosing-loop variables), then the
outer loops are proved equivalent with the inner abstracted as one uninterpreted function INNER (a
QF_UFBV query). A semantics-preserving inner-body transform is accepted; two-sided teeth: an
inconsistent inner change fails the inner check (`inner-step`) and an outer change fails the outer
check (`outer-step`), localizing the failure. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_nested as N


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("loop_nested_fixture: z3 not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "loop_nested_cases.ll").read_text()

    # the function really has two nested loops (outer + inner).
    from o2t.validate.mem2reg_ir import _blocks, _function_body
    blocks = _blocks(_function_body(src, "nested"))
    bmap = {lab: (l, t) for lab, l, t in blocks}
    assert N._loop_headers(blocks, bmap) == ["oh", "ih"], N._loop_headers(blocks, bmap)

    # 1) the nested loop is proved equivalent to itself (inner + outer checks pass).
    r = N.validate_nested(z3, src, src, "nested")
    assert r["status"] == "proved" and r["inner_checked"] and r["outer_checked"], r

    # 2) a semantics-preserving inner-body transform (acc += j  ->  acc += j + 0) is accepted: the
    #    inner transition is unchanged, so the inner check still proves.
    inner_eq = src.replace("%acc.i = add i32 %accn, %j",
                           "%t0 = add i32 %j, 0\n  %acc.i = add i32 %accn, %t0", 1)
    assert N.validate_nested(z3, src, inner_eq, "nested")["status"] == "proved"

    # 3) TEETH (inner): an inconsistent inner change (acc += j -> acc += 1) fails the INNER check.
    inner_bad = src.replace("%acc.i = add i32 %accn, %j", "%acc.i = add i32 %accn, 1", 1)
    ri = N.validate_nested(z3, src, inner_bad, "nested")
    assert ri["status"] == "refuted" and ri["failed"] == "inner-step", ri

    # 4) TEETH (outer): an outer change (i += 1 -> i += 2) fails the OUTER check (inner unchanged).
    outer_bad = src.replace("%i.n = add i32 %i, 1", "%i.n = add i32 %i, 2", 1)
    ro = N.validate_nested(z3, src, outer_bad, "nested")
    assert ro["status"] == "refuted" and ro["failed"] == "outer-step", ro

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-loop-nested.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 2' in proc.stdout, proc.stdout

    print("loop_nested_fixture OK: nested loops proved equivalent COMPOSITIONALLY (inner transition "
          "equal, then outer equivalent with the inner as an uninterpreted function); a "
          "transition-preserving inner transform accepted; an inner change fails `inner-step`, an "
          "outer change fails `outer-step`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
