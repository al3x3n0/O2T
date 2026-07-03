#!/usr/bin/env python3
"""Cover the model-checking backend without requiring CBMC/ESBMC to be installed."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FX = ROOT / "tests" / "fixtures"

from o2t.orchestrate.classify import classify
from o2t.orchestrate.plan import plan_for
from o2t.orchestrate.run import execute_check, resolve_context
from o2t.symexec import modelcheck as M


FAKE_ENGINE = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "unguarded" in fn or fn.endswith("_raw"):
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  function=" + fn)
    sys.exit(10)

print("VERIFICATION SUCCESSFUL")
sys.exit(0)
"""


def write_fake_engine(path: Path) -> None:
    path.write_text(FAKE_ENGINE, encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    old_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        write_fake_engine(td / "cbmc")
        write_fake_engine(td / "esbmc")
        os.environ["PATH"] = str(td) + os.pathsep + old_path
        try:
            path, engine = M.resolve_engine("auto")
            assert engine == "cbmc" and Path(path).name == "cbmc", (path, engine)
            path, engine = M.resolve_engine("esbmc")
            assert engine == "esbmc" and Path(path).name == "esbmc", (path, engine)

            rep = M.run_modelcheck(M.DEFAULT_SOURCE, [
                "urem_guarded",
                "urem_unguarded",
                "add_nsw_unguarded",
                "select_to_or_raw",
            ])
            assert rep["status"] == "ok" and rep["engine"] == "cbmc", rep
            assert rep["proved"] == 1 and rep["refuted"] == 3 and not rep["ok"], rep
            assert all(r.get("command") for r in rep["results"]), rep
            assert next(r for r in rep["results"] if r["status"] == "refuted").get("witness_excerpt"), rep

            tool = ROOT / "tools" / "cv-modelcheck-real-pass.py"
            ok_proc = subprocess.run(
                [sys.executable, str(tool), "--fold", "select_to_or_freeze", "--engine", "cbmc"],
                capture_output=True, text=True)
            assert ok_proc.returncode == 0 and '"ok": true' in ok_proc.stdout, ok_proc.stdout
            bad_proc = subprocess.run(
                [sys.executable, str(tool), "--fold", "select_to_or_raw", "--engine", "cbmc"],
                capture_output=True, text=True)
            assert bad_proc.returncode == 1 and '"refuted": 1' in bad_proc.stdout, bad_proc.stdout

            # The orchestrator plans and dispatches the new canonical peephole strategy when a
            # model checker is available. The CLI defaults to sound folds, so this maps to proved.
            ctx = resolve_context()
            ctx["model-checker"] = str(td / "cbmc")
            plan = plan_for(classify((FX / "intent_inference_snippet.cpp").read_text(),
                                     "instcombine"), ctx, has_source=True)
            mc = next(c for c in plan if c.strategy == "modelcheck-real-pass")
            assert mc.feasible, mc
            verdict = execute_check(mc, FX / "intent_inference_snippet.cpp", "instcombine", ctx)
            assert verdict["verdict"] == "proved" and verdict["proved"] == 3, verdict
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_real_pass_fake_fixture OK: fake CBMC/ESBMC selection, JSON, CLI exits, "
          "and orchestrator mapping are covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
