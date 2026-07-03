#!/usr/bin/env python3
"""Run O2T pass-source audit on an external LLVM-pass source tree."""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--compile-commands", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--passes")
    parser.add_argument("--marker", action="append", default=[])
    parser.add_argument("--marker-prefix", action="append", default=[])
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--mine-pass-impl-ir", action="store_true")
    parser.add_argument("--modelcheck-intents", action="store_true")
    parser.add_argument("--modelcheck-engine", choices=["auto", "cbmc", "esbmc"], default="auto")
    parser.add_argument("--modelcheck-unwind", type=int)
    parser.add_argument("--modelcheck-timeout", type=int)
    parser.add_argument("--modelcheck-widths")
    parser.add_argument("--pass-impl-ir-slice-window", type=int)
    parser.add_argument("--require-clean-mining", action="store_true")
    parser.add_argument("--ast-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-source-ast")
    parser.add_argument("--ir-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-impl-ir")
    parser.add_argument("--z3", default="z3")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--min-proved", type=int)
    parser.add_argument("--max-unsupported", type=int)
    parser.add_argument("--max-proof-failures", type=int)
    parser.add_argument("--max-mining-errors", type=int)
    parser.add_argument("--max-new-unsupported", type=int)
    parser.add_argument("--max-new-fallback-transactions", type=int)
    parser.add_argument("--max-incomplete-formal-provenance", type=int)
    parser.add_argument("--max-modelcheck-refuted", type=int)
    parser.add_argument("--max-modelcheck-errors", type=int)
    parser.add_argument("--max-new-modelcheck-refuted", type=int)
    parser.add_argument("--max-new-modelcheck-errors", type=int)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_compile_commands(path: Path) -> tuple[bool, str]:
    db_path = path / "compile_commands.json" if path.is_dir() else path
    if not db_path.is_file():
        return False, f"compile commands path does not exist: {path}"
    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"compile commands JSON is invalid: {exc}"
    except OSError as exc:
        return False, str(exc)
    if not isinstance(data, list):
        return False, "compile_commands.json must contain an array"
    return True, ""


def append_optional(command: list[str], name: str, value: Any) -> None:
    if value is not None:
        command.extend([name, str(value)])


def audit_command(args: argparse.Namespace, audit_out: Path) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "cv-run-pass-source-audit.py"),
        "--compile-commands",
        str(args.compile_commands),
        "--out",
        str(audit_out),
        "--ast-miner",
        str(args.ast_miner),
        "--ir-miner",
        str(args.ir_miner),
        "--z3",
        str(args.z3),
    ]
    append_optional(command, "--passes", args.passes)
    append_optional(command, "--baseline", args.baseline)
    append_optional(command, "--pass-impl-ir-slice-window", args.pass_impl_ir_slice_window)
    for value in args.marker:
        command.extend(["--marker", value])
    for value in args.marker_prefix:
        command.extend(["--marker-prefix", value])
    for value in args.include:
        command.extend(["--include", value])
    for value in args.exclude:
        command.extend(["--exclude", value])
    for flag in ("mine_pass_impl_ir", "require_clean_mining"):
        if getattr(args, flag):
            command.append("--" + flag.replace("_", "-"))
    if args.modelcheck_intents:
        command.append("--modelcheck-intents")
        command.extend(["--modelcheck-engine", args.modelcheck_engine])
    append_optional(command, "--modelcheck-unwind", args.modelcheck_unwind)
    append_optional(command, "--modelcheck-timeout", args.modelcheck_timeout)
    append_optional(command, "--modelcheck-widths", args.modelcheck_widths)
    for name in (
        "min_proved",
        "max_unsupported",
        "max_proof_failures",
        "max_mining_errors",
        "max_new_unsupported",
        "max_new_fallback_transactions",
        "max_incomplete_formal_provenance",
        "max_modelcheck_refuted",
        "max_modelcheck_errors",
        "max_new_modelcheck_refuted",
        "max_new_modelcheck_errors",
    ):
        append_optional(command, "--" + name.replace("_", "-"), getattr(args, name))
    command.extend(str(source) for source in args.sources)
    return command


def top_reasons(reasons: dict[str, Any], limit: int = 10) -> dict[str, int]:
    counter = collections.Counter({str(key): int(value) for key, value in reasons.items()})
    return dict(counter.most_common(limit))


