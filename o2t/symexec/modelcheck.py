#!/usr/bin/env python3
"""Run optional CBMC/ESBMC bounded model checks for real-pass fold harnesses."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER_DIR = ROOT / "o2t" / "symexec"
DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "modelcheck_folds.cpp"
DEFAULT_SOUND_FOLDS = ("urem_guarded", "add_nsw_guarded", "select_to_or_freeze")
ENGINES = ("cbmc", "esbmc")

_FOLD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUCCESS_MARKERS = (
    "VERIFICATION SUCCESSFUL",
    "VERIFICATION SUCCEEDED",
    "VERIFICATION PASSED",
)
_FAILURE_MARKERS = (
    "VERIFICATION FAILED",
    "Counterexample",
    "Violated property",
    "assertion",
)


def resolve_engine(engine: str = "auto") -> tuple[str | None, str | None]:
    """Resolve a model-checker binary, preferring CBMC when `engine` is auto."""
    if engine == "auto":
        for name in ENGINES:
            path = shutil.which(name)
            if path:
                return path, name
        return None, None
    if engine not in ENGINES:
        return None, None
    path = shutil.which(engine)
    return (path, engine) if path else (None, engine)


def function_for_fold(fold: str) -> str:
    """Map a public fold id to the harness check function name."""
    if not _FOLD_RE.match(fold):
        raise ValueError(f"invalid fold name: {fold!r}")
    return fold if fold.startswith("check_") else f"check_{fold}"


def _engine_command(engine: str, engine_path: str, source: Path, function: str,
                    unwind: int) -> list[str]:
    common = [engine_path, "-I", str(HEADER_DIR), str(source), "--function", function,
              "--unwind", str(unwind), "--trace"]
    if engine == "cbmc":
        return common + ["--stop-on-fail", "--unwinding-assertions"]
    return common


def _excerpt(text: str, max_lines: int = 80, max_chars: int = 4000) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    excerpt = "\n".join(lines[-max_lines:])
    return excerpt[-max_chars:]


def _status_from_output(returncode: int, output: str) -> str:
    if any(marker in output for marker in _SUCCESS_MARKERS):
        return "proved"
    if any(marker in output for marker in _FAILURE_MARKERS):
        return "refuted"
    return "proved" if returncode == 0 else "error"


def run_fold(source: Path, fold: str, engine: str, engine_path: str, unwind: int = 8,
             timeout_s: int = 30) -> dict:
    """Run one harness function through the selected model checker."""
    try:
        function = function_for_fold(fold)
    except ValueError as exc:
        return {"fold": fold, "status": "error", "reason": str(exc), "command": []}

    command = _engine_command(engine, engine_path, source, function, unwind)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return {"fold": fold, "status": "error", "reason": "model checker timed out",
                "command": command, "witness_excerpt": _excerpt(output)}
    except OSError as exc:
        return {"fold": fold, "status": "error", "reason": str(exc), "command": command}

    output = proc.stdout + proc.stderr
    status = _status_from_output(proc.returncode, output)
    result = {"fold": fold, "status": status, "command": command}
    if status == "refuted":
        result["witness_excerpt"] = _excerpt(output)
    elif status == "error":
        result["reason"] = _excerpt(output) or f"exit code {proc.returncode}"
    return result


def run_modelcheck(source: Path | str = DEFAULT_SOURCE, folds: list[str] | tuple[str, ...] | None = None,
                   engine: str = "auto", unwind: int = 8, timeout_s: int = 30) -> dict:
    """Run a batch of fold checks and return a machine-readable report."""
    source = Path(source)
    folds = tuple(folds or DEFAULT_SOUND_FOLDS)
    engine_path, engine_name = resolve_engine(engine)
    if engine_path is None:
        wanted = engine if engine != "auto" else "cbmc/esbmc"
        return {"status": "skipped", "reason": f"model checker not found: {wanted}",
                "engine": engine_name, "engine_path": "", "folds": len(folds), "proved": 0,
                "refuted": 0, "errors": 0, "ok": False, "results": []}

    results = [run_fold(source, fold, engine_name, engine_path, unwind, timeout_s)
               for fold in folds]
    proved = sum(1 for r in results if r["status"] == "proved")
    refuted = sum(1 for r in results if r["status"] == "refuted")
    errors = sum(1 for r in results if r["status"] == "error")
    return {"status": "ok", "engine": engine_name, "engine_path": engine_path,
            "folds": len(results), "proved": proved, "refuted": refuted, "errors": errors,
            "ok": bool(results) and proved == len(results), "results": results}
