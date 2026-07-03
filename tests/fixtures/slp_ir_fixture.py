#!/usr/bin/env python3
"""Cover closed-loop SLP translation validation (validate/slp_ir.py).

Asserts the LITERAL output of the real `opt -passes=slp-vectorizer` is proved equivalent to the
scalar input -- every output memory cell gets the same value for all inputs -- including the
vector load/op/store bundle and a reversing shufflevector, with translation-validation teeth: a
wrong vector op (add->sub) and a wrong shuffle mask are each REFUTED with a witness. Also checks
the sound boundary (an unmodeled shape is declined, not mis-proved) and that vectorization really
happened (non-vacuous). Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import slp_ir as si


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("slp_ir_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "slp_ir_cases.ll").read_text()
    opt_text = si.run_slp(src, opt, threshold="-1")
    assert opt_text is not None, "opt -passes=slp-vectorizer failed"
    # SLP actually vectorized (non-vacuous): the output has a vector type.
    assert "x i32>" in opt_text, ("SLP did not vectorize", opt_text)

    # 1) the REAL SLP output proves equivalent per output cell for both bundles.
    by = {fn: si.validate_slp(z3, src, opt_text, fn) for fn in si.function_names(src)}
    assert by["vadd"]["status"] == "proved" and by["vadd"]["cells"] == 4, by["vadd"]
    assert by["vrev"]["status"] == "proved", by["vrev"]            # via a reversing shufflevector

    # 2) TEETH (wrong op): corrupt the vector add -> sub -> must REFUTE.
    bad_op = opt_text.replace("add <4 x i32>", "sub <4 x i32>", 1)
    assert bad_op != opt_text, "no vector add to corrupt"
    assert si.validate_slp(z3, src, bad_op, "vadd")["status"] == "refuted", "wrong vector op not caught"

    # 3) TEETH (wrong shuffle mask): permute the reverse mask -> must REFUTE.
    assert "i32 3, i32 2, i32 1, i32 0" in opt_text, opt_text
    bad_mask = opt_text.replace("i32 3, i32 2, i32 1, i32 0", "i32 3, i32 2, i32 0, i32 1", 1)
    assert si.validate_slp(z3, src, bad_mask, "vrev")["status"] == "refuted", "wrong shuffle mask not caught"

    # 3b) POISON/UB-REFINEMENT teeth: SLP builds new instructions, so the validator is Alive2
    #     refinement (not raw equality). A vectorization that adds an unjustified nsw (poison) on
    #     otherwise-equal values is refuted, while dropping the flag still proves.
    def pack(flag):
        return ("define void @g(ptr %p, ptr %q){\n"
                "  %a0 = load i32, ptr %q\n"
                "  %q1 = getelementptr i32, ptr %q, i32 1\n"
                "  %a1 = load i32, ptr %q1\n"
                f"  %r0 = add{flag} i32 %a0, %a0\n"
                f"  %r1 = add{flag} i32 %a1, %a1\n"
                "  store i32 %r0, ptr %p\n"
                "  %p1 = getelementptr i32, ptr %p, i32 1\n"
                "  store i32 %r1, ptr %p1\n  ret void\n}")
    assert si.validate_slp(z3, pack(""), pack(" nsw"), "g")["status"] == "refuted", "nsw introduction not caught"
    assert si.validate_slp(z3, pack(" nsw"), pack(""), "g")["status"] == "proved", "flag drop should still prove"

    # 4) SOUNDNESS boundary: an unmodeled instruction is declined, not mis-proved.
    weird = ("target triple = \"x86_64-unknown-linux-gnu\"\n"
             "define i32 @w(ptr %p) {\n  %v = call i32 @ext(ptr %p)\n  ret i32 %v\n}\n"
             "declare i32 @ext(ptr)\n")
    wout = si.run_slp(weird, opt) or weird
    assert si.validate_slp(z3, weird, wout, "w")["status"] in ("unsupported", "error")

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-slp-ir.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 2' in proc.stdout, proc.stdout

    print("slp_ir_fixture OK: real `opt -passes=slp-vectorizer` output proved equivalent per "
          "memory cell over scalar/vector IR (incl. a reversing shufflevector); a wrong vector op "
          "and a wrong shuffle mask both refuted with witnesses; an introduced nsw (poison) refuted "
          "by Alive2 refinement while a flag drop still proves; unmodeled shapes declined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
