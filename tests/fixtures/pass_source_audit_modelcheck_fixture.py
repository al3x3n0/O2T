#!/usr/bin/env python3
"""Cover pass-source audit modelcheck artifacts with stubbed mining/validation tools."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


FAKE_CBMC = """#!/usr/bin/env python3
print("VERIFICATION SUCCESSFUL")
"""

FAKE_CBMC_FAIL = """#!/usr/bin/env python3
print("VERIFICATION FAILED")
print("Counterexample:")
print("  fake budget witness")
raise SystemExit(10)
"""

FAKE_CBMC_CFG = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "foldDiamondSwappedOperands" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  cfg swapped-operand witness")
    raise SystemExit(10)

print("VERIFICATION SUCCESSFUL")
"""

FAKE_CBMC_MEMORY = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "eliminateStoreNoOverwriteGuard" in fn or "forwardStoreToLoadNoAliasMissing" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  memory missing-overwrite witness")
    raise SystemExit(10)

print("VERIFICATION SUCCESSFUL")
"""

FAKE_CBMC_LICM = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "hoistInvariantOnly" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  licm invariant-only witness")
    raise SystemExit(10)

print("VERIFICATION SUCCESSFUL")
"""

FAKE_CBMC_GLOBALOPT = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "removeUnsafeGlobalInitializer" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  globalopt observable-initializer witness")
    raise SystemExit(10)

print("VERIFICATION SUCCESSFUL")
"""

FAKE_CBMC_DCE = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "eraseWithoutGuard" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  dce missing-trivially-dead witness")
    raise SystemExit(10)

print("VERIFICATION SUCCESSFUL")
"""

FAKE_CBMC_SLP = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "vectorizeFPAddReductionUnguarded" in fn or "vectorizeAddPackSwappedExtract" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  slp lane-or-reassociation witness")
    raise SystemExit(10)

print("VERIFICATION SUCCESSFUL")
"""

AST_MINER = """#!/usr/bin/env python3
import json, sys
source = sys.argv[-1]
print(json.dumps([{
  "file": source,
  "line": 12,
  "marker": "probe.synthetic.add-zero",
  "pass": "instcombine",
  "rewrite_source": "replace x + 0 with x"
}]))
"""

INFERER = """#!/usr/bin/env python3
import argparse, json
p = argparse.ArgumentParser()
p.add_argument("--findings")
p.add_argument("--out")
p.add_argument("--format")
p.add_argument("--min-confidence")
args = p.parse_args()
findings = json.load(open(args.findings, encoding="utf-8"))
source = findings[0].get("file", "unknown.cpp") if findings else "unknown.cpp"
record = {
  "marker": "probe.synthetic.add-zero",
  "file": source,
  "line": 12,
  "confidence": "high",
  "side_conditions": [],
  "intent_candidate": {
    "formal": {
      "domain": "scalar-bv32",
      "equivalence": "result",
      "variables": ["x"],
      "poison_variables": [],
      "refinement": "refinement",
      "before": {"op": "bvadd", "args": [{"op": "var", "name": "x"}, {"op": "bvconst", "bits": 32, "value": 0}]},
      "after": {"op": "var", "name": "x"}
    }
  }
}
open(args.out, "w", encoding="utf-8").write(json.dumps(record, sort_keys=True) + "\\n")
print(json.dumps({"inferred": 1}))
"""

VALIDATOR = """#!/usr/bin/env python3
import argparse, shutil
p = argparse.ArgumentParser()
p.add_argument("--input")
p.add_argument("--out")
p.add_argument("--z3")
args = p.parse_args()
shutil.copyfile(args.input, args.out)
print('{"validated": 1, "proof_status": {"proved": 1}}')
"""

COVERAGE = """#!/usr/bin/env python3
import argparse, json
p = argparse.ArgumentParser()
p.add_argument("--validated")
p.add_argument("--intent-registry")
p.add_argument("--semantic-facts")
p.add_argument("--guard-semantics")
p.add_argument("--out")
p.add_argument("--report")
args = p.parse_args()
data = {"summary": {"recommendation": {}, "optimization_transactions": {}, "source_program_graph_contract": {}}}
open(args.out, "w", encoding="utf-8").write(json.dumps(data))
open(args.report, "w", encoding="utf-8").write("coverage ok\\n")
print('{"coverage": "ok"}')
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    return parser.parse_args()


