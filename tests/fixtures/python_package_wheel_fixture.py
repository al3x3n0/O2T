#!/usr/bin/env python3
"""Build the Python wheel offline and check renamed/compat packages."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")
    return result


def can_build_with(python: str) -> bool:
    result = subprocess.run(
        [python, "-c", "import setuptools.build_meta"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode == 0


def packaging_python() -> str | None:
    candidates = [sys.executable]
    path_python = shutil.which("python3")
    if path_python and path_python not in candidates:
        candidates.append(path_python)
    for candidate in candidates:
        if can_build_with(candidate):
            return candidate
    return None


def main() -> int:
    args = parse_args()
    python = packaging_python()
    if python is None:
        print("python_package_wheel_fixture: setuptools.build_meta not available, skipped")
        return 0
    wheel_dir = args.work_dir / "wheelhouse"
    if wheel_dir.exists():
        shutil.rmtree(wheel_dir)
    wheel_dir.mkdir(parents=True, exist_ok=True)
    run([
        python,
        "-m",
        "pip",
        "wheel",
        str(args.repo),
        "--no-deps",
        "--no-build-isolation",
        "--wheel-dir",
        str(wheel_dir),
    ])
    wheels = sorted(wheel_dir.glob("o2t-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert "o2t/__init__.py" in names, wheels[0]
    assert "compilerverif/__init__.py" in names, wheels[0]
    assert "o2t-0.1.0.dist-info/METADATA" in names, wheels[0]
    assert not [name for name in names if name.endswith(".pyc")], wheels[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
