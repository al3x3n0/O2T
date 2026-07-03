#!/usr/bin/env python3
"""Cover CBMC/ESBMC harness generation for source-mined LICM hoist folds."""

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

from o2t.symexec.modelcheck_licm import source_records  # noqa: E402

FX = ROOT / "tests" / "fixtures"

FAKE_CBMC = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "hoistInvariantOnly" in fn:
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
            records = source_records(FX / "licm_hoist_folds.cpp")
            by_function = {record["source_function"]: record for record in records}
            assert set(by_function) == {
                "hoistInvariantSpeculatable",
                "hoistInvariantGuaranteed",
                "hoistInvariantOnly",
            }, records
            assert by_function["hoistInvariantOnly"]["obligation"] == "safety", by_function
            assert by_function["hoistInvariantOnly"]["line"] > 0, by_function

            out_dir = td / "licm-modelcheck"
            summary_path = td / "licm-summary.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-licm-pass.py"),
                    "--source",
                    str(FX / "licm_hoist_folds.cpp"),
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
            assert summary["model"] == "o2t-modelcheck-licm-source-summary-v1", summary
            assert summary["source_kind"] == "licm-source", summary
            assert summary["records"] == 3 and summary["transforms"] == 3, summary
            assert summary["generated"] == 3 and summary["proved"] == 2, summary
            assert summary["refuted"] == 1 and summary["unsupported"] == 0, summary
            assert summary["widths"]["32"]["proved"] == 2 and summary["widths"]["32"]["refuted"] == 1, summary["widths"]
            finding = summary["findings"][0]
            assert finding["marker"] == "probe.licm.invariant-op", finding
            assert finding["domain"] == "loop-bv32", finding
            assert finding["function"] == "hoistInvariantOnly", finding
            assert finding["source_function"] == "hoistInvariantOnly", finding
            assert "hoistInvariantOnly" in finding["harness_function"], finding
            assert "Counterexample" in finding["witness_excerpt"], finding

            harnesses = sorted((out_dir / "harnesses").glob("*.cpp"))
            assert len(harnesses) == 3, harnesses
            assert any("hoistInvariantOnly" in harness.name for harness in harnesses), harnesses
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

            sound_summary_path = td / "licm-sound-summary.json"
            sound_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-licm-pass.py"),
                    "--source",
                    str(FX / "licm_hoist_sound.cpp"),
                    "--out-dir",
                    str(td / "licm-modelcheck-sound"),
                    "--engine",
                    "cbmc",
                    "--summary-json",
                    str(sound_summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert sound_proc.returncode == 0, sound_proc.stdout + sound_proc.stderr
            sound = json.loads(sound_summary_path.read_text(encoding="utf-8"))
            assert sound["records"] == 2 and sound["proved"] == 2 and sound["refuted"] == 0, sound

            expanded_summary_path = td / "licm-expanded-summary.json"
            expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-licm-pass.py"),
                    "--source",
                    str(FX / "licm_hoist_folds.cpp"),
                    "--out-dir",
                    str(td / "licm-modelcheck-expanded"),
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
            assert expanded["instances"] == 6 and expanded["generated"] == 6, expanded
            assert expanded["widths"]["8"]["proved"] == 2 and expanded["widths"]["8"]["refuted"] == 1, expanded["widths"]
            assert expanded["widths"]["16"]["proved"] == 2 and expanded["widths"]["16"]["refuted"] == 1, expanded["widths"]
            assert {r["domain"] for r in expanded["results"]} == {"loop-bv8", "loop-bv16"}, expanded["results"]
            assert {f["domain"] for f in expanded["findings"]} == {"loop-bv8", "loop-bv16"}, expanded["findings"]
            expanded_harnesses = sorted((td / "licm-modelcheck-expanded" / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 6, expanded_harnesses
            assert any("_w8" in harness.name for harness in expanded_harnesses), expanded_harnesses
            assert any("_w16" in harness.name for harness in expanded_harnesses), expanded_harnesses
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_licm_source_fixture OK: source-mined LICM hoists generate modelcheck "
          "harnesses; invariant-only trapping hoists are refuted with an actionable finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
