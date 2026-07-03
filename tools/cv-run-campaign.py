#!/usr/bin/env python3
"""Run a O2T mining-to-replay campaign."""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=["discover", "verify"], default="discover")
    parser.add_argument("--passes")
    parser.add_argument("--host-opt", type=Path)
    parser.add_argument("--host-llvm-as", type=Path)
    parser.add_argument("--semantic-clang")
    parser.add_argument("--alive2", action="store_true")
    parser.add_argument("--alive2-bin", type=Path)
    parser.add_argument("--llm-findings", type=Path)
    parser.add_argument("--llm-rejected", type=Path)
    parser.add_argument("--llm-unsupported", type=Path)
    parser.add_argument("--llm-command")
    parser.add_argument("--llm-keep-going", action="store_true")
    parser.add_argument("--strict-constraints", action="store_true")
    parser.add_argument("--require-observed-probes", action="store_true")
    parser.add_argument("--emit-instrumentation", action="store_true")
    parser.add_argument("--instrumentation-dry-run", action="store_true")
    parser.add_argument("--audit-instrumentation", action="store_true")
    parser.add_argument("--recommend-instrumentation", action="store_true")
    parser.add_argument("--repair-instrumentation-candidates", action="store_true")
    parser.add_argument("--retry-repaired-instrumentation", action="store_true")
    parser.add_argument("--require-instrumentation-coverage", action="store_true")
    parser.add_argument("--infer-intents", action="store_true")
    parser.add_argument("--validate-intents", action="store_true")
    parser.add_argument("--require-intent-proof", action="store_true")
    parser.add_argument("--intent-min-confidence", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--intent-emit-smt", action="store_true")
    parser.add_argument("--promote-intents", action="store_true")
    parser.add_argument("--replace-existing-intents", action="store_true")
    parser.add_argument("--require-promotable-intent", action="store_true")
    parser.add_argument("--emit-intent-evidence", action="store_true")
    parser.add_argument("--require-intent-evidence", action="store_true")
    parser.add_argument("--globalopt-coverage", type=Path)
    parser.add_argument("--globalopt-witness-contract-verification", type=Path)
    parser.add_argument("--predicate-provenance-verification", type=Path)
    parser.add_argument("--verify-predicate-provenance", action="store_true")
    parser.add_argument("--require-globalopt-witnesses", action="store_true")
    parser.add_argument("--max-globalopt-witness-failures", type=int)
    parser.add_argument("--audit-intent-coverage", action="store_true")
    parser.add_argument("--guard-semantics", type=Path, default=ROOT / "constraints" / "guard_semantics.json")
    parser.add_argument("--compile-commands", type=Path)
    parser.add_argument("--replay", type=Path, default=ROOT / "build" / "cv-replay")
    parser.add_argument("--reducer", type=Path, default=ROOT / "build" / "cv-reduce-config")
    parser.add_argument("--miner", type=Path, default=ROOT / "tools" / "cv-mine-pass-source.py")
    parser.add_argument("--ast-miner", type=Path)
    parser.add_argument("--instrumenter", type=Path, default=ROOT / "build" / "cv-instrument-pass-source")
    parser.add_argument(
        "--instrumentation-tool",
        type=Path,
        default=ROOT / "tools" / "cv-instrument-llvm-tree.py",
    )
    parser.add_argument("--llm-packer", type=Path, default=ROOT / "tools" / "cv-llm-candidate-pack.py")
    parser.add_argument("--llm-runner", type=Path, default=ROOT / "tools" / "cv-llm-runner.py")
    parser.add_argument("--llm-importer", type=Path, default=ROOT / "tools" / "cv-llm-import-candidates.py")
    parser.add_argument(
        "--constraints-to-configs",
        type=Path,
        default=ROOT / "tools" / "cv-constraints-to-configs.py",
    )
    parser.add_argument("--opt-checker", type=Path, default=ROOT / "scripts" / "opt-check-cases.sh")
    parser.add_argument("--summarizer", type=Path, default=ROOT / "tools" / "cv-summarize-manifest.py")
    parser.add_argument("--llm-reviewer", type=Path, default=ROOT / "tools" / "cv-llm-review-candidates.py")
    parser.add_argument(
        "--instrumentation-auditor",
        type=Path,
        default=ROOT / "tools" / "cv-audit-instrumentation-candidates.py",
    )
    parser.add_argument(
        "--instrumentation-repairer",
        type=Path,
        default=ROOT / "tools" / "cv-repair-instrumentation-candidates.py",
    )
    parser.add_argument(
        "--intent-inferer",
        type=Path,
        default=ROOT / "tools" / "cv-infer-optimization-intent.py",
    )
    parser.add_argument(
        "--intent-validator",
        type=Path,
        default=ROOT / "tools" / "cv-validate-intent-candidates.py",
    )
    parser.add_argument(
        "--intent-promoter",
        type=Path,
        default=ROOT / "tools" / "cv-promote-intent-candidates.py",
    )
    parser.add_argument(
        "--intent-evidence-builder",
        type=Path,
        default=ROOT / "tools" / "cv-build-intent-evidence.py",
    )
    parser.add_argument(
        "--predicate-provenance-verifier",
        type=Path,
        default=ROOT / "tools" / "cv-verify-predicate-provenance.py",
    )
    parser.add_argument(
        "--intent-coverage-auditor",
        type=Path,
        default=ROOT / "tools" / "cv-audit-intent-coverage.py",
    )
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None, stdout=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, stdout=stdout, stderr=subprocess.PIPE, text=True, check=False)


