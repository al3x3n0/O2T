#!/usr/bin/env python3
"""Lock in integer-intrinsic emission in cv-grammar-gen.

The generator reaches InstCombine's intrinsic-fold family (smax/smin/umax/umin, abs, ctlz/cttz,
ctpop, bitreverse, bswap) that plain binops cannot. This pins: (1) intrinsics are actually emitted;
(2) every `call @llvm.<name>.<t>` has a matching module-scope `declare`; (3) the poison flag on
abs/ctlz/cttz is emitted `false` so `--main` modules stay UB-free and executable; (4) bswap is only
generated at widths that are a multiple of 16. Optionally validates with llvm-as when present."""

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


_CALL = re.compile(r"call (i\d+) @(llvm\.[\w.]+)\(([^)]*)\)")
_DECL = re.compile(r"declare (i\d+) @(llvm\.[\w.]+)\(")


def main() -> int:
    gg = _load()
    names = {n for n, *_ in gg.INT_INTRINSICS}

    seen_intrinsics: set[str] = set()
    checked_modules = 0
    for seed in range(60):
        for main_mode in (False, True):
            module = gg.Generator(seed=seed, n_instructions=30, cfg=True, emit_main=main_mode).module()
            decls = {(m.group(2), m.group(1)) for m in _DECL.finditer(module)}
            for call in _CALL.finditer(module):
                ret_t, fq, args = call.group(1), call.group(2), call.group(3)
                base = fq[len("llvm."):].rsplit(".", 1)[0]   # llvm.<base>.<type>; <base> may have dots
                if base not in names:
                    continue
                seen_intrinsics.add(base)
                # (2) every intrinsic call must be declared at module scope with a matching type.
                assert (fq, ret_t) in decls, ("intrinsic call without a matching declare", fq, ret_t, sorted(decls))
                # (3) poison-flag intrinsics carry `i1 false` (UB-free) -- required for the driver.
                if base in {"abs", "ctlz", "cttz"}:
                    assert args.rstrip().endswith("i1 false"), ("poison flag not false", base, args)
                # (4) bswap only at multiples of 16.
                if base == "bswap":
                    assert ret_t in {"i16", "i32", "i64"}, ("bswap at non-16-multiple width", ret_t)
            checked_modules += 1

    # (1) the generator actually reaches the intrinsic family (across seeds).
    assert len(seen_intrinsics) >= 6, ("too few intrinsic kinds exercised", sorted(seen_intrinsics))

    # Optional end-to-end: a generated intrinsic-bearing module parses under llvm-as.
    llvm_as = shutil.which("llvm-as") or "/opt/homebrew/opt/llvm@18/bin/llvm-as"
    validated = "skipped"
    if Path(llvm_as).exists():
        for seed in range(60):
            module = gg.Generator(seed=seed, n_instructions=40, cfg=True).module()
            if any(f"@llvm.{n}." in module for n in names):
                proc = subprocess.run([llvm_as, "-o", "/dev/null", "-"], input=module,
                                      capture_output=True, text=True)
                assert proc.returncode == 0, ("llvm-as rejected an intrinsic module", proc.stderr[:200])
                validated = "ok"
                break

    print(f"grammar_intrinsics_fixture OK: {checked_modules} modules checked, "
          f"{len(seen_intrinsics)} intrinsic kinds exercised {sorted(seen_intrinsics)}; every call "
          f"declared, abs/ctlz/cttz UB-free (i1 false), bswap width-legal; llvm-as validation: {validated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
