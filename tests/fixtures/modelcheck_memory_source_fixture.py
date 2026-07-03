#!/usr/bin/env python3
"""Cover CBMC/ESBMC harness generation for source-mined memory transforms."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.symexec.modelcheck_memory import source_records  # noqa: E402

FX = ROOT / "tests" / "fixtures"

FAKE_CBMC = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "eliminateStoreNoOverwriteGuard" in fn or "forwardStoreToLoadNoAliasMissing" in fn:
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
            records = source_records(FX / "dse_memory_folds.cpp")
            by_function = {record["source_function"]: record for record in records}
            assert set(by_function) == {
                "eliminateOverwrittenStore",
                "forwardStoreToLoad",
                "forwardStoreToLoadNoAliasMissing",
                "eliminateStoreNoOverwriteGuard",
            }, records
            assert by_function["eliminateOverwrittenStore"]["marker"] == "probe.dse.overwritten-store", by_function
            assert by_function["forwardStoreToLoad"]["marker"] == "probe.mem2reg.store-load-forward", by_function
            assert by_function["forwardStoreToLoadNoAliasMissing"]["marker"] == "probe.mem2reg.store-load-forward", by_function
            assert by_function["eliminateStoreNoOverwriteGuard"]["line"] > 0, by_function

            out_dir = td / "memory-modelcheck"
            summary_path = td / "memory-summary.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-memory-pass.py"),
                    "--source",
                    str(FX / "dse_memory_folds.cpp"),
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
            assert summary["model"] == "o2t-modelcheck-memory-source-summary-v1", summary
            assert summary["records"] == 4 and summary["transforms"] == 4, summary
            assert summary["generated"] == 4 and summary["proved"] == 2, summary
            assert summary["refuted"] == 2 and summary["unsupported"] == 0, summary
            assert summary["widths"]["32"]["proved"] == 2 and summary["widths"]["32"]["refuted"] == 2, summary["widths"]
            findings = {finding["source_function"]: finding for finding in summary["findings"]}
            assert findings["eliminateStoreNoOverwriteGuard"]["marker"] == "probe.dse.overwritten-store", findings
            assert findings["forwardStoreToLoadNoAliasMissing"]["marker"] == "probe.mem2reg.store-load-forward", findings
            for finding in findings.values():
                assert finding["domain"] == "memory-bv32", finding
                assert finding["source_function"] in finding["harness_function"], finding
                assert "Counterexample" in finding["witness_excerpt"], finding

            harnesses = sorted((out_dir / "harnesses").glob("*.cpp"))
            assert len(harnesses) == 4, harnesses
            assert any("eliminateStoreNoOverwriteGuard" in harness.name for harness in harnesses), harnesses
            assert any("forwardStoreToLoadNoAliasMissing" in harness.name for harness in harnesses), harnesses
            for harness in harnesses:
                subprocess.run(
                    ["clang++", "-std=c++17", "-I", str(ROOT / "o2t" / "symexec"),
                     "-fsyntax-only", str(harness)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            expanded_dir = td / "memory-modelcheck-expanded"
            expanded_summary_path = td / "memory-expanded-summary.json"
            expanded = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-memory-pass.py"),
                    "--source",
                    str(FX / "dse_memory_folds.cpp"),
                    "--out-dir",
                    str(expanded_dir),
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
            assert expanded.returncode == 1, expanded.stdout + expanded.stderr
            expanded_summary = json.loads(expanded_summary_path.read_text(encoding="utf-8"))
            assert expanded_summary["selected_widths"] == [8, 16], expanded_summary
            assert expanded_summary["instances"] == 8 and expanded_summary["generated"] == 8, expanded_summary
            assert expanded_summary["widths"]["8"]["proved"] == 2, expanded_summary["widths"]
            assert expanded_summary["widths"]["8"]["refuted"] == 2, expanded_summary["widths"]
            assert expanded_summary["widths"]["16"]["proved"] == 2, expanded_summary["widths"]
            assert expanded_summary["widths"]["16"]["refuted"] == 2, expanded_summary["widths"]
            expanded_domains = {result["domain"] for result in expanded_summary["results"]}
            assert expanded_domains == {"memory-bv8", "memory-bv16"}, expanded_domains
            finding_domains = {finding["domain"] for finding in expanded_summary["findings"]}
            assert finding_domains == {"memory-bv8", "memory-bv16"}, finding_domains
            expanded_harnesses = sorted((expanded_dir / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 8, expanded_harnesses
            assert any("_w8" in harness.name for harness in expanded_harnesses), expanded_harnesses
            assert any("_w16" in harness.name for harness in expanded_harnesses), expanded_harnesses
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_memory_source_fixture OK: source-mined memory transforms generate "
          "modelcheck harnesses; missing overwrite guard is refuted with an actionable finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
