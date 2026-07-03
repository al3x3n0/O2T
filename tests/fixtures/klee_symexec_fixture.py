#!/usr/bin/env python3
"""Cover KLEE-driven symbolic execution of real fold control flow (o2t/symexec/klee_driver.py).

Asserts that KLEE explores the fold harness's REAL feasible paths -- the analysis queries AND the
input opcode are symbolic, so KLEE forks on input shape x guard outcomes, including the `&&`
short-circuit -- and that the driver discharges per-path refinement: every rewriting path of the
sound harness is proved under the facts its branches established, and the separate under-guarded
harness is REFUTED with a witness (caught without enumerating anything). Requires KLEE + its
matching clang (LLVM 16); skipped if absent (the cv-symexec-real-pass enumeration is the fallback).
Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.symexec import klee_driver as K


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("klee_symexec_fixture: z3 not found, skipped")
        return 0
    if not K.available():
        print("klee_symexec_fixture: klee or matching clang (LLVM 16) not found, skipped")
        return 0

    # 1) the SOUND harness: KLEE finds the feasible paths over opcode x guard outcomes; every
    #    rewriting path is proved under the facts its branches established.
    s = K.run_klee(z3)
    assert s["status"] == "ok" and s["ok"], s
    assert s["proved"] == 2 and s["refuted"] == 0 and s["rewriting_paths"] == 2, s
    # KLEE found more total paths than rewriting ones (the no-fold branches), incl. the && short
    # circuit on the sdiv guard (a path that checked only the first operand).
    assert s["paths"] >= 5, s
    short_circuit = any(r["decisions"] == ["nonneg!"] for r in s["rows"])
    assert short_circuit, ("KLEE should explore the && short-circuit path", s["rows"])
    # every rewriting path established the facts its rewrite needs.
    for r in s["rows"]:
        if r["rewrote"]:
            assert r["status"] == "proved" and r["facts"] >= 1, r

    # 2) the UNDER-GUARDED harness: KLEE finds the one rewriting path (no facts) -> REFUTED.
    bad = K.run_klee(z3, ROOT / "o2t" / "symexec" / "klee_fold_bad.c")
    assert bad["status"] == "ok" and not bad["ok"] and bad["refuted"] == 1, bad
    brow = next(r for r in bad["rows"] if r["rewrote"])
    assert brow["status"] == "refuted" and brow["facts"] == 0 and brow["witness"], brow

    # 3) the CLI agrees and exits 0 on the sound harness.
    tool = ROOT / "tools" / "cv-klee-symexec-pass.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout

    print("klee_symexec_fixture OK: KLEE explored the fold's real feasible paths (opcode x guards, "
          "incl. the && short-circuit); every rewriting path proved to refine the input under its "
          "established facts; the under-guarded harness refuted with a witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
