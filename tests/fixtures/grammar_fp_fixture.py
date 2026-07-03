#!/usr/bin/env python3
"""Lock in floating-point + fast-math emission in cv-grammar-gen.

Fast-math flags license the optimizer to reassociate/refine, so O0 and O3 may LEGALLY disagree on
the numeric result -- FP therefore belongs only in the non-executed `--validate` path, never in the
executable `--main` differential (whose soundness rests on every optimizer preserving the exact
result). This pins both halves:
  * `--validate` modules emit FP arithmetic (fadd/fsub/fmul/fdiv/fneg), fcmp, int<->fp casts, and
    fast-math flags, and still parse under llvm-as (when present);
  * `--main` modules contain NO float/double at all (the soundness invariant that keeps the
    execution differential free of licensed-nondeterminism false positives)."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FP_OPS = ("fadd", "fsub", "fmul", "fdiv", "fneg", "fcmp",
           "sitofp", "uitofp", "fptosi", "fptoui", "fpext", "fptrunc")
_FMF = re.compile(r"\b(?:fadd|fsub|fmul|fdiv|fneg|fcmp)(?:\s+(?:fast|nnan|ninf|nsz|arcp|contract|reassoc))")


def _load():
    spec = importlib.util.spec_from_file_location("cv_grammar_gen", ROOT / "tools" / "cv-grammar-gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    gg = _load()
    llvm_as = shutil.which("llvm-as") or "/opt/homebrew/opt/llvm@18/bin/llvm-as"
    has_llvm_as = Path(llvm_as).exists()

    fp_ops_seen: set[str] = set()
    fmf_modules = 0
    fp_vector_modules = 0
    validated = 0
    validate_attempts = 0

    # --validate path (non-main): FP + fast-math present and valid (scalar and vector FP).
    for seed in range(50):
        module = gg.Generator(seed=seed, n_instructions=35, cfg=(seed % 2 == 0)).module()
        for op in _FP_OPS:
            if re.search(rf"\b{op}\b", module):
                fp_ops_seen.add(op)
        if _FMF.search(module):
            fmf_modules += 1
        if re.search(r"<\d+ x (?:float|double)>", module):
            fp_vector_modules += 1
        if has_llvm_as and any(re.search(rf"\b{op}\b", module) for op in _FP_OPS):
            proc = subprocess.run([llvm_as, "-o", "/dev/null", "-"], input=module,
                                  capture_output=True, text=True)
            validate_attempts += 1
            assert proc.returncode == 0, ("llvm-as rejected an FP module", proc.stderr[:200])
            validated += 1

    assert len(fp_ops_seen) >= 6, ("too few FP ops exercised", sorted(fp_ops_seen))
    assert fmf_modules >= 10, ("fast-math flags barely emitted", fmf_modules)
    assert {"fadd", "fmul", "fcmp"} <= fp_ops_seen, ("core FP ops missing", sorted(fp_ops_seen))
    # FP VECTORS (validate-only, like scalar FP): the vector-FP reassociation fold surface.
    assert fp_vector_modules >= 10, ("FP vectors barely emitted in --validate", fp_vector_modules)

    # --main path: NO floating point (the soundness invariant).
    for seed in range(40):
        module = gg.Generator(seed=seed, n_instructions=35, cfg=True, emit_main=True).module()
        assert not re.search(r"\b(?:float|double)\b", module), \
            ("FP leaked into the executable --main differential (licensed nondeterminism)", seed)
        # also no fast-math tokens anywhere in the executable module
        assert not re.search(r"\b(?:fadd|fmul|fcmp)\b", module), ("FP op in --main", seed)

    v = f"{validated}/{validate_attempts} llvm-as-validated" if has_llvm_as else "llvm-as skipped"
    print(f"grammar_fp_fixture OK: --validate emits scalar+vector FP {sorted(fp_ops_seen)} with "
          f"fast-math in {fmf_modules}/50 and FP vectors in {fp_vector_modules}/50 modules ({v}); "
          "--main is FP-free (scalar and vector) -- execution-differential soundness held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
