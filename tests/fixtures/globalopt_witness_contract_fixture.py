#!/usr/bin/env python3
"""Regression fixture for GlobalOpt witness contract verification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MARKER = "probe.globalopt.dead-initializer"
CONTRACT_MODEL = "globalopt-dead-initializer-witness-contract-v1"
WITNESS_MODEL = "global-initializer-default-null-family-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--z3")
    return parser.parse_args()


def run(command: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}, expected {expect}")
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")


def structural_case(name: str) -> dict[str, Any]:
    details = {
        "i32": {
            "initializer_type": "i32",
            "before_initializer": "42",
            "after_initializer": "0",
            "changed_lines": [3],
        },
        "ptr": {
            "initializer_type": "ptr",
            "before_initializer": "@cv_target",
            "after_initializer": "null",
            "changed_lines": [4],
        },
        "array": {
            "initializer_type": "[2 x i32]",
            "before_initializer": "[i32 1, i32 2]",
            "after_initializer": "zeroinitializer",
            "changed_lines": [3],
        },
    }[name]
    return {
        "global_name": "@cv_dead_init",
        "linkage": "internal",
        "changed_line_count": 1,
        **details,
    }


def witness_case(name: str, status: str = "passed", structural_checks: str = "passed") -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "before": f"/tmp/{name}/before.ll",
        "after": f"/tmp/{name}/after.ll",
        "structural_checks": structural_checks,
        "structural_details": structural_case(name),
        "failure_reasons": [] if status == "passed" and structural_checks == "passed" else [f"{name}-failed"],
    }


def materialized_contract(root: Path) -> dict[str, Any]:
    cases = []
    for name in ["i32", "ptr", "array"]:
        case_dir = root / name
        case_dir.mkdir(parents=True, exist_ok=True)
        before = case_dir / "before.ll"
        after = case_dir / "after.ll"
        before.write_text(f"define i32 @cv_{name}() {{\n  ret i32 0\n}}\n", encoding="utf-8")
        after.write_text(f"define i32 @cv_{name}() {{\n  ret i32 0\n}}\n", encoding="utf-8")
        case = witness_case(name)
        case["before"] = str(before)
        case["after"] = str(after)
        cases.append(case)
    return contract(cases=cases)


def contract(
    status: str = "passed",
    witness_model: str = WITNESS_MODEL,
    cases: list[dict[str, Any]] | None = None,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    contract_cases = cases if cases is not None else [
        witness_case("i32"),
        witness_case("ptr"),
        witness_case("array"),
    ]
    structural_status = "failed" if any(case.get("structural_checks") == "failed" for case in contract_cases) else "passed"
    missing_cases = [] if missing is None else missing
    contract_status = status if not missing_cases and structural_status == "passed" else "failed"
    return {
        "model": CONTRACT_MODEL,
        "witness_model": witness_model,
        "status": contract_status,
        "witness_status": status,
        "structural_status": structural_status,
        "required_cases": ["i32", "ptr", "array"],
        "missing_required_cases": missing_cases,
        "cases": contract_cases,
    }


def evidence_record(contract_value: dict[str, Any] | None) -> dict[str, Any]:
    record = {
        "marker": MARKER,
        "file": "GlobalOpt.cpp",
        "line": 321,
        "evidence_status": "verified",
    }
    if contract_value is not None:
        record["globalopt_witness_contract"] = contract_value
        record["globalopt_witness_structural_status"] = contract_value.get("structural_status")
        record["globalopt_required_witness_cases"] = contract_value.get("required_cases")
        record["globalopt_missing_required_witness_cases"] = contract_value.get("missing_required_cases")
    return record


def coverage_record(contract_value: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": "o2t-globalopt-coverage-v1",
        "witnesses": {
            "records": [
                {
                    "key": "GlobalOpt.cpp|321|probe.globalopt.dead-initializer",
                    "marker": MARKER,
                    "status": "passed",
                    "witness_contract": contract_value,
                }
            ],
        },
    }


def verify(
    repo: Path,
    input_path: Path,
    out_dir: Path,
    expect: int = 0,
    z3: str = "",
    emit_smt: bool = False,
    alive_tv: Path | None = None,
    require_alive2_proved: bool = False,
) -> tuple[dict[str, Any], str, str]:
    out = out_dir / "verification.json"
    report = out_dir / "verification.txt"
    command = [
        sys.executable,
        str(repo / "tools" / "cv-verify-globalopt-witness-contract.py"),
        "--input",
        str(input_path),
        "--out",
        str(out),
        "--report",
        str(report),
        "--require-clean",
    ]
    if z3:
        command.extend(["--z3", z3])
    if emit_smt:
        command.extend(["--emit-smt", str(out_dir / "smt")])
    if alive_tv is not None:
        command.extend([
            "--alive2-checker",
            str(repo / "tools" / "cv-alive2-check-ir.py"),
            "--alive-tv",
            str(alive_tv),
            "--emit-alive2",
            str(out_dir / "alive2"),
        ])
    if require_alive2_proved:
        command.append("--require-alive2-proved")
    result = run(command, expect=expect)
    return json.loads(out.read_text(encoding="utf-8")), report.read_text(encoding="utf-8"), result.stderr


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)

    passed = args.work_dir / "passed"
    write_jsonl(passed / "evidence.jsonl", [evidence_record(contract())])
    passed_data, passed_report, _ = verify(args.repo, passed / "evidence.jsonl", passed)
    assert passed_data["summary"]["status"] == {"passed": 1}
    assert passed_data["summary"]["formal_status"] == {"not-run": 3}
    assert passed_data["summary"]["failed_checks"] == {}
    assert "failed_checks: none" in passed_report

    if args.z3:
        formal = args.work_dir / "formal-passed"
        write_jsonl(formal / "evidence.jsonl", [evidence_record(contract())])
        formal_data, formal_report, _ = verify(args.repo, formal / "evidence.jsonl", formal, z3=args.z3, emit_smt=True)
        assert formal_data["summary"]["status"] == {"passed": 1}
        assert formal_data["summary"]["formal_status"] == {"proved": 3}
        smt_files = sorted((formal / "smt").glob("*.smt2"))
        assert len(smt_files) == 3
        assert all("(check-sat)" in path.read_text(encoding="utf-8") for path in smt_files)
        assert "formal_status: proved=3" in formal_report

    semantic = args.work_dir / "semantic-passed"
    write_jsonl(semantic / "evidence.jsonl", [evidence_record(materialized_contract(semantic / "ir"))])
    semantic_data, semantic_report, _ = verify(
        args.repo,
        semantic / "evidence.jsonl",
        semantic,
        alive_tv=args.repo / "tests" / "fixtures" / "fake-alive-tv-success.sh",
        require_alive2_proved=True,
    )
    assert semantic_data["summary"]["status"] == {"passed": 1}
    assert semantic_data["summary"]["semantic_status"] == {"proved": 3}
    assert semantic_data["summary"]["record_semantic_status"] == {"proved": 1}
    assert len(list((semantic / "alive2").glob("*.json"))) == 3
    assert "semantic_status: proved=3" in semantic_report

    semantic_failed = args.work_dir / "semantic-failed"
    write_jsonl(semantic_failed / "evidence.jsonl", [evidence_record(materialized_contract(semantic_failed / "ir"))])
    failed_data, _, _ = verify(
        args.repo,
        semantic_failed / "evidence.jsonl",
        semantic_failed,
        expect=1,
        alive_tv=args.repo / "tests" / "fixtures" / "fake-alive-tv-fail.sh",
    )
    assert failed_data["summary"]["status"] == {"failed": 1}
    assert failed_data["summary"]["semantic_status"] == {"failed": 3}
    assert failed_data["summary"]["record_semantic_status"] == {"failed": 1}
    assert failed_data["summary"]["failed_checks"]["i32-semantic-failed"] == 1

    semantic_unsupported = args.work_dir / "semantic-unsupported"
    write_jsonl(semantic_unsupported / "evidence.jsonl", [evidence_record(materialized_contract(semantic_unsupported / "ir"))])
    unsupported_data, _, _ = verify(
        args.repo,
        semantic_unsupported / "evidence.jsonl",
        semantic_unsupported,
        alive_tv=args.repo / "tests" / "fixtures" / "fake-alive-tv-unsupported.sh",
    )
    assert unsupported_data["summary"]["status"] == {"passed": 1}
    assert unsupported_data["summary"]["semantic_status"] == {"unsupported": 3}
    unsupported_strict_data, _, _ = verify(
        args.repo,
        semantic_unsupported / "evidence.jsonl",
        semantic_unsupported / "strict",
        expect=1,
        alive_tv=args.repo / "tests" / "fixtures" / "fake-alive-tv-unsupported.sh",
        require_alive2_proved=True,
    )
    assert unsupported_strict_data["summary"]["failed_checks"]["i32-semantic-unsupported"] == 1

    semantic_error = args.work_dir / "semantic-error"
    error_script = semantic_error / "fake-alive-tv-error.sh"
    error_script.parent.mkdir(parents=True, exist_ok=True)
    error_script.write_text("#!/usr/bin/env bash\necho 'error: fixture failure'\nexit 1\n", encoding="utf-8")
    error_script.chmod(0o755)
    write_jsonl(semantic_error / "evidence.jsonl", [evidence_record(materialized_contract(semantic_error / "ir"))])
    error_data, _, _ = verify(
        args.repo,
        semantic_error / "evidence.jsonl",
        semantic_error,
        expect=1,
        alive_tv=error_script,
    )
    assert error_data["summary"]["semantic_status"] == {"error": 3}

    semantic_missing = args.work_dir / "semantic-missing-executable"
    write_jsonl(semantic_missing / "evidence.jsonl", [evidence_record(materialized_contract(semantic_missing / "ir"))])
    missing_exec_data, _, _ = verify(
        args.repo,
        semantic_missing / "evidence.jsonl",
        semantic_missing,
        expect=1,
        alive_tv=semantic_missing / "missing-alive-tv",
    )
    assert missing_exec_data["summary"]["semantic_status"] == {"error": 3}

    coverage = args.work_dir / "coverage"
    write_json(coverage / "coverage.json", coverage_record(contract()))
    coverage_data, _, _ = verify(args.repo, coverage / "coverage.json", coverage, z3=args.z3 or "")
    assert coverage_data["summary"]["status"] == {"passed": 1}

    missing = args.work_dir / "missing-case"
    missing_cases = [witness_case("i32"), witness_case("array")]
    write_jsonl(missing / "evidence.jsonl", [evidence_record(contract(cases=missing_cases, missing=["ptr"]))])
    missing_data, missing_report, missing_stderr = verify(args.repo, missing / "evidence.jsonl", missing, expect=1)
    assert missing_data["summary"]["status"] == {"failed": 1}
    assert missing_data["summary"]["failed_checks"]["ptr-required-case-missing"] == 2
    assert "globalopt witness contract verification failed: 1" in missing_stderr
    assert "ptr-required-case-missing" in missing_report

    structural = args.work_dir / "structural-failed"
    bad_i32 = witness_case("i32", structural_checks="failed")
    write_jsonl(structural / "evidence.jsonl", [evidence_record(contract(cases=[bad_i32, witness_case("ptr"), witness_case("array")]))])
    structural_data, _, _ = verify(args.repo, structural / "evidence.jsonl", structural, expect=1, z3=args.z3 or "", emit_smt=True)
    assert structural_data["summary"]["failed_checks"]["structural-status-not-passed"] == 1
    assert structural_data["summary"]["failed_checks"]["i32-structural-checks-not-passed"] == 1
    assert structural_data["summary"]["formal_status"]["not-run"] == 1
    assert len(list((structural / "smt").glob("*.smt2"))) == 2

    model = args.work_dir / "model-mismatch"
    write_jsonl(model / "evidence.jsonl", [evidence_record(contract(witness_model="wrong-model"))])
    model_data, _, _ = verify(args.repo, model / "evidence.jsonl", model, expect=1)
    assert model_data["summary"]["failed_checks"]["witness-model-mismatch"] == 1

    malformed = args.work_dir / "malformed"
    write_jsonl(malformed / "evidence.jsonl", [evidence_record(None)])
    malformed_data, _, _ = verify(args.repo, malformed / "evidence.jsonl", malformed, expect=1)
    assert malformed_data["summary"]["failed_checks"]["globalopt-witness-contract-absent"] == 1

    drift = args.work_dir / "flat-drift"
    drift_record = evidence_record(contract())
    drift_record["globalopt_witness_structural_status"] = "failed"
    write_jsonl(drift / "evidence.jsonl", [drift_record])
    drift_data, _, _ = verify(args.repo, drift / "evidence.jsonl", drift)
    assert drift_data["summary"]["status"] == {"passed": 1}
    assert drift_data["summary"]["diagnostics"] == {"flat-structural-status-drift": 1}

    tampered = args.work_dir / "tampered-after"
    bad_case = witness_case("i32")
    bad_case["structural_details"]["after_initializer"] = "7"
    write_jsonl(tampered / "evidence.jsonl", [evidence_record(contract(cases=[bad_case, witness_case("ptr"), witness_case("array")]))])
    tampered_data, _, _ = verify(args.repo, tampered / "evidence.jsonl", tampered, expect=1, z3=args.z3 or "")
    assert tampered_data["summary"]["failed_checks"]["i32-after-initializer-not-default"] == 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
