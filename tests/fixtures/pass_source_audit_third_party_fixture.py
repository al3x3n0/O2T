#!/usr/bin/env python3
"""Third-party-style pass source audit fixture."""

from __future__ import annotations

import argparse
import json
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
    records = [
        {
            "directory": str(source.parent),
            "command": f"{compiler} -std=c++17 {source}",
            "file": str(source),
        }
        for source in sources
    ]
    path.write_text(json.dumps(records), encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    copied_sources = write_external_sources(repo, work_dir)
    compile_db = work_dir / "compile-db" / "compile_commands.json"
    write_compile_db(compile_db, copied_sources, args.compiler)

    out = work_dir / "audit"
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-pass-source-audit.py"),
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
            "--require-clean-mining",
        ]
        + [str(source) for source in copied_sources]
    )

    manifest = [json.loads(line) for line in (out / "source-manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    findings = load_json(out / "findings.json")
    summary = load_json(out / "run-summary.json")

    assert summary["sources"]["selected"] == 3
    assert all(record["status"] == "selected" for record in manifest)
    assert all("/external-src/ExampleVendor/" in record["file"] for record in manifest)
    assert summary["budget_violations"] == []
    assert summary["pass_impl_ir"]["intent_check_status"].get("matched", 0) >= 4
    assert summary["pass_impl_ir"]["intent_check_status"].get("blocked", 0) >= 2
    assert summary["pass_impl_ir"]["intent_check_family_status"]["dse"] == {
        "blocked": 3,
        "impl-ir-incomplete": 1,
        "matched": 1,
    }

    by_marker: dict[str, list[dict]] = {}
    for finding in findings:
        by_marker.setdefault(finding["marker"], []).append(finding)

    assert set(by_marker) >= {
        "probe.instcombine.add-zero",
        "probe.instcombine.mul-one",
        "probe.instcombine.and-self",
        "probe.globalopt.dead-initializer",
        "probe.dse.dead-store",
        "probe.dse.overwritten-store",
    }

    add = by_marker["probe.instcombine.add-zero"][0]["pass_impl_ir_intent_check"]
    assert add["status"] in {"matched", "partial"}
    assert add["predicate_evidence"] and add["rewrite_evidence"]
    assert add["rewrite_binding_evidence"]["replacement_symbol"] == "Kept"

    derived_add = [
        finding for finding in by_marker["probe.instcombine.add-zero"]
        if "CreateAdd" in finding.get("rewrite_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert derived_add["status"] in {"matched", "partial"}
    assert derived_add["rewrite_binding_evidence"]["normalized_replacement"] == "add(Kept,0)"
    assert derived_add["rewrite_binding_evidence"]["normalized_source_result"] == "add(Kept,0)"

    temp_add = [
        finding for finding in by_marker["probe.instcombine.add-zero"]
        if "Value *New" in finding.get("rewrite_source", "")
        and "buildNeutralAdd" not in finding.get("rewrite_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert temp_add["status"] in {"matched", "partial"}
    assert temp_add["rewrite_binding_evidence"]["replacement_symbol"] == "New"
    assert temp_add["rewrite_binding_evidence"]["replacement_definition_source"].startswith("Builder.CreateAdd")
    assert temp_add["rewrite_binding_evidence"]["normalized_replacement"] == "add(Kept,0)"
    assert "derived-builder-to-replacement" in temp_add["rewrite_flow_evidence"]

    helper_add = [
        finding for finding in by_marker["probe.instcombine.add-zero"]
        if "buildNeutralAdd" in finding.get("rewrite_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert helper_add["status"] in {"matched", "partial"}
    assert helper_add["rewrite_binding_evidence"]["replacement_symbol"] == "New"
    assert helper_add["rewrite_binding_evidence"]["replacement_helper"] == "buildNeutralAdd"
    assert helper_add["rewrite_binding_evidence"]["normalized_replacement"] == "add(Kept,0)"
    assert "derived-builder-to-replacement" in helper_add["rewrite_flow_evidence"]

    mul = by_marker["probe.instcombine.mul-one"][0]["pass_impl_ir_intent_check"]
    assert mul["status"] in {"matched", "partial"}
    assert mul["predicate_evidence"] and mul["rewrite_evidence"]
    assert mul["rewrite_binding_evidence"]["replacement_symbol"] == "Kept"

    and_self = by_marker["probe.instcombine.and-self"][0]["pass_impl_ir_intent_check"]
    assert and_self["status"] == "matched"
    assert and_self["rewrite_binding_evidence"]["replacement_role"] == "constant-replacement"
    assert and_self["rewrite_binding_evidence"]["normalized_replacement"] == "0"

    global_check = by_marker["probe.globalopt.dead-initializer"][0]["pass_impl_ir_intent_check"]
    assert global_check["status"] == "matched"
    assert global_check["global_rewrite_api"] == "setInitializer"
    assert set(global_check["global_safety_ir_evidence"]) == {
        "initializer-dead",
        "local-linkage",
        "no-uses",
    }
    assert set(global_check["rewrite_flow_evidence"]) == {
        "value-type-to-null-factory",
        "null-factory-to-set-initializer",
    }
    assert all(global_check["rewrite_flow_evidence"].values())

    dse_dead = [
        finding for finding in by_marker["probe.dse.dead-store"]
        if "AA.isNoAlias" in finding.get("predicate_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert dse_dead["status"] == "matched"
    assert dse_dead["intent_shape"] == "dse-analysis-facts"
    assert dse_dead["analysis_fact_impl_ir_evidence"]["memoryssa.dead-store"]
    assert dse_dead["analysis_fact_impl_ir_evidence"]["alias.noalias"]

    dse_overwrite = [
        finding for finding in by_marker["probe.dse.overwritten-store"]
        if "!mayAlias" in finding.get("predicate_source", "")
    ][0]["pass_impl_ir_intent_check"]
    assert dse_overwrite["status"] == "impl-ir-incomplete"
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["memoryssa.clobber"]
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["memory.no-intervening-store"]
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["memory.no-intervening-read"]
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["memory.overwrite.size.known"]
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["memory.overwrite.size.bounded-four-lane"]
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["memory.overwrite.full"]
    assert dse_overwrite["analysis_fact_impl_ir_evidence"]["alias.noalias"]
    assert dse_overwrite["missing_impl_ir_evidence"] == ["memory.no-intervening-memory-effect"]

    dse_blocked = [
        finding["pass_impl_ir_intent_check"]
        for finding in findings
        if finding["marker"].startswith("probe.dse.")
        and finding["pass_impl_ir_intent_check"]["status"] == "blocked"
    ]
    assert len(dse_blocked) == 3
    assert {
        tuple(check["source_analysis_fact_blockers"])
        for check in dse_blocked
    } == {
        (
            "memory.volatile-atomic-blocker",
            "memory.volatile-blocker",
            "memory.atomic-ordering-unknown-blocker",
        ),
        ("alias.unknown",),
        ("memory.overwrite.partial",),
    }

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