def require_executable(path: Path, label: str) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    print(f"{label} is not executable: {path}", file=sys.stderr)
    return False


def require_command(command: str, label: str) -> bool:
    if Path(command).is_file() and os.access(command, os.X_OK):
        return True
    if shutil.which(command):
        return True
    print(f"{label} is not executable or on PATH: {command}", file=sys.stderr)
    return False


def compile_commands_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    resolved = path.resolve()
    return resolved.parent if resolved.name == "compile_commands.json" else resolved


def write_command_log(path: Path, commands: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for command in commands:
            output.write(" ".join(command) + "\n")


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []
    return [
        record
        for record in (json.loads(line) for line in text.splitlines() if line.strip())
        if isinstance(record, dict)
    ]


def merge_findings(static_path: Path, llm_path: Path | None, output_path: Path) -> None:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for source, path in [("static", static_path), ("llm", llm_path)]:
        if path is None:
            continue
        for record in load_records(path):
            merged = dict(record)
            merged.setdefault("finding_source", source)
            key = (
                str(merged.get("file", "")),
                int(merged.get("line") or 0),
                str(merged.get("marker", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(merged)
    with output_path.open("w", encoding="utf-8") as output:
        json.dump(records, output, indent=2, sort_keys=True)
        output.write("\n")


def counter(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(record.get(key) or "unset") for record in records).items()))


def append_alive2_args(command: list[str], args: argparse.Namespace) -> None:
    if args.alive2:
        command.append("--alive2")
    if args.alive2_bin:
        command.extend(["--alive2-bin", str(args.alive2_bin)])


def summarize_intents(records: list[dict[str, Any]]) -> str:
    marker_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    ready: list[dict[str, Any]] = []
    for record in records:
        marker = str(record.get("marker") or "")
        status = str(record.get("promotion_status") or "candidate")
        marker_counts[marker][status] += 1
        if status == "ready":
            ready.append(record)

    lines = [
        "O2T Intent Summary",
        f"candidates: {len(records)}",
        "Confidence",
    ]
    for key, value in counter(records, "confidence").items():
        lines.append(f"  {key}: {value}")
    lines.append("Proof status")
    for key, value in counter(records, "proof_status").items():
        lines.append(f"  {key}: {value}")
    lines.append("Promotion status")
    for key, value in counter(records, "promotion_status").items():
        lines.append(f"  {key}: {value}")
    lines.append("Markers")
    for marker, counts in sorted(marker_counts.items()):
        rendered = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
        lines.append(f"  {marker}: {rendered}")
    lines.append("Promotion ready")
    if ready:
        for record in ready:
            intent = record.get("intent_candidate", {})
            rewrite = str(intent.get("rewrite", "")) if isinstance(intent, dict) else ""
            lines.append(
                f"  {record.get('marker', '')} {record.get('file', '')}:{record.get('line', '')} rewrite={rewrite}"
            )
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def high_confidence_proof_issues(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record.get("confidence") == "high" and record.get("proof_status") != "proved"
    ]


def main() -> int:
    args = parse_args()
    if args.require_intent_proof:
        args.validate_intents = True
    if args.promote_intents:
        args.validate_intents = True
    if args.require_intent_evidence:
        args.emit_intent_evidence = True
    if args.emit_intent_evidence:
        args.validate_intents = True
    if args.verify_predicate_provenance:
        args.validate_intents = True
    if args.audit_intent_coverage:
        args.validate_intents = True
    if args.validate_intents:
        args.infer_intents = True

    args.out.mkdir(parents=True, exist_ok=True)
    findings = args.out / "findings.json"
    static_findings = args.out / "findings.static.json"
    cases = args.out / "cases"
    summary = args.out / "summary.txt"
    llm_review = args.out / "llm-review.txt"
    llm_prompts = args.out / "llm-prompts.jsonl"
    llm_runner_dir = args.out / "llm-runner"
    generated_llm_findings = args.out / "llm-findings.json"
    generated_llm_rejected = args.out / "llm-rejected.jsonl"
    generated_llm_unsupported = args.out / "llm-unsupported.jsonl"
    instrumentation_dir = args.out / "instrumentation"
    instrumentation_audit = args.out / "instrumentation-audit.json"
    instrumentation_audit_text = args.out / "instrumentation-audit.txt"
    instrumentation_recommendations = args.out / "instrumentation-recommendations.jsonl"
    repaired_findings = args.out / "instrumentation-repaired-findings.json"
    repair_report = args.out / "instrumentation-repair-report.txt"
    repaired_instrumentation_dir = args.out / "instrumentation-repaired"
    intent_candidates = args.out / "intent-candidates.jsonl"
    intent_validated = args.out / "intent-validated.jsonl"
    intent_summary = args.out / "intent-summary.txt"
    intent_smt = args.out / "intent-smt"
    proposed_intents = args.out / "proposed-optimization-intents.json"
    intent_promotion_report = args.out / "intent-promotion-report.txt"
    intent_evidence = args.out / "intent-evidence.jsonl"
    intent_evidence_summary = args.out / "intent-evidence-summary.txt"
    predicate_provenance_verification = args.out / "predicate-provenance-verification.json"
    predicate_provenance_report = args.out / "predicate-provenance-verification.txt"
    intent_coverage = args.out / "intent-coverage.json"
    intent_coverage_report = args.out / "intent-coverage.txt"
    command_log = args.out / "commands.log"
    commands: list[list[str]] = []

    ok = True
    for path, label in [
        (args.miner, "source miner"),
        (args.constraints_to_configs, "constraint converter"),
        (args.opt_checker, "opt checker"),
        (args.summarizer, "manifest summarizer"),
        (args.llm_reviewer, "LLM reviewer"),
        (args.replay, "cv-replay"),
        (args.reducer, "cv-reduce-config"),
    ]:
        ok = require_executable(path, label) and ok
    if args.emit_instrumentation:
        ok = require_executable(args.instrumentation_tool, "instrumentation tool") and ok
        if (
            args.audit_instrumentation
            or args.recommend_instrumentation
            or args.repair_instrumentation_candidates
            or args.retry_repaired_instrumentation
            or args.require_instrumentation_coverage
        ):
            ok = require_executable(args.instrumentation_auditor, "instrumentation auditor") and ok
        if args.repair_instrumentation_candidates or args.retry_repaired_instrumentation:
            ok = require_executable(args.instrumentation_repairer, "instrumentation repairer") and ok
        if not args.instrumentation_dry_run:
            ok = require_executable(args.instrumenter, "source instrumenter") and ok
        if args.compile_commands and not args.compile_commands.exists():
            print(f"compile commands path does not exist: {args.compile_commands}", file=sys.stderr)
            ok = False
    if args.llm_command:
        for path, label in [
            (args.llm_packer, "LLM prompt packer"),
            (args.llm_runner, "LLM runner"),
            (args.llm_importer, "LLM importer"),
        ]:
            ok = require_executable(path, label) and ok
    if args.llm_command and args.llm_findings:
        print("--llm-command cannot be combined with --llm-findings", file=sys.stderr)
        ok = False
    if args.llm_command and (args.llm_rejected or args.llm_unsupported):
        print("--llm-command writes its own rejected/unsupported sidecars", file=sys.stderr)
        ok = False
    if args.host_opt:
        ok = require_executable(args.host_opt, "host opt") and ok
    if args.host_llvm_as:
        ok = require_executable(args.host_llvm_as, "host llvm-as") and ok
    if args.alive2_bin:
        ok = require_executable(args.alive2_bin, "Alive2 executable") and ok
    if args.llm_findings and not args.llm_findings.is_file():
        print(f"LLM findings file does not exist: {args.llm_findings}", file=sys.stderr)
        ok = False
    if args.llm_rejected and not args.llm_rejected.is_file():
        print(f"LLM rejected file does not exist: {args.llm_rejected}", file=sys.stderr)
        ok = False
    if args.llm_unsupported and not args.llm_unsupported.is_file():
        print(f"LLM unsupported file does not exist: {args.llm_unsupported}", file=sys.stderr)
        ok = False
    if args.ast_miner:
        ok = require_executable(args.ast_miner, "AST source miner") and ok
    if args.infer_intents:
        ok = require_executable(args.intent_inferer, "intent inferer") and ok
    if args.validate_intents:
        ok = require_executable(args.intent_validator, "intent validator") and ok
        ok = require_command(args.z3, "z3") and ok
    if args.promote_intents:
        ok = require_executable(args.intent_promoter, "intent promoter") and ok
    if args.emit_intent_evidence:
        ok = require_executable(args.intent_evidence_builder, "intent evidence builder") and ok
    if args.verify_predicate_provenance:
        ok = require_executable(args.predicate_provenance_verifier, "predicate provenance verifier") and ok
    if args.audit_intent_coverage:
        ok = require_executable(args.intent_coverage_auditor, "intent coverage auditor") and ok
    if not ok:
        return 2

    if args.ast_miner:
        mine_command = [
            str(args.ast_miner),
            "--format",
            "json",
            "--registry",
            str(ROOT / "constraints" / "pass_constraints.json"),
            "--guard-semantics",
            str(args.guard_semantics),
        ]
        compile_dir = compile_commands_dir(args.compile_commands)
        if compile_dir:
            mine_command.extend(["-p", str(compile_dir)])
        mine_command.extend(str(source) for source in args.sources)
        if not compile_dir:
            mine_command.extend(["--", "-std=c++17", f"-I{ROOT / 'include'}"])
    else:
        mine_command = [str(args.miner), *[str(source) for source in args.sources]]
    commands.append(mine_command)
    with static_findings.open("w", encoding="utf-8") as output:
        mined = run(mine_command, stdout=output)
    if mined.returncode != 0:
        print(mined.stderr, file=sys.stderr, end="")
        return mined.returncode

    effective_llm_findings = args.llm_findings
    effective_llm_rejected = args.llm_rejected
    effective_llm_unsupported = args.llm_unsupported
    if args.llm_command:
        pack_command = [
            str(args.llm_packer),
            "--out",
            str(llm_prompts),
            *[str(source) for source in args.sources],
        ]
        commands.append(pack_command)
        packed = run(pack_command)
        if packed.returncode != 0:
            print(packed.stderr, file=sys.stderr, end="")
            return packed.returncode

        runner_command = [
            str(args.llm_runner),
            "--prompts",
            str(llm_prompts),
            "--out-dir",
            str(llm_runner_dir),
            "--command",
            args.llm_command,
        ]
        if args.llm_keep_going:
            runner_command.append("--keep-going")
        commands.append(runner_command)
        ran_llm = run(runner_command)
        if ran_llm.returncode != 0:
            print(ran_llm.stderr, file=sys.stderr, end="")
            return ran_llm.returncode

        import_command = [
            str(args.llm_importer),
            "--input",
            str(llm_runner_dir / "responses.jsonl"),
            "--out",
            str(generated_llm_findings),
            "--rejected-out",
            str(generated_llm_rejected),
            "--unsupported-out",
            str(generated_llm_unsupported),
        ]
        commands.append(import_command)
        imported = run(import_command)
        if imported.returncode != 0:
            print(imported.stderr, file=sys.stderr, end="")
            return imported.returncode
        effective_llm_findings = generated_llm_findings
        effective_llm_rejected = generated_llm_rejected
        effective_llm_unsupported = generated_llm_unsupported

    merge_findings(static_findings, effective_llm_findings, findings)

    if args.infer_intents:
        infer_command = [
            str(args.intent_inferer),
            "--findings",
            str(findings),
            "--out",
            str(intent_candidates),
            "--format",
            "jsonl",
            "--min-confidence",
            args.intent_min_confidence,
        ]
        commands.append(infer_command)
        inferred = run(infer_command)
        if inferred.stdout:
            print(inferred.stdout, end="")
        if inferred.returncode != 0:
            print(inferred.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return inferred.returncode

        intent_records = load_records(intent_candidates)
        intent_summary.write_text(summarize_intents(intent_records), encoding="utf-8")

    if args.validate_intents:
        validate_command = [
            str(args.intent_validator),
            "--input",
            str(intent_candidates),
            "--out",
            str(intent_validated),
            "--z3",
            args.z3,
        ]
        if args.intent_emit_smt:
            validate_command.extend(["--emit-smt", str(intent_smt)])
        commands.append(validate_command)
        validated = run(validate_command)
        if validated.stdout:
            print(validated.stdout, end="")
        if validated.returncode != 0 and not intent_validated.exists():
            print(validated.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return validated.returncode

        validated_records = load_records(intent_validated)
        intent_summary.write_text(summarize_intents(validated_records), encoding="utf-8")
        issues = high_confidence_proof_issues(validated_records)
        if args.require_intent_proof and issues:
            print(f"intent proof issues: {len(issues)}", file=sys.stderr)
            write_command_log(command_log, commands)
            return 1

    if args.verify_predicate_provenance:
        predicate_command = [
            str(args.predicate_provenance_verifier),
            "--input",
            str(intent_validated),
            "--out",
            str(predicate_provenance_verification),
            "--report",
            str(predicate_provenance_report),
            "--require-clean",
        ]
        commands.append(predicate_command)
        verified_predicate = run(predicate_command)
        if verified_predicate.stdout:
            print(verified_predicate.stdout, end="")
        if verified_predicate.returncode != 0:
            print(verified_predicate.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return verified_predicate.returncode

    if args.audit_intent_coverage:
        coverage_command = [
            str(args.intent_coverage_auditor),
            "--validated",
            str(intent_validated),
            "--intent-registry",
            str(ROOT / "constraints" / "optimization_intents.json"),
            "--semantic-facts",
            str(ROOT / "constraints" / "semantic_facts.json"),
            "--guard-semantics",
            str(args.guard_semantics),
            "--out",
            str(intent_coverage),
            "--report",
            str(intent_coverage_report),
        ]
        commands.append(coverage_command)
        audited_coverage = run(coverage_command)
        if audited_coverage.stdout:
            print(audited_coverage.stdout, end="")
        if audited_coverage.returncode != 0:
            print(audited_coverage.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return audited_coverage.returncode

    if args.promote_intents and not args.emit_intent_evidence:
        promote_command = [
            str(args.intent_promoter),
            "--validated",
            str(intent_validated),
            "--out",
            str(proposed_intents),
            "--report",
            str(intent_promotion_report),
        ]
        if args.replace_existing_intents:
            promote_command.append("--replace-existing")
        if args.require_promotable_intent:
            promote_command.append("--require-ready")
        commands.append(promote_command)
        promoted = run(promote_command)
        if promoted.stdout:
            print(promoted.stdout, end="")
        if promoted.returncode != 0:
            print(promoted.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return promoted.returncode

    if args.emit_instrumentation:
        instrumentation_command = [
            str(args.instrumentation_tool),
            "--out-dir",
            str(instrumentation_dir),
            "--llm-findings",
            str(findings),
        ]
        if args.instrumentation_dry_run:
            instrumentation_command.append("--dry-run")
        else:
            instrumentation_command.extend(["--instrumenter", str(args.instrumenter)])
        if args.compile_commands:
            instrumentation_command.extend(["--compile-commands", str(args.compile_commands)])
        if args.passes:
            instrumentation_command.extend(["--passes", args.passes])
        instrumentation_command.extend(str(source) for source in args.sources)
        commands.append(instrumentation_command)
        instrumented = run(instrumentation_command)
        if instrumented.returncode != 0:
            print(instrumented.stderr, file=sys.stderr, end="")
            return instrumented.returncode

        if (
            args.audit_instrumentation
            or args.recommend_instrumentation
            or args.repair_instrumentation_candidates
            or args.retry_repaired_instrumentation
            or args.require_instrumentation_coverage
        ):
            audit_command = [
                str(args.instrumentation_auditor),
                "--findings",
                str(findings),
                "--manifest",
                str(instrumentation_dir / "instrumentation-manifest.jsonl"),
                "--patch",
                str(instrumentation_dir / "instrumentation.patch"),
                "--out",
                str(instrumentation_audit),
                "--text-out",
                str(instrumentation_audit_text),
            ]
            if (
                args.recommend_instrumentation
                or args.repair_instrumentation_candidates
                or args.retry_repaired_instrumentation
            ):
                audit_command.extend(["--recommendations-out", str(instrumentation_recommendations)])
            if args.require_instrumentation_coverage:
                audit_command.append("--require-coverage")
            commands.append(audit_command)
            audited = run(audit_command)
            if audited.stdout:
                print(audited.stdout, end="")
            if audited.returncode != 0:
                print(audited.stderr, file=sys.stderr, end="")
                write_command_log(command_log, commands)
                return audited.returncode

        if args.repair_instrumentation_candidates or args.retry_repaired_instrumentation:
            repair_command = [
                str(args.instrumentation_repairer),
                "--findings",
                str(findings),
                "--recommendations",
                str(instrumentation_recommendations),
                "--out",
                str(repaired_findings),
                "--report-out",
                str(repair_report),
            ]
            commands.append(repair_command)
            repaired = run(repair_command)
            if repaired.stdout:
                print(repaired.stdout, end="")
            if repaired.returncode != 0:
                print(repaired.stderr, file=sys.stderr, end="")
                write_command_log(command_log, commands)
                return repaired.returncode

        if args.retry_repaired_instrumentation:
            retry_command = [
                str(args.instrumentation_tool),
                "--out-dir",
                str(repaired_instrumentation_dir),
                "--llm-findings",
                str(repaired_findings),
            ]
            if args.instrumentation_dry_run:
                retry_command.append("--dry-run")
            else:
                retry_command.extend(["--instrumenter", str(args.instrumenter)])
            if args.compile_commands:
                retry_command.extend(["--compile-commands", str(args.compile_commands)])
            if args.passes:
                retry_command.extend(["--passes", args.passes])
            retry_command.extend(str(source) for source in args.sources)
            commands.append(retry_command)
            retried = run(retry_command)
            if retried.returncode != 0:
                print(retried.stderr, file=sys.stderr, end="")
                write_command_log(command_log, commands)
                return retried.returncode

    convert_command = [
        str(args.constraints_to_configs),
        "--input",
        str(findings),
        "--out-dir",
        str(cases),
        "--replay",
        str(args.replay),
        "--reducer",
        str(args.reducer),
    ]
    if args.strict_constraints:
        convert_command.append("--strict")
    commands.append(convert_command)
    converted = run(convert_command)
    if converted.returncode != 0:
        print(converted.stderr, file=sys.stderr, end="")
        return converted.returncode

    check_command = [str(args.opt_checker)]
    if args.require_observed_probes:
        check_command.append("--require-observed-probes")
    append_alive2_args(check_command, args)
    check_command.append(str(cases))
    if args.passes:
        check_command.append(args.passes)
    commands.append(check_command)
    env = os.environ.copy()
    if args.host_opt:
        env["O2T_HOST_OPT"] = str(args.host_opt)
        env["COMPILERVERIF_HOST_OPT"] = str(args.host_opt)
    if args.host_llvm_as:
        env["O2T_HOST_LLVM_AS"] = str(args.host_llvm_as)
        env["COMPILERVERIF_HOST_LLVM_AS"] = str(args.host_llvm_as)
    if args.semantic_clang:
        env["O2T_SEMANTIC_CLANG"] = args.semantic_clang
        env["COMPILERVERIF_SEMANTIC_CLANG"] = args.semantic_clang

    checked = run(check_command, env=env)
    if checked.returncode != 0:
        print(checked.stderr, file=sys.stderr, end="")
        if args.mode == "verify":
            write_command_log(command_log, commands)
            manifest = cases / "opt" / "manifest.jsonl"
            if manifest.exists():
                run([str(args.summarizer), str(manifest), "--out", str(summary)])
            return checked.returncode

    manifest = cases / "opt" / "manifest.jsonl"
    summarize_command = [str(args.summarizer), str(manifest), "--out", str(summary)]
    commands.append(summarize_command)
    summarized = run(summarize_command)
    if summarized.returncode != 0:
        print(summarized.stderr, file=sys.stderr, end="")
        return summarized.returncode

    if effective_llm_findings and (effective_llm_rejected or effective_llm_unsupported):
        review_command = [
            str(args.llm_reviewer),
            "--static-findings",
            str(static_findings),
            "--llm-findings",
            str(effective_llm_findings),
            "--cases-manifest",
            str(manifest),
            "--out",
            str(llm_review),
        ]
        if effective_llm_rejected:
            review_command.extend(["--rejected", str(effective_llm_rejected)])
        if effective_llm_unsupported:
            review_command.extend(["--unsupported", str(effective_llm_unsupported)])
        commands.append(review_command)
        reviewed = run(review_command)
        if reviewed.returncode != 0:
            print(reviewed.stderr, file=sys.stderr, end="")
            return reviewed.returncode

    if args.emit_intent_evidence:
        evidence_command = [
            str(args.intent_evidence_builder),
            "--validated",
            str(intent_validated),
            "--opt-manifest",
            str(manifest),
            "--intents",
            str(ROOT / "constraints" / "optimization_intents.json"),
            "--out",
            str(intent_evidence),
            "--report",
            str(intent_evidence_summary),
        ]
        if args.require_intent_evidence:
            evidence_command.append("--require-clean")
        if args.globalopt_coverage:
            evidence_command.extend(["--globalopt-coverage", str(args.globalopt_coverage)])
        if args.globalopt_witness_contract_verification:
            evidence_command.extend([
                "--globalopt-witness-contract-verification",
                str(args.globalopt_witness_contract_verification),
            ])
        if args.predicate_provenance_verification:
            evidence_command.extend([
                "--predicate-provenance-verification",
                str(args.predicate_provenance_verification),
            ])
        if args.verify_predicate_provenance:
            evidence_command.extend([
                "--predicate-provenance-verification",
                str(predicate_provenance_verification),
            ])
        if args.require_globalopt_witnesses:
            evidence_command.append("--require-globalopt-witnesses")
        if args.max_globalopt_witness_failures is not None:
            evidence_command.extend([
                "--max-globalopt-witness-failures",
                str(args.max_globalopt_witness_failures),
            ])
        commands.append(evidence_command)
        built_evidence = run(evidence_command)
        if built_evidence.stdout:
            print(built_evidence.stdout, end="")
        if built_evidence.returncode != 0:
            print(built_evidence.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return built_evidence.returncode

        if args.promote_intents:
            promote_command = [
                str(args.intent_promoter),
                "--validated",
                str(intent_validated),
                "--evidence",
                str(intent_evidence),
                "--out",
                str(proposed_intents),
                "--report",
                str(intent_promotion_report),
            ]
            if args.replace_existing_intents:
                promote_command.append("--replace-existing")
            if args.require_promotable_intent:
                promote_command.extend(["--require-ready", "--require-verified-evidence"])
            commands.append(promote_command)
            promoted = run(promote_command)
            if promoted.stdout:
                print(promoted.stdout, end="")
            if promoted.returncode != 0:
                print(promoted.stderr, file=sys.stderr, end="")
                write_command_log(command_log, commands)
                return promoted.returncode

    write_command_log(command_log, commands)
    print(f"findings: {findings}")
    print(f"cases: {cases}")
    print(f"manifest: {manifest}")
    print(f"summary: {summary}")
    if llm_review.exists():
        print(f"llm_review: {llm_review}")
    instrumentation_patch = instrumentation_dir / "instrumentation.patch"
    if instrumentation_patch.exists():
        print(f"instrumentation_patch: {instrumentation_patch}")
    if instrumentation_audit.exists():
        print(f"instrumentation_audit: {instrumentation_audit}")
    if instrumentation_recommendations.exists():
        print(f"instrumentation_recommendations: {instrumentation_recommendations}")
    if repaired_findings.exists():
        print(f"instrumentation_repaired_findings: {repaired_findings}")
    if repair_report.exists():
        print(f"instrumentation_repair_report: {repair_report}")
    repaired_patch = repaired_instrumentation_dir / "instrumentation.patch"
    if repaired_patch.exists():
        print(f"instrumentation_repaired_patch: {repaired_patch}")
    if intent_candidates.exists():
        print(f"intent_candidates: {intent_candidates}")
    if intent_validated.exists():
        print(f"intent_validated: {intent_validated}")
    if intent_summary.exists():
        print(f"intent_summary: {intent_summary}")
    if predicate_provenance_verification.exists():
        print(f"predicate_provenance_verification: {predicate_provenance_verification}")
    if predicate_provenance_report.exists():
        print(f"predicate_provenance_report: {predicate_provenance_report}")
    if proposed_intents.exists():
        print(f"proposed_intents: {proposed_intents}")
    if intent_promotion_report.exists():
        print(f"intent_promotion_report: {intent_promotion_report}")
    if intent_evidence.exists():
        print(f"intent_evidence: {intent_evidence}")
    if intent_evidence_summary.exists():
        print(f"intent_evidence_summary: {intent_evidence_summary}")
    if intent_coverage.exists():
        print(f"intent_coverage: {intent_coverage}")
    if intent_coverage_report.exists():
        print(f"intent_coverage_report: {intent_coverage_report}")
    if checked.returncode != 0:
        print("opt check failed; discover mode kept campaign artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