def write_tool(path: Path, text: str, source: Path | None = None) -> None:
    content = text
    if source is not None:
        content = content.replace("SOURCE_PLACEHOLDER", str(source))
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    args = parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as d:
        td = Path(d)
        source_dir = td / "src"
        source_dir.mkdir()
        source = source_dir / "third_party_instcombine_like_pass.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "third_party_instcombine_like_pass.cpp", source)
        cfg_source = source_dir / "cfg_ifconv_folds.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "cfg_ifconv_folds.cpp", cfg_source)
        memory_source = source_dir / "dse_memory_folds.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "dse_memory_folds.cpp", memory_source)
        licm_source = source_dir / "licm_hoist_folds.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "licm_hoist_folds.cpp", licm_source)
        globalopt_source = source_dir / "global_dead_initializer_unsafe_snippet.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "global_dead_initializer_unsafe_snippet.cpp", globalopt_source)
        dce_source = source_dir / "dce_dead_instruction_folds.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "dce_dead_instruction_folds.cpp", dce_source)
        slp_reduction_source = source_dir / "slp_reduction_folds.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "slp_reduction_folds.cpp", slp_reduction_source)
        slp_pack_source = source_dir / "slp_pack_folds.cpp"
        shutil.copyfile(ROOT / "tests" / "fixtures" / "slp_pack_folds.cpp", slp_pack_source)
        compile_db = td / "compile_commands.json"
        compile_db.write_text(
            json.dumps([
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {source}", "file": str(source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {cfg_source}", "file": str(cfg_source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {memory_source}", "file": str(memory_source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {licm_source}", "file": str(licm_source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {globalopt_source}", "file": str(globalopt_source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {dce_source}", "file": str(dce_source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {slp_reduction_source}", "file": str(slp_reduction_source)},
                {"directory": str(source_dir), "command": f"clang++ -std=c++17 {slp_pack_source}", "file": str(slp_pack_source)},
            ]),
            encoding="utf-8",
        )
        bin_dir = td / "bin"
        bin_dir.mkdir()
        write_tool(bin_dir / "cbmc", FAKE_CBMC)
        write_tool(bin_dir / "ast-miner", AST_MINER, source)
        write_tool(bin_dir / "inferer", INFERER, source)
        write_tool(bin_dir / "validator", VALIDATOR)
        write_tool(bin_dir / "coverage", COVERAGE)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bin_dir) + os.pathsep + old_path
        try:
            out = td / "audit"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, proc.stdout + proc.stderr
            summary = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
            readiness = json.loads((out / "real-pass-readiness.json").read_text(encoding="utf-8"))
            modelcheck = summary["modelcheck"]
            assert modelcheck["enabled"] is True and modelcheck["generated"] == 1, modelcheck
            assert modelcheck["proved"] == 1 and modelcheck["refuted"] == 0, modelcheck
            assert Path(modelcheck["summary"]).is_file(), modelcheck
            merged = json.loads(Path(modelcheck["summary"]).read_text(encoding="utf-8"))
            components = {component["source_kind"]: component for component in merged["components"]}
            assert components["intent"]["generated"] == 1, components
            assert components["cfg-source"]["records"] == 0, components
            assert components["memory-source"]["records"] == 0, components
            assert components["licm-source"]["records"] == 0, components
            assert components["globalopt-source"]["records"] == 0, components
            assert components["dce-source"]["records"] == 0, components
            assert components["slp-source"]["records"] == 0, components
            run_report = (out / "run-summary.txt").read_text(encoding="utf-8")
            assert "Modelcheck components" in run_report, run_report
            assert "intent: records=1 generated=1" in run_report, run_report
            assert "cfg-source: records=0 generated=0" in run_report, run_report
            assert "memory-source: records=0 generated=0" in run_report, run_report
            assert "licm-source: records=0 generated=0" in run_report, run_report
            assert "globalopt-source: records=0 generated=0" in run_report, run_report
            assert "dce-source: records=0 generated=0" in run_report, run_report
            assert "slp-source: records=0 generated=0" in run_report, run_report
            assert "Modelcheck widths" in run_report, run_report
            assert "selected=native" in run_report, run_report
            assert "32: proved=1 refuted=0 unsupported=0 skipped=0 error=0" in run_report, run_report
            readiness_report = (out / "real-pass-readiness.txt").read_text(encoding="utf-8")
            assert "modelcheck widths" in readiness_report, readiness_report
            assert "selected=native" in readiness_report, readiness_report
            assert "32: proved=1 refuted=0 unsupported=0 skipped=0 error=0" in readiness_report, readiness_report
            assert readiness["modelcheck"]["proved"] == 1, readiness
            assert "modelcheck_intents:" in proc.stdout, proc.stdout
            baseline = json.loads((out / "audit-baseline.json").read_text(encoding="utf-8"))
            assert baseline["modelcheck"]["model"] == "o2t-modelcheck-baseline-v1", baseline
            assert baseline["modelcheck"]["records"] == [], baseline["modelcheck"]

            expanded_out = td / "audit-expanded-widths"
            expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(expanded_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--modelcheck-widths",
                    "8,16",
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            assert expanded_proc.returncode == 0, expanded_proc.stdout + expanded_proc.stderr
            expanded_summary = json.loads((expanded_out / "run-summary.json").read_text(encoding="utf-8"))
            expanded_modelcheck = expanded_summary["modelcheck"]
            assert expanded_modelcheck["selected_widths"] == [8, 16], expanded_modelcheck
            assert expanded_modelcheck["generated"] == 2 and expanded_modelcheck["proved"] == 2, expanded_modelcheck
            assert expanded_modelcheck["widths"]["8"]["proved"] == 1, expanded_modelcheck["widths"]
            assert expanded_modelcheck["widths"]["16"]["proved"] == 1, expanded_modelcheck["widths"]
            expanded_components = {
                component["source_kind"]: component
                for component in expanded_modelcheck["components"]
            }
            assert expanded_components["intent"]["selected_widths"] == [8, 16], expanded_components
            assert expanded_components["memory-source"]["selected_widths"] == [8, 16], expanded_components
            expanded_report = (expanded_out / "run-summary.txt").read_text(encoding="utf-8")
            assert "Modelcheck widths" in expanded_report, expanded_report
            assert "selected=8,16" in expanded_report, expanded_report
            assert "intent: records=1 generated=2" in expanded_report, expanded_report
            assert "memory-source: records=0 generated=0" in expanded_report, expanded_report
            assert "8: proved=1 refuted=0 unsupported=0 skipped=0 error=0" in expanded_report, expanded_report
            assert "16: proved=1 refuted=0 unsupported=0 skipped=0 error=0" in expanded_report, expanded_report

            audit_helpers = runpy.run_path(str(ROOT / "tools" / "cv-run-pass-source-audit.py"))
            error_summary = audit_helpers["modelcheck_error_summary"](
                td / "missing-component-summary.json",
                "memory-source",
                1,
                "cbmc",
                "8,16",
                "modelcheck memory-source did not write a summary",
            )
            assert error_summary["selected_widths"] == [8, 16], error_summary
            merged_error_summary = audit_helpers["merge_modelcheck_summaries"](
                td / "merged-missing-component-summary.json",
                [error_summary],
                "8,16",
            )
            assert merged_error_summary["selected_widths"] == [8, 16], merged_error_summary
            assert merged_error_summary["components"][0]["selected_widths"] == [8, 16], merged_error_summary
            assert audit_helpers["modelcheck_width_lines"](merged_error_summary) == [
                "  selected=8,16",
                "  none",
            ], merged_error_summary
            explicit_empty_width_summary = {
                **error_summary,
                "width_mode": "8,bad",
                "selected_widths": [],
                "reason": "unsupported-width:bad",
            }
            merged_empty_width_summary = audit_helpers["merge_modelcheck_summaries"](
                td / "merged-invalid-width-summary.json",
                [explicit_empty_width_summary],
                "8,bad",
            )
            assert merged_empty_width_summary["selected_widths"] == [], merged_empty_width_summary
            assert merged_empty_width_summary["components"][0]["selected_widths"] == [], merged_empty_width_summary
            assert merged_empty_width_summary["components"][0]["width_mode"] == "8,bad", merged_empty_width_summary
            assert audit_helpers["modelcheck_width_lines"](merged_empty_width_summary) == [
                "  selected=none",
                "  none",
            ], merged_empty_width_summary
            legacy_width_summary = dict(error_summary)
            legacy_width_summary.pop("selected_widths")
            merged_legacy_width_summary = audit_helpers["merge_modelcheck_summaries"](
                td / "merged-legacy-width-summary.json",
                [legacy_width_summary],
                "8,16",
            )
            assert merged_legacy_width_summary["selected_widths"] == [8, 16], merged_legacy_width_summary
            assert audit_helpers["selected_widths_from_mode"](" native ") == []
            assert audit_helpers["selected_widths_label"]({"selected_widths": [], "width_mode": "8,bad"}) == "none"
            many_findings = {
                "findings": [
                    {
                        "status": "refuted",
                        "marker": f"probe.synthetic.{index}",
                        "file": "many.cpp",
                        "line": index,
                        "width": 8,
                        "domain": "scalar-bv8",
                        "reason": "counterexample",
                    }
                    for index in range(7)
                ]
            }
            many_finding_lines = audit_helpers["modelcheck_finding_lines"](many_findings, 5)
            assert len(many_finding_lines) == 6, many_finding_lines
            assert many_finding_lines[-1] == "  ... 2 more", many_finding_lines
            colliding_previous = {
                "model": "o2t-pass-source-audit-baseline-v1",
                "records": [],
                "modelcheck": {
                    "model": "o2t-modelcheck-baseline-v1",
                    "records": [
                        {
                            "key": "same.cpp|10|probe.same|8|refuted",
                            "file": "same.cpp",
                            "line": 10,
                            "marker": "probe.same",
                            "width": 8,
                            "status": "refuted",
                            "domain": "cfg-bv8",
                            "source_function": "foldA",
                            "function": "check_foldA",
                            "reason": "counterexample",
                        }
                    ],
                },
            }
            colliding_current = {
                "model": "o2t-pass-source-audit-baseline-v1",
                "records": [],
                "modelcheck": {
                    "model": "o2t-modelcheck-baseline-v1",
                    "records": [
                        *colliding_previous["modelcheck"]["records"],
                        {
                            "key": "same.cpp|10|probe.same|8|refuted",
                            "file": "same.cpp",
                            "line": 10,
                            "marker": "probe.same",
                            "width": 8,
                            "status": "refuted",
                            "domain": "cfg-bv8",
                            "source_function": "foldB",
                            "function": "check_foldB",
                            "reason": "counterexample",
                        },
                    ],
                },
            }
            colliding_diff = audit_helpers["compare_modelcheck_baselines"](
                colliding_previous,
                colliding_current,
                True,
            )
            assert colliding_diff["summary"]["new_refuted"] == 1, colliding_diff
            assert colliding_diff["new"][0]["source_function"] == "foldB", colliding_diff
            colliding_resolved_diff = audit_helpers["compare_modelcheck_baselines"](
                colliding_current,
                colliding_previous,
                True,
            )
            assert colliding_resolved_diff["summary"]["new_refuted"] == 0, colliding_resolved_diff
            assert colliding_resolved_diff["summary"]["resolved_refuted"] == 1, colliding_resolved_diff
            assert colliding_resolved_diff["resolved"][0]["source_function"] == "foldB", colliding_resolved_diff
            duplicate_without_identity_record = {
                "key": "same.cpp|10|probe.same|8|refuted",
                "file": "same.cpp",
                "line": 10,
                "marker": "probe.same",
                "width": 8,
                "status": "refuted",
                "domain": "cfg-bv8",
                "reason": "counterexample",
            }
            duplicate_without_identity_previous = {
                "model": "o2t-pass-source-audit-baseline-v1",
                "records": [],
                "modelcheck": {
                    "model": "o2t-modelcheck-baseline-v1",
                    "records": [
                        dict(duplicate_without_identity_record),
                        dict(duplicate_without_identity_record),
                    ],
                },
            }
            duplicate_without_identity_current = {
                "model": "o2t-pass-source-audit-baseline-v1",
                "records": [],
                "modelcheck": {
                    "model": "o2t-modelcheck-baseline-v1",
                    "records": [dict(duplicate_without_identity_record)],
                },
            }
            duplicate_without_identity_resolved = audit_helpers["compare_modelcheck_baselines"](
                duplicate_without_identity_previous,
                duplicate_without_identity_current,
                True,
            )
            assert duplicate_without_identity_resolved["summary"]["previous_records"] == 2, duplicate_without_identity_resolved
            assert duplicate_without_identity_resolved["summary"]["current_records"] == 1, duplicate_without_identity_resolved
            assert duplicate_without_identity_resolved["summary"]["resolved_refuted"] == 1, duplicate_without_identity_resolved
            duplicate_without_identity_new = audit_helpers["compare_modelcheck_baselines"](
                duplicate_without_identity_current,
                duplicate_without_identity_previous,
                True,
            )
            assert duplicate_without_identity_new["summary"]["previous_records"] == 1, duplicate_without_identity_new
            assert duplicate_without_identity_new["summary"]["current_records"] == 2, duplicate_without_identity_new
            assert duplicate_without_identity_new["summary"]["new_refuted"] == 1, duplicate_without_identity_new
            changed_reason_current = json.loads(json.dumps(colliding_previous))
            changed_reason_current["modelcheck"]["records"][0]["reason"] = "timeout"
            changed_reason_diff = audit_helpers["compare_modelcheck_baselines"](
                colliding_previous,
                changed_reason_current,
                True,
            )
            assert changed_reason_diff["summary"]["changed"] == 1, changed_reason_diff
            assert changed_reason_diff["summary"]["new_refuted"] == 0, changed_reason_diff
            assert changed_reason_diff["changed"][0]["changes"]["reason"] == {
                "before": "counterexample",
                "after": "timeout",
            }, changed_reason_diff
            resolved_reason_diff = audit_helpers["compare_modelcheck_baselines"](
                colliding_previous,
                {
                    "model": "o2t-pass-source-audit-baseline-v1",
                    "records": [],
                    "modelcheck": {
                        "model": "o2t-modelcheck-baseline-v1",
                        "records": [],
                    },
                },
                True,
            )
            changed_reason_text = audit_helpers["format_baseline_diff"]({
                "model": "o2t-pass-source-audit-baseline-diff-v1",
                "baseline_present": True,
                "summary": {
                    "previous_records": 0,
                    "current_records": 0,
                    "new": 0,
                    "resolved": 0,
                    "changed": 0,
                    "new_unsupported": 0,
                    "new_fallback_transactions": 0,
                },
                "new": [],
                "resolved": [],
                "changed": [],
                "modelcheck": {
                    **changed_reason_diff,
                    "resolved": resolved_reason_diff["resolved"],
                    "summary": {
                        **changed_reason_diff["summary"],
                        "resolved": resolved_reason_diff["summary"]["resolved"],
                        "resolved_refuted": resolved_reason_diff["summary"]["resolved_refuted"],
                    },
                },
            })
            assert "Changed modelcheck findings" in changed_reason_text, changed_reason_text
            assert "probe.same foldA reason:counterexample->timeout" in changed_reason_text, changed_reason_text
            assert "Top resolved modelcheck findings" in changed_reason_text, changed_reason_text
            assert "resolved_refuted=1" in changed_reason_text, changed_reason_text
            assert "refuted @8b cfg-bv8 probe.same same.cpp:10 (counterexample)" in changed_reason_text, changed_reason_text
            many_baseline_records = [
                {
                    "status": "refuted",
                    "width": 8,
                    "domain": "cfg-bv8",
                    "marker": f"probe.same.{index}",
                    "file": "same.cpp",
                    "line": index,
                    "reason": "counterexample",
                    "source_function": f"fold{index}",
                }
                for index in range(12)
            ]
            many_baseline_text = audit_helpers["format_baseline_diff"]({
                "model": "o2t-pass-source-audit-baseline-diff-v1",
                "baseline_present": True,
                "summary": {
                    "previous_records": 0,
                    "current_records": 0,
                    "new": 0,
                    "resolved": 0,
                    "changed": 0,
                    "new_unsupported": 0,
                    "new_fallback_transactions": 0,
                },
                "new": [],
                "resolved": [],
                "changed": [],
                "modelcheck": {
                    "baseline_present": True,
                    "summary": {
                        "previous_records": 12,
                        "current_records": 12,
                        "new": 12,
                        "resolved": 12,
                        "changed": 12,
                        "new_refuted": 12,
                        "new_error": 0,
                        "resolved_refuted": 12,
                        "resolved_error": 0,
                    },
                    "new": many_baseline_records,
                    "resolved": many_baseline_records,
                    "changed": [
                        {
                            "before": record,
                            "after": {**record, "reason": "timeout"},
                            "changes": {"reason": {"before": "counterexample", "after": "timeout"}},
                        }
                        for record in many_baseline_records
                    ],
                },
            })
            assert many_baseline_text.count("  ... 2 more") == 3, many_baseline_text
            many_source_records = [
                {
                    "key": f"key-{index}",
                    "marker": f"probe.source.{index}",
                    "file": "source.cpp",
                    "line": index,
                    "proof_status": "unsupported",
                    "transaction_lowering": "fallback",
                    "transaction_kind": "rewrite",
                    "transaction_opcode": "add",
                }
                for index in range(12)
            ]
            many_source_text = audit_helpers["format_baseline_diff"]({
                "model": "o2t-pass-source-audit-baseline-diff-v1",
                "baseline_present": True,
                "summary": {
                    "previous_records": 12,
                    "current_records": 12,
                    "new": 12,
                    "resolved": 0,
                    "changed": 12,
                    "new_unsupported": 12,
                    "new_fallback_transactions": 12,
                },
                "new": many_source_records,
                "resolved": [],
                "changed": [
                    {
                        "key": record["key"],
                        "before": {**record, "recommendation": "old"},
                        "after": {**record, "recommendation": "new"},
                        "changes": {"recommendation": {"before": "old", "after": "new"}},
                    }
                    for record in many_source_records
                ],
                "modelcheck": {
                    "baseline_present": True,
                    "summary": {
                        "previous_records": 0,
                        "current_records": 0,
                        "new": 0,
                        "resolved": 0,
                        "changed": 0,
                        "new_refuted": 0,
                        "new_error": 0,
                        "resolved_refuted": 0,
                        "resolved_error": 0,
                    },
                    "new": [],
                    "resolved": [],
                    "changed": [],
                },
            })
            assert many_source_text.count("  ... 2 more") == 3, many_source_text
            legacy_modelcheck_previous = json.loads(json.dumps(colliding_previous))
            legacy_modelcheck_previous["model"] = "compilerverif-pass-source-audit-baseline-v1"
            legacy_modelcheck_previous["modelcheck"]["model"] = "compilerverif-modelcheck-baseline-v1"
            legacy_modelcheck_previous["modelcheck"]["records"][0].pop("source_function")
            legacy_modelcheck_previous["modelcheck"]["records"][0].pop("function")
            legacy_modelcheck_current = json.loads(json.dumps(colliding_previous))
            legacy_modelcheck_diff = audit_helpers["compare_modelcheck_baselines"](
                legacy_modelcheck_previous,
                legacy_modelcheck_current,
                True,
            )
            assert legacy_modelcheck_diff["summary"]["new_refuted"] == 0, legacy_modelcheck_diff
            assert legacy_modelcheck_diff["summary"]["resolved_refuted"] == 0, legacy_modelcheck_diff
            assert legacy_modelcheck_diff["summary"]["changed"] == 1, legacy_modelcheck_diff
            legacy_baseline_path = td / "legacy-pass-source-baseline.json"
            legacy_baseline_path.write_text(json.dumps(legacy_modelcheck_previous), encoding="utf-8")
            loaded_legacy_baseline = audit_helpers["load_baseline"](legacy_baseline_path)
            assert loaded_legacy_baseline["model"] == "compilerverif-pass-source-audit-baseline-v1"
            assert loaded_legacy_baseline["modelcheck"]["model"] == "compilerverif-modelcheck-baseline-v1"

            write_tool(bin_dir / "cbmc", FAKE_CBMC_CFG)
            cfg_budget_out = td / "audit-cfg-budget"
            cfg_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(cfg_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(cfg_source),
                ],
                capture_output=True,
                text=True,
            )
            assert cfg_budget_proc.returncode == 1, cfg_budget_proc.stdout + cfg_budget_proc.stderr
            cfg_budget_summary = json.loads((cfg_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            assert cfg_budget_summary["modelcheck"]["generated"] == 4, cfg_budget_summary["modelcheck"]
            assert cfg_budget_summary["modelcheck"]["proved"] == 3, cfg_budget_summary["modelcheck"]
            assert cfg_budget_summary["modelcheck"]["refuted"] == 1, cfg_budget_summary["modelcheck"]
            cfg_finding = cfg_budget_summary["modelcheck"]["findings"][0]
            assert cfg_finding["marker"] == "probe.simplifycfg.diamond", cfg_finding
            assert cfg_finding["domain"] == "cfg-bv32", cfg_finding
            assert cfg_finding["source_function"] == "foldDiamondSwappedOperands", cfg_finding
            cfg_mc_diff = cfg_budget_summary["modelcheck_baseline_diff"]
            assert cfg_mc_diff["summary"]["new_refuted"] == 1, cfg_mc_diff
            assert cfg_budget_summary["budget_violations"] == [
                {"actual": 1, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], cfg_budget_summary["budget_violations"]
            cfg_diff_text = (cfg_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "probe.simplifycfg.diamond" in cfg_diff_text, cfg_diff_text

            cfg_expanded_budget_out = td / "audit-cfg-expanded-budget"
            cfg_expanded_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(cfg_expanded_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--modelcheck-widths",
                    "8,16",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(cfg_source),
                ],
                capture_output=True,
                text=True,
            )
            assert cfg_expanded_budget_proc.returncode == 1, (
                cfg_expanded_budget_proc.stdout + cfg_expanded_budget_proc.stderr
            )
            cfg_expanded_summary = json.loads(
                (cfg_expanded_budget_out / "run-summary.json").read_text(encoding="utf-8")
            )
            cfg_expanded_modelcheck = cfg_expanded_summary["modelcheck"]
            assert cfg_expanded_modelcheck["selected_widths"] == [8, 16], cfg_expanded_modelcheck
            assert cfg_expanded_modelcheck["generated"] == 8, cfg_expanded_modelcheck
            assert cfg_expanded_modelcheck["proved"] == 6, cfg_expanded_modelcheck
            assert cfg_expanded_modelcheck["refuted"] == 2, cfg_expanded_modelcheck
            assert cfg_expanded_modelcheck["widths"]["8"]["proved"] == 3, cfg_expanded_modelcheck["widths"]
            assert cfg_expanded_modelcheck["widths"]["8"]["refuted"] == 1, cfg_expanded_modelcheck["widths"]
            assert cfg_expanded_modelcheck["widths"]["16"]["proved"] == 3, cfg_expanded_modelcheck["widths"]
            assert cfg_expanded_modelcheck["widths"]["16"]["refuted"] == 1, cfg_expanded_modelcheck["widths"]
            cfg_expanded_components = {
                component["source_kind"]: component
                for component in cfg_expanded_modelcheck["components"]
            }
            assert cfg_expanded_components["intent"]["selected_widths"] == [8, 16], cfg_expanded_components
            assert cfg_expanded_components["cfg-source"]["selected_widths"] == [8, 16], cfg_expanded_components
            cfg_expanded_findings = [
                finding
                for finding in cfg_expanded_modelcheck["findings"]
                if finding.get("marker") == "probe.simplifycfg.diamond"
            ]
            assert {finding["width"] for finding in cfg_expanded_findings} == {8, 16}, cfg_expanded_findings
            assert {finding["domain"] for finding in cfg_expanded_findings} == {"cfg-bv8", "cfg-bv16"}, cfg_expanded_findings
            cfg_expanded_merged = json.loads(
                Path(cfg_expanded_modelcheck["summary"]).read_text(encoding="utf-8")
            )
            cfg_expanded_domains = {
                result["domain"]
                for result in cfg_expanded_merged["results"]
                if result.get("marker") == "probe.simplifycfg.diamond"
            }
            assert cfg_expanded_domains == {"cfg-bv8", "cfg-bv16"}, cfg_expanded_merged["results"]
            assert cfg_expanded_summary["modelcheck_baseline_diff"]["summary"]["new_refuted"] == 2
            assert cfg_expanded_summary["budget_violations"] == [
                {"actual": 2, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], cfg_expanded_summary["budget_violations"]
            cfg_expanded_report = (cfg_expanded_budget_out / "run-summary.txt").read_text(encoding="utf-8")
            assert "refuted: @8b cfg-bv8 probe.simplifycfg.diamond foldDiamondSwappedOperands" in cfg_expanded_report, cfg_expanded_report
            assert "refuted: @16b cfg-bv16 probe.simplifycfg.diamond foldDiamondSwappedOperands" in cfg_expanded_report, cfg_expanded_report

            write_tool(bin_dir / "cbmc", FAKE_CBMC_MEMORY)
            memory_budget_out = td / "audit-memory-budget"
            memory_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(memory_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(memory_source),
                ],
                capture_output=True,
                text=True,
            )
            assert memory_budget_proc.returncode == 1, memory_budget_proc.stdout + memory_budget_proc.stderr
            memory_budget_summary = json.loads((memory_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            assert memory_budget_summary["modelcheck"]["generated"] == 5, memory_budget_summary["modelcheck"]
            assert memory_budget_summary["modelcheck"]["proved"] == 3, memory_budget_summary["modelcheck"]
            assert memory_budget_summary["modelcheck"]["refuted"] == 2, memory_budget_summary["modelcheck"]
            memory_findings = {
                finding["source_function"]: finding
                for finding in memory_budget_summary["modelcheck"]["findings"]
            }
            assert memory_findings["eliminateStoreNoOverwriteGuard"]["marker"] == "probe.dse.overwritten-store", memory_findings
            assert memory_findings["forwardStoreToLoadNoAliasMissing"]["marker"] == "probe.mem2reg.store-load-forward", memory_findings
            assert all(finding["domain"] == "memory-bv32" for finding in memory_findings.values()), memory_findings
            memory_mc_diff = memory_budget_summary["modelcheck_baseline_diff"]
            assert memory_mc_diff["summary"]["new_refuted"] == 2, memory_mc_diff
            memory_baseline = json.loads((memory_budget_out / "audit-baseline.json").read_text(encoding="utf-8"))
            memory_baseline_domains = {
                record["source_function"]: record.get("domain")
                for record in memory_baseline["modelcheck"]["records"]
            }
            assert memory_baseline_domains == {
                "eliminateStoreNoOverwriteGuard": "memory-bv32",
                "forwardStoreToLoadNoAliasMissing": "memory-bv32",
            }, memory_baseline_domains
            assert memory_budget_summary["budget_violations"] == [
                {"actual": 2, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], memory_budget_summary["budget_violations"]
            memory_diff_text = (memory_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "probe.dse.overwritten-store" in memory_diff_text, memory_diff_text
            assert "probe.mem2reg.store-load-forward" in memory_diff_text, memory_diff_text
            assert "memory-bv32" in memory_diff_text, memory_diff_text
            memory_report = (memory_budget_out / "run-summary.txt").read_text(encoding="utf-8")
            assert "refuted: @32b memory-bv32 probe.dse.overwritten-store eliminateStoreNoOverwriteGuard" in memory_report, memory_report
            assert "refuted: @32b memory-bv32 probe.mem2reg.store-load-forward forwardStoreToLoadNoAliasMissing" in memory_report, memory_report

            write_tool(bin_dir / "cbmc", FAKE_CBMC_LICM)
            licm_budget_out = td / "audit-licm-budget"
            licm_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(licm_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(licm_source),
                ],
                capture_output=True,
                text=True,
            )
            assert licm_budget_proc.returncode == 1, licm_budget_proc.stdout + licm_budget_proc.stderr
            licm_budget_summary = json.loads((licm_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            assert licm_budget_summary["modelcheck"]["generated"] == 4, licm_budget_summary["modelcheck"]
            assert licm_budget_summary["modelcheck"]["proved"] == 3, licm_budget_summary["modelcheck"]
            assert licm_budget_summary["modelcheck"]["refuted"] == 1, licm_budget_summary["modelcheck"]
            licm_finding = licm_budget_summary["modelcheck"]["findings"][0]
            assert licm_finding["marker"] == "probe.licm.invariant-op", licm_finding
            assert licm_finding["domain"] == "loop-bv32", licm_finding
            assert licm_finding["source_function"] == "hoistInvariantOnly", licm_finding
            licm_components = {
                component["source_kind"]: component
                for component in licm_budget_summary["modelcheck"]["components"]
            }
            assert licm_components["licm-source"]["records"] == 3, licm_components
            licm_mc_diff = licm_budget_summary["modelcheck_baseline_diff"]
            assert licm_mc_diff["summary"]["new_refuted"] == 1, licm_mc_diff
            assert licm_budget_summary["budget_violations"] == [
                {"actual": 1, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], licm_budget_summary["budget_violations"]
            licm_diff_text = (licm_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "probe.licm.invariant-op" in licm_diff_text, licm_diff_text

            write_tool(bin_dir / "cbmc", FAKE_CBMC_GLOBALOPT)
            globalopt_budget_out = td / "audit-globalopt-budget"
            globalopt_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(globalopt_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(globalopt_source),
                ],
                capture_output=True,
                text=True,
            )
            assert globalopt_budget_proc.returncode == 1, globalopt_budget_proc.stdout + globalopt_budget_proc.stderr
            globalopt_budget_summary = json.loads((globalopt_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            assert globalopt_budget_summary["modelcheck"]["generated"] == 2, globalopt_budget_summary["modelcheck"]
            assert globalopt_budget_summary["modelcheck"]["proved"] == 1, globalopt_budget_summary["modelcheck"]
            assert globalopt_budget_summary["modelcheck"]["refuted"] == 1, globalopt_budget_summary["modelcheck"]
            globalopt_finding = globalopt_budget_summary["modelcheck"]["findings"][0]
            assert globalopt_finding["marker"] == "probe.globalopt.dead-initializer", globalopt_finding
            assert globalopt_finding["domain"] == "global-initializer-observable-v1", globalopt_finding
            assert globalopt_finding["source_function"] == "removeUnsafeGlobalInitializer", globalopt_finding
            globalopt_components = {
                component["source_kind"]: component
                for component in globalopt_budget_summary["modelcheck"]["components"]
            }
            assert globalopt_components["globalopt-source"]["records"] == 1, globalopt_components
            assert globalopt_budget_summary["modelcheck_baseline_diff"]["summary"]["new_refuted"] == 1
            assert globalopt_budget_summary["budget_violations"] == [
                {"actual": 1, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], globalopt_budget_summary["budget_violations"]
            globalopt_diff_text = (globalopt_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "probe.globalopt.dead-initializer" in globalopt_diff_text, globalopt_diff_text

            write_tool(bin_dir / "cbmc", FAKE_CBMC_DCE)
            dce_budget_out = td / "audit-dce-budget"
            dce_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(dce_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(dce_source),
                ],
                capture_output=True,
                text=True,
            )
            assert dce_budget_proc.returncode == 1, dce_budget_proc.stdout + dce_budget_proc.stderr
            dce_budget_summary = json.loads((dce_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            assert dce_budget_summary["modelcheck"]["generated"] == 5, dce_budget_summary["modelcheck"]
            assert dce_budget_summary["modelcheck"]["proved"] == 4, dce_budget_summary["modelcheck"]
            assert dce_budget_summary["modelcheck"]["refuted"] == 1, dce_budget_summary["modelcheck"]
            dce_finding = dce_budget_summary["modelcheck"]["findings"][0]
            assert dce_finding["marker"] == "probe.dce.dead-instruction", dce_finding
            assert dce_finding["domain"] == "dce-dead-instruction-observable-v1", dce_finding
            assert dce_finding["source_function"] == "eraseWithoutGuard", dce_finding
            dce_components = {
                component["source_kind"]: component
                for component in dce_budget_summary["modelcheck"]["components"]
            }
            assert dce_components["dce-source"]["records"] == 4, dce_components
            assert dce_budget_summary["modelcheck_baseline_diff"]["summary"]["new_refuted"] == 1
            assert dce_budget_summary["budget_violations"] == [
                {"actual": 1, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], dce_budget_summary["budget_violations"]
            dce_diff_text = (dce_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "probe.dce.dead-instruction" in dce_diff_text, dce_diff_text

            write_tool(bin_dir / "cbmc", FAKE_CBMC_SLP)
            slp_budget_out = td / "audit-slp-budget"
            slp_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(slp_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(slp_reduction_source),
                    str(slp_pack_source),
                ],
                capture_output=True,
                text=True,
            )
            assert slp_budget_proc.returncode == 1, slp_budget_proc.stdout + slp_budget_proc.stderr
            slp_budget_summary = json.loads((slp_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            assert slp_budget_summary["modelcheck"]["generated"] == 8, slp_budget_summary["modelcheck"]
            assert slp_budget_summary["modelcheck"]["proved"] == 6, slp_budget_summary["modelcheck"]
            assert slp_budget_summary["modelcheck"]["refuted"] == 2, slp_budget_summary["modelcheck"]
            slp_findings = {
                finding["source_function"]: finding
                for finding in slp_budget_summary["modelcheck"]["findings"]
            }
            assert slp_findings["vectorizeFPAddReductionUnguarded"]["marker"] == "probe.slp.vectorize-reduction", slp_findings
            assert slp_findings["vectorizeAddPackSwappedExtract"]["marker"] == "probe.slp.vectorize-binop", slp_findings
            slp_components = {
                component["source_kind"]: component
                for component in slp_budget_summary["modelcheck"]["components"]
            }
            assert slp_components["slp-source"]["records"] == 7, slp_components
            assert slp_budget_summary["modelcheck_baseline_diff"]["summary"]["new_refuted"] == 2
            assert slp_budget_summary["budget_violations"] == [
                {"actual": 2, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], slp_budget_summary["budget_violations"]
            slp_diff_text = (slp_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "probe.slp.vectorize-reduction" in slp_diff_text, slp_diff_text
            assert "probe.slp.vectorize-binop" in slp_diff_text, slp_diff_text

            write_tool(bin_dir / "cbmc", FAKE_CBMC_FAIL)
            budget_out = td / "audit-budget"
            budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-modelcheck-refuted",
                    "0",
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            assert budget_proc.returncode == 1, budget_proc.stdout + budget_proc.stderr
            budget_summary = json.loads((budget_out / "run-summary.json").read_text(encoding="utf-8"))
            budget_readiness = json.loads((budget_out / "real-pass-readiness.json").read_text(encoding="utf-8"))
            assert budget_summary["modelcheck"]["refuted"] == 1, budget_summary["modelcheck"]
            assert budget_summary["modelcheck"]["findings"][0]["marker"] == "probe.synthetic.add-zero"
            assert budget_summary["modelcheck"]["findings"][0]["reason"] == "counterexample"
            assert budget_readiness["modelcheck"]["findings"] == budget_summary["modelcheck"]["findings"]
            budget_report = (budget_out / "run-summary.txt").read_text(encoding="utf-8")
            assert "Modelcheck findings" in budget_report
            assert "probe.synthetic.add-zero" in budget_report
            assert budget_summary["budget_violations"] == [
                {"actual": 1, "budget": "max-modelcheck-refuted", "limit": 0}
            ], budget_summary["budget_violations"]
            assert "budget violation: max-modelcheck-refuted actual=1 limit=0" in budget_proc.stderr

            new_budget_out = td / "audit-new-budget"
            new_budget_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(new_budget_out),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--baseline",
                    str(out / "audit-baseline.json"),
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            assert new_budget_proc.returncode == 1, new_budget_proc.stdout + new_budget_proc.stderr
            new_budget_summary = json.loads((new_budget_out / "run-summary.json").read_text(encoding="utf-8"))
            mc_diff = new_budget_summary["modelcheck_baseline_diff"]
            assert mc_diff["baseline_present"] is True, mc_diff
            assert mc_diff["summary"]["new_refuted"] == 1, mc_diff
            assert new_budget_summary["budget_violations"] == [
                {"actual": 1, "budget": "max-new-modelcheck-refuted", "limit": 0}
            ], new_budget_summary["budget_violations"]
            assert "budget violation: max-new-modelcheck-refuted actual=1 limit=0" in new_budget_proc.stderr
            diff_text = (new_budget_out / "baseline-diff.txt").read_text(encoding="utf-8")
            assert "Top new modelcheck findings" in diff_text
            assert "new_refuted=1" in diff_text

            known_baseline = td / "known-modelcheck-baseline.json"
            known_write_out = td / "audit-known-write"
            known_write_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(known_write_out),
                    "--write-baseline",
                    str(known_baseline),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            assert known_write_proc.returncode == 0, known_write_proc.stdout + known_write_proc.stderr
            known_data = json.loads(known_baseline.read_text(encoding="utf-8"))
            known_records = known_data["modelcheck"]["records"]
            assert len(known_records) == 1 and known_records[0]["status"] == "refuted", known_records
            assert "witness_excerpt" not in known_records[0] and "harness" not in known_records[0], known_records[0]

            known_again_out = td / "audit-known-again"
            known_again_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
                    "--compile-commands",
                    str(compile_db),
                    "--out",
                    str(known_again_out),
                    "--baseline",
                    str(known_baseline),
                    "--ast-miner",
                    str(bin_dir / "ast-miner"),
                    "--intent-inferer",
                    str(bin_dir / "inferer"),
                    "--intent-validator",
                    str(bin_dir / "validator"),
                    "--coverage-auditor",
                    str(bin_dir / "coverage"),
                    "--z3",
                    "true",
                    "--modelcheck-intents",
                    "--modelcheck-engine",
                    "cbmc",
                    "--max-new-modelcheck-refuted",
                    "0",
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            assert known_again_proc.returncode == 0, known_again_proc.stdout + known_again_proc.stderr
            known_again_summary = json.loads((known_again_out / "run-summary.json").read_text(encoding="utf-8"))
            assert known_again_summary["modelcheck_baseline_diff"]["summary"]["new_refuted"] == 0
            assert known_again_summary["budget_violations"] == [], known_again_summary["budget_violations"]
        finally:
            os.environ["PATH"] = old_path

    print("pass_source_audit_modelcheck_fixture OK: pass-source audit emits modelcheck artifacts "
          "and readiness rollups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
