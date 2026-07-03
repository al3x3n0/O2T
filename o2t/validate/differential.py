#!/usr/bin/env python3
"""Semi-formal differential validation: execute the REAL functions on an input sweep.

Where the symbolic prover stops -- a transform whose optimized side has no loop recurrence
(indvars/SCEV deletes the accumulator into a closed form using mixed-width i33/udiv/trunc that
the integer-ring discharge does not cover) -- this provides a SEMI-FORMAL verdict: compile both
the source and the optimized function with clang and run them on a sweep of small inputs, using
*actual LLVM/CPU semantics* (no interpreter to get wrong). Agreement on all sampled inputs is a
`differential-pass`; a disagreement is a concrete `differential-mismatch` witness -- a real bug.

Inputs are kept small (loops are bounded by a parameter) and each run is hard-timeout'd, so an
adversarial trip count cannot hang the suite. This is testing, not proof: a pass is bounded by
the sweep, never silently called "verified".
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_FALLBACK_CLANG = "/opt/homebrew/opt/llvm@18/bin/clang"
DEFAULT_VALUES = (0, 1, 2, 3, 4, 5, 8)


def find_clang(clang_bin="clang"):
    return shutil.which(clang_bin) or (_FALLBACK_CLANG if Path(_FALLBACK_CLANG).exists() else None)


def defined_names(ll_text):
    return [m.group(1) for m in re.finditer(r"define\b[^@]*@(\w+)\s*\(", ll_text)]


def rename_all(ll_text, suffix):
    """Suffix every user-defined function (definition + internal calls) so a source module and an
    optimized module can be linked together without symbol clashes. Intrinsics (@llvm.*) are
    untouched because they are declared, not defined."""
    for name in defined_names(ll_text):
        ll_text = re.sub(r"@" + re.escape(name) + r"\b", "@" + name + suffix, ll_text)
    return ll_text


def arity_of(ll_text, fn):
    """Return the parameter count if `fn` is `i32 (i32, ...)`, else None (unsupported signature)."""
    m = re.search(r"define\s+(\S+)\s+@" + re.escape(fn) + r"\s*\(([^)]*)\)", ll_text)
    if not m or m.group(1) != "i32":
        return None
    params = [p.strip() for p in m.group(2).split(",") if p.strip()]
    if any(p.split()[0] != "i32" for p in params):
        return None
    return len(params)


def build_driver(fn, arity, values):
    n = len(values)
    vlist = ", ".join(str(v) for v in values)
    sig = ", ".join(["int32_t"] * arity)
    decode = "\n".join(f"    int32_t a{j} = V[(idx / {n ** j}) % {n}];" for j in range(arity))
    call = ", ".join(f"a{j}" for j in range(arity))
    fmt = " ".join(["%d"] * arity) + " src=%d opt=%d"
    pargs = ", ".join(f"a{j}" for j in range(arity)) + ", s, o"
    return f"""#include <stdint.h>
#include <stdio.h>
int32_t {fn}_src({sig});
int32_t {fn}_opt({sig});
int main(void) {{
  int32_t V[] = {{{vlist}}};
  long total = 1; for (int i = 0; i < {arity}; i++) total *= {n};
  for (long idx = 0; idx < total; idx++) {{
{decode}
    int32_t s = {fn}_src({call}), o = {fn}_opt({call});
    if (s != o) {{ printf("MISMATCH {fmt}\\n", {pargs}); return 1; }}
  }}
  printf("OK %ld\\n", total);
  return 0;
}}
"""


def _signed(x):
    x &= (1 << 32) - 1
    return x - (1 << 32) if x >> 31 else x


def differential(src_text, opt_text, fn, clang_bin="clang", values=DEFAULT_VALUES, timeout=10):
    """Compile source+optimized `fn` and run them over the value sweep. Returns a status dict:
    differential-pass | differential-mismatch(+witness) | unsupported-signature | compile-failed |
    inconclusive(timeout) | skipped(no clang)."""
    clang = find_clang(clang_bin)
    if clang is None:
        return {"status": "skipped", "reason": "clang not found"}
    arity = arity_of(src_text, fn)
    if arity is None:
        return {"status": "unsupported-signature"}
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "s.ll").write_text(rename_all(src_text, "_src"))
        (dp / "o.ll").write_text(rename_all(opt_text, "_opt"))
        (dp / "drv.c").write_text(build_driver(fn, arity, values))
        exe = dp / "exe"
        comp = subprocess.run([clang, "-O0", "-Wno-override-module",
                               str(dp / "drv.c"), str(dp / "s.ll"), str(dp / "o.ll"),
                               "-o", str(exe)], capture_output=True, text=True)
        if comp.returncode != 0:
            return {"status": "compile-failed", "stderr": comp.stderr[-400:]}
        try:
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "inconclusive", "reason": "timeout"}
        if run.returncode == 0:
            return {"status": "differential-pass", "samples": len(values) ** arity}
        m = re.search(r"MISMATCH (.+) src=(-?\d+) opt=(-?\d+)", run.stdout)
        if m:
            params = [int(x) for x in m.group(1).split()]
            return {"status": "differential-mismatch",
                    "witness": {"params": params, "source": _signed(int(m.group(2))),
                                "optimized": _signed(int(m.group(3)))}}
        return {"status": "differential-mismatch", "raw": run.stdout.strip()}
