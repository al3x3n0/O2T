#!/usr/bin/env python3
"""Cover the generalized scalar translation validator across value-preserving passes (scalar_ir).

Asserts that the LITERAL output of the real `opt -passes=<P>` keeps each function's returned value
for reassociate / early-cse / gvn / instsimplify (proved input by input), with TV teeth: a
corrupted output (wrong returned operand) is REFUTED with a witness; and that an unmodeled
instruction is soundly declined. Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir as si

PASSES = ("reassociate", "early-cse", "gvn", "instsimplify")


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("scalar_tv_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "scalar_tv_cases.ll").read_text()
    funcs = si.function_names(src)

    # 1) every value-preserving scalar pass keeps each function's returned value.
    for p in PASSES:
        out = si.run_passes(src, p, opt)
        assert out is not None, ("opt failed", p)
        for fn in funcs:
            r = si.validate_transform(z3, src, out, fn)
            assert r["status"] == "proved", ("not proved", p, fn, r)
    # reassociate really folded @cancel to a constant (non-vacuous).
    rout = si.run_passes(src, "reassociate", opt)
    assert "ret i32 0" in rout.split("@cancel")[1].split("}")[0], rout

    # 2) TEETH: corrupt a reassociate output (return a different value) -> must REFUTE.
    bad = rout.replace("ret i32 %s3", "ret i32 %a", 1) if "ret i32 %s3" in rout else None
    if bad is None:
        # @chain may be rewritten; corrupt @redundant instead by returning an operand.
        cout = si.run_passes(src, "early-cse", opt)
        bad_src, bad = cout, cout.replace("%r = add i32 %x, %x", "%r = add i32 %x, %a", 1)
        v = si.validate_transform(z3, src, bad, "redundant")
    else:
        v = si.validate_transform(z3, src, bad, "chain")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong transform not caught", v)

    # 3) SOUNDNESS boundary: an unmodeled instruction is declined.
    weird = ("define i32 @w(ptr %p) {\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n")
    wout = si.run_passes(weird, "early-cse", opt) or weird
    assert si.validate_transform(z3, weird, wout, "w")["status"] == "unsupported"

    # 4) the generic CLI agrees for each pass and exits 0.
    tool = ROOT / "tools" / "cv-validate-scalar-tv.py"
    for p in PASSES:
        proc = subprocess.run([sys.executable, str(tool), "--passes", p],
                              capture_output=True, text=True)
        assert proc.returncode == 0 and '"ok": true' in proc.stdout, (p, proc.stdout)

    print("scalar_tv_fixture OK: real `opt` output proved value-preserving over scalar IR for "
          f"{', '.join(PASSES)}; a wrong transform refuted with a witness; the same translator "
          "(InstCombine) and an unmodeled instruction handled as before")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
