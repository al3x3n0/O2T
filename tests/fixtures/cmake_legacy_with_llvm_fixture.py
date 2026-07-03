#!/usr/bin/env python3
"""Check that the legacy LLVM CMake option maps onto the O2T option."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cmake", required=True)
    parser.add_argument("--llvm-dir", type=Path)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")
    return result


def detect_llvm_dir(explicit: Path | None) -> Path | None:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    if os.environ.get("LLVM_DIR"):
        candidates.append(Path(os.environ["LLVM_DIR"]))
    llvm_config = shutil.which("llvm-config")
    if llvm_config:
        result = subprocess.run(
            [llvm_config, "--cmakedir"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(Path(result.stdout.strip()))
    candidates.extend([
        Path("/opt/homebrew/opt/llvm@18/lib/cmake/llvm"),
        Path("/opt/homebrew/opt/llvm/lib/cmake/llvm"),
        Path("/usr/local/opt/llvm/lib/cmake/llvm"),
    ])
    for candidate in candidates:
        if (candidate / "LLVMConfig.cmake").is_file():
            return candidate
    return None


def cache_has(cache: str, key: str, value: str) -> bool:
    prefix = f"{key}:"
    return any(line.startswith(prefix) and line.endswith(f"={value}") for line in cache.splitlines())


def main() -> int:
    args = parse_args()
    llvm_dir = detect_llvm_dir(args.llvm_dir)
    if llvm_dir is None:
        print("cmake_legacy_with_llvm_fixture: LLVMConfig.cmake not found, skipped")
        return 0
    print(f"cmake_legacy_with_llvm_fixture: using LLVM_DIR={llvm_dir}")

    run([
        args.cmake,
        "-S",
        str(args.repo),
        "-B",
        str(args.work_dir),
        "-DCOMPILERVERIF_BUILD_TESTS=OFF",
        "-DCOMPILERVERIF_BUILD_CLANG_TOOLS=OFF",
        "-DCOMPILERVERIF_WITH_LLVM=ON",
        f"-DLLVM_DIR={llvm_dir}",
    ])
    cache = (args.work_dir / "CMakeCache.txt").read_text(encoding="utf-8")
    assert cache_has(cache, "O2T_WITH_LLVM", "ON"), cache
    assert cache_has(cache, "COMPILERVERIF_WITH_LLVM", "ON"), cache
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
