#!/usr/bin/env python3
"""Canonical discovery of O2T's external tools -- ONE place, so nothing hardcodes a platform path.

Resolution order for every tool:

    1. an env override -- `$O2T_<TOOL>` (and a couple of legacy `$<TOOL>` names), a path OR a name;
    2. the PATH -- the caller's preferred name first, then the versioned name (`opt-18`, `clang-18`,
       `lli-18`) so a stock Debian/Ubuntu apt.llvm.org install works with no symlinks;
    3. the macOS homebrew `llvm@18` keg, which is not on PATH by default.

So a non-macOS user needs only LLVM 18 on PATH (versioned names are fine) or `$O2T_OPT` etc. set --
the previous `/opt/homebrew/...` hardcode is now just the last of three fallbacks.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

HOMEBREW_LLVM18 = Path("/opt/homebrew/opt/llvm@18/bin")


def resolve(env_names: list[str], candidates: list[str], homebrew: str | None) -> str | None:
    """env override -> PATH (in `candidates` order) -> homebrew llvm@18. Absolute path or None."""
    for e in env_names:
        v = os.environ.get(e)
        if v:
            if Path(v).exists():
                return str(Path(v))
            w = shutil.which(v)
            if w:
                return w
    for c in candidates:
        if c:
            w = shutil.which(c)
            if w:
                return w
    if homebrew and (HOMEBREW_LLVM18 / homebrew).exists():
        return str(HOMEBREW_LLVM18 / homebrew)
    return None


def resolve_z3(prefer: str = "z3") -> str | None:
    return resolve(["O2T_Z3", "Z3"], [prefer, "z3"], None)


def resolve_opt(prefer: str = "opt") -> str | None:
    return resolve(["O2T_OPT", "COMPILERVERIF_HOST_OPT"], [prefer, "opt-18", "opt"], "opt")


def resolve_lli(prefer: str = "lli") -> str | None:
    return resolve(["O2T_LLI"], [prefer, "lli-18", "lli"], "lli")


def resolve_clang(prefer: str = "clang") -> str | None:
    return resolve(["O2T_CLANG", "COMPILERVERIF_SEMANTIC_CLANG"], [prefer, "clang-18", "clang"], "clang")


def resolve_bitwuzla(prefer: str = "bitwuzla") -> str | None:
    return resolve(["O2T_BITWUZLA"], [prefer, "bitwuzla"], None)
