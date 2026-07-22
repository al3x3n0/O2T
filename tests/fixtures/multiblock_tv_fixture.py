#!/usr/bin/env python3
"""Multi-block CFG support: whole-function TV for ACYCLIC branch/phi functions (not just straight-line).

Many real functions branch. This extends the translator to symbolically execute an acyclic single-
function CFG (o2t/validate/scalar_ir._translate_multiblock): each block gets a path condition, a `phi`
becomes an `ite` over predecessors' reached-from conditions, and returns combine by path condition.
SOUND-BY-SCOPE: div/rem (the only UB sources) DECLINE (so whole-function UB stays `false`); a back-edge
(loop) or an unhandled terminator DECLINES; poison propagates through the phi/return ites.

The soundness cornerstone here is LLI VALIDATION: the multi-block VALUE model is checked against real
`lli` execution on a battery of inputs -- if the symbolic executor computed a wrong value, lli catches
it. Then a real diamond (min via branch+phi) is proved equivalent to its `select`-canonicalized form; a
wrong version is refuted; a loop is a bounded sound decline. Needs z3 + opt + lli (18).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402

_HB_LLI = "/opt/homebrew/opt/llvm@18/bin/lli"
MIN = ("define i32 @m(i32 %x, i32 %y) {\n"
       "entry:\n  %c = icmp slt i32 %x, %y\n  br i1 %c, label %t, label %f\n"
       "t:\n  br label %j\n"
       "f:\n  br label %j\n"
       "j:\n  %r = phi i32 [ %x, %t ], [ %y, %f ]\n  ret i32 %r\n}\n")
_INPUTS = [(1, 2), (5, 3), (7, 7), (0, -1), (-5, -2), (100, 50), (2147483647, -1), (-2147483648, 0)]


def _lli_and_model_agree(z3, lli) -> bool:
    _, ret, _, _, _ = si.translate(MIN, "m")
    lines = [MIN, "declare i32 @printf(ptr, ...)", '@.f = private constant [4 x i8] c"%d\\0A\\00"',
             "define i32 @main() {"]
    for i, (a, b) in enumerate(_INPUTS):
        lines += [f"  %r{i} = call i32 @m(i32 {a}, i32 {b})",
                  f"  call i32 (ptr, ...) @printf(ptr @.f, i32 %r{i})"]
    lines += ["  ret i32 0", "}"]
    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False) as tf:
        tf.write("\n".join(lines) + "\n"); path = tf.name
    try:
        out = subprocess.run([lli, path], capture_output=True, text=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    if out.returncode != 0:
        return False
    lli_vals = [int(v) for v in out.stdout.split()]

    def model(a, b):
        q = (f"(declare-const %x (_ BitVec 32))\n(declare-const %y (_ BitVec 32))\n"
             f"(assert (= %x (_ bv{a % (1 << 32)} 32)))\n(assert (= %y (_ bv{b % (1 << 32)} 32)))\n"
             f"(declare-const r (_ BitVec 32))\n(assert (= r {ret}))\n(check-sat)\n(get-value (r))\n")
        o = subprocess.run([z3, "-in"], input=q, capture_output=True, text=True).stdout
        v = int(re.search(r"#x([0-9a-fA-F]+)", o).group(1), 16)
        return v - (1 << 32) if v >= (1 << 31) else v

    return len(lli_vals) == len(_INPUTS) and all(lli_vals[i] == model(a, b)
                                                 for i, (a, b) in enumerate(_INPUTS))


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    lli = shutil.which("lli") or (_HB_LLI if Path(_HB_LLI).exists() else None)
    if z3 is None or opt is None or lli is None:
        print("multiblock_tv_fixture: z3 / opt / lli (18) not all found, skipped")
        return 0

    # 1. SOUNDNESS CORNERSTONE: the multi-block value model agrees with real lli execution on a battery
    #    of inputs (incl. INT_MIN/MAX). A wrong symbolic execution would disagree with LLVM's semantics.
    assert _lli_and_model_agree(z3, lli), "the multi-block model must match lli execution on every input"

    # 2. A real transform: the diamond (min via branch+phi) is proved equivalent to its select form
    #    (what instcombine/simplifycfg canonicalizes it to), and to opt's actual output.
    sel = ("define i32 @m(i32 %x, i32 %y) {\n  %c = icmp slt i32 %x, %y\n"
           "  %r = select i1 %c, i32 %x, i32 %y\n  ret i32 %r\n}\n")
    assert si.validate_transform(z3, MIN, sel, "m")["status"] == "proved", "diamond == select-min"
    after = si.run_passes(MIN, "instcombine,simplifycfg", opt)
    assert si.validate_transform(z3, MIN, after, "m")["status"] == "proved", "diamond -> opt output"

    # 3. TEETH -- a wrong version (always returns x, not min) is refuted with a witness.
    wrong = "define i32 @m(i32 %x, i32 %y) {\n  ret i32 %x\n}\n"
    v = si.validate_transform(z3, MIN, wrong, "m")
    assert v["status"] == "refuted" and v.get("witness"), ("wrong diamond must refute", v)

    # 4. A LOOP (back-edge) is a bounded sound DECLINE -- not mis-modeled as acyclic.
    loop = ("define i32 @l(i32 %n) {\nentry:\n  br label %h\n"
            "h:\n  %i = phi i32 [ 0, %entry ], [ %j, %h ]\n  %j = add i32 %i, 1\n"
            "  %c = icmp slt i32 %j, %n\n  br i1 %c, label %h, label %e\n"
            "e:\n  ret i32 %j\n}\n")
    assert si.validate_transform(z3, loop, loop, "l")["status"] == "unsupported", "a loop must decline"

    print("multiblock_tv_fixture OK: acyclic branch/phi functions are symbolically executed and "
          "whole-function TV'd -- the model AGREES WITH LLI execution on a battery of inputs (incl. "
          "INT_MIN/MAX), a diamond (min via branch+phi) is PROVED equivalent to its select-canonicalized "
          "form and to opt's real output, a wrong version is REFUTED with a witness, and a loop is a "
          "bounded sound decline. Multi-block reach, opened -- validated against LLVM's own execution")
    return 0


if __name__ == "__main__":
    sys.exit(main())
