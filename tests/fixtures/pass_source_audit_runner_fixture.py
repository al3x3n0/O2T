#!/usr/bin/env python3
"""Regression fixture for production pass source audit runner behavior."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ast-miner", type=Path, required=True)
    parser.add_argument("--z3", required=True)
    return parser.parse_args()


def run(command: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}, expected {expect}")
    return result


def write_compile_db(repo: Path, path: Path) -> None:
    files = [
        repo / "tests" / "fixtures" / "intent_inference_snippet.cpp",
        repo / "tests" / "fixtures" / "vector_pass_snippet.cpp",
        repo / "tests" / "fixtures" / "slp_scalable_minmax_transaction_snippet.cpp",
        repo / "tests" / "fixtures" / "slp_scalable_widening_ambiguous_reduction_transaction_snippet.cpp",
        repo / "tests" / "fixtures" / "slp_transaction_helper_pack_snippet.cpp",
        repo / "tests" / "fixtures" / "slp_transaction_graph_select_snippet.cpp",
        repo / "tests" / "fixtures" / "slp_transaction_graph_helper_ambiguous_slice_snippet.cpp",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([
            {"directory": str(repo), "command": "clang++ -std=c++17 " + str(file), "file": str(file)}
            for file in files
        ]),
        encoding="utf-8",
    )


def audit_command(repo: Path, ast_miner: Path, z3: str, compile_db: Path, out: Path) -> list[str]:
    return [
        sys.executable,
        str(repo / "tools" / "cv-run-pass-source-audit.py"),
        "--compile-commands",
        str(compile_db),
        "--out",
        str(out),
        "--ast-miner",
        str(ast_miner),
        "--z3",
        z3,
    ]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    compile_db = args.work_dir / "compile-db" / "compile_commands.json"
    write_compile_db(args.repo, compile_db)
    sources = [
        str(args.repo / "tests" / "fixtures" / "intent_inference_snippet.cpp"),
        str(args.repo / "tests" / "fixtures" / "vector_pass_snippet.cpp"),
    ]
    scalable_minmax_source = str(args.repo / "tests" / "fixtures" / "slp_scalable_minmax_transaction_snippet.cpp")
    scalable_widening_gap_source = str(
        args.repo / "tests" / "fixtures" / "slp_scalable_widening_ambiguous_reduction_transaction_snippet.cpp"
    )
    helper_gap_source = str(args.repo / "tests" / "fixtures" / "slp_transaction_graph_helper_ambiguous_slice_snippet.cpp")
    slp_graph_source = str(args.repo / "tests" / "fixtures" / "slp_transaction_graph_select_snippet.cpp")

    summary_out = args.work_dir / "summary"
    run(audit_command(args.repo, args.ast_miner, args.z3, compile_db, summary_out) + sources)
    summary = load_json(summary_out / "run-summary.json")
    assert summary["sources"]["selected"] == 2
    assert summary["findings"]["total"] == 30
    assert summary["intents"]["proof_status"] == {"proved": 30}
    assert summary["coverage"]["recommendations"] == {"covered by source-derived formal IR": 30}
    assert summary["budget_violations"] == []
    assert summary["baseline_diff"]["new"] == 0
    assert (summary_out / "audit-baseline.json").is_file()
    assert (summary_out / "baseline-diff.json").is_file()
    readiness = load_json(summary_out / "real-pass-readiness.json")
    assert readiness["model"] == "o2t-real-pass-readiness-v1"
    assert readiness["sources"]["selected"] == 2
    assert readiness["findings"]["total"] == 30
    assert "Transaction graph status" in (summary_out / "real-pass-readiness.txt").read_text(encoding="utf-8")
    assert "O2T Pass Source Audit Run Summary" in (summary_out / "run-summary.txt").read_text(encoding="utf-8")

    slp_ir_out = args.work_dir / "slp-ir-readiness"
    run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, slp_ir_out)
        + [
            "--emit-slp-transaction-ir",
            "--validate-slp-ir",
            "--llvm-as",
            str(args.repo / "tests" / "fixtures" / "fake-llvm-as.sh"),
        ]
        + [slp_graph_source]
    )
    slp_readiness = load_json(slp_ir_out / "real-pass-readiness.json")
    assert slp_readiness["transaction_graph"]["graph_status"] == {"present": 1}
    assert slp_readiness["slp_transaction_ir"]["enabled"] is True
    assert slp_readiness["slp_transaction_ir"]["generated"] == 1
    assert slp_readiness["slp_transaction_ir"]["graph_ir"] == {"used": 1}
    slp_readiness_text = (slp_ir_out / "real-pass-readiness.txt").read_text(encoding="utf-8")
    assert "SLP transaction IR graph lowering" in slp_readiness_text
    assert "  used: 1" in slp_readiness_text

    marker_out = args.work_dir / "marker-prefix"
    run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, marker_out)
        + ["--baseline", str(summary_out / "audit-baseline.json")]
        + ["--marker-prefix", "probe.vector.scalable."]
        + sources
    )
    marker_findings = load_json(marker_out / "findings.json")
    assert marker_findings
    assert all(record["marker"].startswith("probe.vector.scalable.") for record in marker_findings)
    marker_summary = load_json(marker_out / "run-summary.json")
    assert marker_summary["filters"]["marker_prefixes"] == ["probe.vector.scalable."]
    assert marker_summary["findings"]["total"] == len(marker_findings)
    marker_diff = load_json(marker_out / "baseline-diff.json")
    assert marker_diff["baseline_present"] is True
    assert marker_diff["summary"]["resolved"] == 23
    assert marker_diff["summary"]["new"] == 0

    budget_pass_out = args.work_dir / "budget-pass"
    run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, budget_pass_out)
        + ["--min-proved", "1", "--max-unsupported", "0", "--max-proof-failures", "0", "--max-mining-errors", "0"]
        + sources
    )
    assert load_json(budget_pass_out / "run-summary.json")["budget_violations"] == []

    budget_fail_out = args.work_dir / "budget-fail"
    result = run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, budget_fail_out)
        + ["--min-proved", "999"]
        + sources,
        expect=1,
    )
    fail_summary = load_json(budget_fail_out / "run-summary.json")
    assert fail_summary["budget_violations"] == [{"actual": 30, "budget": "min-proved", "limit": 999}]
    assert "budget violation: min-proved actual=30 limit=999" in result.stderr
    assert (budget_fail_out / "intent-coverage.json").is_file()

    helper_gap_out = args.work_dir / "helper-gap"
    run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, helper_gap_out)
        + [helper_gap_source]
    )
    helper_summary = load_json(helper_gap_out / "run-summary.json")
    helper_diagnostics = helper_summary["coverage"]["helper_slice_diagnostics"]
    assert helper_diagnostics
    assert helper_diagnostics[0]["helper"] == "ambiguousMask"
    assert helper_diagnostics[0]["role"] == "memory-pack"
    assert helper_diagnostics[0]["reason"] == "unsupported-unresolved-helper-slice"
    helper_summary_text = (helper_gap_out / "run-summary.txt").read_text(encoding="utf-8")
    assert "Top helper slice diagnostics" in helper_summary_text
    assert "helper=ambiguousMask role=memory-pack reason=unsupported-unresolved-helper-slice" in helper_summary_text

    new_gap_out = args.work_dir / "new-gap"
    run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, new_gap_out)
        + ["--baseline", str(marker_out / "audit-baseline.json")]
        + [scalable_minmax_source]
    )
    new_gap_diff = load_json(new_gap_out / "baseline-diff.json")
    assert new_gap_diff["summary"]["new_unsupported"] == 0
    assert new_gap_diff["summary"]["new_fallback_transactions"] == 0
    assert "Top new fallback transactions" in (new_gap_out / "baseline-diff.txt").read_text(encoding="utf-8")

    summary_baseline_out = args.work_dir / "summary-baseline"
    run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, summary_baseline_out)
        + ["--baseline", str(summary_out / "run-summary.json")]
        + sources
    )
    summary_baseline_diff = load_json(summary_baseline_out / "baseline-diff.json")
    assert summary_baseline_diff["summary"]["new"] == 0
    assert summary_baseline_diff["summary"]["resolved"] == 0

    new_budget_fail_out = args.work_dir / "new-budget-fail"
    result = run(
        audit_command(args.repo, args.ast_miner, args.z3, compile_db, new_budget_fail_out)
        + [
            "--baseline",
            str(marker_out / "audit-baseline.json"),
            "--max-new-unsupported",
            "0",
            "--max-new-fallback-transactions",
            "0",
        ]
        + [scalable_widening_gap_source],
        expect=1,
    )
    new_budget_summary = load_json(new_budget_fail_out / "run-summary.json")
    assert new_budget_summary["budget_violations"] == [
        {"actual": 1, "budget": "max-new-unsupported", "limit": 0},
        {"actual": 1, "budget": "max-new-fallback-transactions", "limit": 0},
    ]
    assert "budget violation: max-new-unsupported actual=1 limit=0" in result.stderr
    assert (new_budget_fail_out / "baseline-diff.json").is_file()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
