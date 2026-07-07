#!/usr/bin/env python3
"""Differential oracle over an external, poison-aware LLVM IR interpreter.

Vellvm (github.com/vellvm/vellvm) ships an interpreter EXTRACTED FROM its Coq/Rocq-mechanized LLVM
semantics -- the only oracle backed by a machine-checked spec, and one that models poison/undef, which
concrete CPU execution (clang/lli at -O0) cannot observe. This module drives such an interpreter as a
REFINEMENT oracle for a recovered fold: it runs the emitted `before` and `after` IR (see
`pass_graph.to_llvm_ir`) over a value sweep and checks `before defined => (after defined AND after ==
before)` -- the same criterion the z3 refinement check uses -- so a divergence flags an obligation the
mechanized semantics disagrees with.

Interpreter protocol (kept minimal so Vellvm/lli/a test stub can all satisfy it): invoked as
`interp <module.ll> <fn> <arg0> <arg1> ...`, it prints the integer result on stdout, or `poison` /
`undef` (or nothing) when the result is not a defined value.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from itertools import product
from pathlib import Path

DEFAULT_VALUES = (0, 1, 2, 3, 5, 8)


def _arity(ll_text: str, fn: str) -> int | None:
    import re
    m = re.search(r"define\s+\S+\s+@" + re.escape(fn) + r"\s*\(([^)]*)\)", ll_text)
    if not m:
        return None
    return len([p for p in m.group(1).split(",") if p.strip()])


def _run(interp: str, ll_text: str, fn: str, args, timeout: int):
    """Run the interpreter; return the int result, or None for a poison/undef/undefined result."""
    with tempfile.TemporaryDirectory() as d:
        mod = Path(d) / "m.ll"
        mod.write_text(ll_text)
        proc = subprocess.run([interp, str(mod), fn, *[str(a) for a in args]],
                              capture_output=True, text=True, timeout=timeout)
        out = proc.stdout.strip()
        if out in ("", "poison", "undef") or proc.returncode != 0:
            return None
        try:
            return int(out)
        except ValueError:
            return None


def differential(before_ir: str, after_ir: str, fn: str, interp_bin: str,
                 values=DEFAULT_VALUES, timeout: int = 10) -> dict:
    """Refinement differential via a poison-aware interpreter. Returns a status dict:
    differential-pass | differential-mismatch(+witness) | unsupported-signature | skipped."""
    interp = shutil.which(interp_bin) or (interp_bin if Path(interp_bin).exists() else None)
    if interp is None:
        return {"status": "skipped", "reason": "no interpreter"}
    arity = _arity(before_ir, fn)
    if arity is None:
        return {"status": "unsupported-signature"}
    for combo in product(values, repeat=arity):
        try:
            before = _run(interp, before_ir, fn, combo, timeout)
            after = _run(interp, after_ir, fn, combo, timeout)
        except subprocess.TimeoutExpired:
            return {"status": "inconclusive", "reason": "timeout"}
        if before is None:
            continue                                          # before poison/undef -> nothing to refine
        if after is None or after != before:                  # after must be defined AND equal
            return {"status": "differential-mismatch",
                    "witness": {"params": list(combo), "before": before, "after": after}}
    return {"status": "differential-pass"}
