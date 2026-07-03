#!/usr/bin/env python3
"""Cover CBMC/ESBMC harness generation for source-mined DCE erasures."""

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

from o2t.symexec.modelcheck_dce import source_records  # noqa: E402

FX = ROOT / "tests" / "fixtures"

FAKE_CBMC = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if ("eraseWithoutGuard" in fn or "eraseAllocaWithoutGuard" in fn or
        "erasePositiveHasNUsesOrMoreAlloca" in fn or "eraseLoopInstructionWithoutGuard" in fn):
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  function=" + fn)
    sys.exit(10)

print("VERIFICATION SUCCESSFUL")
sys.exit(0)
"""


def main() -> int:
    old_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as directory:
        td = Path(directory)
        fake = td / "cbmc"
        fake.write_text(FAKE_CBMC, encoding="utf-8")
        fake.chmod(0o755)
        os.environ["PATH"] = str(td) + os.pathsep + old_path
        try:
            records = source_records(FX / "dce_dead_instruction_folds.cpp")
            assert [record["source_function"] for record in records] == [
                "eraseTriviallyDead",
                "eraseWouldBeDead",
                "eraseRecursiveDead",
                "eraseWithoutGuard",
            ], records
            assert records[0]["obligation"] == "unobservable", records
            assert records[-1]["obligation"] == "may-be-observable", records
            assert records[-1]["line"] > 0, records
            alloca_records = source_records(FX / "dce_unused_alloca_folds.cpp")
            assert [record["source_function"] for record in alloca_records] == [
                "eraseUnusedAlloca",
                "eraseUserEmptyAlloca",
                "eraseHasNUsesZeroAlloca",
                "eraseUsersEmptyAlloca",
                "eraseNotHasNUsesOrMoreAlloca",
                "erasePositiveHasNUsesOrMoreAlloca",
                "eraseAllocaWithoutGuard",
            ], alloca_records
            assert alloca_records[0]["marker"] == "probe.cleanup.unused-alloca", alloca_records
            assert alloca_records[0]["domain"] == "unused-alloca-observable-v1", alloca_records
            assert alloca_records[-1]["obligation"] == "alloca-may-be-observable", alloca_records
            loop_records = source_records(FX / "dce_dead_loop_instruction_folds.cpp")
            assert [record["source_function"] for record in loop_records] == [
                "eraseDeadLoopInstruction",
                "deleteDeadLoopInstruction",
                "eraseLoopInstructionWithoutGuard",
            ], loop_records
            assert loop_records[0]["marker"] == "probe.dce.dead-loop-instruction", loop_records
            assert loop_records[0]["domain"] == "dce-dead-loop-instruction-observable-v1", loop_records
            assert loop_records[-1]["obligation"] == "loop-instruction-may-be-observable", loop_records

            out_dir = td / "dce-modelcheck"
            summary_path = td / "dce-summary.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-dce-pass.py"),
                    "--source",
                    str(FX / "dce_dead_instruction_folds.cpp"),
                    "--source",
                    str(FX / "dce_unused_alloca_folds.cpp"),
                    "--source",
                    str(FX / "dce_dead_loop_instruction_folds.cpp"),
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
            assert summary["model"] == "o2t-modelcheck-dce-source-summary-v1", summary
            assert summary["source_kind"] == "dce-source", summary
            assert summary["records"] == 14 and summary["transforms"] == 14, summary
            assert summary["generated"] == 14 and summary["proved"] == 10, summary
            assert summary["refuted"] == 4 and summary["unsupported"] == 0, summary
            assert summary["width_mode"] == "native", summary
            assert summary["selected_widths"] == [], summary
            assert summary["widths"]["32"]["proved"] == 10, summary["widths"]
            assert summary["widths"]["32"]["refuted"] == 4, summary["widths"]
            findings = {finding["source_function"]: finding for finding in summary["findings"]}
            finding = findings["eraseWithoutGuard"]
            assert finding["marker"] == "probe.dce.dead-instruction", finding
            assert finding["domain"] == "dce-dead-instruction-observable-v1", finding
            assert finding["width"] == 32, finding
            assert "eraseWithoutGuard" in finding["harness_function"], finding
            assert "Counterexample" in finding["witness_excerpt"], finding
            alloca_finding = findings["eraseAllocaWithoutGuard"]
            assert alloca_finding["marker"] == "probe.cleanup.unused-alloca", alloca_finding
            assert alloca_finding["domain"] == "unused-alloca-observable-v1", alloca_finding
            positive_alloca_finding = findings["erasePositiveHasNUsesOrMoreAlloca"]
            assert positive_alloca_finding["marker"] == "probe.cleanup.unused-alloca", positive_alloca_finding
            assert positive_alloca_finding["domain"] == "unused-alloca-observable-v1", positive_alloca_finding
            loop_finding = findings["eraseLoopInstructionWithoutGuard"]
            assert loop_finding["marker"] == "probe.dce.dead-loop-instruction", loop_finding
            assert loop_finding["domain"] == "dce-dead-loop-instruction-observable-v1", loop_finding

            harnesses = sorted((out_dir / "harnesses").glob("*.cpp"))
            assert len(harnesses) == 14, harnesses
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

            expanded_out = td / "dce-modelcheck-expanded"
            expanded_summary_path = td / "dce-summary-expanded.json"
            expanded = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-dce-pass.py"),
                    "--source",
                    str(FX / "dce_dead_instruction_folds.cpp"),
                    "--source",
                    str(FX / "dce_unused_alloca_folds.cpp"),
                    "--source",
                    str(FX / "dce_dead_loop_instruction_folds.cpp"),
                    "--out-dir",
                    str(expanded_out),
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
            assert expanded_summary["generated"] == 28, expanded_summary
            assert expanded_summary["proved"] == 20, expanded_summary
            assert expanded_summary["refuted"] == 8, expanded_summary
            assert expanded_summary["widths"]["8"]["proved"] == 10, expanded_summary["widths"]
            assert expanded_summary["widths"]["8"]["refuted"] == 4, expanded_summary["widths"]
            assert expanded_summary["widths"]["16"]["proved"] == 10, expanded_summary["widths"]
            assert expanded_summary["widths"]["16"]["refuted"] == 4, expanded_summary["widths"]
            assert "eraseWithoutGuard @8b dce-dead-instruction-observable-v1" in expanded.stderr, expanded.stderr
            assert "eraseAllocaWithoutGuard @16b unused-alloca-observable-v1" in expanded.stderr, expanded.stderr
            assert "eraseLoopInstructionWithoutGuard @16b dce-dead-loop-instruction-observable-v1" in expanded.stderr, expanded.stderr
            expanded_harnesses = sorted((expanded_out / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 28, expanded_harnesses
            assert any("_w8.cpp" in harness.name for harness in expanded_harnesses), expanded_harnesses
            assert any("_w16.cpp" in harness.name for harness in expanded_harnesses), expanded_harnesses
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_dce_source_fixture OK: source-mined DCE, dead-loop, and unused-alloca "
          "erasures generate modelcheck harnesses; unguarded erases are refuted with findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
