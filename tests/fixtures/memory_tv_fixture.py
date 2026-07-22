#!/usr/bin/env python3
"""Local scalar memory: whole-function TV for non-escaping alloca/store/load (symbolic mem2reg).

Many functions use a local `alloca` as a scalar temporary (the not-yet-promoted form). This extends the
translator to model LOCAL non-escaping scalar allocas by symbolic mem2reg (o2t/validate/scalar_ir.py):
each alloca is a distinct cell, a `store` updates it, a `load` reads the last stored value (textual =
execution order in a single block). An ESCAPING pointer (passed to a call, gep'd, returned) is never a
value the resolver can use, so its use DECLINES -- no aliasing is assumed; an uninitialized load
declines; memory is single-BB only.

The headline use is verifying **mem2reg / sroa**: the before (alloca+store+load) is proved a refinement
of opt's own mem2reg'd SSA output -- so the model is checked against LLVM's own correct transformation,
and additionally against `lli` execution. A wrong version refutes; an escaped/uninitialized pointer
declines. Needs z3 + opt + lli (18).
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
# f(x) = *(p := &x) + 1 = x + 1, via a local alloca used as a scalar temporary.
MEM = ("define i32 @f(i32 %x) {\n  %p = alloca i32\n  store i32 %x, ptr %p\n"
       "  %v = load i32, ptr %p\n  %r = add i32 %v, 1\n  ret i32 %r\n}\n")
_INPUTS = [0, 1, 5, -1, 100, 2147483647, -2147483648]


def _lli_agrees(z3, lli) -> bool:
    """The memory model AGREES with lli execution on every input (compared to the SMT model itself, so
    i32 wraparound is handled -- not to naive Python arithmetic)."""
    _, ret, _, _, _ = si.translate(MEM, "f")
    lines = [MEM, "declare i32 @printf(ptr, ...)", '@.f = private constant [4 x i8] c"%d\\0A\\00"',
             "define i32 @main() {"]
    for i, v in enumerate(_INPUTS):
        lines += [f"  %r{i} = call i32 @f(i32 {v})",
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
    lli_vals = [int(x) for x in out.stdout.split()]

    def model(v):
        q = (f"(declare-const %x (_ BitVec 32))\n(assert (= %x (_ bv{v % (1 << 32)} 32)))\n"
             f"(declare-const r (_ BitVec 32))\n(assert (= r {ret}))\n(check-sat)\n(get-value (r))\n")
        o = subprocess.run([z3, "-in"], input=q, capture_output=True, text=True).stdout
        n = int(re.search(r"#x([0-9a-fA-F]+)", o).group(1), 16)
        return n - (1 << 32) if n >= (1 << 31) else n

    return len(lli_vals) == len(_INPUTS) and all(lli_vals[i] == model(v) for i, v in enumerate(_INPUTS))


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    lli = shutil.which("lli") or (_HB_LLI if Path(_HB_LLI).exists() else None)
    if z3 is None or opt is None or lli is None:
        print("memory_tv_fixture: z3 / opt / lli (18) not all found, skipped")
        return 0

    # 1. MEM2REG verified: the before (local memory) is proved a refinement of opt's mem2reg'd SSA
    #    output -- the memory model checked against LLVM's own correct promotion.
    after = si.run_passes(MEM, "mem2reg,instcombine", opt)
    assert after is not None
    assert si.validate_transform(z3, MEM, after, "f")["status"] == "proved", "mem2reg TV must prove"

    # 2. ...and the memory model agrees with real lli EXECUTION on a battery of inputs.
    assert _lli_agrees(z3, lli), "the memory model must match lli execution"

    # 3. TEETH -- a wrong version (returns x, not x+1) is refuted with a witness.
    wrong = "define i32 @f(i32 %x) {\n  ret i32 %x\n}\n"
    v = si.validate_transform(z3, MEM, wrong, "f")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong memory fold must refute", v)

    # 4. ESCAPE declines: an alloca whose pointer is passed to a call (escapes) -> unsupported, never a
    #    mis-model (no aliasing assumed).
    esc = ("declare void @g(ptr)\n"
           "define i32 @f(i32 %x) {\n  %p = alloca i32\n  store i32 %x, ptr %p\n"
           "  call void @g(ptr %p)\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n")
    assert si.validate_transform(z3, esc, esc, "f")["status"] == "unsupported", "escape must decline"

    # 5. Uninitialized load declines (no undef guessing).
    uninit = "define i32 @f(i32 %x) {\n  %p = alloca i32\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n"
    assert si.validate_transform(z3, uninit, uninit, "f")["status"] == "unsupported", "uninit load declines"

    print("memory_tv_fixture OK: local non-escaping scalar allocas are modeled by symbolic mem2reg -- a "
          "before-with-memory (alloca+store+load) is PROVED a refinement of opt's mem2reg'd SSA output "
          "(the model checked against LLVM's own promotion) AND agrees with lli execution; a wrong "
          "version is REFUTED; an escaped or uninitialized pointer is a sound decline. Memory reach, "
          "opened -- verifying mem2reg/sroa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
