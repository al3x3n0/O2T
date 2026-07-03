#!/usr/bin/env python3
"""Cover CBMC/ESBMC harness generation for source-mined SLP folds."""

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

from o2t.symexec.modelcheck_slp import source_records  # noqa: E402

FX = ROOT / "tests" / "fixtures"

FAKE_CBMC = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "vectorizeFPAddReductionUnguarded" in fn or "vectorizeAddPackSwappedExtract" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  function=" + fn)
    sys.exit(10)

print("VERIFICATION SUCCESSFUL")
sys.exit(0)
"""


def _syntax_check(harness_dir: Path) -> None:
    clang = shutil.which("clang++")
    if not clang:
        return
    for harness in sorted(harness_dir.glob("*.cpp")):
        subprocess.run(
            [clang, "-std=c++17", "-I", str(ROOT / "o2t" / "symexec"),
             "-fsyntax-only", str(harness)],
            check=True,
            capture_output=True,
            text=True,
        )


def main() -> int:
    old_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        fake = td / "cbmc"
        fake.write_text(FAKE_CBMC, encoding="utf-8")
        fake.chmod(0o755)
        os.environ["PATH"] = str(td) + os.pathsep + old_path
        try:
            reduction_records = source_records(FX / "slp_reduction_folds.cpp")
            by_function = {record["source_function"]: record for record in reduction_records}
            assert set(by_function) == {
                "vectorizeIntAddReduction",
                "vectorizeIntMulReduction",
                "vectorizeFPAddReductionGuarded",
                "vectorizeFPAddReductionUnguarded",
            }, reduction_records
            assert by_function["vectorizeFPAddReductionGuarded"]["status_detail"] == "reassoc-allowed", by_function
            assert by_function["vectorizeFPAddReductionUnguarded"]["domain"] == "vector-fp32xN", by_function

            reduction_out = td / "slp-reduction-modelcheck"
            reduction_summary_path = td / "slp-reduction-summary.json"
            reduction_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-slp-pass.py"),
                    "--source",
                    str(FX / "slp_reduction_folds.cpp"),
                    "--out-dir",
                    str(reduction_out),
                    "--engine",
                    "cbmc",
                    "--summary-json",
                    str(reduction_summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert reduction_proc.returncode == 1, reduction_proc.stdout + reduction_proc.stderr
            reduction = json.loads(reduction_summary_path.read_text(encoding="utf-8"))
            assert reduction["model"] == "o2t-modelcheck-slp-source-summary-v1", reduction
            assert reduction["source_kind"] == "slp-source", reduction
            assert reduction["records"] == 4 and reduction["generated"] == 4, reduction
            assert reduction["proved"] == 3 and reduction["refuted"] == 1, reduction
            finding = reduction["findings"][0]
            assert finding["marker"] == "probe.slp.vectorize-reduction", finding
            assert finding["domain"] == "vector-fp32xN", finding
            assert finding["source_function"] == "vectorizeFPAddReductionUnguarded", finding
            assert "Counterexample" in finding["witness_excerpt"], finding
            _syntax_check(reduction_out / "harnesses")

            reduction_expanded_out = td / "slp-reduction-modelcheck-expanded"
            reduction_expanded_summary_path = td / "slp-reduction-expanded-summary.json"
            reduction_expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-slp-pass.py"),
                    "--source",
                    str(FX / "slp_reduction_folds.cpp"),
                    "--out-dir",
                    str(reduction_expanded_out),
                    "--engine",
                    "cbmc",
                    "--widths",
                    "8,16",
                    "--summary-json",
                    str(reduction_expanded_summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert reduction_expanded_proc.returncode == 1, reduction_expanded_proc.stdout + reduction_expanded_proc.stderr
            reduction_expanded = json.loads(reduction_expanded_summary_path.read_text(encoding="utf-8"))
            assert reduction_expanded["selected_widths"] == [8, 16], reduction_expanded
            assert reduction_expanded["instances"] == 8 and reduction_expanded["generated"] == 8, reduction_expanded
            reduction_domains = {result["domain"] for result in reduction_expanded["results"]}
            assert reduction_domains == {"vector-bv8xN", "vector-bv16xN", "vector-fp32xN"}, reduction_domains
            assert {finding["domain"] for finding in reduction_expanded["findings"]} == {"vector-fp32xN"}, reduction_expanded["findings"]
            _syntax_check(reduction_expanded_out / "harnesses")

            pack_out = td / "slp-pack-modelcheck"
            pack_summary_path = td / "slp-pack-summary.json"
            pack_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-slp-pass.py"),
                    "--source",
                    str(FX / "slp_pack_folds.cpp"),
                    "--out-dir",
                    str(pack_out),
                    "--engine",
                    "cbmc",
                    "--summary-json",
                    str(pack_summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert pack_proc.returncode == 1, pack_proc.stdout + pack_proc.stderr
            pack = json.loads(pack_summary_path.read_text(encoding="utf-8"))
            assert pack["records"] == 3 and pack["generated"] == 3, pack
            assert pack["proved"] == 2 and pack["refuted"] == 1, pack
            pack_finding = pack["findings"][0]
            assert pack_finding["marker"] == "probe.slp.vectorize-binop", pack_finding
            assert pack_finding["domain"] == "vector-bv32xN", pack_finding
            assert pack_finding["source_function"] == "vectorizeAddPackSwappedExtract", pack_finding
            _syntax_check(pack_out / "harnesses")

            pack_expanded_out = td / "slp-pack-modelcheck-expanded"
            pack_expanded_summary_path = td / "slp-pack-expanded-summary.json"
            pack_expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-slp-pass.py"),
                    "--source",
                    str(FX / "slp_pack_folds.cpp"),
                    "--out-dir",
                    str(pack_expanded_out),
                    "--engine",
                    "cbmc",
                    "--widths",
                    "8,16",
                    "--summary-json",
                    str(pack_expanded_summary_path),
                ],
                capture_output=True,
                text=True,
            )
            assert pack_expanded_proc.returncode == 1, pack_expanded_proc.stdout + pack_expanded_proc.stderr
            pack_expanded = json.loads(pack_expanded_summary_path.read_text(encoding="utf-8"))
            assert pack_expanded["selected_widths"] == [8, 16], pack_expanded
            assert pack_expanded["instances"] == 6 and pack_expanded["generated"] == 6, pack_expanded
            assert pack_expanded["widths"]["8"]["proved"] == 2 and pack_expanded["widths"]["8"]["refuted"] == 1, pack_expanded["widths"]
            assert pack_expanded["widths"]["16"]["proved"] == 2 and pack_expanded["widths"]["16"]["refuted"] == 1, pack_expanded["widths"]
            assert {result["domain"] for result in pack_expanded["results"]} == {"vector-bv8xN", "vector-bv16xN"}, pack_expanded["results"]
            assert {finding["domain"] for finding in pack_expanded["findings"]} == {"vector-bv8xN", "vector-bv16xN"}, pack_expanded["findings"]
            expanded_harnesses = sorted((pack_expanded_out / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 6, expanded_harnesses
            assert any("_w8" in harness.name for harness in expanded_harnesses), expanded_harnesses
            assert any("_w16" in harness.name for harness in expanded_harnesses), expanded_harnesses
            _syntax_check(pack_expanded_out / "harnesses")
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_slp_source_fixture OK: source-mined SLP reductions and packs generate "
          "modelcheck harnesses; FP reassociation and swapped lanes are refuted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