def modelcheck_findings(modelcheck: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    raw = modelcheck.get("findings")
    findings = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return findings[:limit] if limit is not None else findings


def modelcheck_finding_lines(modelcheck: dict[str, Any], limit: int = 5) -> list[str]:
    findings = modelcheck_findings(modelcheck)
    if not findings:
        return ["  none"]
    lines = [format_modelcheck_finding(item) for item in findings[:limit]]
    omitted = len(findings) - limit
    if omitted > 0:
        lines.append(f"  ... {omitted} more")
    return lines


def modelcheck_component_lines(modelcheck: dict[str, Any]) -> list[str]:
    raw = modelcheck.get("components")
    components = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if not components:
        return ["  none"]
    return [
        "  "
        + " ".join(
            [
                str(component.get("source_kind") or "component") + ":",
                f"records={int(component.get('records') or 0)}",
                f"generated={int(component.get('generated') or 0)}",
                f"proved={int(component.get('proved') or 0)}",
                f"refuted={int(component.get('refuted') or 0)}",
                f"unsupported={int(component.get('unsupported') or 0)}",
                f"skipped={int(component.get('skipped') or 0)}",
                f"error={int(component.get('error') or 0)}",
                "selected=" + selected_widths_label(component),
            ]
        )
        for component in components
    ]


def selected_widths_label(record: dict[str, Any]) -> str:
    selected = record.get("selected_widths")
    if isinstance(selected, list) and selected:
        return ",".join(str(item) for item in selected)
    width_mode = str(record.get("width_mode") or "").strip()
    return "native" if width_mode in {"", "native"} else "none"


def modelcheck_width_lines(modelcheck: dict[str, Any]) -> list[str]:
    widths = modelcheck.get("widths")
    width_counts = widths if isinstance(widths, dict) else {}
    lines = ["  selected=" + selected_widths_label(modelcheck)]
    if not width_counts:
        lines.append("  none")
        return lines
    statuses = ("proved", "refuted", "unsupported", "skipped", "error")
    for width in sorted(
        width_counts,
        key=lambda value: (str(value) == "none", int(value) if str(value).isdigit() else str(value)),
    ):
        counts = width_counts.get(width)
        if not isinstance(counts, dict):
            continue
        lines.append(
            "  "
            + str(width)
            + ": "
            + " ".join(f"{status}={int(counts.get(status) or 0)}" for status in statuses)
        )
    return lines


def format_modelcheck_finding(item: dict[str, Any]) -> str:
    location = ""
    file = str(item.get("file") or "")
    line = int(item.get("line") or 0)
    if file or line:
        location = f" {file}:{line}"
    width = int(item.get("width") or 0)
    width_text = f" @{width}b" if width else ""
    domain = str(item.get("domain") or "")
    domain_text = f" {domain}" if domain else ""
    source_function = str(item.get("source_function") or "")
    function_text = f" {source_function}" if source_function else ""
    reason = str(item.get("reason") or "")
    suffix = f" ({reason})" if reason else ""
    return (
        f"  {str(item.get('status') or 'unknown')}:"
        f"{width_text}{domain_text} {str(item.get('marker') or 'record')}"
        f"{function_text}{location}{suffix}"
    )


def build_summary(out: Path, audit_out: Path, exit_code: int, stderr: str) -> dict[str, Any]:
    run_summary_path = audit_out / "run-summary.json"
    readiness_path = audit_out / "real-pass-readiness.json"
    run_summary = load_json(run_summary_path) if run_summary_path.is_file() else {}
    readiness = load_json(readiness_path) if readiness_path.is_file() else {}

    sources = run_summary.get("sources") if isinstance(run_summary.get("sources"), dict) else {}
    findings = run_summary.get("findings") if isinstance(run_summary.get("findings"), dict) else {}
    intents = run_summary.get("intents") if isinstance(run_summary.get("intents"), dict) else {}
    pass_impl_ir = run_summary.get("pass_impl_ir") if isinstance(run_summary.get("pass_impl_ir"), dict) else {}
    modelcheck = run_summary.get("modelcheck") if isinstance(run_summary.get("modelcheck"), dict) else {}
    coverage = run_summary.get("coverage") if isinstance(run_summary.get("coverage"), dict) else {}
    return {
        "model": "o2t-external-pass-audit-summary-v1",
        "audit_exit_code": exit_code,
        "audit_out": str(audit_out),
        "audit_stderr": stderr.strip(),
        "artifacts": {
            "run_summary": str(run_summary_path) if run_summary_path.is_file() else "",
            "real_pass_readiness": str(readiness_path) if readiness_path.is_file() else "",
            "findings": str(audit_out / "findings.json") if (audit_out / "findings.json").is_file() else "",
            "source_manifest": str(audit_out / "source-manifest.jsonl") if (audit_out / "source-manifest.jsonl").is_file() else "",
            "modelcheck_intents": str(modelcheck.get("summary") or ""),
        },
        "sources": sources,
        "findings": findings,
        "intents": intents,
        "pass_impl_ir": pass_impl_ir,
        "modelcheck": modelcheck,
        "coverage": {
            "recommendations": (coverage.get("recommendations") if isinstance(coverage.get("recommendations"), dict) else {}),
            "next_modeling_target": str(coverage.get("next_modeling_target") or ""),
            "source_program_graph_contract": (
                coverage.get("source_program_graph_contract")
                if isinstance(coverage.get("source_program_graph_contract"), dict)
                else {}
            ),
        },
        "source_reasons": top_reasons(sources.get("reasons", {}) if isinstance(sources.get("reasons"), dict) else {}),
        "budget_violations": run_summary.get("budget_violations", []),
        "readiness_diagnostics": readiness.get("diagnostics", {}) if isinstance(readiness.get("diagnostics"), dict) else {},
    }


def format_summary(summary: dict[str, Any]) -> str:
    sources = summary.get("sources") if isinstance(summary.get("sources"), dict) else {}
    findings = summary.get("findings") if isinstance(summary.get("findings"), dict) else {}
    intents = summary.get("intents") if isinstance(summary.get("intents"), dict) else {}
    pass_impl_ir = summary.get("pass_impl_ir") if isinstance(summary.get("pass_impl_ir"), dict) else {}
    modelcheck = summary.get("modelcheck") if isinstance(summary.get("modelcheck"), dict) else {}
    lines = [
        "O2T External Pass Audit Summary",
        f"audit_exit_code: {int(summary.get('audit_exit_code') or 0)}",
        f"audit_out: {summary.get('audit_out') or ''}",
        f"sources: selected={int(sources.get('selected') or 0)} skipped={int(sources.get('skipped') or 0)} errors={int(sources.get('errors') or 0)}",
        f"findings: {int(findings.get('total') or 0)}",
        f"intents: {int(intents.get('total') or 0)}",
        "Findings by pass",
    ]
    by_pass = findings.get("by_pass") if isinstance(findings.get("by_pass"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(by_pass.items())) if by_pass else lines.append("  none")
    lines.append("Findings by marker")
    by_marker = findings.get("by_marker") if isinstance(findings.get("by_marker"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(by_marker.items())) if by_marker else lines.append("  none")
    lines.append("Intent proof status")
    proof_status = intents.get("proof_status") if isinstance(intents.get("proof_status"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(proof_status.items())) if proof_status else lines.append("  none")
    lines.append("Pass implementation IR intent checks")
    intent_status = pass_impl_ir.get("intent_check_status") if isinstance(pass_impl_ir.get("intent_check_status"), dict) else {}
    lines.extend(f"  {key}: {value}" for key, value in sorted(intent_status.items())) if intent_status else lines.append("  none")
    if modelcheck.get("enabled"):
        lines.append(
            "Modelcheck intents: "
            + " ".join(
                [
                    f"generated={int(modelcheck.get('generated') or 0)}",
                    f"proved={int(modelcheck.get('proved') or 0)}",
                    f"refuted={int(modelcheck.get('refuted') or 0)}",
                    f"unsupported={int(modelcheck.get('unsupported') or 0)}",
                    f"skipped={int(modelcheck.get('skipped') or 0)}",
                    f"error={int(modelcheck.get('error') or 0)}",
                ]
            )
        )
        lines.append("Modelcheck components")
        lines.extend(modelcheck_component_lines(modelcheck))
        lines.append("Modelcheck widths")
        lines.extend(modelcheck_width_lines(modelcheck))
        lines.append("Modelcheck findings")
        lines.extend(modelcheck_finding_lines(modelcheck, 5))
    coverage = summary.get("coverage") if isinstance(summary.get("coverage"), dict) else {}
    source_program_graph = (
        coverage.get("source_program_graph_contract")
        if isinstance(coverage.get("source_program_graph_contract"), dict)
        else {}
    )
    if source_program_graph:
        lines.append("Source program graph contract")
        status = source_program_graph.get("status") if isinstance(source_program_graph.get("status"), dict) else {}
        lines.extend(f"  {key}: {value}" for key, value in sorted(status.items())) if status else lines.append("  none")
        gaps = source_program_graph.get("gaps") if isinstance(source_program_graph.get("gaps"), dict) else {}
        next_target = str(gaps.get("next_modeling_target") or "")
        if next_target:
            lines.append(f"  next_modeling_target: {next_target}")
    reasons = summary.get("source_reasons") if isinstance(summary.get("source_reasons"), dict) else {}
    if reasons:
        lines.append("Top source skip/error reasons")
        lines.extend(f"  {key}: {value}" for key, value in reasons.items())
    violations = summary.get("budget_violations") if isinstance(summary.get("budget_violations"), list) else []
    lines.append(f"budget_violations: {len(violations)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    ok, message = validate_compile_commands(args.compile_commands)
    audit_out = args.out / "audit"
    if not ok:
        summary = {
            "model": "o2t-external-pass-audit-summary-v1",
            "audit_exit_code": 2,
            "audit_out": str(audit_out),
            "audit_stderr": message,
            "sources": {},
            "findings": {},
            "intents": {},
            "pass_impl_ir": {},
            "coverage": {},
            "source_reasons": {},
            "budget_violations": [],
            "readiness_diagnostics": {},
        }
        write_json(args.out / "external-pass-audit-summary.json", summary)
        (args.out / "external-pass-audit-summary.txt").write_text(format_summary(summary), encoding="utf-8")
        print(message, file=sys.stderr)
        return 2

    result = subprocess.run(audit_command(args, audit_out), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    summary = build_summary(args.out, audit_out, result.returncode, result.stderr)
    write_json(args.out / "external-pass-audit-summary.json", summary)
    (args.out / "external-pass-audit-summary.txt").write_text(format_summary(summary), encoding="utf-8")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
