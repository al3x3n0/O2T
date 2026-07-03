#!/usr/bin/env python3
"""Regression fixture for the upstream DSE readiness wrapper."""

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
    parser.add_argument("--ir-miner", type=Path, required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--z3", required=True)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}")
    return result


def run_raw(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_compile_db(path: Path, source: Path, compiler: str, repo: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "directory": str(repo),
                    "command": f"{compiler} -std=c++17 {source}",
                    "file": str(source),
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    missing_out = work_dir / "missing"
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-upstream-dse-readiness.py"),
            "--upstream-dse-source",
            str(work_dir / "does-not-exist" / "DSE.cpp"),
            "--out",
            str(missing_out),
            "--allow-missing",
        ]
    )
    missing_summary = load_json(missing_out / "upstream-dse-readiness.json")
    missing_text = (missing_out / "upstream-dse-readiness.txt").read_text(encoding="utf-8")
    assert missing_summary["model"] == "o2t-upstream-dse-readiness-v1"
    assert missing_summary["source_status"] == "missing"
    assert missing_summary["dse"]["findings"] == 0
    assert "source_status: missing" in missing_text

    source = repo / "tests" / "fixtures" / "upstream_dse_like_pass.cpp"
    compile_db = work_dir / "compile-db" / "compile_commands.json"
    write_compile_db(compile_db, source, args.compiler, repo)
    out = work_dir / "present"
    baseline_path = work_dir / "present-baseline.json"
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-upstream-dse-readiness.py"),
            "--upstream-dse-source",
            str(source),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--compiler",
            args.compiler,
            "--z3",
            args.z3,
            "--mine-pass-impl-ir",
            "--min-dse-matched",
            "3",
            "--max-dse-blocked",
            "3",
            "--max-dse-source-incomplete",
            "1",
            "--write-baseline",
            str(baseline_path),
        ]
    )

    summary = load_json(out / "upstream-dse-readiness.json")
    text = (out / "upstream-dse-readiness.txt").read_text(encoding="utf-8")
    assert summary["model"] == "o2t-upstream-dse-readiness-v1"
    assert summary["source_status"] == "present"
    assert summary["audit_exit_code"] == 0
    assert summary["budget_violations"] == []
    assert summary["dse"]["findings"] == 7
    assert summary["dse"]["markers"] == {
        "probe.dse.dead-store": 1,
        "probe.dse.overwritten-store": 6,
    }
    assert summary["dse"]["intent_check_status"] == {
        "blocked": 3,
        "matched": 3,
        "source-incomplete": 1,
    }
    assert summary["dse"]["blocked_reasons"] == {
        "memory.overwrite.nonoverlap": 1,
        "memory.overwrite.unknown-size": 1,
        "memory.unknown-intervening-effect": 1,
    }
    assert summary["dse"]["source_incomplete_missing_facts"] == {
        "memory.overwrite.size.bounded-four-lane": 1,
        "memory.overwrite.size.known": 1,
    }
    assert len(summary["dse"]["samples"]) == 4
    assert (out / "audit" / "findings.json").is_file()
    assert (out / "audit" / "run-summary.json").is_file()
    baseline = load_json(baseline_path)
    assert baseline["model"] == "o2t-upstream-dse-baseline-v1"
    assert len(baseline["records"]) == 7
    assert any(record["status"] == "blocked" for record in baseline["records"])
    assert "O2T Upstream DSE Readiness" in text
    assert "DSE intent check status" in text
    assert "memory.overwrite.unknown-size: 1" in text

    diff_out = work_dir / "baseline-diff"
    run(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-upstream-dse-readiness.py"),
            "--upstream-dse-source",
            str(source),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(diff_out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--compiler",
            args.compiler,
            "--z3",
            args.z3,
            "--mine-pass-impl-ir",
            "--baseline",
            str(baseline_path),
            "--max-new-dse-unsupported",
            "0",
        ]
    )
    diff_summary = load_json(diff_out / "upstream-dse-readiness.json")
    diff = load_json(diff_out / "upstream-dse-baseline-diff.json")
    assert diff_summary["budget_violations"] == []
    assert diff["counts"] == {"changed": 0, "new_unsupported": 0, "resolved_unsupported": 0}
    assert "new_unsupported: 0" in (diff_out / "upstream-dse-baseline-diff.txt").read_text(encoding="utf-8")

    synthetic_baseline = dict(baseline)
    removed = False
    records = []
    for record in baseline["records"]:
        if not removed and record["status"] == "blocked":
            removed = True
            continue
        records.append(record)
    synthetic_baseline["records"] = records
    synthetic_path = work_dir / "synthetic-previous-baseline.json"
    synthetic_path.write_text(json.dumps(synthetic_baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    synthetic_out = work_dir / "synthetic-diff"
    synthetic_result = run_raw(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-upstream-dse-readiness.py"),
            "--upstream-dse-source",
            str(source),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(synthetic_out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--compiler",
            args.compiler,
            "--z3",
            args.z3,
            "--mine-pass-impl-ir",
            "--baseline",
            str(synthetic_path),
            "--max-new-dse-unsupported",
            "0",
        ]
    )
    assert synthetic_result.returncode == 1
    synthetic_summary = load_json(synthetic_out / "upstream-dse-readiness.json")
    synthetic_diff = load_json(synthetic_out / "upstream-dse-baseline-diff.json")
    assert synthetic_diff["counts"]["new_unsupported"] == 1
    assert synthetic_summary["budget_violations"] == [
        {"actual": 1, "budget": "max-new-dse-unsupported", "limit": 0}
    ]

    budget_out = work_dir / "budget-failure"
    budget_result = run_raw(
        [
            sys.executable,
            str(repo / "tools" / "cv-run-upstream-dse-readiness.py"),
            "--upstream-dse-source",
            str(source),
            "--compile-commands",
            str(compile_db),
            "--out",
            str(budget_out),
            "--ast-miner",
            str(args.ast_miner),
            "--ir-miner",
            str(args.ir_miner),
            "--compiler",
            args.compiler,
            "--z3",
            args.z3,
            "--mine-pass-impl-ir",
            "--max-dse-blocked",
            "2",
        ]
    )
    assert budget_result.returncode == 1
    budget_summary = load_json(budget_out / "upstream-dse-readiness.json")
    assert budget_summary["budget_violations"] == [
        {"actual": 3, "budget": "max-dse-blocked", "limit": 2}
    ]
    assert "budget_violations: 1" in (budget_out / "upstream-dse-readiness.txt").read_text(encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
