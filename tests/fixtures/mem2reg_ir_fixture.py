#!/usr/bin/env python3
"""Cover closed-loop Mem2Reg translation validation (validate/mem2reg_ir.py) -- the first
multi-block + phi validator.

Asserts the real `opt -passes=mem2reg` output (SSA + phi) is proved to return the same value as the
original (memory) function for all inputs and branch conditions, across a diamond, a straight-line
chain, a store-before-branch partial, and a nested 3-incoming phi -- with TV teeth: a phi placed
with swapped incoming values is REFUTED with a witness. Also checks the sound boundary -- a CFG with
a loop (phi cycle) is declined `unsupported`, not mis-proved. Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import mem2reg_ir as m2r


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("mem2reg_ir_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "mem2reg_ir_cases.ll").read_text()
    out = m2r.run_mem2reg(src, opt)
    assert out is not None, "opt -passes=mem2reg failed"
    # promotion really happened (non-vacuous): phis built, allocas gone.
    assert "phi i32" in out and "alloca" not in out, ("mem2reg did not promote", out)

    # 1) every function's promoted SSA+phi form proves equal to its memory form.
    by = {fn: m2r.validate_mem2reg(z3, src, out, fn) for fn in m2r.function_names(src)}
    for fn in ("diamond", "chain", "partial", "nested"):
        assert by[fn]["status"] == "proved", ("mem2reg not proved", fn, by[fn])

    # 2) TEETH: swap the diamond's phi incoming values -> the merge no longer matches memory ->
    #    must REFUTE with a witness (a branch condition + inputs where they differ).
    bad = out.replace("phi i32 [ %x, %t ], [ %y, %e ]", "phi i32 [ %y, %t ], [ %x, %e ]", 1)
    assert bad != out, "could not find the diamond phi to corrupt"
    r = m2r.validate_mem2reg(z3, src, bad, "diamond")
    assert r["status"] == "refuted" and r.get("witness"), ("a swapped phi not caught", r)

    # 3) SOUNDNESS boundary: a loop (phi cycle) is declined, not mis-proved.
    loop = ("define i32 @loop(i32 %n) {\nentry:\n  %p = alloca i32\n  store i32 0, ptr %p\n"
            "  br label %h\nh:\n  %v = load i32, ptr %p\n  %nv = add i32 %v, 1\n"
            "  store i32 %nv, ptr %p\n  %c = icmp slt i32 %nv, %n\n"
            "  br i1 %c, label %h, label %x\nx:\n  %r = load i32, ptr %p\n  ret i32 %r\n}\n")
    lout = m2r.run_mem2reg(loop, opt) or loop
    assert m2r.validate_mem2reg(z3, loop, lout, "loop")["status"] == "unsupported"

    # 4) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-mem2reg-ir.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 4' in proc.stdout, proc.stdout

    print("mem2reg_ir_fixture OK: real `opt -passes=mem2reg` output proved to return the same value "
          "as the memory form over a diamond / chain / partial / nested-phi (multi-block + phi); a "
          "swapped phi refuted with a witness; a loop CFG soundly declined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
