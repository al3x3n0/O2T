#!/usr/bin/env python3
"""Cover CBMC/ESBMC harness generation for source-mined CFG if-conversion folds."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.symexec.modelcheck_cfg import source_records  # noqa: E402

FX = ROOT / "tests" / "fixtures"

FAKE_CBMC = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "foldDiamondSwappedOperands" in fn:
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
            records = source_records(FX / "cfg_ifconv_folds.cpp")
            by_function = {record["source_function"]: record for record in records}
            assert set(by_function) == {
                "foldDiamondToSelect",
                "foldDiamondNegatedSwapped",
                "foldDiamondSwappedOperands",
            }, records
            assert by_function["foldDiamondSwappedOperands"]["line"] > 0, by_function

            out_dir = td / "cfg-modelcheck"
            summary_path = td / "cfg-summary.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-cfg-pass.py"),
                    "--source",
                    str(FX / "cfg_ifconv_folds.cpp"),
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
            assert summary["model"] == "o2t-modelcheck-cfg-source-summary-v1", summary
            assert summary["records"] == 3 and summary["transforms"] == 3, summary
            assert summary["generated"] == 3 and summary["proved"] == 2, summary
            assert summary["refuted"] == 1 and summary["unsupported"] == 0, summary
            assert summary["widths"]["32"]["proved"] == 2 and summary["widths"]["32"]["refuted"] == 1, summary["widths"]
            finding = summary["findings"][0]
            assert finding["marker"] == "probe.simplifycfg.diamond", finding
            assert finding["domain"] == "cfg-bv32", finding
            assert finding["function"] == "foldDiamondSwappedOperands", finding
            assert finding["source_function"] == "foldDiamondSwappedOperands", finding
            assert "foldDiamondSwappedOperands" in finding["harness_function"], finding
            assert "Counterexample" in finding["witness_excerpt"], finding

            harnesses = sorted((out_dir / "harnesses").glob("*.cpp"))
            assert len(harnesses) == 3, harnesses
            assert any("foldDiamondSwappedOperands" in harness.name for harness in harnesses), harnesses
            for harness in harnesses:
                subprocess.run(
                    ["clang++", "-std=c++17", "-I", str(ROOT / "o2t" / "symexec"),
                     "-fsyntax-only", str(harness)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            expanded_summary_path = td / "cfg-expanded-summary.json"
            expanded_out_dir = td / "cfg-modelcheck-expanded"
            expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-cfg-pass.py"),
                    "--source",
                    str(FX / "cfg_ifconv_folds.cpp"),
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
            assert expanded["generated"] == 6 and expanded["proved"] == 4, expanded
            assert expanded["refuted"] == 2 and expanded["unsupported"] == 0, expanded
            assert expanded["widths"]["8"]["proved"] == 2 and expanded["widths"]["8"]["refuted"] == 1, expanded["widths"]
            assert expanded["widths"]["16"]["proved"] == 2 and expanded["widths"]["16"]["refuted"] == 1, expanded["widths"]
            assert {result["domain"] for result in expanded["results"]} == {"cfg-bv8", "cfg-bv16"}, expanded["results"]
            assert {finding["domain"] for finding in expanded["findings"]} == {"cfg-bv8", "cfg-bv16"}, expanded["findings"]
            assert "foldDiamondSwappedOperands @8b cfg-bv8" in expanded_proc.stderr, expanded_proc.stderr
            assert "foldDiamondSwappedOperands @16b cfg-bv16" in expanded_proc.stderr, expanded_proc.stderr
            expanded_harnesses = sorted((expanded_out_dir / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 6, expanded_harnesses
            assert any("_w8.cpp" in harness.name for harness in expanded_harnesses), expanded_harnesses
            assert any("_w16.cpp" in harness.name for harness in expanded_harnesses), expanded_harnesses

            sound_summary_path = td / "cfg-sound-summary.json"
            sound_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-cfg-pass.py"),
                    "--source",
                    str(FX / "cfg_ifconv_sound.cpp"),
                    "--out-dir",
                    str(td / "cfg-modelcheck-sound"),
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
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_cfg_source_fixture OK: source-mined if-conversions generate modelcheck "
          "harnesses; swapped select operands are refuted with an actionable finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
