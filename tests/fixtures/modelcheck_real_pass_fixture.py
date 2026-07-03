#!/usr/bin/env python3
"""Optional real CBMC/ESBMC fixture for the bounded model-checking backend."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.symexec import modelcheck as M


def main() -> int:
    engine_path, engine = M.resolve_engine("auto")
    if engine_path is None:
        print("modelcheck_real_pass_fixture: cbmc/esbmc not found, skipped")
        return 0

    folds = [
        "urem_guarded",
        "add_nsw_guarded",
        "select_to_or_freeze",
        "urem_unguarded",
        "add_nsw_unguarded",
        "select_to_or_raw",
    ]
    rep = M.run_modelcheck(M.DEFAULT_SOURCE, folds, engine=engine)
    assert rep["status"] == "ok", rep
    assert rep["proved"] >= 3 and rep["refuted"] >= 3 and not rep["ok"], rep

    tool = ROOT / "tools" / "cv-modelcheck-real-pass.py"
    proc = subprocess.run([sys.executable, str(tool), "--engine", engine],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout

    print(f"modelcheck_real_pass_fixture OK: {engine} proved guarded/freeze folds and "
          "refuted under-guarded/raw folds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
