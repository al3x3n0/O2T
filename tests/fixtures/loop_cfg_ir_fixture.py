#!/usr/bin/env python3
"""Cover BOUNDED loop-CFG translation validation (validate/loop_cfg_ir.py).

Asserts that for a constant-trip-count loop, the real `opt -passes=loop-rotate` and
`simple-loop-unswitch` outputs are proved to preserve the computation -- by fully unrolling the
original and transformed loops and proving the acyclic forms equal for all inputs -- with two-sided
teeth (a corrupted unrolled output is refuted with a witness) and an honest bound (a non-constant
trip count is not unrolled and is declined `unsupported`). Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_cfg_ir as L
from o2t.validate import scalar_ir as si


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("loop_cfg_ir_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "loop_cfg_ir_cases.ll").read_text()

    # 1) loop-rotate and simple-loop-unswitch each preserve the computation (bounded) for both
    #    constant-trip loops.
    for fn in ("poly", "unswitchable"):
        for t in ("loop-rotate", "simple-loop-unswitch"):
            r = L.validate_loop_transform(z3, src, t, fn, opt)
            assert r["status"] == "proved", ("loop transform not proved", fn, t, r)

    # 2) the unrolling really happened (non-vacuous): the normalized form is acyclic straight-line.
    ref = L.normalize(src, "", opt)
    assert ref is not None and "mul i32" in ref and "phi" not in ref.split("@poly")[1].split("}")[0]

    # 3) TEETH: corrupt the transformed unrolled output (change a constant) -> must REFUTE.
    test = L.normalize(src, "loop-rotate", opt)
    bad = test.replace(", 3\n", ", 9\n", 1)
    assert bad != test, "could not find a constant to corrupt"
    r = si.validate_transform(z3, ref, bad, "poly")
    assert r["status"] == "refuted" and r.get("witness"), ("a corrupted transform not caught", r)

    # 4) BOUND boundary: a non-constant trip count is not fully unrolled -> declined, not proved.
    sym = src.replace("icmp slt i32 %i, 4", "icmp slt i32 %i, %x", 1)
    rr = L.validate_loop_transform(z3, sym, "loop-rotate", "poly", opt)
    assert rr["status"] in ("unsupported", "error"), ("symbolic trip must be declined", rr)

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-loop-cfg-ir.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 4' in proc.stdout, proc.stdout

    print("loop_cfg_ir_fixture OK: loop-rotate and simple-loop-unswitch bounded-proved to preserve "
          "the computation (unroll + prove) on constant-trip loops; a corrupted output refuted with "
          "a witness; a non-constant trip count soundly declined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
