#!/usr/bin/env python3
"""O2T front door: hand it LLVM pass sources, it classifies each pass and schedules checks.

For each input (a pass `.cpp` and/or a pass name), O2T:
  1. CLASSIFIES the transform family from the source idioms (+ name hint) -- loop-scev,
     peephole, memory-dse, global, cfg, vectorize-slp, ...;
  2. PLANS the verification strategies that family supports, marking each feasible (its tool
     and prerequisites are present) or skipped-with-reason;
  3. DISPATCHES the feasible checks to the real O2T verifiers and aggregates the verdicts.

Optionally an LLM "brain" (`--llm-command`, provider-agnostic) breaks ties when the
feature-based classifier is ambiguous; the deterministic result is always the default.

Examples:
  cv-orchestrate.py --source pass1.cpp pass2.cpp
  cv-orchestrate.py --source /path/to/Transforms --include Vendor --no-execute
  cv-orchestrate.py --pass indvars --pass instcombine
  cv-orchestrate.py --source mypass.cpp --pass dse --report out.json --fail-on-refuted
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from o2t.orchestrate.run import resolve_context, orchestrate  # noqa: E402
from o2t.orchestrate.brain import maybe_llm_classify  # noqa: E402
from o2t.orchestrate.classify import FAMILIES  # noqa: E402

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc"}
POSITIVE_VERDICTS = {"proved", "sound", "validated"}
NEGATIVE_VERDICTS = {"refuted", "miscompile"}
FAMILY_STRATEGIES = {family.name: set(family.strategies) for family in FAMILIES}


def _selftest(args) -> dict:
    fx = ROOT / "tests" / "fixtures"
    inputs = [
        {"source": str(fx / "loop_pass_scev.cpp"), "pass_name": None},
        {"source": str(fx / "intent_inference_snippet.cpp"), "pass_name": "instcombine"},
        {"source": str(fx / "third_party_dse_like_pass.cpp"), "pass_name": "dse"},
        {"source": None, "pass_name": "indvars"},
    ]
    ctx = resolve_context(args.z3_bin, args.opt_bin, args.clang_bin, ast_miner=args.ast_miner)
    return orchestrate(inputs, ctx)


def _selected(path: Path, includes: list[str], excludes: list[str]) -> bool:
    text = str(path)
    if includes and not any(pattern in text for pattern in includes):
        return False
    return not any(pattern in text for pattern in excludes)


def _expand_sources(paths: list[Path], includes: list[str], excludes: list[str]) -> list[Path]:
    """Expand source files and directories into deterministic pass-source inputs."""
    expanded: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = raw.resolve()
        candidates: list[Path]
        if path.is_dir():
            candidates = sorted(
                p.resolve() for p in path.rglob("*")
                if p.is_file() and p.suffix in SOURCE_SUFFIXES
            )
        else:
            candidates = [path]
        for candidate in candidates:
            if candidate in seen or not _selected(candidate, includes, excludes):
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


def _pair_inputs(sources: list[Path], passes: list[str]) -> list[dict]:
    # Preserve the historical positional pairing. A single pass name is broadcast across
    # several expanded sources because that is the common "audit this tree as pass X" case.
    if len(passes) == 1 and len(sources) > 1:
        names: list[str | None] = [passes[0]] * len(sources)
        paired_sources: list[Path | None] = list(sources)
    else:
        n = max(len(sources), len(passes))
        paired_sources = list(sources) + [None] * (n - len(sources))
        names = list(passes) + [None] * (n - len(passes))
    return [{"source": str(s) if s else None, "pass_name": p} for s, p in zip(paired_sources, names)]


def _primary_check_names(item: dict, checks: list[dict]) -> list[str]:
    primary = item.get("primary_family")
    owned = FAMILY_STRATEGIES.get(primary, set())
    return [str(check.get("strategy") or "") for check in checks if check.get("strategy") in owned]


def _headline_for_pass(item: dict) -> dict:
    """Collapse the primary-family checks for one source into an audit headline."""
    primary = item.get("primary_family")
    if not primary:
        return {"status": "unclassified", "reason": "no family matched", "primary_checks": []}
    raw_checks = item.get("checks")
    plan_only = raw_checks is None
    checks = raw_checks if isinstance(raw_checks, list) else item.get("planned_checks", [])
    primary_checks = [
        check for check in checks
        if check.get("strategy") in FAMILY_STRATEGIES.get(primary, set())
    ]
    names = _primary_check_names(item, primary_checks)
    if not primary_checks:
        return {"status": "advisory", "reason": "no primary-family checks planned", "primary_checks": []}
    if plan_only:
        feasible = [check for check in primary_checks if check.get("feasible", True)]
        return {
            "status": "planned" if feasible else "skipped",
            "reason": "" if feasible else "all primary-family checks skipped",
            "primary_checks": names,
        }

    verdicts = [str(check.get("verdict") or "unknown") for check in primary_checks]
    if any(verdict in NEGATIVE_VERDICTS for verdict in verdicts):
        status = "refuted"
    elif any(verdict == "error" for verdict in verdicts):
        status = "error"
    elif any(verdict in POSITIVE_VERDICTS for verdict in verdicts):
        status = "proved"
    elif any(verdict == "planned" for verdict in verdicts):
        status = "planned"
    else:
        status = "advisory"
    return {
        "status": status,
        "reason": "" if status not in {"advisory", "planned"} else "no primary proof/refutation",
        "primary_checks": names,
        "verdicts": dict(sorted((verdict, verdicts.count(verdict)) for verdict in set(verdicts))),
    }


def _annotate_headlines(report: dict) -> None:
    for item in report.get("passes", []):
        item["headline"] = _headline_for_pass(item)


def _pass_label(item: dict) -> str:
    return str(item.get("source") or item.get("pass_name") or "<unknown>")


def _attention(report: dict) -> dict[str, list[dict]]:
    attention: dict[str, list[dict]] = {
        "refuted": [],
        "error": [],
        "advisory": [],
        "skipped": [],
        "unclassified": [],
    }
    for item in report.get("passes", []):
        headline = item.get("headline") if isinstance(item.get("headline"), dict) else {}
        status = str(headline.get("status") or "")
        if status not in attention:
            continue
        attention[status].append({
            "target": _pass_label(item),
            "source": item.get("source"),
            "pass_name": item.get("pass_name"),
            "primary_family": item.get("primary_family"),
            "reason": headline.get("reason", ""),
            "primary_checks": headline.get("primary_checks", []),
        })
    return {key: value for key, value in attention.items() if value}


def _summarize(report: dict) -> dict:
    passes = report.get("passes", [])
    verdicts: dict[str, int] = {}
    strategies: dict[str, int] = {}
    families: dict[str, int] = {}
    headlines: dict[str, int] = {}
    positive = negative = errors = planned = 0
    for item in passes:
        family = str(item.get("primary_family") or "unclassified")
        families[family] = families.get(family, 0) + 1
        headline = str((item.get("headline") or {}).get("status") or "unset")
        headlines[headline] = headlines.get(headline, 0) + 1
        for check in item.get("checks", []):
            verdict = str(check.get("verdict") or "unknown")
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            strategy = str(check.get("strategy") or "unknown")
            strategies[strategy] = strategies.get(strategy, 0) + 1
            if verdict in POSITIVE_VERDICTS:
                positive += 1
            elif verdict in NEGATIVE_VERDICTS:
                negative += 1
            elif verdict == "error":
                errors += 1
            elif verdict == "planned":
                planned += 1
        if "checks" not in item:
            for check in item.get("planned_checks", []):
                strategy = str(check.get("strategy") or "unknown")
                strategies[strategy] = strategies.get(strategy, 0) + 1
                planned += 1
    summary = {
        "passes": len(passes),
        "classified": sum(1 for item in passes if item.get("primary_family")),
        "unclassified": sum(1 for item in passes if not item.get("primary_family")),
        "positive_verdicts": positive,
        "negative_verdicts": negative,
        "error_verdicts": errors,
        "planned_or_skipped": planned,
        "by_family": dict(sorted(families.items())),
        "by_headline": dict(sorted(headlines.items())),
        "by_verdict": dict(sorted(verdicts.items())),
        "by_strategy": dict(sorted(strategies.items())),
        "attention": _attention(report),
    }
    if isinstance(report.get("deep_audit"), dict):
        summary["deep_audit"] = _summarize_deep_audit(report["deep_audit"])
    summary["readiness_matrix"] = _readiness_matrix(report)
    summary["next_actions"] = _next_actions(report)
    return summary


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _add_arg(command: list[str], name: str, value) -> None:
    if value is not None:
        command.extend([name, str(value)])


def _modelcheck_finding_summary(finding: dict) -> dict:
    return {
        "status": str(finding.get("status") or "unknown"),
        "marker": str(finding.get("marker") or "record"),
        "reason": str(finding.get("reason") or ""),
        "file": str(finding.get("file") or ""),
        "line": int(finding.get("line") or 0),
        "width": int(finding.get("width") or 0),
        "domain": str(finding.get("domain") or ""),
        "source_function": str(finding.get("source_function") or ""),
    }


def _format_modelcheck_finding_summary(finding: dict) -> str:
    width = int(finding.get("width") or 0)
    width_text = f" @{width}b" if width else ""
    domain = str(finding.get("domain") or "")
    domain_text = f" {domain}" if domain else ""
    source_function = str(finding.get("source_function") or "")
    function_text = f" {source_function}" if source_function else ""
    file = str(finding.get("file") or "")
    line = int(finding.get("line") or 0)
    location = f" {file}:{line}" if file or line else ""
    reason = str(finding.get("reason") or "")
    suffix = f" ({reason})" if reason else ""
    return (
        f"{str(finding.get('status') or 'unknown')}:"
        f"{width_text}{domain_text} {str(finding.get('marker') or 'record')}"
        f"{function_text}{location}{suffix}"
    )


def _deep_audit_command(args: argparse.Namespace, sources: list[Path]) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "cv-run-external-pass-audit.py"),
        "--compile-commands",
        str(args.compile_commands),
        "--out",
        str(args.audit_out),
        "--z3",
        str(args.z3_bin),
    ]
    _add_arg(command, "--ast-miner", args.ast_miner)
    _add_arg(command, "--ir-miner", args.ir_miner)
    _add_arg(command, "--baseline", args.baseline)
    _add_arg(command, "--pass-impl-ir-slice-window", args.pass_impl_ir_slice_window)
    _add_arg(command, "--modelcheck-unwind", args.modelcheck_unwind)
    _add_arg(command, "--modelcheck-timeout", args.modelcheck_timeout)
    _add_arg(command, "--modelcheck-widths", args.modelcheck_widths)
    _add_arg(command, "--max-modelcheck-refuted", args.max_modelcheck_refuted)
    _add_arg(command, "--max-modelcheck-errors", args.max_modelcheck_errors)
    _add_arg(command, "--max-new-modelcheck-refuted", args.max_new_modelcheck_refuted)
    _add_arg(command, "--max-new-modelcheck-errors", args.max_new_modelcheck_errors)
    if len(args.passes) == 1:
        command.extend(["--passes", args.passes[0]])
    for marker in args.marker:
        command.extend(["--marker", marker])
    for prefix in args.marker_prefix:
        command.extend(["--marker-prefix", prefix])
    for include in args.include:
        command.extend(["--include", include])
    for exclude in args.exclude:
        command.extend(["--exclude", exclude])
    if args.mine_pass_impl_ir:
        command.append("--mine-pass-impl-ir")
    if args.modelcheck_intents:
        command.extend(["--modelcheck-intents", "--modelcheck-engine", args.modelcheck_engine])
    if args.require_clean_mining:
        command.append("--require-clean-mining")
    command.extend(str(source) for source in sources)
    return command


def _run_deep_audit(args: argparse.Namespace, sources: list[Path]) -> dict:
    args.audit_out.mkdir(parents=True, exist_ok=True)
    command = _deep_audit_command(args, sources)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    summary_path = args.audit_out / "external-pass-audit-summary.json"
    text_path = args.audit_out / "external-pass-audit-summary.txt"
    wrapper_summary = _load_json(summary_path)
    audit_out = args.audit_out / "audit"
    artifacts = wrapper_summary.get("artifacts") if isinstance(wrapper_summary.get("artifacts"), dict) else {}
    return {
        "enabled": True,
        "exit_code": result.returncode,
        "out": str(args.audit_out),
        "audit_out": str(audit_out),
        "command": command,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "summary_path": str(summary_path) if summary_path.is_file() else "",
        "summary_text_path": str(text_path) if text_path.is_file() else "",
        "artifacts": {
            "run_summary": str(artifacts.get("run_summary") or ""),
            "real_pass_readiness": str(artifacts.get("real_pass_readiness") or ""),
            "findings": str(artifacts.get("findings") or ""),
            "source_manifest": str(artifacts.get("source_manifest") or ""),
            "modelcheck_intents": str(artifacts.get("modelcheck_intents") or ""),
        },
        "summary": wrapper_summary,
    }


def _summarize_deep_audit(deep: dict) -> dict:
    wrapper = deep.get("summary") if isinstance(deep.get("summary"), dict) else {}
    sources = wrapper.get("sources") if isinstance(wrapper.get("sources"), dict) else {}
    findings = wrapper.get("findings") if isinstance(wrapper.get("findings"), dict) else {}
    intents = wrapper.get("intents") if isinstance(wrapper.get("intents"), dict) else {}
    modelcheck = wrapper.get("modelcheck") if isinstance(wrapper.get("modelcheck"), dict) else {}
    modelcheck_findings = (
        [item for item in modelcheck.get("findings", []) if isinstance(item, dict)]
        if isinstance(modelcheck.get("findings"), list)
        else []
    )
    modelcheck_components = (
        [item for item in modelcheck.get("components", []) if isinstance(item, dict)]
        if isinstance(modelcheck.get("components"), list)
        else []
    )
    selected_widths = modelcheck.get("selected_widths") if isinstance(modelcheck.get("selected_widths"), list) else []
    artifacts = deep.get("artifacts") if isinstance(deep.get("artifacts"), dict) else {}
    violations = wrapper.get("budget_violations") if isinstance(wrapper.get("budget_violations"), list) else []
    return {
        "enabled": bool(deep.get("enabled")),
        "exit_code": int(deep.get("exit_code") or 0),
        "out": str(deep.get("out") or ""),
        "sources_selected": int(sources.get("selected") or 0),
        "findings": int(findings.get("total") or 0),
        "intents": int(intents.get("total") or 0),
        "modelcheck_generated": int(modelcheck.get("generated") or 0),
        "modelcheck_refuted": int(modelcheck.get("refuted") or 0),
        "modelcheck_error": int(modelcheck.get("error") or 0),
        "modelcheck_width_mode": str(modelcheck.get("width_mode") or ""),
        "modelcheck_selected_widths": selected_widths,
        "modelcheck_widths": modelcheck.get("widths") if isinstance(modelcheck.get("widths"), dict) else {},
        "modelcheck_findings": len(modelcheck_findings),
        "modelcheck_top_findings": [
            _modelcheck_finding_summary(finding)
            for finding in modelcheck_findings[:5]
        ],
        "modelcheck_omitted_findings": max(0, len(modelcheck_findings) - 5),
        "modelcheck_components": [
            {
                "source_kind": str(component.get("source_kind") or "component"),
                "summary": str(component.get("summary") or ""),
                "records": int(component.get("records") or 0),
                "generated": int(component.get("generated") or 0),
                "proved": int(component.get("proved") or 0),
                "refuted": int(component.get("refuted") or 0),
                "unsupported": int(component.get("unsupported") or 0),
                "skipped": int(component.get("skipped") or 0),
                "error": int(component.get("error") or 0),
                "width_mode": str(component.get("width_mode") or ""),
                "selected_widths": (
                    component.get("selected_widths")
                    if isinstance(component.get("selected_widths"), list)
                    else []
                ),
            }
            for component in modelcheck_components
        ],
        "budget_violations": len(violations),
        "has_readiness": bool(artifacts.get("real_pass_readiness")),
        "summary_path": str(deep.get("summary_path") or ""),
    }


def _counter_inc(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _readiness_matrix(report: dict) -> dict:
    family_rows: dict[str, dict] = {}
    for item in report.get("passes", []):
        family = str(item.get("primary_family") or "unclassified")
        row = family_rows.setdefault(
            family,
            {
                "sources": 0,
                "headlines": {},
                "planned_checks": 0,
                "executed_checks": 0,
                "strategies": {},
                "attention": 0,
            },
        )
        row["sources"] += 1
        headline = str((item.get("headline") or {}).get("status") or "unset")
        _counter_inc(row["headlines"], headline)
        checks = item.get("checks") if isinstance(item.get("checks"), list) else item.get("planned_checks", [])
        if isinstance(item.get("checks"), list):
            row["executed_checks"] += len(checks)
        else:
            row["planned_checks"] += len(checks)
        for check in checks:
            strategy = str(check.get("strategy") or "unknown")
            _counter_inc(row["strategies"], strategy)
        if headline in {"refuted", "error", "advisory", "skipped", "unclassified"}:
            row["attention"] += 1

    matrix = {"families": dict(sorted(family_rows.items()))}
    deep = report.get("deep_audit") if isinstance(report.get("deep_audit"), dict) else {}
    if deep:
        wrapper = deep.get("summary") if isinstance(deep.get("summary"), dict) else {}
        readiness_path = str((deep.get("artifacts") or {}).get("real_pass_readiness") or "")
        readiness = _load_json(Path(readiness_path)) if readiness_path else {}
        coverage = wrapper.get("coverage") if isinstance(wrapper.get("coverage"), dict) else {}
        pass_impl_ir = wrapper.get("pass_impl_ir") if isinstance(wrapper.get("pass_impl_ir"), dict) else {}
        intents = wrapper.get("intents") if isinstance(wrapper.get("intents"), dict) else {}
        graph = readiness.get("transaction_graph") if isinstance(readiness.get("transaction_graph"), dict) else {}
        diagnostics = readiness.get("diagnostics") if isinstance(readiness.get("diagnostics"), dict) else {}
        matrix["deep_audit"] = {
            **_summarize_deep_audit(deep),
            "proof_status": intents.get("proof_status") if isinstance(intents.get("proof_status"), dict) else {},
            "recommendations": coverage.get("recommendations") if isinstance(coverage.get("recommendations"), dict) else {},
            "next_modeling_target": str(coverage.get("next_modeling_target") or ""),
            "pass_impl_ir_status": (
                pass_impl_ir.get("intent_check_status")
                if isinstance(pass_impl_ir.get("intent_check_status"), dict)
                else {}
            ),
            "transaction_graph_status": (
                graph.get("graph_status") if isinstance(graph.get("graph_status"), dict) else {}
            ),
            "transaction_graph_absent_reasons": (
                graph.get("absent_reasons") if isinstance(graph.get("absent_reasons"), dict) else {}
            ),
            "readiness_diagnostics": diagnostics,
        }
    return matrix


def _add_action(actions: list[dict], priority: int, kind: str, target: str, detail: str, source: str = "") -> None:
    actions.append({
        "priority": priority,
        "kind": kind,
        "target": target,
        "detail": detail,
        "source": source,
    })


def _next_actions(report: dict) -> list[dict]:
    actions: list[dict] = []
    for status, priority in (("refuted", 10), ("error", 20), ("unclassified", 30), ("advisory", 40), ("skipped", 50)):
        for item in _attention(report).get(status, []):
            _add_action(
                actions,
                priority,
                f"source-{status}",
                str(item.get("target") or ""),
                str(item.get("reason") or status),
                "orchestrator",
            )

    deep = report.get("deep_audit") if isinstance(report.get("deep_audit"), dict) else {}
    if deep:
        wrapper = deep.get("summary") if isinstance(deep.get("summary"), dict) else {}
        if int(deep.get("exit_code") or 0) != 0:
            _add_action(actions, 60, "deep-audit-error", str(deep.get("out") or ""), str(deep.get("stderr") or "deep audit failed"), "deep_audit")
        violations = wrapper.get("budget_violations") if isinstance(wrapper.get("budget_violations"), list) else []
        for violation in violations[:10]:
            _add_action(actions, 70, "deep-audit-budget", str(deep.get("out") or ""), str(violation), "deep_audit")
        modelcheck = wrapper.get("modelcheck") if isinstance(wrapper.get("modelcheck"), dict) else {}
        modelcheck_findings = (
            [item for item in modelcheck.get("findings", []) if isinstance(item, dict)]
            if isinstance(modelcheck.get("findings"), list)
            else []
        )
        for finding in modelcheck_findings[:10]:
            file = str(finding.get("file") or "")
            line = int(finding.get("line") or 0)
            location = f"{file}:{line}" if file or line else str(deep.get("out") or "")
            _add_action(
                actions,
                65,
                "modelcheck-finding",
                location,
                _format_modelcheck_finding_summary(_modelcheck_finding_summary(finding)),
                "modelcheck_intents",
            )
        omitted_modelcheck_findings = len(modelcheck_findings) - 10
        if omitted_modelcheck_findings > 0:
            _add_action(
                actions,
                66,
                "modelcheck-findings-omitted",
                str(deep.get("out") or ""),
                f"{omitted_modelcheck_findings} additional modelcheck findings omitted from next actions",
                "modelcheck_intents",
            )

        readiness_path = str((deep.get("artifacts") or {}).get("real_pass_readiness") or "")
        readiness = _load_json(Path(readiness_path)) if readiness_path else {}
        diagnostics = readiness.get("diagnostics") if isinstance(readiness.get("diagnostics"), dict) else {}
        recommendation = str(diagnostics.get("pass_impl_ir_intent_recommendation") or "")
        if recommendation:
            _add_action(actions, 80, "readiness-recommendation", str(deep.get("out") or ""), recommendation, "real_pass_readiness")
        coverage = wrapper.get("coverage") if isinstance(wrapper.get("coverage"), dict) else {}
        next_target = str(coverage.get("next_modeling_target") or "")
        if next_target:
            _add_action(actions, 90, "coverage-next-target", str(deep.get("out") or ""), next_target, "external_audit_summary")
        source_graph = coverage.get("source_program_graph_contract") if isinstance(coverage.get("source_program_graph_contract"), dict) else {}
        graph_gaps = source_graph.get("gaps") if isinstance(source_graph.get("gaps"), dict) else {}
        graph_target = str(graph_gaps.get("next_modeling_target") or "")
        if graph_target:
            _add_action(actions, 100, "source-graph-gap", str(deep.get("out") or ""), graph_target, "source_program_graph_contract")
        transaction_graph = readiness.get("transaction_graph") if isinstance(readiness.get("transaction_graph"), dict) else {}
        absent = transaction_graph.get("absent_reasons") if isinstance(transaction_graph.get("absent_reasons"), dict) else {}
        for reason, count in sorted(absent.items(), key=lambda item: (-int(item[1]), str(item[0])))[:3]:
            _add_action(actions, 110, "transaction-graph-gap", str(deep.get("out") or ""), f"{reason}: {count}", "real_pass_readiness")
    return sorted(actions, key=lambda item: (int(item["priority"]), str(item["kind"]), str(item["target"])))[:25]


def _should_fail(args: argparse.Namespace, summary: dict) -> bool:
    headlines = summary.get("by_headline") if isinstance(summary.get("by_headline"), dict) else {}
    deep = summary.get("deep_audit") if isinstance(summary.get("deep_audit"), dict) else {}
    return (
        (args.fail_on_refuted and int(headlines.get("refuted") or 0) > 0)
        or (args.fail_on_any_refuted and int(summary.get("negative_verdicts") or 0) > 0)
        or (args.fail_on_error and int(headlines.get("error") or 0) > 0)
        or (args.fail_on_advisory and int(headlines.get("advisory") or 0) > 0)
        or (args.fail_on_unclassified and int(summary.get("unclassified") or 0) > 0)
        or (args.fail_on_no_positive and int(headlines.get("proved") or 0) == 0)
        or (args.fail_on_deep_audit_error and int(deep.get("exit_code") or 0) != 0)
    )


def _format_counts(counts: dict) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _selected_widths_label(record: dict) -> str:
    selected_widths = record.get("modelcheck_selected_widths")
    if not isinstance(selected_widths, list):
        selected_widths = record.get("selected_widths")
    if isinstance(selected_widths, list) and selected_widths:
        return ",".join(str(width) for width in selected_widths)
    width_mode = str(record.get("modelcheck_width_mode") or record.get("width_mode") or "").strip()
    return "native" if width_mode in {"", "native"} else "none"


def _render_summary_text(report: dict) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "O2T Orchestrator Summary",
        f"passes: {int(summary.get('passes') or 0)}",
        f"classified: {int(summary.get('classified') or 0)}",
        f"unclassified: {int(summary.get('unclassified') or 0)}",
        f"headlines: {_format_counts(summary.get('by_headline', {}))}",
        f"families: {_format_counts(summary.get('by_family', {}))}",
        f"raw_verdicts: {_format_counts(summary.get('by_verdict', {}))}",
        "",
        "Passes",
    ]
    for item in report.get("passes", []):
        headline = item.get("headline") if isinstance(item.get("headline"), dict) else {}
        status = str(headline.get("status") or "unset")
        reason = str(headline.get("reason") or "")
        suffix = f" ({reason})" if reason else ""
        lines.append(f"  [{status}] {item.get('primary_family') or 'unclassified'} { _pass_label(item)}{suffix}")
        checks = item.get("checks") if isinstance(item.get("checks"), list) else item.get("planned_checks", [])
        for check in checks:
            verdict = str(check.get("verdict") or ("planned" if check.get("feasible", True) else "skipped"))
            reason = str(check.get("reason") or "")
            suffix = f" - {reason}" if reason else ""
            lines.append(f"    {verdict:12} {check.get('strategy')}{suffix}")
    attention = summary.get("attention") if isinstance(summary.get("attention"), dict) else {}
    if attention:
        lines.extend(["", "Attention"])
        for status, records in sorted(attention.items()):
            lines.append(f"  {status}: {len(records)}")
            for record in records[:10]:
                reason = str(record.get("reason") or "")
                suffix = f" - {reason}" if reason else ""
                lines.append(f"    {record.get('target')}{suffix}")
            if len(records) > 10:
                lines.append(f"    ... {len(records) - 10} more")
    matrix = summary.get("readiness_matrix") if isinstance(summary.get("readiness_matrix"), dict) else {}
    if matrix:
        lines.extend(["", "Readiness Matrix"])
        families = matrix.get("families") if isinstance(matrix.get("families"), dict) else {}
        for family, row in sorted(families.items()):
            row = row if isinstance(row, dict) else {}
            lines.append(
                f"  {family}: sources={int(row.get('sources') or 0)} "
                f"headlines={_format_counts(row.get('headlines', {}))} "
                f"executed={int(row.get('executed_checks') or 0)} "
                f"planned={int(row.get('planned_checks') or 0)} "
                f"attention={int(row.get('attention') or 0)}"
            )
        deep_matrix = matrix.get("deep_audit") if isinstance(matrix.get("deep_audit"), dict) else {}
        if deep_matrix:
            lines.append(
                f"  deep_audit: sources={int(deep_matrix.get('sources_selected') or 0)} "
                f"findings={int(deep_matrix.get('findings') or 0)} "
                f"intents={int(deep_matrix.get('intents') or 0)} "
                f"proof={_format_counts(deep_matrix.get('proof_status', {}))} "
                f"impl_ir={_format_counts(deep_matrix.get('pass_impl_ir_status', {}))} "
                f"graphs={_format_counts(deep_matrix.get('transaction_graph_status', {}))} "
                f"modelcheck_widths={_selected_widths_label(deep_matrix)}"
            )
    actions = summary.get("next_actions") if isinstance(summary.get("next_actions"), list) else []
    lines.extend(["", "Next Actions"])
    if actions:
        for action in actions[:10]:
            lines.append(
                f"  p{int(action.get('priority') or 0)} {action.get('kind')}: "
                f"{action.get('target')} - {action.get('detail')}"
            )
    else:
        lines.append("  none")
    deep = summary.get("deep_audit") if isinstance(summary.get("deep_audit"), dict) else {}
    if deep:
        lines.extend([
            "",
            "Deep Audit",
            f"  exit_code: {int(deep.get('exit_code') or 0)}",
            f"  out: {deep.get('out') or ''}",
            f"  sources_selected: {int(deep.get('sources_selected') or 0)}",
            f"  findings: {int(deep.get('findings') or 0)}",
            f"  intents: {int(deep.get('intents') or 0)}",
            f"  modelcheck_generated: {int(deep.get('modelcheck_generated') or 0)}",
            f"  modelcheck_refuted: {int(deep.get('modelcheck_refuted') or 0)}",
            f"  modelcheck_error: {int(deep.get('modelcheck_error') or 0)}",
            f"  modelcheck_findings: {int(deep.get('modelcheck_findings') or 0)}",
            f"  budget_violations: {int(deep.get('budget_violations') or 0)}",
            f"  readiness: {'present' if deep.get('has_readiness') else 'missing'}",
        ])
        components = deep.get("modelcheck_components") if isinstance(deep.get("modelcheck_components"), list) else []
        if components:
            lines.append("  modelcheck_components:")
            for component in components:
                if not isinstance(component, dict):
                    continue
                lines.append(
                    "    "
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
                            f"selected={_selected_widths_label(component)}",
                        ]
                    )
                )
        top_findings = deep.get("modelcheck_top_findings") if isinstance(deep.get("modelcheck_top_findings"), list) else []
        if top_findings:
            lines.append("  modelcheck_top_findings:")
            for finding in top_findings:
                if isinstance(finding, dict):
                    lines.append("    " + _format_modelcheck_finding_summary(finding))
            omitted = int(deep.get("modelcheck_omitted_findings") or 0)
            if omitted > 0:
                lines.append(f"    ... {omitted} more")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", nargs="*", type=Path, default=[],
                    help="pass source file(s) or directories")
    ap.add_argument("--pass", dest="passes", action="append", default=[],
                    help="pass name (repeatable); pairs positionally with --source when both given")
    ap.add_argument("--include", action="append", default=[],
                    help="only include expanded source paths containing this substring")
    ap.add_argument("--exclude", action="append", default=[],
                    help="exclude expanded source paths containing this substring")
    ap.add_argument("--marker", action="append", default=[],
                    help="deep audit marker filter, forwarded to cv-run-external-pass-audit.py")
    ap.add_argument("--marker-prefix", action="append", default=[],
                    help="deep audit marker-prefix filter, forwarded to cv-run-external-pass-audit.py")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-execute", action="store_true", help="classify + plan only, do not run checks")
    ap.add_argument("--llm-command", help="optional provider-agnostic LLM command for ambiguous classification")
    ap.add_argument("--fail-on-refuted", action="store_true",
                    help="exit non-zero if any source headline is refuted")
    ap.add_argument("--fail-on-any-refuted", action="store_true",
                    help="exit non-zero if any executed check refutes a transform, including secondary checks")
    ap.add_argument("--fail-on-error", action="store_true",
                    help="exit non-zero if any source headline is error")
    ap.add_argument("--fail-on-advisory", action="store_true",
                    help="exit non-zero if any source headline is advisory")
    ap.add_argument("--fail-on-unclassified", action="store_true",
                    help="exit non-zero if any input cannot be classified")
    ap.add_argument("--fail-on-no-positive", action="store_true",
                    help="exit non-zero if no check reaches proved/sound/validated")
    ap.add_argument("--fail-on-deep-audit-error", action="store_true",
                    help="exit non-zero if the optional deep external audit exits non-zero")
    ap.add_argument("--compile-commands", type=Path,
                    help="enable deep external pass audit with this compile_commands.json")
    ap.add_argument("--audit-out", type=Path,
                    help="output directory for the optional deep external pass audit")
    ap.add_argument("--mine-pass-impl-ir", action="store_true",
                    help="forward --mine-pass-impl-ir to the optional deep audit")
    ap.add_argument("--modelcheck-intents", action="store_true",
                    help="forward --modelcheck-intents to the optional deep audit")
    ap.add_argument("--modelcheck-engine", choices=["auto", "cbmc", "esbmc"], default="auto",
                    help="forward model-checker engine selection to the optional deep audit")
    ap.add_argument("--modelcheck-unwind", type=int,
                    help="forward model-checker unwind bound to the optional deep audit")
    ap.add_argument("--modelcheck-timeout", type=int,
                    help="forward model-checker per-record timeout to the optional deep audit")
    ap.add_argument("--modelcheck-widths",
                    help="forward modelcheck width mode to the optional deep audit")
    ap.add_argument("--max-modelcheck-refuted", type=int,
                    help="forward modelcheck refutation budget to the optional deep audit")
    ap.add_argument("--max-modelcheck-errors", type=int,
                    help="forward modelcheck error budget to the optional deep audit")
    ap.add_argument("--max-new-modelcheck-refuted", type=int,
                    help="forward new modelcheck refutation budget to the optional deep audit")
    ap.add_argument("--max-new-modelcheck-errors", type=int,
                    help="forward new modelcheck error budget to the optional deep audit")
    ap.add_argument("--require-clean-mining", action="store_true",
                    help="forward --require-clean-mining to the optional deep audit")
    ap.add_argument("--ast-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-source-ast")
    ap.add_argument("--ir-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-impl-ir")
    ap.add_argument("--baseline", type=Path,
                    help="forward a baseline file to the optional deep audit")
    ap.add_argument("--pass-impl-ir-slice-window", type=int,
                    help="forward implementation-IR slice window to the optional deep audit")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--clang-bin", default="clang")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--summary-text", type=Path,
                    help="write a stable human-readable audit summary")
    args = ap.parse_args()

    if args.selftest:
        report = _selftest(args)
        selected_sources: list[Path] = []
    else:
        if not args.source and not args.passes:
            ap.error("provide --source and/or --pass (or --selftest)")
        sources = _expand_sources(args.source, args.include, args.exclude)
        if args.source and not sources:
            ap.error("no source files selected after expansion/filtering")
        selected_sources = sources
        inputs = _pair_inputs(sources, args.passes)
        ctx = resolve_context(args.z3_bin, args.opt_bin, args.clang_bin, ast_miner=args.ast_miner)
        report = orchestrate(inputs, ctx, execute=not args.no_execute)
        if args.llm_command:
            maybe_llm_classify(report, args.llm_command)

    _annotate_headlines(report)
    if args.compile_commands:
        if args.selftest:
            ap.error("--compile-commands cannot be combined with --selftest")
        if not args.audit_out:
            ap.error("--audit-out is required with --compile-commands")
        if not selected_sources:
            ap.error("--compile-commands requires at least one selected source")
        report["deep_audit"] = _run_deep_audit(args, selected_sources)
    report["summary"] = _summarize(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.summary_text:
        args.summary_text.parent.mkdir(parents=True, exist_ok=True)
        args.summary_text.write_text(_render_summary_text(report), encoding="utf-8")
    # Human-readable summary to stderr; machine summary to stdout.
    for p in report["passes"]:
        who = p.get("source") or p.get("pass_name")
        headline = (p.get("headline") or {}).get("status") or "unset"
        print(f"\n[{p['primary_family'] or 'unclassified'}:{headline}] {who}", file=sys.stderr)
        plan_only = "checks" not in p
        for c in p.get("checks", p.get("planned_checks", [])):
            if plan_only:
                v = "planned" if c.get("feasible", True) else "skipped"
            else:
                v = c.get("verdict", "planned" if not c.get("feasible", True) else "?")
            print(f"   {v:13} {c['strategy']:22} {c.get('reason','')}", file=sys.stderr)
    summary = report["summary"]
    print(json.dumps({
        "passes": summary["passes"],
        "positive_verdicts": summary["positive_verdicts"],
        "negative_verdicts": summary["negative_verdicts"],
        "error_verdicts": summary["error_verdicts"],
        "unclassified": summary["unclassified"],
        "headlines": summary["by_headline"],
        "attention": {key: len(value) for key, value in summary["attention"].items()},
        "deep_audit": summary.get("deep_audit", {}),
    }, sort_keys=True))
    return 1 if _should_fail(args, summary) else 0


if __name__ == "__main__":
    sys.exit(main())
