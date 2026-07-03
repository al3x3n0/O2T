#!/usr/bin/env python3
"""Minimize a raw .ll witness with llvm-reduce and a caller-supplied oracle.

The config reducers (`cv-reduce-config` / `cv-reduce-failing-config.py`) shrink a
`GeneratorConfig`. The grammar generator emits `.ll` directly, so its findings
have no config to reduce -- they need IR-level shrinking. This wraps LLVM's
`llvm-reduce`: it writes an interestingness test that runs the oracle on each
candidate module and reduces the IR while the oracle still fires.

Oracle contract matches the other reducers: a shell command with `{ll}`
substituted by the candidate module; exit 0 means "still failing/interesting".
Use `--invert` when the oracle is a validity check you want to keep failing, or
the `--opt-invalid` convenience (interesting when `opt` crashes or emits IR that
fails to verify -- i.e. a genuine opt bug).

Needs `llvm-reduce` (and `opt`/`llvm-as` for `--opt-invalid`); set CV_LLVM_BIN
(default /opt/homebrew/opt/llvm@18/bin, then PATH) or the explicit flags.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def resolve_tool(explicit: str | None, name: str) -> str:
    if explicit:
        return explicit
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    return str(base / name) if (base / name).exists() else name


def build_test_script(path: Path, oracle: str, invert: bool) -> None:
    body = ["#!/bin/sh", oracle.replace("{ll}", '"$1"'), "__s=$?"]
    if invert:
        body.append('if [ "$__s" -ne 0 ]; then exit 0; else exit 1; fi')
    else:
        body.append('exit "$__s"')
    path.write_text("\n".join(body) + "\n")
    path.chmod(0o755)


def line_count(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path, help="witness .ll")
    parser.add_argument("--out", type=Path, default=Path("reduced.ll"))
    parser.add_argument("--oracle", help="oracle command; {ll} is the candidate")
    parser.add_argument("--invert", action="store_true",
                        help="treat non-zero oracle exit as 'still failing'")
    parser.add_argument("--opt-invalid", action="store_true",
                        help="built-in oracle: opt crashes or emits IR that fails to verify")
    parser.add_argument("--passes", default="default<O2>")
    parser.add_argument("--llvm-reduce", dest="llvm_reduce")
    parser.add_argument("--opt")
    parser.add_argument("--llvm-as", dest="llvm_as")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2

    llvm_reduce = resolve_tool(args.llvm_reduce, "llvm-reduce")
    opt = resolve_tool(args.opt, "opt")
    llvm_as = resolve_tool(args.llvm_as, "llvm-as")

    if args.opt_invalid:
        oracle = (f"{opt} -S -passes={args.passes} {{ll}} -o - 2>/dev/null "
                  f"| {llvm_as} - -o {os.devnull} 2>/dev/null")
        invert = True
    elif args.oracle:
        oracle, invert = args.oracle, args.invert
    else:
        print("error: provide --oracle or --opt-invalid", file=sys.stderr)
        return 2

    before = line_count(args.input)
    with tempfile.TemporaryDirectory(prefix="cv-reduce-ir-") as tmp:
        test = Path(tmp) / "interesting.sh"
        build_test_script(test, oracle, invert)

        # The input must already be interesting, or there is nothing to reduce.
        if subprocess.run([str(test), str(args.input)]).returncode != 0:
            print("error: input is not interesting under the oracle", file=sys.stderr)
            return 1

        args.out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [llvm_reduce, f"--test={test}", str(args.input), "-o", str(args.out)],
            capture_output=True, text=True)
        if proc.returncode != 0 or not args.out.exists():
            print(f"error: llvm-reduce failed: {proc.stderr.strip()[:200]}",
                  file=sys.stderr)
            return 1

        still = subprocess.run([str(test), str(args.out)]).returncode == 0

    after = line_count(args.out)
    report = {
        "input": str(args.input),
        "reduced": str(args.out),
        "lines_before": before,
        "lines_after": after,
        "still_interesting": still,
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"reduced {before} -> {after} lines (still_interesting={still}) -> {args.out}",
          file=sys.stderr)
    return 0 if still else 1


if __name__ == "__main__":
    raise SystemExit(main())
