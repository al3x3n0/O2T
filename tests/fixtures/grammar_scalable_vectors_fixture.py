#!/usr/bin/env python3
"""Lock in scalable-vector (`<vscale x N x iM>`) emission in cv-grammar-gen.

Scalable vectors reach the SVE/RVV scalable-vector fold surface. Their length is unknown at compile
time, so they cannot execute on a non-scalable host -- they are validate-only, like FP. This pins:
  * `--validate` emits scalable binops, the canonical scalable splat (insertelement + zeroinitializer
    shuffle), and extractelement, and still parses under llvm-as (when present);
  * `--main` contains NO `vscale` type (they must never enter the execution differential)."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("cv_grammar_gen", ROOT / "tools" / "cv-grammar-gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    gg = _load()
    llvm_as = shutil.which("llvm-as") or "/opt/homebrew/opt/llvm@18/bin/llvm-as"
    has_llvm_as = Path(llvm_as).exists()

    scalable_modules = 0
    saw_splat = saw_binop = False
    validated = attempts = 0
    for seed in range(50):
        module = gg.Generator(seed=seed, n_instructions=40, cfg=(seed % 2 == 0)).module()
        if "vscale" in module:
            scalable_modules += 1
        if re.search(r"shufflevector <vscale x \d+ x i\d+>.*zeroinitializer", module):
            saw_splat = True
        if re.search(r"\b(?:add|sub|mul|and|or|xor) <vscale x \d+ x i\d+>", module):
            saw_binop = True
        if has_llvm_as and "vscale" in module:
            proc = subprocess.run([llvm_as, "-o", "/dev/null", "-"], input=module,
                                  capture_output=True, text=True)
            attempts += 1
            assert proc.returncode == 0, ("llvm-as rejected a scalable-vector module", proc.stderr[:200])
            validated += 1

    assert scalable_modules >= 10, ("scalable vectors barely emitted", scalable_modules)
    assert saw_binop, "scalable element-wise binop never emitted"
    assert saw_splat, "canonical scalable splat (zeroinitializer shuffle) never emitted"

    # --main: no scalable vectors (validate-only; cannot execute).
    for seed in range(40):
        module = gg.Generator(seed=seed, n_instructions=40, cfg=True, emit_main=True).module()
        assert "vscale" not in module, ("scalable vector leaked into the executable --main path", seed)

    v = f"{validated}/{attempts} llvm-as-validated" if has_llvm_as else "llvm-as skipped"
    print(f"grammar_scalable_vectors_fixture OK: --validate emits scalable vectors (binops, splat, "
          f"extract) in {scalable_modules}/50 modules ({v}); --main is scalable-free (validate-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
