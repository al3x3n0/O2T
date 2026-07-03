#!/usr/bin/env python3
"""Regression fixture for the external pass audit wrapper."""

from __future__ import annotations

import argparse
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ast-miner", type=Path, required=True)
    parser.add_argument("--ir-miner", type=Path, required=True)
    parser.add_argument("--z3", required=True)
    parser.add_argument("--compiler", default="clang++")
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")
    return result


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_external_sources(repo: Path, work_dir: Path) -> list[Path]:
    source_dir = work_dir / "external-src" / "ExampleVendor" / "lib" / "Transforms"
    source_dir.mkdir(parents=True, exist_ok=True)
    mappings = [
        ("third_party_instcombine_like_pass.cpp", "NeutralArithmeticPass.cpp"),
        ("third_party_globalopt_like_pass.cpp", "DormantGlobalPass.cpp"),
        ("third_party_dse_like_pass.cpp", "VendorDSEPass.cpp"),
    ]
    copied: list[Path] = []
    for fixture_name, external_name in mappings:
        source = repo / "tests" / "fixtures" / fixture_name
        target = source_dir / external_name
        shutil.copyfile(source, target)
        copied.append(target)
    return copied


def write_compile_db(path: Path, sources: list[Path], compiler: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([
            {
                "directory": str(source.parent),
                "command": f"{compiler} -std=c++17 {source}",
                "file": str(source),
            }
            for source in sources
        ]),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    sources = write_external_sources(repo, work_dir)
    compile_db = work_dir / "compile-db" / "compile_commands.json"
    write_compile_db(compile_db, sources, args.compiler)

    out = work_dir / "external-audit"
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-external-pass-audit.py"),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--z3",
            args.z3,
            "--mine-pass-impl-ir",
            str(work_dir / "external-src" / "ExampleVendor" / "lib" / "Transforms"),
        ]
    )

    wrapper_summary = load_json(out / "external-pass-audit-summary.json")
    wrapper_text = (out / "external-pass-audit-summary.txt").read_text(encoding="utf-8")
    audit_out = out / "audit"
    run_summary = load_json(audit_out / "run-summary.json")
    findings = load_json(audit_out / "findings.json")
    manifest = [json.loads(line) for line in (audit_out / "source-manifest.jsonl").read_text(encoding="utf-8").splitlines()]

    assert wrapper_summary["model"] == "o2t-external-pass-audit-summary-v1"
    assert wrapper_summary["audit_exit_code"] == 0
    assert wrapper_summary["audit_out"] == str(audit_out)
    assert "O2T External Pass Audit Summary" in wrapper_text
    assert "Pass implementation IR intent checks" in wrapper_text
    assert "source_program_graph_contract" in wrapper_summary["coverage"]
    assert wrapper_summary["sources"]["selected"] == 3
    assert wrapper_summary["findings"]["total"] == run_summary["findings"]["total"]
    assert wrapper_summary["pass_impl_ir"]["intent_check_status"].get("matched", 0) >= 3
    assert wrapper_summary["pass_impl_ir"]["intent_check_status"].get("blocked", 0) >= 2
    assert wrapper_summary["pass_impl_ir"]["intent_check_family_status"]["dse"] == {
        "blocked": 3,
        "impl-ir-incomplete": 1,
        "matched": 1,
    }
    assert wrapper_summary["budget_violations"] == []
    assert all(record["status"] == "selected" for record in manifest)
    assert all("/external-src/ExampleVendor/" in record["file"] for record in manifest)

    markers = {finding["marker"] for finding in findings}
    assert {
        "probe.instcombine.add-zero",
        "probe.instcombine.mul-one",
        "probe.globalopt.dead-initializer",
        "probe.dse.dead-store",
        "probe.dse.overwritten-store",
    }.issubset(markers)
    derived = [
        finding for finding in findings
        if finding["marker"] == "probe.instcombine.add-zero"
        and "CreateAdd" in finding.get("rewrite_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert derived["status"] in {"matched", "partial"}
    assert derived["predicate_evidence"] and derived["rewrite_evidence"]
    temp_derived = [
        finding for finding in findings
        if finding["marker"] == "probe.instcombine.add-zero"
        and "Value *New" in finding.get("rewrite_source", "")
        and "buildNeutralAdd" not in finding.get("rewrite_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert temp_derived["status"] in {"matched", "partial"}
    assert temp_derived["rewrite_binding_evidence"]["replacement_symbol"] == "New"
    assert temp_derived["rewrite_binding_evidence"]["normalized_replacement"] == "add(Kept,0)"
    assert "derived-builder-to-replacement" in temp_derived["rewrite_flow_evidence"]
    helper_derived = [
        finding for finding in findings
        if finding["marker"] == "probe.instcombine.add-zero"
        and "buildNeutralAdd" in finding.get("rewrite_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert helper_derived["status"] in {"matched", "partial"}
    assert helper_derived["rewrite_binding_evidence"]["replacement_helper"] == "buildNeutralAdd"
    assert helper_derived["rewrite_binding_evidence"]["normalized_replacement"] == "add(Kept,0)"

    dse_checks = [
        finding["pass_impl_ir_intent_check"]
        for finding in findings
        if finding["marker"].startswith("probe.dse.")
    ]
    assert sum(check["status"] == "matched" for check in dse_checks) == 1
    assert sum(check["status"] == "impl-ir-incomplete" for check in dse_checks) == 1
    assert sum(check["status"] == "blocked" for check in dse_checks) == 3
    assert all(check["intent_shape"] == "dse-analysis-facts" for check in dse_checks)
    assert (audit_out / "real-pass-readiness.json").is_file()
    assert (audit_out / "commands.log").is_file()

    external_helpers = runpy.run_path(str(repo / "tools" / "cv-run-external-pass-audit.py"))
    formatted_modelcheck = external_helpers["format_summary"]({
        "audit_exit_code": 0,
        "audit_out": str(out / "audit-modelcheck"),
        "sources": {"selected": 1, "skipped": 0, "errors": 0, "reasons": {}},
        "findings": {"total": 1, "by_pass": {}, "by_marker": {}},
        "intents": {"total": 1, "proof_status": {"proved": 1}},
        "pass_impl_ir": {"intent_check_status": {}},
        "modelcheck": {
            "enabled": True,
            "generated": 2,
            "proved": 2,
            "refuted": 0,
            "unsupported": 0,
            "skipped": 0,
            "error": 0,
            "selected_widths": [8, 16],
            "widths": {
                "8": {"proved": 1, "refuted": 0, "unsupported": 0, "skipped": 0, "error": 0},
                "16": {"proved": 1, "refuted": 0, "unsupported": 0, "skipped": 0, "error": 0},
            },
            "components": [
                {
                    "source_kind": "intent",
                    "records": 1,
                    "generated": 2,
                    "proved": 2,
                    "refuted": 0,
                    "unsupported": 0,
                    "skipped": 0,
                    "error": 0,
                    "selected_widths": [8, 16],
                }
            ],
            "findings": [
                {
                    "status": "refuted",
                    "width": 16,
                    "domain": "memory-bv16",
                    "marker": "probe.dse.overwritten-store",
                    "source_function": "eliminateStoreNoOverwriteGuard",
                    "file": "VendorDSEPass.cpp",
                    "line": 42,
                    "reason": "counterexample",
                }
            ],
        },
        "coverage": {},
        "source_reasons": {},
    })
    assert "Modelcheck components" in formatted_modelcheck, formatted_modelcheck
    assert "intent: records=1 generated=2 proved=2 refuted=0 unsupported=0 skipped=0 error=0 selected=8,16" in formatted_modelcheck, formatted_modelcheck
    assert "Modelcheck widths" in formatted_modelcheck, formatted_modelcheck
    assert "selected=8,16" in formatted_modelcheck, formatted_modelcheck
    assert "8: proved=1 refuted=0 unsupported=0 skipped=0 error=0" in formatted_modelcheck, formatted_modelcheck
    assert "16: proved=1 refuted=0 unsupported=0 skipped=0 error=0" in formatted_modelcheck, formatted_modelcheck
    assert (
        "refuted: @16b memory-bv16 probe.dse.overwritten-store "
        "eliminateStoreNoOverwriteGuard VendorDSEPass.cpp:42 (counterexample)"
    ) in formatted_modelcheck, formatted_modelcheck
    assert external_helpers["selected_widths_label"]({"selected_widths": [], "width_mode": "8,bad"}) == "none"
    assert external_helpers["modelcheck_finding_lines"](
        {
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
        },
        5,
    )[-1] == "  ... 2 more"

    unified_report = work_dir / "unified-orchestrate.json"
    unified_text = work_dir / "unified-orchestrate.txt"
    unified_deep_out = work_dir / "unified-deep-audit"
    source_tree = work_dir / "external-src" / "ExampleVendor" / "lib" / "Transforms"
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-orchestrate.py"),
            "--source",
            str(source_tree),
            "--no-execute",
            "--compile-commands",
            str(compile_db),
            "--audit-out",
            str(unified_deep_out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--z3-bin",
            args.z3,
            "--mine-pass-impl-ir",
            "--report",
            str(unified_report),
            "--summary-text",
            str(unified_text),
        ]
    )
    unified = load_json(unified_report)
    unified_summary = unified["summary"]
    assert unified["deep_audit"]["enabled"] is True
    assert unified["deep_audit"]["exit_code"] == 0
    assert unified["deep_audit"]["out"] == str(unified_deep_out)
    assert unified_summary["passes"] == 3
    assert unified_summary["by_headline"] == {"planned": 3}
    assert unified_summary["deep_audit"]["enabled"] is True
    assert unified_summary["deep_audit"]["sources_selected"] == 3
    assert unified_summary["deep_audit"]["findings"] == wrapper_summary["findings"]["total"]
    assert unified_summary["deep_audit"]["has_readiness"] is True
    matrix = unified_summary["readiness_matrix"]
    assert matrix["families"]["memory-dse"]["planned_checks"] == 6, matrix
    assert matrix["families"]["global"]["planned_checks"] == 5, matrix
    assert matrix["families"]["peephole"]["planned_checks"] == 7, matrix
    assert matrix["deep_audit"]["enabled"] is True, matrix
    assert matrix["deep_audit"]["sources_selected"] == 3, matrix
    assert matrix["deep_audit"]["findings"] == wrapper_summary["findings"]["total"], matrix
    assert matrix["deep_audit"]["modelcheck_selected_widths"] == [], matrix
    assert matrix["deep_audit"]["modelcheck_widths"] == {}, matrix
    assert all(
        "selected_widths" in component
        for component in matrix["deep_audit"]["modelcheck_components"]
    ), matrix
    assert all(
        "summary" in component
        for component in matrix["deep_audit"]["modelcheck_components"]
    ), matrix
    assert matrix["deep_audit"]["pass_impl_ir_status"].get("matched", 0) >= 3, matrix
    assert "transaction_graph_status" in matrix["deep_audit"], matrix
    assert isinstance(unified_summary["next_actions"], list), unified_summary
    assert (unified_deep_out / "external-pass-audit-summary.json").is_file()
    assert (unified_deep_out / "external-pass-audit-summary.txt").is_file()
    assert (unified_deep_out / "audit" / "real-pass-readiness.json").is_file()
    unified_text_data = unified_text.read_text(encoding="utf-8")
    assert "O2T Orchestrator Summary" in unified_text_data
    assert "Readiness Matrix" in unified_text_data
    assert "Next Actions" in unified_text_data
    assert "Deep Audit" in unified_text_data
    assert "readiness: present" in unified_text_data
    assert "modelcheck_widths=native" in unified_text_data
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
