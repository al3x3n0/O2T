#!/usr/bin/env python3
"""Quarantined staging for agent-synthesized tool candidates.

Staged code is DATA until a human promotes it: it is written only under the run's
`<out-dir>/agent-staging/`, never under tools/ or tests/, never imported, and never on sys.path.
Its fixture is executed once, via `python -I` (isolated mode: no cwd/user-site on the path) in a
fresh temp cwd with a minimal environment, and the result is labeled `advisory-staged` -- it can
never feed a headline or a fail gate. Promotion is a MANUAL, documented procedure (human review,
`git mv` into tools/, CMake registration); this module deliberately provides no promotion API.

This is quarantine against accidents and prompt-injected sloppiness, NOT a security sandbox --
the fixture still runs with user privileges. docs/agent.md states this plainly.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_SAFE_NAME = re.compile(r"^cv-agent-[a-z0-9][a-z0-9-]{2,40}$")


class StagingArea:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def stage_tool(self, name: str, purpose: str, tool_source: str, fixture_source: str) -> dict:
        """Write the candidate tool + fixture under the staging root; refuse unsafe names and
        any path that escapes the root. Records sha256 digests in a manifest."""
        if not _SAFE_NAME.match(name):
            return {"error": f"unsafe tool name {name!r} (must match {_SAFE_NAME.pattern})"}
        tool_dir = (self.root / name).resolve()
        if self.root not in tool_dir.parents:
            return {"error": "staged path escapes the staging root"}
        tool_dir.mkdir(parents=True, exist_ok=True)
        tool_path = tool_dir / f"{name}.py"
        fixture_path = tool_dir / f"{name}_fixture.py"
        tool_path.write_text(tool_source)
        fixture_path.write_text(fixture_source)
        record = {
            "name": name,
            "purpose": purpose[:500],
            "path": str(tool_path),
            "fixture": str(fixture_path),
            "sha256": hashlib.sha256(tool_source.encode()).hexdigest(),
            "fixture_sha256": hashlib.sha256(fixture_source.encode()).hexdigest(),
        }
        self._append_manifest(record)
        return record

    def run_fixture(self, record: dict, timeout: int = 120) -> dict:
        """Run the staged fixture once, isolated (`python -I`, temp cwd, minimal env). The exit
        code and output tail are recorded as ADVISORY-STAGED evidence -- never a formal verdict."""
        fixture = record.get("fixture")
        if not fixture or not Path(fixture).exists():
            return {"exit_code": None, "error": "no staged fixture", "trust": "advisory-staged"}
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", fixture], cwd=cwd, timeout=timeout,
                    capture_output=True, text=True,
                    env={"PATH": "/usr/bin:/bin", "HOME": cwd})
                return {"exit_code": proc.returncode,
                        "stdout_tail": proc.stdout[-1000:], "stderr_tail": proc.stderr[-500:],
                        "trust": "advisory-staged"}
            except subprocess.TimeoutExpired:
                return {"exit_code": None, "error": f"fixture timeout ({timeout}s)",
                        "trust": "advisory-staged"}
            except OSError as exc:
                return {"exit_code": None, "error": str(exc), "trust": "advisory-staged"}

    def _append_manifest(self, record: dict) -> None:
        manifest = self.root / "manifest.json"
        entries = []
        if manifest.exists():
            try:
                entries = json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError):
                entries = []
        entries.append({k: record[k] for k in ("name", "purpose", "sha256", "fixture_sha256")})
        manifest.write_text(json.dumps(entries, indent=2) + "\n")

    def manifest(self) -> list:
        manifest = self.root / "manifest.json"
        try:
            return json.loads(manifest.read_text()) if manifest.exists() else []
        except (OSError, json.JSONDecodeError):
            return []
