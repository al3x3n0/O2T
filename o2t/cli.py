#!/usr/bin/env python3
"""o2t -- one entry point for the Optimizer Testing Toolkit.

A thin, dependency-free dispatcher over the toolkit's ~160 `tools/cv-*.py` scripts, so a newcomer
has ONE discoverable command instead of a directory of them:

    o2t doctor              # is my toolchain ready? (z3 / LLVM-18 opt,lli,clang / optional solvers)
    o2t verify <pass.cpp>   # verify an LLVM pass from its source
    o2t orchestrate <tree>  # classify + verify a whole vendor pass tree
    o2t agent <tree>        # LLM-driven triage of the residue
    o2t list [pattern]      # discover the underlying cv-* tools
    o2t run <tool> [args]   # run any cv-* tool by name (long tail)
    o2t version

`doctor` is the first-run star: it reports exactly which tools are found or missing and what each
gap disables, using the same env->PATH->homebrew resolution the fixtures use. Tool binaries are
resolved from `$O2T_<TOOL>` / `$<TOOL>` env vars first, then PATH, then the macOS homebrew llvm@18
fallback -- so a non-macOS user points the env vars at their LLVM 18 and everything follows.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from o2t import toolchain
from o2t.toolchain import resolve as _resolve       # env -> PATH (versioned) -> homebrew llvm@18

_PKG = Path(__file__).resolve().parent          # <repo>/o2t
_REPO = _PKG.parent                             # <repo>
_HOMEBREW = toolchain.HOMEBREW_LLVM18


def _tools_dir() -> Path:
    """Where the cv-* shims live. Honors $O2T_HOME; defaults to the repo containing this package
    (works for a clone and for `pip install -e .`)."""
    return Path(os.environ.get("O2T_HOME", _REPO)) / "tools"


def _version(path: str) -> str:
    """A short version string for a resolved binary (best-effort, never raises)."""
    for flag in ("--version", "-version"):
        try:
            out = subprocess.run([path, flag], capture_output=True, text=True, timeout=10)
            text = (out.stdout + out.stderr).strip()
            if text:
                for ln in text.splitlines():
                    if any(k in ln.lower() for k in ("version", "z3", "bitwuzla")):
                        return ln.strip()[:70]
                return text.splitlines()[0].strip()[:70]
        except (OSError, subprocess.SubprocessError):
            pass
    return "(version unknown)"


# name -> (env overrides, PATH candidates, homebrew binary | None, required?, enables)
_TOOLCHAIN = [
    ("z3", (["O2T_Z3", "Z3"], ["z3"], None, True,
            "the SMT prover -- REQUIRED for every proof (Track A recovery and Track B TV)")),
    ("opt", (["O2T_OPT", "COMPILERVERIF_HOST_OPT"], ["opt-18", "opt"], "opt", False,
             "LLVM 18 `opt` -- whole-function translation validation, observation, SCEV ingestion")),
    ("lli", (["O2T_LLI"], ["lli-18", "lli"], "lli", False,
             "LLVM 18 `lli` -- the execution oracle for self-enrichment (validates proposed semantics)")),
    ("clang", (["O2T_CLANG", "COMPILERVERIF_SEMANTIC_CLANG"], ["clang-18", "clang"], "clang", False,
               "LLVM 18 `clang` -- the Clang-AST front-end (recovers folds parser-free from real source)")),
    ("bitwuzla", (["O2T_BITWUZLA"], ["bitwuzla"], None, False,
                  "optional second solver -- independent cross-check of Z3 verdicts")),
    ("klee", (["O2T_KLEE"], ["klee"], None, False,
              "optional -- symbolic IR-generator exploration")),
]


def _cmd_doctor(args: argparse.Namespace) -> int:
    print(f"o2t doctor -- toolchain check\n  python  {sys.version.split()[0]}  ({sys.executable})")
    missing_required = False
    llvm18 = {"opt": None, "clang": None}
    for name, (envs, cands, hb, required, enables) in _TOOLCHAIN:
        path = _resolve(envs, cands, hb)
        if path:
            ver = _version(path)
            note = ""
            if name in llvm18:
                is18 = "18" in ver
                llvm18[name] = is18
                if not is18:
                    alt = _HOMEBREW / hb if hb else None
                    if alt and alt.exists() and "18" in _version(str(alt)):
                        note = f"  [!] not LLVM 18 -- set O2T_{name.upper()}={alt}"
                    else:
                        note = f"  [!] not LLVM 18 -- install llvm@18 and set $O2T_{name.upper()}"
            print(f"  ✓ {name:<9} {path}\n      {ver}{note}")
        else:
            tag = "REQUIRED" if required else "optional"
            print(f"  ✗ {name:<9} MISSING ({tag}) -- {enables}")
            if required:
                missing_required = True
    print()
    if missing_required:
        print("  Result: NOT READY -- install z3 first (e.g. `brew install z3`, `apt install z3`,\n"
              "          or `pip install z3-solver` for the Python bindings).")
        return 1
    have_opt = _resolve(*_TOOLCHAIN[1][1][:3]) is not None
    if have_opt:
        print("  Result: READY -- Track A (source recovery) and Track B (translation validation) both run.")
    else:
        print("  Result: PARTIAL -- z3 present, so Track A source-recovery proofs run. Install LLVM 18\n"
              "          (`brew install llvm@18` / apt.llvm.org) and set $O2T_OPT to enable Track B TV.")
    print("  Tip: on non-macOS, point $O2T_OPT / $O2T_LLI / $O2T_CLANG at your LLVM 18 binaries.")
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    try:
        from importlib.metadata import version
        print(f"o2t {version('o2t')}")
    except Exception:
        print("o2t 0.1.0")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    tools = _tools_dir()
    if not tools.is_dir():
        print(f"tools directory not found at {tools} (set $O2T_HOME to your clone).", file=sys.stderr)
        return 1
    pat = args.pattern
    names = sorted(p.stem[3:] for p in tools.glob("cv-*.py")
                   if pat is None or pat in p.stem)
    if not names:
        print(f"no cv-* tools match {pat!r}" if pat else "no cv-* tools found")
        return 0
    print(f"{len(names)} tool(s){f' matching {pat!r}' if pat else ''} -- run one with `o2t run <name> [args]`:\n")
    for n in names:
        print(f"  {n}")
    return 0


def _dispatch(tool_stem: str, argv: list[str]) -> int:
    """Run tools/cv-<stem>.py as a subprocess, passing argv through."""
    tools = _tools_dir()
    script = tools / f"cv-{tool_stem}.py"
    if not script.exists():
        print(f"tool not found: {script}\n  (run from a clone, or set $O2T_HOME; `o2t list` shows names)",
              file=sys.stderr)
        return 1
    return subprocess.run([sys.executable, str(script), *argv]).returncode


# Curated front-door flows -> the backing tool; ALL remaining args are forwarded verbatim (so
# `o2t verify --selftest` and `o2t verify --help` reach the tool unchanged).
_PASSTHROUGH = {"verify": "verify-pass", "orchestrate": "orchestrate", "agent": "agent"}


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="o2t", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="check the toolchain (z3 / LLVM-18 / optional solvers)")
    sub.add_parser("version", help="print the o2t version")
    p_list = sub.add_parser("list", help="list the underlying cv-* tools")
    p_list.add_argument("pattern", nargs="?", help="substring filter")
    sub.add_parser("run", help="run any cv-* tool by name: o2t run <tool> [args]")
    sub.add_parser("verify", help="verify an LLVM pass from its source (forwards args)")
    sub.add_parser("orchestrate", help="classify + verify a whole pass tree (forwards args)")
    sub.add_parser("agent", help="LLM-driven triage of the residue (forwards args)")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Passthrough commands are split BEFORE argparse so arbitrary tool flags forward cleanly
    # (argparse REMAINDER mishandles a flag immediately after the subcommand).
    if argv and argv[0] in _PASSTHROUGH:
        return _dispatch(_PASSTHROUGH[argv[0]], argv[1:])
    if argv and argv[0] == "run":
        if len(argv) < 2:
            print("usage: o2t run <tool> [args...]   (see `o2t list`)", file=sys.stderr)
            return 1
        stem = argv[1][3:] if argv[1].startswith("cv-") else argv[1]
        return _dispatch(stem, argv[2:])

    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        return 0
    return {"doctor": _cmd_doctor, "version": _cmd_version, "list": _cmd_list}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
