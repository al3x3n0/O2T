#!/usr/bin/env python3
"""Toolchain discovery (o2t/toolchain.py): env -> PATH (versioned) -> homebrew, hermetically.

This is the layer that lets O2T run off macOS: the previous `/opt/homebrew/...` hardcode is now the
LAST of three fallbacks. Verified with fake dirs and no real tools, so it runs in any CI:

  * an env override ($O2T_OPT, a path or a name) wins over everything;
  * with no env and no plain `opt`, the VERSIONED `opt-18` on PATH resolves -- the stock
    Debian/Ubuntu apt.llvm.org layout, so a Linux user needs no symlinks;
  * the homebrew llvm@18 keg is used only when env and PATH both miss (the macOS case);
  * a tool that is genuinely absent resolves to None (callers then skip/decline).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402

BASE = Path("/tmp/o2t-toolchain-fixture")
PATHDIR = BASE / "path"      # stands in for the Linux PATH (apt.llvm.org: versioned names)
KEG = BASE / "keg"           # stands in for the macOS homebrew llvm@18 keg


def _fake(dir_: Path, *names):
    dir_.mkdir(parents=True, exist_ok=True)
    for n in names:
        (dir_ / n).write_text("#!/bin/sh\necho fake\n")
        (dir_ / n).chmod(0o755)


def main() -> int:
    _fake(PATHDIR, "opt-18", "somewhere-else")   # Linux: only the VERSIONED opt, no plain `opt`
    _fake(KEG, "opt")                             # macOS keg: plain `opt`

    saved_env = dict(os.environ)
    saved_hb = toolchain.HOMEBREW_LLVM18
    try:
        for k in ("O2T_OPT", "O2T_CLANG", "O2T_LLI", "O2T_Z3",
                  "COMPILERVERIF_HOST_OPT", "COMPILERVERIF_SEMANTIC_CLANG"):
            os.environ.pop(k, None)
        os.environ["PATH"] = str(PATHDIR)
        toolchain.HOMEBREW_LLVM18 = Path("/nonexistent-homebrew")   # behave like non-macOS

        # 1. VERSIONED candidate: only opt-18 on PATH -> resolves (no plain `opt`, no symlink needed).
        assert toolchain.resolve_opt() == str(PATHDIR / "opt-18"), toolchain.resolve_opt()

        # 2. A genuinely-absent tool -> None (clang-18 / z3 not present).
        assert toolchain.resolve_clang() is None, "absent clang must be None"
        assert toolchain.resolve_z3() is None, "absent z3 must be None"

        # 3. ENV OVERRIDE wins over PATH -- as a bare name...
        os.environ["O2T_OPT"] = "somewhere-else"
        assert toolchain.resolve_opt() == str(PATHDIR / "somewhere-else"), "env name override"
        # ...and as an absolute path.
        os.environ["O2T_OPT"] = str(PATHDIR / "somewhere-else")
        assert toolchain.resolve_opt() == str(PATHDIR / "somewhere-else"), "env path override"
        os.environ.pop("O2T_OPT")

        # 4. HOMEBREW keg: reached ONLY when env + PATH both miss (the macOS fallback).
        os.environ["PATH"] = ""
        toolchain.HOMEBREW_LLVM18 = KEG
        assert toolchain.resolve_opt() == str(KEG / "opt"), toolchain.resolve_opt()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        toolchain.HOMEBREW_LLVM18 = saved_hb

    print("toolchain_fixture OK: external-tool discovery is env -> PATH(versioned) -> homebrew -- a "
          "versioned opt-18 resolves with no symlinks (stock Ubuntu), env overrides win (name or path), "
          "absent tools give None, and the homebrew keg is only the last fallback. O2T runs off macOS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
