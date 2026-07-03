#!/usr/bin/env python3
"""Cover the deep DCE dead-instruction erasure model."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import dce_model as dce


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("dce_model_fixture: z3 not found, skipped")
        return 0

    results = dce.run_contracts(z3)
    assert all(result["ok"] for result in results.values()), results
    assert results["erase-trivially-dead"]["status"] == "proved"
    for name in ("erase-with-live-use", "erase-with-side-effect", "erase-unguarded"):
        assert results[name]["status"] == "refuted" and results[name]["witness"], (name, results[name])
    assert results["erase-unused-alloca"]["status"] == "proved"
    for name in ("erase-used-alloca", "erase-escaped-alloca", "erase-lifetime-observed-alloca"):
        assert results[name]["status"] == "refuted" and results[name]["witness"], (name, results[name])
    assert results["erase-dead-loop-instruction"]["status"] == "proved"
    for name in ("erase-loop-result-use", "erase-loop-control-effect", "erase-loop-side-effect"):
        assert results[name]["status"] == "refuted" and results[name]["witness"], (name, results[name])

    assert dce.prove_dead_erase(z3, no_live_use=True, no_side_effect=True)[0] == "proved"
    assert dce.prove_dead_erase(z3, no_live_use=False, no_side_effect=True)[0] == "refuted"
    assert dce.prove_dead_erase(z3, no_live_use=True, no_side_effect=False)[0] == "refuted"
    assert dce.prove_unused_alloca_erase(
        z3,
        no_uses=True,
        no_escape=True,
        no_lifetime_effect=True,
    )[0] == "proved"
    assert dce.prove_unused_alloca_erase(
        z3,
        no_uses=False,
        no_escape=True,
        no_lifetime_effect=True,
    )[0] == "refuted"
    assert dce.prove_dead_loop_instruction_erase(
        z3,
        no_loop_result_use=True,
        no_loop_control_effect=True,
        no_loop_side_effect=True,
    )[0] == "proved"
    assert dce.prove_dead_loop_instruction_erase(
        z3,
        no_loop_result_use=True,
        no_loop_control_effect=False,
        no_loop_side_effect=True,
    )[0] == "refuted"

    tool = ROOT / "tools" / "cv-validate-dce.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout
    assert '"proved": 3' in proc.stdout and '"refuted": 9' in proc.stdout, proc.stdout

    print("dce_model_fixture OK: guarded dead-instruction, dead-loop-instruction, and "
          "unused-alloca erasures prove; observable cases refute with witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
