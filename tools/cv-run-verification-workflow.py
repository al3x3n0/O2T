#!/usr/bin/env python3
"""Orchestrate O2T instrumentation, KLEE, packaging, and verification."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STAGES = ["instrument", "klee", "package", "verify"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--from-stage", choices=STAGES, default="instrument")
    parser.add_argument("--to-stage", choices=STAGES, default="verify")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--sources", nargs="*", type=Path, default=[])
    parser.add_argument("--llvm-source", type=Path)
    parser.add_argument("--llvm-build", type=Path)
    parser.add_argument("--execute-instrumented", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--llm-command")
    parser.add_argument("--klee-check", action="store_true")
    parser.add_argument("--backfill-gaps", action="store_true")
    parser.add_argument("--backfill-check", action="store_true")
    parser.add_argument("--package-campaign", action="store_true")
    parser.add_argument("--audit-instrumentation", action="store_true")
    parser.add_argument("--recommend-instrumentation", action="store_true")
    parser.add_argument("--repair-instrumentation-candidates", action="store_true")
    parser.add_argument("--retry-repaired-instrumentation", action="store_true")
    parser.add_argument("--require-instrumentation-coverage", action="store_true")
    parser.add_argument("--emit-intent-evidence", action="store_true")
    parser.add_argument("--require-intent-evidence", action="store_true")
    parser.add_argument("--promote-intents", action="store_true")
    parser.add_argument("--replace-existing-intents", action="store_true")
    parser.add_argument("--require-promotable-intent", action="store_true")
    parser.add_argument("--klee-campaign", type=Path)
    parser.add_argument("--instrumentation-dir", type=Path)
    parser.add_argument("--package-out", type=Path)
    parser.add_argument("--host-opt", type=Path)
    parser.add_argument("--host-llvm-as", type=Path)
    parser.add_argument("--alive2", action="store_true")
    parser.add_argument("--alive2-bin", type=Path)
    parser.add_argument("--passes")
    parser.add_argument("--globalopt-coverage", action="store_true")
    parser.add_argument("--globalopt-source", type=Path)
    parser.add_argument("--globalopt-min-findings", type=int)
    parser.add_argument("--globalopt-min-graph-derived", type=int)
    parser.add_argument("--globalopt-max-unsupported", type=int)
    parser.add_argument("--globalopt-max-incomplete-safety", type=int)
    parser.add_argument("--globalopt-max-missing-fact", action="append", default=[])
    parser.add_argument("--globalopt-baseline", type=Path)
    parser.add_argument("--globalopt-write-baseline", type=Path)
    parser.add_argument("--globalopt-max-new-unsupported", type=int)
    parser.add_argument("--globalopt-max-new-incomplete-safety", type=int)
    parser.add_argument("--globalopt-emit-witnesses", action="store_true")
    parser.add_argument("--globalopt-min-witnesses", type=int)
    parser.add_argument("--globalopt-max-witness-failures", type=int)
    parser.add_argument("--globalopt-verify-witness-contracts", action="store_true")
    parser.add_argument("--globalopt-verify-witness-semantics", action="store_true")
    parser.add_argument("--globalopt-require-witness-semantics", action="store_true")
    parser.add_argument("--verify-predicate-provenance", action="store_true")
    parser.add_argument("--require-globalopt-witnesses", action="store_true")
    parser.add_argument("--max-globalopt-witness-failures", type=int)
    parser.add_argument("--z3", default="z3")
    parser.add_argument("--playbook", type=Path, default=ROOT / "scripts" / "instrumented-llvm-playbook.sh")
    parser.add_argument("--campaign-driver", type=Path, default=ROOT / "tools" / "cv-run-campaign.py")
    parser.add_argument("--klee-runner", type=Path, default=ROOT / "tools" / "cv-run-klee-campaign.py")
    parser.add_argument(
        "--campaign-packager",
        type=Path,
        default=ROOT / "tools" / "cv-package-verification-campaign.py",
    )
    parser.add_argument(
        "--instrumented-runner",
        type=Path,
        default=ROOT / "tools" / "cv-run-instrumented-campaign.py",
    )
    parser.add_argument(
        "--globalopt-runner",
        type=Path,
        default=ROOT / "tools" / "cv-run-globalopt-coverage.py",
    )
    parser.add_argument(
        "--globalopt-witness-contract-verifier",
        type=Path,
        default=ROOT / "tools" / "cv-verify-globalopt-witness-contract.py",
    )
    parser.add_argument(
        "--predicate-provenance-verifier",
        type=Path,
        default=ROOT / "tools" / "cv-verify-predicate-provenance.py",
    )
    return parser.parse_args()


def selected_stages(first: str, last: str) -> list[str]:
    start = STAGES.index(first)
    end = STAGES.index(last)
    if start > end:
        raise ValueError("--from-stage must not come after --to-stage")
    return STAGES[start : end + 1]


def require_executable(path: Path, label: str) -> bool:
    if path.is_file() and path.stat().st_mode & 0o111:
        return True
    print(f"{label} is not executable: {path}", file=sys.stderr)
    return False


def require_dir(path: Path | None, label: str) -> bool:
    if path is not None and path.is_dir():
        return True
    print(f"{label} is required and must be a directory", file=sys.stderr)
    return False


def command_text(command: list[str]) -> str:
    return shlex.join(command)


def write_command_log(path: Path, stages: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for stage in stages:
            output.write(f"[{stage['stage']}] {command_text(stage['command'])}\n")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def build_plan(args: argparse.Namespace, stages: list[str]) -> list[dict[str, Any]]:
    workflow = args.out
    instrumentation_campaign = workflow / "instrumentation"
    instrumentation_dir = args.instrumentation_dir or (instrumentation_campaign / "instrumentation")
    klee_campaign = args.klee_campaign or (workflow / "klee")
    package_out = args.package_out or (workflow / "verification-campaign")
    globalopt_out = workflow / "globalopt-coverage"
    globalopt_contract_out = workflow / "globalopt-witness-contract"
    predicate_provenance_out = workflow / "predicate-provenance"
    feed_globalopt_coverage = bool(args.globalopt_coverage and args.emit_intent_evidence and "instrument" in stages)
    feed_globalopt_contract_verification = bool(
        feed_globalopt_coverage
        and (args.globalopt_verify_witness_contracts or args.globalopt_verify_witness_semantics)
    )

    plan: list[dict[str, Any]] = []

    def globalopt_stage() -> dict[str, Any]:
        command = [
            sys.executable,
            str(args.globalopt_runner),
            "--out",
            str(globalopt_out),
        ]
        if args.globalopt_source:
            command.extend(["--source", str(args.globalopt_source)])
        if args.globalopt_min_findings is not None:
            command.extend(["--min-findings", str(args.globalopt_min_findings)])
        if args.globalopt_min_graph_derived is not None:
            command.extend(["--min-graph-derived", str(args.globalopt_min_graph_derived)])
        if args.globalopt_max_unsupported is not None:
            command.extend(["--max-unsupported", str(args.globalopt_max_unsupported)])
        if args.globalopt_max_incomplete_safety is not None:
            command.extend(["--max-incomplete-safety", str(args.globalopt_max_incomplete_safety)])
        for budget in args.globalopt_max_missing_fact:
            command.extend(["--max-missing-fact", str(budget)])
        if args.globalopt_baseline:
            command.extend(["--baseline", str(args.globalopt_baseline)])
        if args.globalopt_write_baseline:
            command.extend(["--write-baseline", str(args.globalopt_write_baseline)])
        if args.globalopt_max_new_unsupported is not None:
            command.extend(["--max-new-unsupported", str(args.globalopt_max_new_unsupported)])
        if args.globalopt_max_new_incomplete_safety is not None:
            command.extend(["--max-new-incomplete-safety", str(args.globalopt_max_new_incomplete_safety)])
        if args.globalopt_emit_witnesses:
            command.append("--emit-witnesses")
        if args.globalopt_min_witnesses is not None:
            command.extend(["--min-witnesses", str(args.globalopt_min_witnesses)])
        if args.globalopt_max_witness_failures is not None:
            command.extend(["--max-witness-failures", str(args.globalopt_max_witness_failures)])
        if args.host_llvm_as:
            command.extend(["--host-llvm-as", str(args.host_llvm_as)])
        return {
            "stage": "globalopt-coverage",
            "command": command,
            "artifacts": {
                "globalopt_coverage": str(globalopt_out / "globalopt-coverage.json"),
                "globalopt_report": str(globalopt_out / "globalopt-coverage.txt"),
                "globalopt_baseline": str(globalopt_out / "globalopt-baseline.json"),
                "globalopt_baseline_diff": str(globalopt_out / "globalopt-baseline-diff.json"),
                "globalopt_baseline_diff_report": str(globalopt_out / "globalopt-baseline-diff.txt"),
                "globalopt_witnesses": str(globalopt_out / "witnesses"),
            },
        }

    if feed_globalopt_coverage:
        plan.append(globalopt_stage())

    def globalopt_contract_stage() -> dict[str, Any]:
        command = [
            sys.executable,
            str(args.globalopt_witness_contract_verifier),
            "--input",
            str(globalopt_out / "globalopt-coverage.json"),
            "--out",
            str(globalopt_contract_out / "globalopt-witness-contract-verification.json"),
            "--report",
            str(globalopt_contract_out / "globalopt-witness-contract-verification.txt"),
            "--emit-smt",
            str(globalopt_contract_out / "smt"),
            "--z3",
            str(args.z3),
            "--require-clean",
        ]
        artifacts = {
            "globalopt_witness_contract_verification": str(globalopt_contract_out / "globalopt-witness-contract-verification.json"),
            "globalopt_witness_contract_report": str(globalopt_contract_out / "globalopt-witness-contract-verification.txt"),
            "globalopt_witness_contract_smt": str(globalopt_contract_out / "smt"),
        }
        if args.globalopt_verify_witness_semantics:
            command.extend([
                "--alive2-checker",
                str(ROOT / "tools" / "cv-alive2-check-ir.py"),
                "--emit-alive2",
                str(globalopt_contract_out / "alive2"),
            ])
            if args.alive2_bin:
                command.extend(["--alive-tv", str(args.alive2_bin)])
            else:
                command.extend(["--alive-tv", "alive-tv"])
            artifacts["globalopt_witness_contract_alive2"] = str(globalopt_contract_out / "alive2")
        if args.globalopt_require_witness_semantics:
            command.append("--require-alive2-proved")
        return {
            "stage": "globalopt-witness-contract",
            "command": command,
            "artifacts": {
                **artifacts,
            },
        }

    if feed_globalopt_contract_verification:
        plan.append(globalopt_contract_stage())

    def predicate_provenance_stage() -> dict[str, Any]:
        return {
            "stage": "predicate-provenance",
            "command": [
                sys.executable,
                str(args.predicate_provenance_verifier),
                "--input",
                str(globalopt_out / "globalopt-coverage.json"),
                "--out",
                str(predicate_provenance_out / "predicate-provenance-verification.json"),
                "--report",
                str(predicate_provenance_out / "predicate-provenance-verification.txt"),
                "--require-clean",
            ],
            "artifacts": {
                "predicate_provenance_verification": str(
                    predicate_provenance_out / "predicate-provenance-verification.json"
                ),
                "predicate_provenance_report": str(
                    predicate_provenance_out / "predicate-provenance-verification.txt"
                ),
            },
        }

    if feed_globalopt_coverage and args.verify_predicate_provenance:
        plan.append(predicate_provenance_stage())

    if "instrument" in stages:
        command = [
            str(args.campaign_driver),
            "--out",
            str(instrumentation_campaign),
            "--emit-instrumentation",
            "--instrumentation-dry-run",
        ]
        if args.audit_instrumentation:
            command.append("--audit-instrumentation")
        if args.recommend_instrumentation:
            command.append("--recommend-instrumentation")
        if args.repair_instrumentation_candidates:
            command.append("--repair-instrumentation-candidates")
        if args.retry_repaired_instrumentation:
            command.append("--retry-repaired-instrumentation")
        if args.require_instrumentation_coverage:
            command.append("--require-instrumentation-coverage")
        if args.emit_intent_evidence:
            command.append("--emit-intent-evidence")
        if args.require_intent_evidence:
            command.append("--require-intent-evidence")
        if args.promote_intents:
            command.append("--promote-intents")
        if args.replace_existing_intents:
            command.append("--replace-existing-intents")
        if args.require_promotable_intent:
            command.append("--require-promotable-intent")
        if feed_globalopt_coverage:
            command.extend(["--globalopt-coverage", str(globalopt_out / "globalopt-coverage.json")])
        if feed_globalopt_contract_verification:
            command.extend([
                "--globalopt-witness-contract-verification",
                str(globalopt_contract_out / "globalopt-witness-contract-verification.json"),
            ])
        if feed_globalopt_coverage and args.verify_predicate_provenance:
            command.extend([
                "--predicate-provenance-verification",
                str(predicate_provenance_out / "predicate-provenance-verification.json"),
            ])
        if args.verify_predicate_provenance:
            command.append("--verify-predicate-provenance")
        if args.require_globalopt_witnesses:
            command.append("--require-globalopt-witnesses")
        if args.max_globalopt_witness_failures is not None:
            command.extend(["--max-globalopt-witness-failures", str(args.max_globalopt_witness_failures)])
        if args.llm_command:
            command.extend(["--llm-command", args.llm_command])
        if args.host_opt:
            command.extend(["--host-opt", str(args.host_opt)])
        if args.host_llvm_as:
            command.extend(["--host-llvm-as", str(args.host_llvm_as)])
        if args.alive2:
            command.append("--alive2")
        if args.alive2_bin:
            command.extend(["--alive2-bin", str(args.alive2_bin)])
        if args.passes:
            command.extend(["--passes", args.passes])
        command.extend(str(source) for source in args.sources)
        plan.append({"stage": "instrument", "command": command, "artifacts": {"instrumentation": str(instrumentation_dir)}})

    if "klee" in stages and args.klee_campaign is None:
        command = [str(args.klee_runner), "--out-dir", str(klee_campaign)]
        if args.klee_check:
            command.append("--check")
        if args.backfill_gaps:
            command.append("--backfill-gaps")
        if args.backfill_check:
            command.append("--backfill-check")
        if args.host_opt:
            command.extend(["--host-opt", str(args.host_opt)])
        if args.host_llvm_as:
            command.extend(["--host-llvm-as", str(args.host_llvm_as)])
        if args.alive2:
            command.append("--alive2")
        if args.alive2_bin:
            command.extend(["--alive2-bin", str(args.alive2_bin)])
        if args.passes:
            command.extend(["--passes", args.passes])
        plan.append({"stage": "klee", "command": command, "artifacts": {"klee": str(klee_campaign)}})

    if "package" in stages:
        command = [str(args.campaign_packager), "--klee-campaign", str(klee_campaign), "--out", str(package_out)]
        if args.instrumentation_dir or "instrument" in stages:
            command.extend(["--instrumentation", str(instrumentation_dir)])
        plan.append({"stage": "package", "command": command, "artifacts": {"campaign": str(package_out)}})

    if "verify" in stages:
        command = [
            str(args.instrumented_runner),
            "--campaign",
            str(package_out),
            "--llvm-source",
            str(args.llvm_source or ""),
            "--llvm-build",
            str(args.llvm_build or ""),
            "--playbook",
            str(args.playbook),
        ]
        if args.execute_instrumented:
            command.append("--execute")
        if args.allow_dirty:
            command.append("--allow-dirty")
        if args.alive2:
            command.append("--alive2")
        if args.alive2_bin:
            command.extend(["--alive2-bin", str(args.alive2_bin)])
        plan.append({"stage": "verify", "command": command, "artifacts": {"verification": str(package_out)}})

    if args.globalopt_coverage and not feed_globalopt_coverage:
        plan.append(globalopt_stage())

    return plan


def validate(args: argparse.Namespace, stages: list[str]) -> bool:
    ok = True
    for path, label in [
        (args.campaign_driver, "campaign driver"),
        (args.klee_runner, "KLEE runner"),
        (args.campaign_packager, "campaign packager"),
        (args.instrumented_runner, "instrumented campaign runner"),
    ]:
        ok = require_executable(path, label) and ok

    if "instrument" in stages and not args.sources:
        print("instrument stage requires --sources", file=sys.stderr)
        ok = False
    if "verify" in stages:
        ok = require_dir(args.llvm_source, "LLVM source") and ok
        ok = require_dir(args.llvm_build, "LLVM build") and ok
        ok = require_executable(args.playbook, "instrumented playbook") and ok
    if args.alive2_bin:
        ok = require_executable(args.alive2_bin, "Alive2 executable") and ok
    if args.globalopt_coverage and not args.globalopt_runner.is_file():
        print(f"GlobalOpt coverage runner does not exist: {args.globalopt_runner}", file=sys.stderr)
        ok = False
    if (
        (args.globalopt_verify_witness_contracts or args.globalopt_verify_witness_semantics)
        and not args.globalopt_witness_contract_verifier.is_file()
    ):
        print(
            f"GlobalOpt witness contract verifier does not exist: {args.globalopt_witness_contract_verifier}",
            file=sys.stderr,
        )
        ok = False
    if args.globalopt_verify_witness_semantics and args.alive2_bin:
        ok = require_executable(args.alive2_bin, "Alive2 executable") and ok
    if args.verify_predicate_provenance and not args.predicate_provenance_verifier.is_file():
        print(f"Predicate provenance verifier does not exist: {args.predicate_provenance_verifier}", file=sys.stderr)
        ok = False
    return ok


def write_summary(path: Path, args: argparse.Namespace, stages: list[dict[str, Any]]) -> None:
    summary = {
        "out": str(args.out),
        "execute": args.execute,
        "execute_instrumented": args.execute_instrumented,
        "stages": stages,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.require_intent_evidence:
        args.emit_intent_evidence = True
    try:
        stages = selected_stages(args.from_stage, args.to_stage)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not validate(args, stages):
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    planned = build_plan(args, stages)
    command_log = args.out / "workflow-commands.log"
    summary_path = args.out / "workflow-summary.json"
    write_command_log(command_log, planned)

    exit_code = 0
    for stage in planned:
        stage["status"] = "planned"
        if not args.execute:
            print(f"[{stage['stage']}] {command_text(stage['command'])}")
            continue
        completed = run(stage["command"])
        stage["returncode"] = completed.returncode
        stage["status"] = "passed" if completed.returncode == 0 else "failed"
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode != 0:
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            exit_code = completed.returncode
            break

    write_summary(summary_path, args, planned)
    print(f"commands: {command_log}")
    print(f"summary: {summary_path}")
    if (args.out / "klee" / "coverage-summary.json").exists() or "klee" in stages:
        print(f"klee_coverage: {args.out / 'klee' / 'coverage-summary.json'}")
    package_out = args.package_out or (args.out / "verification-campaign")
    if "package" in stages or "verify" in stages:
        print(f"verification_campaign: {package_out}")
        print(f"verification_summary: {package_out / 'verification-summary.json'}")
    globalopt_out = args.out / "globalopt-coverage"
    globalopt_contract_out = args.out / "globalopt-witness-contract"
    predicate_provenance_out = args.out / "predicate-provenance"
    if args.globalopt_coverage:
        print(f"globalopt_coverage: {globalopt_out / 'globalopt-coverage.json'}")
        print(f"globalopt_report: {globalopt_out / 'globalopt-coverage.txt'}")
        print(f"globalopt_baseline: {globalopt_out / 'globalopt-baseline.json'}")
        print(f"globalopt_baseline_diff: {globalopt_out / 'globalopt-baseline-diff.json'}")
        print(f"globalopt_baseline_diff_report: {globalopt_out / 'globalopt-baseline-diff.txt'}")
        print(f"globalopt_witnesses: {globalopt_out / 'witnesses'}")
    if args.globalopt_verify_witness_contracts or args.globalopt_verify_witness_semantics:
        print(f"globalopt_witness_contract_verification: {globalopt_contract_out / 'globalopt-witness-contract-verification.json'}")
        print(f"globalopt_witness_contract_report: {globalopt_contract_out / 'globalopt-witness-contract-verification.txt'}")
    if args.globalopt_verify_witness_semantics:
        print(f"globalopt_witness_contract_alive2: {globalopt_contract_out / 'alive2'}")
    if args.verify_predicate_provenance:
        print(f"predicate_provenance_verification: {predicate_provenance_out / 'predicate-provenance-verification.json'}")
        print(f"predicate_provenance_report: {predicate_provenance_out / 'predicate-provenance-verification.txt'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
