#!/usr/bin/env python3
"""Compile pass implementation source to LLVM IR and mine the resulting IR."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--compile-commands", "-p", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--ir-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-impl-ir")
    return parser.parse_args()


def compile_db_path(path: Path) -> Path:
    return path / "compile_commands.json" if path.is_dir() else path


def load_compile_command(path: Path, source: Path) -> tuple[dict[str, Any], list[str]]:
    db_path = compile_db_path(path)
    data = json.loads(db_path.read_text(encoding="utf-8"))
    source = source.resolve()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        file_value = entry.get("file")
        if not isinstance(file_value, str):
            continue
        file_path = Path(file_value)
        directory = Path(str(entry.get("directory") or db_path.parent))
        if not file_path.is_absolute():
            file_path = directory / file_path
        if file_path.resolve() != source:
            continue
        if isinstance(entry.get("arguments"), list):
            return entry, [str(item) for item in entry["arguments"]]
        if isinstance(entry.get("command"), str):
            return entry, shlex.split(str(entry["command"]))
    raise ValueError(f"missing compile command for source: {source}")


def strip_compile_outputs(args: list[str], source: Path) -> list[str]:
    out: list[str] = []
    skip_next = False
    source_resolved = source.resolve()
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {"-o", "-MF", "-MT", "-MQ"}:
            skip_next = True
            continue
        if arg in {"-c", "-S", "-emit-llvm"}:
            continue
        if arg.startswith("-o") and arg != "-objective-c":
            continue
        if arg.startswith("-MF") or arg.startswith("-MT") or arg.startswith("-MQ"):
            continue
        candidate = Path(arg)
        try:
            if candidate.exists() and candidate.resolve() == source_resolved:
                continue
        except OSError:
            pass
        out.append(arg)
    return out


def compile_to_ir(
    compile_command: list[str],
    directory: Path,
    source: Path,
    ll_path: Path,
    *,
    debug_info: bool,
) -> subprocess.CompletedProcess[str]:
    command = list(compile_command)
    command.extend(["-S", "-emit-llvm"])
    if debug_info:
        command.append("-g")
    command.extend(["-O0", str(source), "-o", str(ll_path)])
    return subprocess.run(
        command,
        cwd=directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def mine_ir(ir_miner: Path, ll_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ir_miner), "--input", str(ll_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def should_retry_without_debug_info(stderr: str) -> bool:
    return "expected instruction opcode" in stderr and "#dbg_" in stderr


def split_top_level_commas(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def insert_dbg_declare_declaration(lines: list[str]) -> list[str]:
    declaration = "declare void @llvm.dbg.declare(metadata, metadata, metadata)\n"
    if any("@llvm.dbg.declare" in line and line.lstrip().startswith("declare ") for line in lines):
        return lines
    for index, line in enumerate(lines):
        if line.startswith("attributes #") or line.startswith("!llvm.") or re.match(r"!\d+ =", line):
            return lines[:index] + [declaration, "\n"] + lines[index:]
    return lines + ["\n", declaration]


def normalize_new_debug_records(ll_path: Path) -> bool:
    text = ll_path.read_text(encoding="utf-8")
    if "#dbg_declare(" not in text:
        return False
    changed = False
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.match(r"^(\s*)#dbg_declare\((.*)\)\s*$", line.rstrip("\n"))
        if not match:
            lines.append(line)
            continue
        parts = split_top_level_commas(match.group(2))
        if len(parts) != 4:
            lines.append(line)
            continue
        lines.append(
            f"{match.group(1)}call void @llvm.dbg.declare("
            f"metadata {parts[0]}, metadata {parts[1]}, metadata {parts[2]}), "
            f"!dbg {parts[3]}\n"
        )
        changed = True
    if changed:
        ll_path.write_text("".join(insert_dbg_declare_declaration(lines)), encoding="utf-8")
    return changed


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    if not args.ir_miner.is_file():
        print(f"IR miner does not exist: {args.ir_miner}", file=sys.stderr)
        return 2
    try:
        entry, command = load_compile_command(args.compile_commands, source)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not command:
        print(f"empty compile command for source: {source}", file=sys.stderr)
        return 1

    directory = Path(str(entry.get("directory") or compile_db_path(args.compile_commands).parent))
    output_dir = args.out_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ll_path = output_dir / f"{source.stem}.pass-impl.ll"

    compile_command = strip_compile_outputs(command, source)
    compiled = compile_to_ir(
        compile_command,
        directory,
        source,
        ll_path,
        debug_info=True,
    )
    if compiled.returncode != 0:
        sys.stderr.write(compiled.stderr)
        return compiled.returncode or 1

    normalize_new_debug_records(ll_path)
    mined = mine_ir(args.ir_miner, ll_path)
    if mined.returncode != 0 and should_retry_without_debug_info(mined.stderr):
        compiled = compile_to_ir(
            compile_command,
            directory,
            source,
            ll_path,
            debug_info=False,
        )
        if compiled.returncode != 0:
            sys.stderr.write(compiled.stderr)
            return compiled.returncode or 1
        normalize_new_debug_records(ll_path)
        mined = mine_ir(args.ir_miner, ll_path)
    if mined.returncode != 0:
        sys.stderr.write(mined.stderr)
        return mined.returncode or 1
    sys.stdout.write(mined.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
