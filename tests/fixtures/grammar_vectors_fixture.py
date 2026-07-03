#!/usr/bin/env python3
"""Lock in integer-vector emission in cv-grammar-gen.

Unlike floating point, integer vector arithmetic is deterministic and (on the safe op subset)
UB-free, so vectors run in the EXECUTABLE `--main` differential -- reaching vector-InstCombine /
VectorCombine / shuffle folds for real miscompile detection, feeding the scalar return via
`extractelement`. This pins:
  * `--validate` emits vector binops, extract/insert-element, and shufflevector, and parses under
    llvm-as (when present);
  * `--main` DOES contain vectors (the point -- they are executable), but only the UB-free subset:
    no vector div/shift, and no poison shuffle-mask lanes -- so the execution differential stays
    sound (a divergence is a real miscompile, never licensed nondeterminism)."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_UNSAFE_VEC = re.compile(r"\b(?:udiv|sdiv|urem|srem|shl|lshr|ashr)\s+<\d+ x i\d+>")


def _load():
    spec = importlib.util.spec_from_file_location("cv_grammar_gen", ROOT / "tools" / "cv-grammar-gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    gg = _load()
    llvm_as = shutil.which("llvm-as") or "/opt/homebrew/opt/llvm@18/bin/llvm-as"
    has_llvm_as = Path(llvm_as).exists()

    seen: set[str] = set()
    validated = attempts = 0
    for seed in range(50):
        module = gg.Generator(seed=seed, n_instructions=35, cfg=(seed % 2 == 0)).module()
        for op in ("extractelement", "insertelement", "shufflevector"):
            if op in module:
                seen.add(op)
        if re.search(r"\b(?:add|sub|mul|xor|and|or)\s+<\d+ x i\d+>", module):
            seen.add("vbinop")
        if has_llvm_as and re.search(r"<\d+ x i\d+>", module):
            proc = subprocess.run([llvm_as, "-o", "/dev/null", "-"], input=module,
                                  capture_output=True, text=True)
            attempts += 1
            assert proc.returncode == 0, ("llvm-as rejected a vector module", proc.stderr[:200])
            validated += 1
    assert {"vbinop", "extractelement", "insertelement", "shufflevector"} <= seen, ("missing vector ops", sorted(seen))

    # --main: vectors present (executable), but only the UB-free subset -- differential soundness.
    vec_main = 0
    for seed in range(40):
        module = gg.Generator(seed=seed, n_instructions=35, cfg=True, emit_main=True).module()
        if re.search(r"<\d+ x i\d+>", module):
            vec_main += 1
        bad = _UNSAFE_VEC.search(module)
        assert not bad, ("UB-capable vector op (div/shift) in the executable --main path", seed, bad.group(0))
        assert "i32 poison" not in module, ("poison shuffle-mask lane in --main (not UB-free)", seed)
    assert vec_main >= 20, ("vectors should reach the executable --main path", vec_main)

    v = f"{validated}/{attempts} llvm-as-validated" if has_llvm_as else "llvm-as skipped"
    print(f"grammar_vectors_fixture OK: --validate emits vector ops {sorted(seen)} ({v}); vectors "
          f"reach --main in {vec_main}/40 modules with only the UB-free subset (no div/shift, no "
          "poison masks) -- executable-differential soundness held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
