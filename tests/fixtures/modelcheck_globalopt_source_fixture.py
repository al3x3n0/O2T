#!/usr/bin/env python3
"""Cover CBMC/ESBMC harness generation for source-mined GlobalOpt folds."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.symexec.modelcheck_globalopt import source_records  # noqa: E402

FX = ROOT / "tests" / "fixtures"

FAKE_CBMC = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "removeUnsafeGlobalInitializer" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  function=" + fn)
    sys.exit(10)

print("VERIFICATION SUCCESSFUL")
sys.exit(0)
"""


def main() -> int:
    old_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        fake = td / "cbmc"
        fake.write_text(FAKE_CBMC, encoding="utf-8")
        fake.chmod(0o755)
        os.environ["PATH"] = str(td) + os.pathsep + old_path
        try:
            sound_records = source_records(FX / "global_dead_initializer_snippet.cpp")
            unsafe_records = source_records(FX / "global_dead_initializer_unsafe_snippet.cpp")
            assert [record["source_function"] for record in sound_records] == ["removeDeadGlobalInitializer"], sound_records
            assert [record["source_function"] for record in unsafe_records] == ["removeUnsafeGlobalInitializer"], unsafe_records
            assert sound_records[0]["obligation"] == "unobservable", sound_records
            assert unsafe_records[0]["obligation"] == "external-observable", unsafe_records
            assert unsafe_records[0]["line"] > 0, unsafe_records

            out_dir = td / "globalopt-modelcheck"
            summary_path = td / "globalopt-summary.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-globalopt-pass.py"),
                    "--source",
                    str(FX / "global_dead_initializer_snippet.cpp"),
                    "--source",
                    str(FX / "global_dead_initializer_unsafe_snippet.cpp"),
                    "--out-dir",
                    str(out_dir),
                    "--engine",
                    "cbmc",
                    "--summary-json",
                    str(summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 1, proc.stdout + proc.stderr
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert summary["model"] == "o2t-modelcheck-globalopt-source-summary-v1", summary
            assert summary["source_kind"] == "globalopt-source", summary
            assert summary["records"] == 2 and summary["transforms"] == 2, summary
            assert summary["generated"] == 2 and summary["proved"] == 1, summary
            assert summary["refuted"] == 1 and summary["unsupported"] == 0, summary
            finding = summary["findings"][0]
            assert finding["marker"] == "probe.globalopt.dead-initializer", finding
            assert finding["domain"] == "global-initializer-observable-v1", finding
            assert finding["function"] == "removeUnsafeGlobalInitializer", finding
            assert finding["source_function"] == "removeUnsafeGlobalInitializer", finding
            assert "removeUnsafeGlobalInitializer" in finding["harness_function"], finding
            assert "Counterexample" in finding["witness_excerpt"], finding

            harnesses = sorted((out_dir / "harnesses").glob("*.cpp"))
            assert len(harnesses) == 2, harnesses
            clang = shutil.which("clang++")
            if clang:
                for harness in harnesses:
                    subprocess.run(
                        [clang, "-std=c++17", "-I", str(ROOT / "o2t" / "symexec"),
                         "-fsyntax-only", str(harness)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )

            expanded_out_dir = td / "globalopt-modelcheck-expanded"
            expanded_summary_path = td / "globalopt-summary-expanded.json"
            expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-globalopt-pass.py"),
                    "--source",
                    str(FX / "global_dead_initializer_snippet.cpp"),
                    "--source",
                    str(FX / "global_dead_initializer_unsafe_snippet.cpp"),
                    "--out-dir",
                    str(expanded_out_dir),
                    "--engine",
                    "cbmc",
                    "--widths",
                    "8,16",
                    "--summary-json",
                    str(expanded_summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert expanded_proc.returncode == 1, expanded_proc.stdout + expanded_proc.stderr
            expanded = json.loads(expanded_summary_path.read_text(encoding="utf-8"))
            assert expanded["selected_widths"] == [8, 16], expanded
            assert expanded["generated"] == 4 and expanded["proved"] == 2, expanded
            assert expanded["refuted"] == 2 and expanded["unsupported"] == 0, expanded
            assert expanded["widths"]["8"]["proved"] == 1 and expanded["widths"]["8"]["refuted"] == 1, expanded["widths"]
            assert expanded["widths"]["16"]["proved"] == 1 and expanded["widths"]["16"]["refuted"] == 1, expanded["widths"]
            expanded_harnesses = sorted((expanded_out_dir / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 4, expanded_harnesses
            assert any("_w8.cpp" in harness.name for harness in expanded_harnesses), expanded_harnesses
            assert any("_w16.cpp" in harness.name for harness in expanded_harnesses), expanded_harnesses
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_globalopt_source_fixture OK: source-mined GlobalOpt folds generate "
          "modelcheck harnesses; unsafe initializer defaulting is refuted with a finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
