#!/usr/bin/env python3
"""Regression fixture for focused GlobalOpt coverage runner."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--z3", required=True)
    return parser.parse_args()


def run(command: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}, expected {expect}")
    return result


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_coverage_module(repo: Path):
    module_path = repo / "tools" / "cv-run-globalopt-coverage.py"
    spec = importlib.util.spec_from_file_location("cv_run_globalopt_coverage_fixture_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def coverage_command(repo: Path, work_dir: Path, z3: str, source: Path, out_name: str) -> list[str]:
    return [
        sys.executable,
        str(repo / "tools" / "cv-run-globalopt-coverage.py"),
        "--source",
        str(source),
        "--out",
        str(work_dir / out_name),
        "--z3",
        z3,
    ]


def budget_args() -> list[str]:
    return [
        "--min-findings",
        "1",
        "--min-graph-derived",
        "1",
        "--max-unsupported",
        "0",
        "--max-incomplete-safety",
        "0",
        "--max-missing-fact",
        "local-linkage=0",
        "--max-missing-fact",
        "no-uses=0",
    ]


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    coverage_module = load_coverage_module(args.repo)
    positive_source = args.repo / "tests" / "fixtures" / "global_dead_initializer_snippet.cpp"
    unsafe_source = args.repo / "tests" / "fixtures" / "global_dead_initializer_unsafe_snippet.cpp"
    nonnull_source = args.repo / "tests" / "fixtures" / "global_dead_initializer_nonnull_replacement_snippet.cpp"

    run(coverage_command(args.repo, args.work_dir, args.z3, positive_source, "positive"))
    positive = load_json(args.work_dir / "positive" / "globalopt-coverage.json")
    assert positive["source_status"] == "explicit"
    assert positive["findings"]["total"] == 1
    assert positive["candidates"]["graph_derived"] == 1
    assert positive["candidates"]["safety_status"] == {"complete": 1}
    assert positive["candidates"]["observed_facts"] == {
        "initializer-dead": 1,
        "local-linkage": 1,
        "no-uses": 1,
    }
    assert positive["candidates"]["missing_facts"] == {}
    assert positive["candidates"]["safety_provenance_status"] == {"complete": 1}
    assert positive["candidates"]["rewrite_provenance_status"] == {"complete": 1}
    assert positive["candidates"]["rewrite_callee"] == {"setInitializer": 1}
    assert positive["candidates"]["replacement_expr"] == {
        "Constant::getNullValue(GV->getValueType())": 1,
    }
    assert positive["candidates"]["value_type_expr"] == {"GV->getValueType()": 1}
    assert positive["validation"]["proof_status"] == {"proved": 1}
    assert positive["baseline"]["model"] == "o2t-globalopt-coverage-baseline-v1"
    assert positive["baseline"]["proof_status"] == {"proved": 1}
    assert positive["baseline_diff"] == {
        "changed": 0,
        "current_records": 1,
        "new": 0,
        "new_incomplete_safety": 0,
        "new_missing_facts": {},
        "new_unsupported": 0,
        "previous_records": 0,
        "resolved": 0,
    }
    assert (args.work_dir / "positive" / "globalopt-baseline.json").is_file()
    assert (args.work_dir / "positive" / "globalopt-baseline-diff.json").is_file()
    assert (args.work_dir / "positive" / "globalopt-baseline-diff.txt").is_file()
    assert not (args.work_dir / "positive" / "work").exists()
    positive_text = (args.work_dir / "positive" / "globalopt-coverage.txt").read_text(encoding="utf-8")
    assert "graph_derived: 1" in positive_text
    assert "safety_provenance: complete=1" in positive_text
    assert "rewrite_provenance: complete=1" in positive_text
    assert "rewrite_callee: setInitializer=1" in positive_text
    assert "replacement_expr: Constant::getNullValue(GV->getValueType())=1" in positive_text
    assert "value_type_expr: GV->getValueType()=1" in positive_text
    assert "proof_status: proved=1" in positive_text
    assert "Budget violations\n  none" in positive_text

    run(
        coverage_command(args.repo, args.work_dir, args.z3, positive_source, "positive-witness")
        + [
            "--emit-witnesses",
            "--host-llvm-as",
            str(args.repo / "tests" / "fixtures" / "fake-llvm-as.sh"),
            "--min-witnesses",
            "1",
            "--max-witness-failures",
            "0",
        ]
    )
    positive_witness = load_json(args.work_dir / "positive-witness" / "globalopt-coverage.json")
    assert positive_witness["witnesses"]["enabled"] is True
    assert positive_witness["witnesses"]["required_cases"] == ["i32", "ptr", "array"]
    assert positive_witness["witnesses"]["total"] == 1
    assert positive_witness["witnesses"]["passed"] == 1
    assert positive_witness["witnesses"]["failed"] == 0
    witness_record = positive_witness["witnesses"]["records"][0]
    assert witness_record["witness_model"] == "global-initializer-default-null-family-v1"
    assert witness_record["witness_contract"]["model"] == "globalopt-dead-initializer-witness-contract-v1"
    assert witness_record["witness_contract"]["status"] == "passed"
    assert witness_record["witness_contract"]["structural_status"] == "passed"
    assert witness_record["required_cases"] == ["i32", "ptr", "array"]
    assert witness_record["missing_required_cases"] == []
    assert [case["name"] for case in witness_record["cases"]] == ["i32", "ptr", "array"]
    assert {case["status"] for case in witness_record["cases"]} == {"passed"}
    for case in witness_record["cases"]:
        details = case["structural_details"]
        expected_changed_lines = [4] if case["name"] == "ptr" else [3]
        assert details["global_name"] == "@cv_dead_init"
        assert details["linkage"] == "internal"
        assert details["changed_line_count"] == 1
        assert details["changed_lines"] == expected_changed_lines
        assert case["structural_checks"] == "passed"
    before = Path(witness_record["before"]).read_text(encoding="utf-8")
    after = Path(witness_record["after"]).read_text(encoding="utf-8")
    assert "@cv_dead_init = internal global i32 42" in before
    assert "@cv_dead_init = internal global i32 0" in after
    assert "define i32 @cv_observe(i32 %x)" in before
    assert "define i32 @cv_observe(i32 %x)" in after
    case_paths = {
        case["name"]: (Path(case["before"]), Path(case["after"]))
        for case in witness_record["cases"]
    }
    assert case_paths["i32"] == (Path(witness_record["before"]), Path(witness_record["after"]))
    assert case_paths["ptr"][0] != Path(witness_record["before"])
    assert case_paths["array"][0] != Path(witness_record["before"])
    ptr_before = case_paths["ptr"][0].read_text(encoding="utf-8")
    ptr_after = case_paths["ptr"][1].read_text(encoding="utf-8")
    array_before = case_paths["array"][0].read_text(encoding="utf-8")
    array_after = case_paths["array"][1].read_text(encoding="utf-8")
    assert "@cv_dead_init = internal global ptr @cv_target" in ptr_before
    assert "@cv_dead_init = internal global ptr null" in ptr_after
    assert "@cv_dead_init = internal global [2 x i32] [i32 1, i32 2]" in array_before
    assert "@cv_dead_init = internal global [2 x i32] zeroinitializer" in array_after
    manifest = load_json(Path(witness_record["before"]).parent / "witness.json")
    assert manifest["status"] == "passed"
    assert manifest["witness_contract"]["status"] == "passed"
    assert manifest["required_cases"] == ["i32", "ptr", "array"]
    assert manifest["missing_required_cases"] == []
    assert len(manifest["cases"]) == 3
    assert manifest["cases"][0]["structural_details"]["initializer_type"] == "i32"
    assert manifest["cases"][1]["structural_details"]["initializer_type"] == "ptr"
    assert manifest["cases"][2]["structural_details"]["initializer_type"] == "[2 x i32]"
    assert manifest["source_provenance"]["replacement_expr"] == "Constant::getNullValue(GV->getValueType())"
    assert manifest["source_provenance"]["value_type_expr"] == "GV->getValueType()"

    base_case = {
        "name": "i32",
        "before_global": "@cv_dead_init = internal global i32 42",
        "after_global": "@cv_dead_init = internal global i32 0",
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    }
    base_before = coverage_module.witness_ir(base_case, base_case["before_global"])
    base_after = coverage_module.witness_ir(base_case, base_case["after_global"])
    base_errors, base_details = coverage_module.global_initializer_contract_details(base_before, base_after, base_case)
    assert base_errors == []
    assert base_details["before_initializer"] == "42"
    assert base_details["after_initializer"] == "0"
    negative_pairs = [
        ("changed-linkage", base_after.replace("internal global i32 0", "private global i32 0"), "global-linkage-changed"),
        ("changed-name", base_after.replace("@cv_dead_init", "@cv_dead_other", 1), "global-line-count-mismatch"),
        ("changed-type", base_after.replace("internal global i32 0", "internal global i64 0"), "global-type-changed"),
        ("extra-edit", base_after + "\n; extra edit", "witness-line-count-changed"),
        ("nonnull-after", base_after.replace("internal global i32 0", "internal global i32 7"), "after-initializer-not-default"),
    ]
    for label, bad_after, expected in negative_pairs:
        errors, _ = coverage_module.global_initializer_contract_details(base_before, bad_after, {**base_case, "name": label})
        assert any(expected in error for error in errors), (label, errors)

    run(coverage_command(args.repo, args.work_dir, args.z3, nonnull_source, "nonnull"))
    nonnull = load_json(args.work_dir / "nonnull" / "globalopt-coverage.json")
    assert nonnull["findings"]["total"] == 1
    assert nonnull["candidates"]["graph_derived"] == 0
    assert nonnull["candidates"]["safety_status"] == {"complete": 1}
    assert nonnull["candidates"]["rewrite_provenance_status"] == {"unsupported": 1}
    assert nonnull["candidates"]["replacement_expr"] == {"SomeOtherValue": 1}
    assert nonnull["validation"]["proof_status"] == {"unsupported": 1}
    nonnull_text = (args.work_dir / "nonnull" / "globalopt-coverage.txt").read_text(encoding="utf-8")
    assert "rewrite_provenance: unsupported=1" in nonnull_text
    assert "replacement_expr: SomeOtherValue=1" in nonnull_text

    run(coverage_command(args.repo, args.work_dir, args.z3, positive_source, "positive-budget") + budget_args())
    positive_budget = load_json(args.work_dir / "positive-budget" / "globalopt-coverage.json")
    assert positive_budget["budget_violations"] == []

    run(
        coverage_command(args.repo, args.work_dir, args.z3, positive_source, "positive-baseline")
        + ["--baseline", str(args.work_dir / "positive" / "globalopt-baseline.json")]
    )
    positive_baseline = load_json(args.work_dir / "positive-baseline" / "globalopt-coverage.json")
    assert positive_baseline["baseline_diff"]["new"] == 0
    assert positive_baseline["baseline_diff"]["changed"] == 0
    assert positive_baseline["baseline_diff"]["resolved"] == 0
    positive_diff = load_json(args.work_dir / "positive-baseline" / "globalopt-baseline-diff.json")
    assert positive_diff["baseline_present"] is True
    legacy_baseline_path = args.work_dir / "legacy-globalopt-baseline.json"
    legacy_baseline = load_json(args.work_dir / "positive" / "globalopt-baseline.json")
    legacy_baseline["model"] = "compilerverif-globalopt-coverage-baseline-v1"
    legacy_baseline_path.write_text(json.dumps(legacy_baseline), encoding="utf-8")
    run(
        coverage_command(args.repo, args.work_dir, args.z3, positive_source, "positive-legacy-baseline")
        + ["--baseline", str(legacy_baseline_path)]
    )
    positive_legacy_diff = load_json(args.work_dir / "positive-legacy-baseline" / "globalopt-baseline-diff.json")
    assert positive_legacy_diff["baseline_present"] is True
    assert positive_legacy_diff["summary"]["new"] == 0
    assert positive_legacy_diff["summary"]["changed"] == 0
    assert positive_legacy_diff["summary"]["resolved"] == 0

    run(coverage_command(args.repo, args.work_dir, args.z3, unsafe_source, "unsafe"))
    unsafe = load_json(args.work_dir / "unsafe" / "globalopt-coverage.json")
    assert unsafe["source_status"] == "explicit"
    assert unsafe["findings"]["total"] == 1
    assert unsafe["candidates"]["graph_derived"] == 0
    assert unsafe["candidates"]["safety_status"] == {"incomplete": 1}
    assert unsafe["candidates"]["observed_facts"] == {"initializer-dead": 1}
    assert unsafe["candidates"]["safety_provenance_status"] == {"incomplete": 1}
    assert unsafe["candidates"]["missing_facts"] == {
        "local-linkage": 1,
        "no-uses": 1,
    }
    assert unsafe["validation"]["proof_status"] == {"unsupported": 1}
    assert unsafe["audit"]["recommendation"] == {"add missing global initializer safety facts": 1}
    assert not (args.work_dir / "unsafe" / "work").exists()
    unsafe_text = (args.work_dir / "unsafe" / "globalopt-coverage.txt").read_text(encoding="utf-8")
    assert "missing_facts: local-linkage=1, no-uses=1" in unsafe_text
    assert "proof_status: unsupported=1" in unsafe_text
    assert "Budget violations\n  none" in unsafe_text

    run(
        coverage_command(args.repo, args.work_dir, args.z3, unsafe_source, "unsafe-witness")
        + ["--emit-witnesses", "--max-witness-failures", "0"]
    )
    unsafe_witness = load_json(args.work_dir / "unsafe-witness" / "globalopt-coverage.json")
    assert unsafe_witness["witnesses"]["enabled"] is True
    assert unsafe_witness["witnesses"]["total"] == 0
    assert unsafe_witness["witnesses"]["skipped"] == 1
    assert unsafe_witness["budget_violations"] == []

    witness_failure_result = run(
        coverage_command(args.repo, args.work_dir, args.z3, positive_source, "witness-failure")
        + [
            "--emit-witnesses",
            "--host-llvm-as",
            "/bin/false",
            "--max-witness-failures",
            "0",
        ],
        expect=1,
    )
    witness_failure = load_json(args.work_dir / "witness-failure" / "globalopt-coverage.json")
    assert witness_failure["witnesses"]["failed"] == 1
    assert witness_failure["witnesses"]["failure_reasons"] == {
        "array-after-llvm-as-failed": 1,
        "array-before-llvm-as-failed": 1,
        "i32-after-llvm-as-failed": 1,
        "i32-before-llvm-as-failed": 1,
        "ptr-after-llvm-as-failed": 1,
        "ptr-before-llvm-as-failed": 1,
    }
    failed_record = witness_failure["witnesses"]["records"][0]
    assert failed_record["witness_model"] == "global-initializer-default-null-family-v1"
    assert failed_record["missing_required_cases"] == ["i32", "ptr", "array"]
    assert [case["status"] for case in failed_record["cases"]] == ["failed", "failed", "failed"]
    assert witness_failure["budget_violations"] == [
        {"actual": 1, "budget": "max-witness-failures", "limit": 0},
    ]
    assert "budget violation: max-witness-failures actual=1 limit=0" in witness_failure_result.stderr

    unsafe_budget_result = run(
        coverage_command(args.repo, args.work_dir, args.z3, unsafe_source, "unsafe-budget")
        + [
            "--max-unsupported",
            "0",
            "--max-incomplete-safety",
            "0",
            "--max-missing-fact",
            "local-linkage=0",
            "--max-missing-fact",
            "no-uses=0",
        ],
        expect=1,
    )
    unsafe_budget = load_json(args.work_dir / "unsafe-budget" / "globalopt-coverage.json")
    assert unsafe_budget["budget_violations"] == [
        {"actual": 1, "budget": "max-unsupported", "limit": 0},
        {"actual": 1, "budget": "max-incomplete-safety", "limit": 0},
        {"actual": 1, "budget": "max-missing-fact", "fact": "local-linkage", "limit": 0},
        {"actual": 1, "budget": "max-missing-fact", "fact": "no-uses", "limit": 0},
    ]
    assert "budget violation: max-unsupported actual=1 limit=0" in unsafe_budget_result.stderr
    assert "budget violation: max-missing-fact fact=local-linkage actual=1 limit=0" in unsafe_budget_result.stderr

    unsafe_baseline_result = run(
        coverage_command(args.repo, args.work_dir, args.z3, unsafe_source, "unsafe-baseline")
        + [
            "--baseline",
            str(args.work_dir / "positive" / "globalopt-baseline.json"),
            "--max-new-unsupported",
            "0",
            "--max-new-incomplete-safety",
            "0",
        ],
        expect=1,
    )
    unsafe_baseline = load_json(args.work_dir / "unsafe-baseline" / "globalopt-coverage.json")
    assert unsafe_baseline["baseline_diff"]["new"] == 1
    assert unsafe_baseline["baseline_diff"]["resolved"] == 1
    assert unsafe_baseline["baseline_diff"]["new_unsupported"] == 1
    assert unsafe_baseline["baseline_diff"]["new_incomplete_safety"] == 1
    assert unsafe_baseline["baseline_diff"]["new_missing_facts"] == {
        "local-linkage": 1,
        "no-uses": 1,
    }
    assert unsafe_baseline["budget_violations"] == [
        {"actual": 1, "budget": "max-new-unsupported", "limit": 0},
        {"actual": 1, "budget": "max-new-incomplete-safety", "limit": 0},
    ]
    assert "budget violation: max-new-unsupported actual=1 limit=0" in unsafe_baseline_result.stderr
    unsafe_diff_text = (args.work_dir / "unsafe-baseline" / "globalopt-baseline-diff.txt").read_text(
        encoding="utf-8"
    )
    assert "new_incomplete_safety: 1" in unsafe_diff_text

    missing_source = args.work_dir / "missing" / "GlobalOpt.cpp"
    run(coverage_command(args.repo, args.work_dir, args.z3, missing_source, "missing") + budget_args())
    missing = load_json(args.work_dir / "missing" / "globalopt-coverage.json")
    assert missing["source_status"] == "source-not-found"
    assert missing["findings"]["total"] == 0
    assert missing["validation"]["total"] == 0
    assert missing["budget_violations"] == []
    assert missing["baseline_diff"]["new"] == 0
    assert not (args.work_dir / "missing" / "work").exists()
    missing_text = (args.work_dir / "missing" / "globalopt-coverage.txt").read_text(encoding="utf-8")
    assert "source_status: source-not-found" in missing_text
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
