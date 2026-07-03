#!/usr/bin/env python3
"""Run an AST-first formal intent audit over real pass source files."""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from cv_optimization_registry import (
    OPERATION_FOR_BUILDER_CALL,
    builder_calls_for_registered_operations,
    registry_spec_for_marker,
    source_patterns_for_marker,
)
from cv_analysis_facts import dse_analysis_fact_contract, normalize_analysis_facts


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc"}
PASS_SOURCE_AUDIT_BASELINE_MODEL = "o2t-pass-source-audit-baseline-v1"
LEGACY_PASS_SOURCE_AUDIT_BASELINE_MODEL = "compilerverif-pass-source-audit-baseline-v1"
MODELCHECK_BASELINE_MODEL = "o2t-modelcheck-baseline-v1"
LEGACY_MODELCHECK_BASELINE_MODEL = "compilerverif-modelcheck-baseline-v1"


def is_model_id(model: object, current: str, legacy: str) -> bool:
    return model in {current, legacy}


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
    parser.add_argument("--require-clean-mining", action="store_true")
    parser.add_argument("--verify-source-slice-contracts", action="store_true")
    parser.add_argument("--verify-transaction-formalization", action="store_true")
    parser.add_argument("--emit-slp-transaction-ir", action="store_true")
    parser.add_argument("--validate-slp-ir", action="store_true")
    parser.add_argument("--emit-smt", action="store_true")
    parser.add_argument("--mine-pass-impl-ir", action="store_true")
    parser.add_argument("--pass-impl-ir-slice-window", type=int, default=2)
    parser.add_argument("--intent-min-confidence", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--ast-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-source-ast")
    parser.add_argument("--ir-miner", type=Path, default=ROOT / "build-clang-tools" / "cv-mine-pass-impl-ir")
    parser.add_argument("--ir-source-wrapper", type=Path, default=ROOT / "tools" / "cv-mine-pass-impl-ir-from-source.py")
    parser.add_argument("--intent-inferer", type=Path, default=ROOT / "tools" / "cv-infer-optimization-intent.py")
    parser.add_argument("--intent-validator", type=Path, default=ROOT / "tools" / "cv-validate-intent-candidates.py")
    parser.add_argument("--coverage-auditor", type=Path, default=ROOT / "tools" / "cv-audit-intent-coverage.py")
    parser.add_argument("--source-slice-contract-verifier", type=Path, default=ROOT / "tools" / "cv-verify-source-slice-contract.py")
    parser.add_argument("--transaction-formalization-verifier", type=Path, default=ROOT / "tools" / "cv-verify-transaction-formalization.py")
    parser.add_argument("--slp-transaction-ir-emitter", type=Path, default=ROOT / "tools" / "cv-slp-transaction-to-ir.py")
    parser.add_argument("--modelcheck-intent-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-intents.py")
    parser.add_argument("--modelcheck-cfg-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-cfg-pass.py")
    parser.add_argument("--modelcheck-memory-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-memory-pass.py")
    parser.add_argument("--modelcheck-licm-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-licm-pass.py")
    parser.add_argument("--modelcheck-globalopt-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-globalopt-pass.py")
    parser.add_argument("--modelcheck-dce-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-dce-pass.py")
    parser.add_argument("--modelcheck-slp-checker", type=Path, default=ROOT / "tools" / "cv-modelcheck-slp-pass.py")
    parser.add_argument("--llvm-as")
    parser.add_argument("--registry", type=Path, default=ROOT / "constraints" / "pass_constraints.json")
    parser.add_argument("--semantic-registry", type=Path, default=ROOT / "constraints" / "semantic_facts.json")
    parser.add_argument("--intent-registry", type=Path, default=ROOT / "constraints" / "optimization_intents.json")
    parser.add_argument("--guard-semantics", type=Path, default=ROOT / "constraints" / "guard_semantics.json")
    parser.add_argument("--z3", default="z3")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--modelcheck-intents", action="store_true")
    parser.add_argument("--modelcheck-engine", choices=["auto", "cbmc", "esbmc"], default="auto")
    parser.add_argument("--modelcheck-unwind", type=int, default=8)
    parser.add_argument("--modelcheck-timeout", type=int, default=30)
    parser.add_argument("--modelcheck-widths", default="native")
    parser.add_argument("--min-proved", type=int)
    parser.add_argument("--max-unsupported", type=int)
    parser.add_argument("--max-proof-failures", type=int)
    parser.add_argument("--max-fallback-transactions", type=int)
    parser.add_argument("--max-mining-errors", type=int)
    parser.add_argument("--max-new-unsupported", type=int)
    parser.add_argument("--max-new-fallback-transactions", type=int)
    parser.add_argument("--max-incomplete-formal-provenance", type=int)
    parser.add_argument("--max-modelcheck-refuted", type=int)
    parser.add_argument("--max-modelcheck-errors", type=int)
    parser.add_argument("--max-new-modelcheck-refuted", type=int)
    parser.add_argument("--max-new-modelcheck-errors", type=int)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def require_executable(path: Path, label: str) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    print(f"{label} is not executable: {path}", file=sys.stderr)
    return False


def require_file(path: Path, label: str) -> bool:
    if path.is_file():
        return True
    print(f"{label} does not exist: {path}", file=sys.stderr)
    return False


def require_command(command: str, label: str) -> bool:
    if Path(command).is_file() and os.access(command, os.X_OK):
        return True
    if shutil.which(command):
        return True
    print(f"{label} is not executable or on PATH: {command}", file=sys.stderr)
    return False


def compile_commands_dir(path: Path) -> Path:
    resolved = path.resolve()
    return resolved.parent if resolved.name == "compile_commands.json" else resolved


def load_compile_files(path: Path) -> set[Path]:
    compile_db = path / "compile_commands.json" if path.is_dir() else path
    data = json.loads(compile_db.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("compile_commands.json must contain an array")
    files: set[Path] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        file_value = entry.get("file")
        if not isinstance(file_value, str) or not file_value:
            continue
        file_path = Path(file_value)
        if not file_path.is_absolute():
            directory = Path(str(entry.get("directory") or compile_db.parent))
            file_path = directory / file_path
        files.add(file_path.resolve())
    return files


def source_files(paths: list[Path]) -> tuple[list[Path], list[dict[str, Any]]]:
    files: list[Path] = []
    manifest: list[dict[str, Any]] = []
    for path in paths:
        if path.is_file():
            files.append(path.resolve())
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix in SOURCE_SUFFIXES:
                    files.append(candidate.resolve())
        else:
            manifest.append({"file": str(path), "status": "error", "reason": "source-not-found"})
    return sorted(set(files)), manifest


def path_selected(path: Path, includes: list[str], excludes: list[str]) -> tuple[bool, str]:
    text = str(path)
    if includes and not any(pattern in text for pattern in includes):
        return False, "include-filter"
    if any(pattern in text for pattern in excludes):
        return False, "exclude-filter"
    return True, ""


def load_records_text(text: str) -> list[dict[str, Any]]:
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


def load_records(path: Path) -> list[dict[str, Any]]:
    return load_records_text(path.read_text(encoding="utf-8"))


def write_json(path: Path, records: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def write_command_log(path: Path, commands: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for command in commands:
            output.write(" ".join(command) + "\n")


def merge_findings(records: list[dict[str, Any]], passes: set[str]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for record in records:
        if passes and str(record.get("pass") or "") not in passes:
            continue
        key = (str(record.get("file") or ""), int(record.get("line") or 0), str(record.get("marker") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)
    return sorted(merged, key=lambda item: (str(item.get("file") or ""), int(item.get("line") or 0), str(item.get("marker") or "")))


def filter_findings_by_marker(
    records: list[dict[str, Any]],
    markers: set[str],
    marker_prefixes: list[str],
) -> list[dict[str, Any]]:
    if not markers and not marker_prefixes:
        return records
    filtered: list[dict[str, Any]] = []
    for record in records:
        marker = str(record.get("marker") or "")
        if marker in markers or any(marker.startswith(prefix) for prefix in marker_prefixes):
            filtered.append(record)
    return filtered


def count(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(record.get(key) or "unset") for record in records).items()))


def manifest_count(manifest: list[dict[str, Any]], status: str) -> int:
    return sum(1 for record in manifest if record.get("status") == status)


def nested_dict(record: dict[str, Any], key: str) -> dict[str, Any]:
    value = record.get(key)
    return value if isinstance(value, dict) else {}


def nested_list(record: dict[str, Any], key: str) -> list[Any]:
    value = record.get(key)
    return value if isinstance(value, list) else []


def modelcheck_findings(modelcheck: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    raw = nested_list(modelcheck, "findings")
    findings = [item for item in raw if isinstance(item, dict)]
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
    components = [
        item
        for item in nested_list(modelcheck, "components")
        if isinstance(item, dict)
    ]
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
    widths = nested_dict(modelcheck, "widths")
    lines = ["  selected=" + selected_widths_label(modelcheck)]
    if not widths:
        lines.append("  none")
        return lines
    for width in sorted(widths, key=lambda value: (str(value) == "none", int(value) if str(value).isdigit() else str(value))):
        counts = widths.get(width)
        if not isinstance(counts, dict):
            continue
        lines.append(
            "  "
            + str(width)
            + ": "
            + " ".join(f"{status}={int(counts.get(status) or 0)}" for status in MODEL_CHECK_STATUSES)
        )
    return lines


MODEL_CHECK_STATUSES = ("proved", "refuted", "unsupported", "skipped", "error")


def selected_widths_from_mode(width_mode: str | None) -> list[int]:
    width_mode = width_mode.strip() if isinstance(width_mode, str) else width_mode
    if width_mode is None or width_mode == "" or width_mode == "native":
        return []
    selected_widths: list[int] = []
    for raw_width in width_mode.split(","):
        raw_width = raw_width.strip()
        if not raw_width:
            continue
        try:
            width = int(raw_width)
        except ValueError:
            continue
        if width not in selected_widths:
            selected_widths.append(width)
    return selected_widths


def selected_widths_from_summary(part: dict[str, Any], width_mode: str | None) -> list[int]:
    selected_widths: list[int] = []
    values = part.get("selected_widths")
    if isinstance(values, list):
        for value in values:
            try:
                width = int(value)
            except (TypeError, ValueError):
                continue
            if width not in selected_widths:
                selected_widths.append(width)
        return selected_widths
    return selected_widths_from_mode(width_mode)


def merge_modelcheck_summaries(
    summary_path: Path,
    parts: list[dict[str, Any]],
    width_mode: str,
) -> dict[str, Any]:
    summaries = [part for part in parts if isinstance(part, dict) and part]
    if not summaries:
        return {}
    widths: dict[str, dict[str, int]] = {}
    for part in summaries:
        for width, counts in nested_dict(part, "widths").items():
            bucket = widths.setdefault(str(width), {status: 0 for status in MODEL_CHECK_STATUSES})
            if not isinstance(counts, dict):
                continue
            for status in MODEL_CHECK_STATUSES:
                bucket[status] += int(counts.get(status) or 0)
    engines = sorted({str(part.get("engine") or "") for part in summaries if str(part.get("engine") or "")})
    engine_paths = sorted({str(part.get("engine_path") or "") for part in summaries if str(part.get("engine_path") or "")})
    selected_widths: list[int] = []
    for part in summaries:
        for width in selected_widths_from_summary(part, width_mode):
            if width not in selected_widths:
                selected_widths.append(width)
    results = [
        result
        for part in summaries
        for result in nested_list(part, "results")
        if isinstance(result, dict)
    ]
    findings = [
        finding
        for part in summaries
        for finding in modelcheck_findings(part)
    ]
    return {
        "model": "o2t-modelcheck-merged-summary-v1",
        "summary": str(summary_path),
        "components": [
            {
                "model": str(part.get("model") or ""),
                "source_kind": str(part.get("source_kind") or "intent"),
                "summary": str(part.get("summary") or ""),
                "records": int(part.get("records") or 0),
                "generated": int(part.get("generated") or 0),
                "proved": int(part.get("proved") or 0),
                "refuted": int(part.get("refuted") or 0),
                "unsupported": int(part.get("unsupported") or 0),
                "skipped": int(part.get("skipped") or 0),
                "error": int(part.get("error") or 0),
                "width_mode": str(part.get("width_mode") or width_mode),
                "selected_widths": selected_widths_from_summary(part, width_mode),
            }
            for part in summaries
        ],
        "engine": engines[0] if len(engines) == 1 else ("mixed" if engines else ""),
        "engine_path": engine_paths[0] if len(engine_paths) == 1 else ("mixed" if engine_paths else ""),
        "width_mode": width_mode,
        "selected_widths": selected_widths,
        "records": sum(int(part.get("records") or 0) for part in summaries),
        "instances": sum(int(part.get("instances") or len(nested_list(part, "results"))) for part in summaries),
        "transforms": sum(int(part.get("transforms") or 0) for part in summaries),
        "generated": sum(int(part.get("generated") or 0) for part in summaries),
        **{status: sum(int(part.get(status) or 0) for part in summaries) for status in MODEL_CHECK_STATUSES},
        "ok": all(bool(part.get("ok")) for part in summaries),
        "widths": widths,
        "findings": findings,
        "results": results,
    }


def modelcheck_error_summary(
    summary_path: Path,
    source_kind: str,
    records: int,
    engine: str,
    width_mode: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "model": "o2t-modelcheck-component-error-v1",
        "source_kind": source_kind,
        "summary": str(summary_path),
        "records": records,
        "instances": 1,
        "generated": 0,
        "proved": 0,
        "refuted": 0,
        "unsupported": 0,
        "skipped": 0,
        "error": 1,
        "ok": False,
        "engine": engine,
        "width_mode": width_mode,
        "selected_widths": selected_widths_from_mode(width_mode),
        "widths": {},
        "findings": [
            {
                "record_index": -1,
                "marker": "",
                "file": "",
                "line": 0,
                "status": "error",
                "reason": reason,
                "function": source_kind,
                "harness": "",
            }
        ],
        "results": [],
    }


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


def relative_output_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def source_line_anchors(finding: dict[str, Any]) -> list[int]:
    anchors: set[int] = set()
    for key in ("line", "rewrite_line"):
        value = finding.get(key)
        if isinstance(value, int) and value > 0:
            anchors.add(value)
    source_range = finding.get("source_range")
    if isinstance(source_range, dict):
        for key in (
            "predicate_begin_line",
            "predicate_end_line",
            "rewrite_begin_line",
            "rewrite_end_line",
        ):
            value = source_range.get(key)
            if isinstance(value, int) and value > 0:
                anchors.add(value)
    return sorted(anchors)


def debug_file_matches(location: dict[str, Any], source: Path) -> bool:
    file_value = location.get("file")
    if not isinstance(file_value, str) or not file_value:
        return False
    debug_path = Path(file_value)
    try:
        if debug_path.resolve() == source.resolve():
            return True
    except OSError:
        pass
    return debug_path.name == source.name


def instruction_debug_line(instruction: dict[str, Any], source: Path) -> int:
    location = instruction.get("debug_location")
    if not isinstance(location, dict) or not debug_file_matches(location, source):
        return 0
    value = location.get("line")
    return value if isinstance(value, int) else 0


def pass_impl_ir_slice_for(
    finding: dict[str, Any],
    graph: dict[str, Any],
    source: Path,
    window: int,
) -> dict[str, Any]:
    anchors = source_line_anchors(finding)
    base = {
        "model": "llvm-pass-impl-ir-slice-v1",
        "anchor_lines": anchors,
        "window": max(0, int(window)),
    }
    if not anchors:
        return {**base, "status": "absent", "reason": "no-source-line-anchor"}

    instructions = [
        item for item in graph.get("instructions", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ] if isinstance(graph.get("instructions"), list) else []
    if not instructions:
        return {**base, "status": "absent", "reason": "no-ir-instructions"}

    instruction_by_id = {str(item["id"]): item for item in instructions}
    matched_ids: set[str] = set()
    for instruction in instructions:
        line = instruction_debug_line(instruction, source)
        if line and any(abs(line - anchor) <= max(0, int(window)) for anchor in anchors):
            matched_ids.add(str(instruction["id"]))
    if not matched_ids:
        return {**base, "status": "absent", "reason": "no-debug-location-match"}

    included_ids = set(matched_ids)
    ssa_edges = [
        item for item in graph.get("ssa_def_use_edges", [])
        if isinstance(item, dict) and isinstance(item.get("from"), str) and isinstance(item.get("to"), str)
    ] if isinstance(graph.get("ssa_def_use_edges"), list) else []
    call_argument_edges = [
        item for item in graph.get("call_argument_edges", [])
        if isinstance(item, dict) and isinstance(item.get("from"), str) and isinstance(item.get("to"), str)
    ] if isinstance(graph.get("call_argument_edges"), list) else []
    for edge in ssa_edges:
        if edge["from"] in matched_ids or edge["to"] in matched_ids:
            included_ids.add(str(edge["from"]))
            included_ids.add(str(edge["to"]))
    for edge in call_argument_edges:
        if edge["from"] in matched_ids or edge["to"] in matched_ids:
            included_ids.add(str(edge["from"]))
            included_ids.add(str(edge["to"]))

    included_instructions = [
        instruction_by_id[item_id]
        for item_id in sorted(included_ids)
        if item_id in instruction_by_id
    ]
    included_blocks = {
        str(item.get("block"))
        for item in included_instructions
        if isinstance(item.get("block"), str)
    }
    included_functions = {
        str(item.get("function"))
        for item in included_instructions
        if isinstance(item.get("function"), str)
    }
    basic_blocks = [
        item for item in graph.get("basic_blocks", [])
        if isinstance(item, dict) and str(item.get("id")) in included_blocks
    ] if isinstance(graph.get("basic_blocks"), list) else []
    functions = [
        item for item in graph.get("functions", [])
        if isinstance(item, dict) and str(item.get("id") or item.get("name")) in included_functions
    ] if isinstance(graph.get("functions"), list) else []
    cfg_edges = [
        item for item in graph.get("cfg_edges", [])
        if isinstance(item, dict)
        and str(item.get("from")) in included_blocks
        and str(item.get("to")) in included_blocks
    ] if isinstance(graph.get("cfg_edges"), list) else []
    call_edges = [
        item for item in graph.get("call_edges", [])
        if isinstance(item, dict) and str(item.get("from")) in included_ids
    ] if isinstance(graph.get("call_edges"), list) else []
    call_operand_refs = [
        item for item in graph.get("call_operand_refs", [])
        if isinstance(item, dict) and str(item.get("call")) in included_ids
    ] if isinstance(graph.get("call_operand_refs"), list) else []
    local_ssa_edges = [
        item for item in ssa_edges
        if str(item.get("from")) in included_ids and str(item.get("to")) in included_ids
    ]
    local_call_argument_edges = [
        item for item in call_argument_edges
        if str(item.get("from")) in included_ids and str(item.get("to")) in included_ids
    ]

    return {
        **base,
        "status": "matched",
        "matched_instruction_ids": sorted(matched_ids),
        "instructions": included_instructions,
        "basic_blocks": basic_blocks,
        "functions": functions,
        "cfg_edges": cfg_edges,
        "call_edges": call_edges,
        "call_argument_edges": local_call_argument_edges,
        "call_operand_refs": call_operand_refs,
        "ssa_def_use_edges": local_ssa_edges,
    }


def pass_impl_ir_slice_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counter = collections.Counter()
    for finding in findings:
        slice_record = finding.get("pass_impl_ir_slice")
        if isinstance(slice_record, dict):
            counter[str(slice_record.get("status") or "unset")] += 1
    return dict(sorted(counter.items()))


def rewrite_status_summary(findings: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    status = collections.Counter()
    reasons = collections.Counter()
    for finding in findings:
        value = finding.get("rewrite_status")
        if value is not None:
            status[str(value or "unset")] += 1
        reason = str(finding.get("rewrite_absent_reason") or "")
        if reason:
            reasons[reason] += 1
    return {
        "rewrite_status": dict(sorted(status.items())),
        "rewrite_absent_reason": dict(sorted(reasons.items())),
    }


def call_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("callee") or ""),
        str(record.get("demangled_callee") or ""),
        str(record.get("opcode") or ""),
    ]
    return " ".join(parts)


def pass_impl_ir_call_evidence(slice_record: dict[str, Any], needles: list[str]) -> list[dict[str, Any]]:
    if not needles:
        return []
    lowered_needles = [needle.lower() for needle in needles if needle]
    records: list[dict[str, Any]] = []
    for collection in ("call_edges", "instructions"):
        values = slice_record.get(collection)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            text = call_text(item).lower()
            if any(needle in text for needle in lowered_needles):
                records.append(
                    {
                        "source": collection,
                        "id": str(item.get("from") or item.get("id") or ""),
                        "callee": str(item.get("callee") or ""),
                        "demangled_callee": str(item.get("demangled_callee") or ""),
                        "opcode": str(item.get("opcode") or ""),
                        "debug_location": item.get("debug_location") if isinstance(item.get("debug_location"), dict) else {},
                    }
                )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = (record["source"], record["id"], record["demangled_callee"] or record["callee"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def pass_impl_ir_record_text_for_id(slice_record: dict[str, Any]) -> dict[str, str]:
    text_by_id: dict[str, str] = {}
    for collection in ("instructions", "call_edges"):
        values = slice_record.get(collection)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("from") or "")
            if not item_id:
                continue
            text = call_text(item)
            if text.strip():
                text_by_id[item_id] = text
    return text_by_id


def pass_impl_ir_argument_flow_evidence(
    slice_record: dict[str, Any],
    producer_needles: list[str],
    consumer_needles: list[str],
    consumer_arg_index: int | None = None,
) -> list[dict[str, Any]]:
    if not producer_needles or not consumer_needles:
        return []
    lowered_producers = [needle.lower() for needle in producer_needles if needle]
    lowered_consumers = [needle.lower() for needle in consumer_needles if needle]
    text_by_id = pass_impl_ir_record_text_for_id(slice_record)
    records: list[dict[str, Any]] = []
    edges = slice_record.get("call_argument_edges")
    if not isinstance(edges, list):
        return []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if consumer_arg_index is not None and edge.get("arg_index") != consumer_arg_index:
            continue
        producer_id = str(edge.get("from") or "")
        consumer_id = str(edge.get("to") or "")
        producer_text = text_by_id.get(producer_id, "")
        consumer_text = text_by_id.get(consumer_id, call_text(edge))
        if not any(needle in producer_text.lower() for needle in lowered_producers):
            continue
        if not any(needle in consumer_text.lower() for needle in lowered_consumers):
            continue
        records.append(
            {
                "source": "call_argument_edges",
                "from": producer_id,
                "to": consumer_id,
                "kind": str(edge.get("kind") or ""),
                "arg_index": edge.get("arg_index"),
                "callee": str(edge.get("callee") or ""),
                "demangled_callee": str(edge.get("demangled_callee") or ""),
                "producer_text": producer_text,
                "consumer_text": consumer_text,
                "debug_location": edge.get("debug_location") if isinstance(edge.get("debug_location"), dict) else {},
            }
        )
    return records


def pass_impl_ir_rewrite_flow_contract_evidence(
    slice_record: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    for contract in contracts:
        name = str(contract.get("name") or "")
        if not name:
            continue
        producer_needles = [
            str(needle)
            for needle in contract.get("producer_needles", [])
            if isinstance(needle, str) and needle
        ]
        consumer_needles = [
            str(needle)
            for needle in contract.get("consumer_needles", [])
            if isinstance(needle, str) and needle
        ]
        arg_index_value = contract.get("consumer_arg_index")
        arg_index = arg_index_value if isinstance(arg_index_value, int) else None
        records = pass_impl_ir_argument_flow_evidence(
            slice_record,
            producer_needles,
            consumer_needles,
            arg_index,
        )
        if not records and contract.get("fallback_any_arg"):
            records = pass_impl_ir_argument_flow_evidence(
                slice_record,
                producer_needles,
                consumer_needles,
            )
        evidence[name] = records
        if contract.get("required", True) and not records:
            missing.append(name)
    return {
        "rewrite_flow_evidence": evidence,
        "missing_rewrite_flows": missing,
    }


def pass_impl_ir_operand_ref_evidence(
    slice_record: dict[str, Any],
    callee_needles: list[str],
    arg_index: int | None = None,
    expected_value_name: str = "",
) -> list[dict[str, Any]]:
    lowered_needles = [needle.lower() for needle in callee_needles if needle]
    expected = expected_value_name.lower()
    records: list[dict[str, Any]] = []
    refs = slice_record.get("call_operand_refs")
    if not isinstance(refs, list):
        return []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if arg_index is not None and ref.get("arg_index") != arg_index:
            continue
        callee_text = f"{ref.get('callee') or ''} {ref.get('demangled_callee') or ''}".lower()
        if lowered_needles and not any(needle in callee_text for needle in lowered_needles):
            continue
        if expected:
            names = [
                str(ref.get("source_variable") or "").lower(),
                str(ref.get("value_name") or "").lower(),
            ]
            if expected not in names:
                continue
        records.append(
            {
                "source": "call_operand_refs",
                "call": str(ref.get("call") or ""),
                "arg_index": ref.get("arg_index"),
                "value_kind": str(ref.get("value_kind") or ""),
                "value_name": str(ref.get("value_name") or ""),
                "source_variable": str(ref.get("source_variable") or ""),
                "value_type": str(ref.get("value_type") or ""),
                "callee": str(ref.get("callee") or ""),
                "demangled_callee": str(ref.get("demangled_callee") or ""),
                "debug_location": ref.get("debug_location") if isinstance(ref.get("debug_location"), dict) else {},
            }
        )
    return records


def predicate_needles_for(finding: dict[str, Any]) -> list[str]:
    marker = str(finding.get("marker") or "")
    source = " ".join(
        str(finding.get(key) or "")
        for key in ("predicate_source", "matched_pattern", "source")
    )
    needles = ["match"] if "match" in source else []
    for token in ("m_Zero", "m_One", "m_Specific", "m_APInt", "m_AllOnes", "m_NegZero"):
        if token in source:
            needles.append(token)
    for pattern in source_patterns_for_marker(marker):
        token = pattern.split("(", 1)[0].strip()
        if token and token in source:
            needles.append(token)
    return sorted(set(needles))


def rewrite_needles_for(finding: dict[str, Any]) -> list[str]:
    source = str(finding.get("rewrite_source") or "")
    needles: list[str] = []
    for token in (
        "replaceInstUsesWith",
        "eraseFromParent",
        "setInitializer",
        "Create",
        "Insert",
        "Replace",
    ):
        if token in source:
            needles.append(token)
    return sorted(set(needles))


def instcombine_rewrite_nodes_for(finding: dict[str, Any]) -> list[dict[str, Any]]:
    graph = finding.get("source_intent_graph")
    graph = graph if isinstance(graph, dict) else {}
    nodes = graph.get("rewrite_nodes")
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def instcombine_local_definition_for(finding: dict[str, Any], name: str) -> dict[str, Any]:
    if not name:
        return {}
    for node in instcombine_rewrite_nodes_for(finding):
        definitions = node.get("local_definitions")
        if not isinstance(definitions, list):
            continue
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            if str(definition.get("name") or "") == name:
                return definition
    return {}


def instcombine_rewrite_provenance_for(finding: dict[str, Any]) -> dict[str, Any]:
    replacement = ""
    for node in instcombine_rewrite_nodes_for(finding):
        if str(node.get("callee") or "") in {"replaceInstUsesWith", "ReplaceInstWithValue"}:
            replacement = str(node.get("replacement") or "")
            break
    source = str(finding.get("rewrite_source") or "")
    subject = ""
    match = re.search(
        r"(?:replaceInstUsesWith|ReplaceInstWithValue)\s*\((.*)\)",
        source,
    )
    if match:
        args = split_call_arguments(match.group(1))
        if args:
            subject = args[0]
        if not replacement and len(args) >= 2:
            replacement = args[-1]
    definition = instcombine_local_definition_for(finding, replacement)
    replacement_value = definition.get("value") if definition else None
    replacement_source = str(definition.get("source") or "") if definition else ""
    return {
        "subject": subject,
        "replacement": replacement,
        "replacement_value": replacement_value,
        "replacement_definition": definition,
        "replacement_definition_source": replacement_source,
    }


def instcombine_rewrite_operand_evidence_for(
    finding: dict[str, Any],
    slice_record: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    provenance = instcombine_rewrite_provenance_for(finding)
    evidence: dict[str, list[dict[str, Any]]] = {}
    if provenance["subject"]:
        evidence["instruction-subject-to-replace-uses"] = pass_impl_ir_operand_ref_evidence(
            slice_record,
            ["replaceInstUsesWith", "ReplaceInstWithValue"],
            0,
            provenance["subject"],
        )
    if provenance["replacement"]:
        evidence["replacement-to-replace-uses"] = pass_impl_ir_operand_ref_evidence(
            slice_record,
            ["replaceInstUsesWith", "ReplaceInstWithValue"],
            1,
            provenance["replacement"],
        )
    return evidence


def instcombine_derived_rewrite_flow_evidence_for(
    finding: dict[str, Any],
    slice_record: dict[str, Any],
) -> list[dict[str, Any]]:
    provenance = instcombine_rewrite_provenance_for(finding)
    replacement = str(provenance.get("replacement") or "")
    definition = provenance.get("replacement_definition")
    definition = definition if isinstance(definition, dict) else {}
    helper_summary = definition.get("helper_summary")
    helper_summary = helper_summary if isinstance(helper_summary, dict) else {}
    helper_name = str(helper_summary.get("name") or "")
    definition_source = str(provenance.get("replacement_definition_source") or "")
    helper_return_source = str(helper_summary.get("return_source") or "")
    if not definition_source:
        return []
    producer_needles = [
        needle
        for needle in builder_calls_for_registered_operations()
        if needle in definition_source or needle in helper_return_source
    ]
    if not producer_needles and helper_name:
        producer_needles = [helper_name]
    if not producer_needles:
        return []
    direct_flow = pass_impl_ir_argument_flow_evidence(
        slice_record,
        producer_needles,
        ["replaceInstUsesWith", "ReplaceInstWithValue"],
        1,
    )
    if direct_flow:
        return direct_flow
    replacement_refs = pass_impl_ir_operand_ref_evidence(
        slice_record,
        ["replaceInstUsesWith", "ReplaceInstWithValue"],
        1,
        replacement,
    )
    return [
        {
            **record,
            "kind": "local-definition-replacement-operand",
            "producer_text": helper_return_source or definition_source,
            "producer": helper_name or definition_source,
            "consumer_text": call_text(record),
        }
        for record in replacement_refs
    ]


def source_intent_value_kind_and_label(value: Any) -> tuple[str, str]:
    if isinstance(value, bool):
        return "constant", str(int(value))
    if isinstance(value, int):
        return "constant", str(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return "constant", text
        return ("symbol", text) if text else ("", "")
    if isinstance(value, dict):
        symbol = value.get("symbol")
        if isinstance(symbol, str) and symbol:
            return "symbol", symbol
        constant = value.get("constant")
        if constant is not None:
            return "constant", str(constant)
        if "result" in value:
            return source_intent_value_kind_and_label(value.get("result"))
        operation = value.get("operation")
        operands = value.get("operands")
        if isinstance(operation, str) and isinstance(operands, list):
            normalized = normalized_source_intent_expression(value)
            return ("derived", normalized) if normalized else ("unknown", "unknown")
        if value.get("unknown") is True:
            return "unknown", "unknown"
    return "", ""


def normalized_source_intent_expression(value: Any) -> str:
    kind, label = source_intent_value_kind_and_label_without_expression(value)
    if kind in {"symbol", "constant"}:
        return label
    if not isinstance(value, dict):
        return ""
    operation = value.get("operation")
    operands = value.get("operands")
    if not isinstance(operation, str) or not isinstance(operands, list) or len(operands) != 2:
        return ""
    lhs = normalized_source_intent_expression(operands[0])
    rhs = normalized_source_intent_expression(operands[1])
    return f"{operation}({lhs},{rhs})" if lhs and rhs else ""


def source_intent_value_kind_and_label_without_expression(value: Any) -> tuple[str, str]:
    if isinstance(value, bool):
        return "constant", str(int(value))
    if isinstance(value, int):
        return "constant", str(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return "constant", text
        return ("symbol", text) if text else ("", "")
    if isinstance(value, dict):
        symbol = value.get("symbol")
        if isinstance(symbol, str) and symbol:
            return "symbol", symbol
        constant = value.get("constant")
        if constant is not None:
            return "constant", str(constant)
        if "result" in value:
            return source_intent_value_kind_and_label_without_expression(value.get("result"))
        if value.get("unknown") is True:
            return "unknown", "unknown"
    return "", ""


def source_intent_symbol_for(value: Any) -> str:
    kind, label = source_intent_value_kind_and_label(value)
    return label if kind == "symbol" else ""


def source_intent_constant_for(value: Any) -> str:
    kind, label = source_intent_value_kind_and_label(value)
    return label if kind == "constant" else ""


def normalized_rewrite_value_kind_and_label(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        return source_intent_value_kind_and_label(value)
    text = value.strip().rstrip(";")
    if not text:
        return "", ""
    if re.fullmatch(r"-?\d+", text):
        return "constant", text
    builder_expression = normalized_builder_expression(text)
    if builder_expression:
        return "derived", builder_expression
    if re.search(r"\bConstant::getNullValue\s*\(", text):
        return "constant", "0"
    if re.search(r"\bConstantInt::get\s*\(", text):
        numeric_args = re.findall(r"(?<![A-Za-z0-9_])-?\d+(?![A-Za-z0-9_])", text)
        return "constant", numeric_args[-1] if numeric_args else "0"
    return "symbol", text


def split_call_arguments(text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def normalized_rewrite_atom(text: str) -> str:
    kind, label = normalized_rewrite_value_kind_and_label(text)
    return label if kind in {"symbol", "constant", "derived"} else ""


def normalized_builder_expression(text: str) -> str:
    builder_pattern = "|".join(re.escape(name) for name in sorted(OPERATION_FOR_BUILDER_CALL))
    match = re.search(rf"\b({builder_pattern})\s*\((.*)\)\s*$", text)
    if not match:
        return ""
    args = split_call_arguments(match.group(2))
    if len(args) < 2:
        return ""
    lhs = normalized_rewrite_atom(args[0])
    rhs = normalized_rewrite_atom(args[1])
    return f"{OPERATION_FOR_BUILDER_CALL[match.group(1)]}({lhs},{rhs})" if lhs and rhs else ""


def derived_expression_components(label: str) -> tuple[set[str], set[str]]:
    symbols = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", label))
    symbols -= set(OPERATION_FOR_BUILDER_CALL.values())
    constants = set(re.findall(r"(?<![A-Za-z0-9_])-?\d+(?![A-Za-z0-9_])", label))
    return symbols, constants


def instcombine_rewrite_binding_qualifiers(source_intent: dict[str, Any]) -> list[str]:
    guards = source_intent.get("guards")
    if not isinstance(guards, list) or not guards:
        return []
    qualifiers = ["guarded-replacement"]
    guard_text = " ".join(json.dumps(guard, sort_keys=True) for guard in guards if isinstance(guard, dict))
    if "poison" in guard_text.lower():
        qualifiers.append("poison-sensitive-replacement")
    return qualifiers


def instcombine_rewrite_binding_check_for(
    finding: dict[str, Any],
    rewrite_operand_evidence: dict[str, list[dict[str, Any]]],
    slice_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance = instcombine_rewrite_provenance_for(finding)
    replacement = provenance["replacement"]
    replacement_value = provenance.get("replacement_value")
    source_intent = finding.get("source_intent")
    source_intent = source_intent if isinstance(source_intent, dict) else {}
    before = source_intent.get("before")
    before = before if isinstance(before, dict) else {}
    after = source_intent.get("after")
    after = after if isinstance(after, dict) else {}
    operands = before.get("operands")
    operands = operands if isinstance(operands, list) else []
    operand_values = [source_intent_value_kind_and_label(item) for item in operands]
    operand_symbols = [label for kind, label in operand_values if kind == "symbol" and label]
    operand_constants = [label for kind, label in operand_values if kind == "constant" and label]
    source_result_value = after.get("result")
    source_result_kind, source_result = source_intent_value_kind_and_label(source_result_value)
    if replacement_value is not None:
        replacement_kind, normalized_replacement = source_intent_value_kind_and_label(replacement_value)
    else:
        replacement_kind, normalized_replacement = normalized_rewrite_value_kind_and_label(replacement)
    replacement_refs = rewrite_operand_evidence.get("replacement-to-replace-uses") or []
    replacement_all_refs = pass_impl_ir_operand_ref_evidence(
        slice_record or {},
        ["replaceInstUsesWith", "ReplaceInstWithValue"],
        1,
        "",
    )
    replacement_ref_symbols = sorted(
        {
            str(record.get("source_variable") or record.get("value_name") or "")
            for record in (replacement_refs or replacement_all_refs)
            if str(record.get("source_variable") or record.get("value_name") or "")
        }
    )
    evidence = {
        "replacement_symbol": replacement,
        "replacement_role": "unknown",
        "replacement_qualifiers": instcombine_rewrite_binding_qualifiers(source_intent),
        "replacement_definition_source": str(provenance.get("replacement_definition_source") or ""),
        "replacement_helper": str(
            (provenance.get("replacement_definition") or {}).get("helper_summary", {}).get("name")
            if isinstance(provenance.get("replacement_definition"), dict)
            and isinstance((provenance.get("replacement_definition") or {}).get("helper_summary"), dict)
            else ""
        ),
        "source_result": source_result,
        "normalized_replacement": normalized_replacement,
        "normalized_source_result": source_result,
        "source_operands": operand_symbols,
        "source_constants": operand_constants,
        "operand_ref_symbols": replacement_ref_symbols,
    }
    if not source_intent or not after:
        return {
            "status": "partial",
            "evidence": evidence,
            "reasons": ["missing-rewrite-binding-evidence"],
        }
    if source_result_kind == "constant":
        evidence["replacement_role"] = "constant-replacement"
        if not normalized_replacement:
            return {
                "status": "partial",
                "evidence": evidence,
                "reasons": ["missing-rewrite-binding-evidence"],
            }
        if replacement_kind != "constant" or normalized_replacement != source_result:
            return {
                "status": "mismatch",
                "evidence": evidence,
                "reasons": ["replacement-binding-mismatch"],
            }
        return {"status": "matched", "evidence": evidence, "reasons": []}
    if source_result_kind == "derived":
        evidence["replacement_role"] = "derived-expression"
        if not normalized_replacement:
            return {
                "status": "partial",
                "evidence": evidence,
                "reasons": ["missing-rewrite-binding-evidence"],
            }
        if replacement_kind != "derived":
            return {
                "status": "partial",
                "evidence": evidence,
                "reasons": ["missing-rewrite-binding-evidence"],
            }
        if normalized_replacement != source_result:
            return {
                "status": "mismatch",
                "evidence": evidence,
                "reasons": ["replacement-binding-mismatch"],
            }
        derived_symbols, derived_constants = derived_expression_components(source_result)
        if not derived_symbols.issubset(set(operand_symbols)) or not derived_constants.issubset(set(operand_constants)):
            return {
                "status": "partial",
                "evidence": evidence,
                "reasons": ["missing-rewrite-binding-evidence"],
            }
        return {"status": "matched", "evidence": evidence, "reasons": []}
    if source_result_kind == "unknown" or (source_result_value is not None and source_result_kind not in {"symbol", "constant"}):
        evidence["replacement_role"] = "derived-expression"
        return {
            "status": "partial",
            "evidence": evidence,
            "reasons": ["missing-rewrite-binding-evidence"],
        }
    if not normalized_replacement or not source_result:
        return {
            "status": "partial",
            "evidence": evidence,
            "reasons": ["missing-rewrite-binding-evidence"],
        }
    if replacement_kind != "symbol" or normalized_replacement != source_result:
        return {
            "status": "mismatch",
            "evidence": evidence,
            "reasons": ["replacement-binding-mismatch"],
        }
    if source_result not in operand_symbols:
        return {
            "status": "partial",
            "evidence": evidence,
            "reasons": ["missing-rewrite-binding-evidence"],
        }
    evidence["replacement_role"] = "preserved-source-operand"
    if replacement_ref_symbols and normalized_replacement not in replacement_ref_symbols:
        return {
            "status": "mismatch",
            "evidence": evidence,
            "reasons": ["replacement-binding-mismatch"],
        }
    return {"status": "matched", "evidence": evidence, "reasons": []}


def pass_impl_ir_intent_shape(predicate_needles: list[str], rewrite_needles: list[str]) -> str:
    if predicate_needles and rewrite_needles:
        return "predicate-and-rewrite"
    if predicate_needles:
        return "predicate-only"
    return "unsupported"


def pass_impl_ir_marker_family(marker: str) -> str:
    spec = registry_spec_for_marker(marker)
    if str(spec.get("pass") or ""):
        return str(spec.get("pass"))
    if marker.startswith("probe.instcombine."):
        return "instcombine"
    if marker.startswith("probe.dse."):
        return "dse"
    if marker == "probe.globalopt.dead-initializer":
        return "globalopt"
    return "unsupported"


DSE_FACT_NEEDLES = {
    "memoryssa.dead-store": ["getMemoryAccess", "isLiveOnEntryDef", "isRemovable"],
    "memoryssa.clobber": ["getClobberingMemoryAccess", "getDomMemoryDef"],
    "memory.no-intervening-store": ["noInterveningStore", "getLocForWrite", "getDomMemoryDef"],
    "memory.no-intervening-read": [
        "noInterveningRead",
        "noReadBetween",
        "mayReadFromMemory",
        "mayReadOrWriteMemory",
    ],
    "memory.no-intervening-memory-effect": [
        "noInterveningMemoryAccess",
        "noUnknownMemoryEffect",
        "mayReadOrWriteMemory",
        "mayHaveSideEffects",
    ],
    "memory.unknown-intervening-effect": [
        "mayReadOrWriteMemory",
        "mayReadFromMemory",
        "mayHaveSideEffects",
        "unknownMemoryEffect",
    ],
    "memory.overwrite.full": [
        "isCompleteOverwrite",
        "isOverwriteComplete",
        "covers",
        "fullyOverwrites",
        "CompleteOverwrite",
    ],
    "memory.overwrite.partial": [
        "isPartialOverwrite",
        "partialOverlap",
        "mayPartiallyOverwrite",
        "PartialOverwrite",
    ],
    "memory.overwrite.partial.fixed-byte-mask": [
        "fixedPartialOverwrite",
        "knownPartialOverwriteByteMask",
        "partialOverwriteByteMask",
        "FixedPartialOverwrite",
    ],
    "memory.overwrite.size.known": [
        "hasKnownSize",
        "hasValue",
        "getValue",
        "getSizeInBytes",
        "LocationSize::precise",
        "knownSizeWithinFourBytes",
        "knownSizeWithinEightBytes",
    ],
    "memory.overwrite.size.symbolic-bounded-eight-lane": [
        "unknownSize",
        "sameSize",
        "equalSize",
        "==",
        "knownSizeWithinEightBytes",
        "getSizeInBytes",
        "getValue",
        "<= 8",
    ],
    "memory.overwrite.size.symbolic-bounded-four-lane": [
        "unknownSize",
        "sameSize",
        "equalSize",
        "==",
        "knownSizeWithinFourBytes",
        "getSizeInBytes",
        "getValue",
        "<= 4",
        "< 5",
    ],
    "memory.overwrite.size.symbolic-equal": [
        "sameSize",
        "equalSize",
        "sameUnknownSize",
        "==",
        "getValue",
        "Size",
    ],
    "memory.overwrite.size.symbolic-upper-bound": [
        "knownSizeWithinEightBytes",
        "getSizeInBytes",
        "getValue",
        "<= 8",
        "< 9",
    ],
    "memory.overwrite.size.bounded-four-lane": [
        "knownSizeWithinFourBytes",
        "getValue",
        "getSizeInBytes",
        "LocationSize::precise",
    ],
    "memory.overwrite.size.bounded-eight-lane": [
        "knownSizeWithinEightBytes",
        "getValue",
        "getSizeInBytes",
        "LocationSize::precise",
    ],
    "memory.overwrite.nonoverlap": ["NoOverlap", "nonOverlapping", "overlap"],
    "memory.overwrite.unknown-size": [
        "unknownSize",
        "hasKnownSize",
        "LocationSize::unknown",
    ],
    "alias.noalias": ["isNoAlias", "mayAlias", "NoAlias"],
    "alias.unknown": ["mayAlias"],
    "memory.volatile-atomic-blocker": ["isVolatile", "isAtomic"],
    "memory.volatile-blocker": ["isVolatile"],
    "memory.atomic-unordered-blocker": ["isAtomic", "getOrdering", "AtomicOrdering::Unordered"],
    "memory.atomic-ordered-blocker": [
        "isAtomic",
        "getOrdering",
        "AtomicOrdering::Monotonic",
        "AtomicOrdering::Acquire",
        "AtomicOrdering::Release",
        "AtomicOrdering::AcquireRelease",
        "AtomicOrdering::SequentiallyConsistent",
    ],
    "memory.atomic-ordering-unknown-blocker": ["isAtomic", "getOrdering", "unknownAtomicOrdering"],
}
DSE_REWRITE_NEEDLES = [
    "deleteDeadInstruction",
    "eraseFromParent",
    "DeleteDeadInstruction",
    "RecursivelyDeleteTriviallyDeadInstructions",
]


def dse_analysis_facts_for_finding(finding: dict[str, Any]) -> list[dict[str, Any]]:
    facts = normalize_analysis_facts(finding.get("analysis_facts"))
    if facts:
        return facts
    graph = finding.get("source_intent_graph")
    if isinstance(graph, dict):
        facts = normalize_analysis_facts(graph.get("analysis_facts"))
        if facts:
            return facts
    evidence = finding.get("evidence")
    if isinstance(evidence, dict):
        facts = normalize_analysis_facts(evidence.get("analysis_facts"))
        if facts:
            return facts
        params = evidence.get("formal_parameters")
        if isinstance(params, dict):
            facts = normalize_analysis_facts(params.get("analysis_facts"))
            if facts:
                return facts
    return []


def dse_impl_ir_evidence_for(
    slice_record: dict[str, Any],
    kinds: list[str],
) -> dict[str, list[dict[str, Any]]]:
    return {
        kind: pass_impl_ir_call_evidence(slice_record, DSE_FACT_NEEDLES.get(kind, [kind]))
        for kind in kinds
    }


def pass_impl_ir_dse_check_for(
    finding: dict[str, Any],
    slice_record: dict[str, Any],
) -> dict[str, Any]:
    marker = str(finding.get("marker") or "")
    facts = dse_analysis_facts_for_finding(finding)
    contract = dse_analysis_fact_contract(marker, facts)
    kinds = sorted({str(fact.get("kind") or "") for fact in facts if str(fact.get("kind") or "")})
    required = [str(kind) for kind in contract.get("required", [])]
    if "memory.overwrite.size.bounded-eight-lane" in kinds:
        required = [
            "memory.overwrite.size.bounded-eight-lane"
            if kind == "memory.overwrite.size.bounded-four-lane"
            else kind
            for kind in required
        ]
    if "memory.overwrite.size.symbolic-bounded-eight-lane" in kinds:
        required = [
            "memory.overwrite.size.symbolic-bounded-eight-lane"
            if kind in {"memory.overwrite.size.known", "memory.overwrite.size.bounded-four-lane"}
            else kind
            for kind in required
        ]
    if "memory.overwrite.size.symbolic-bounded-four-lane" in kinds:
        required = [
            "memory.overwrite.size.symbolic-bounded-four-lane"
            if kind in {"memory.overwrite.size.known", "memory.overwrite.size.bounded-four-lane"}
            else kind
            for kind in required
        ]
    required = list(dict.fromkeys(required))
    blockers = [str(kind) for kind in contract.get("blockers", [])]
    missing_source = [str(kind) for kind in contract.get("missing", [])]
    rewrite_status = str(finding.get("rewrite_status") or "")
    rewrite_absent_reason = str(finding.get("rewrite_absent_reason") or "")
    evidence_kinds = sorted({*required, *blockers, *kinds})
    fact_evidence = dse_impl_ir_evidence_for(slice_record, evidence_kinds)
    rewrite_evidence = pass_impl_ir_call_evidence(slice_record, DSE_REWRITE_NEEDLES)
    missing_impl = [
        kind for kind in required
        if not fact_evidence.get(kind)
    ]
    reasons: list[str] = []
    if blockers:
        status = "blocked"
        reasons.extend(f"blocked-by-source-fact:{kind}" for kind in blockers)
        for kind in blockers:
            if not fact_evidence.get(kind):
                reasons.append(f"missing-blocker-impl-evidence:{kind}")
    elif missing_source:
        status = "source-incomplete"
        reasons.extend(f"missing-source-analysis-fact:{kind}" for kind in missing_source)
    elif not facts:
        status = "source-incomplete"
        reasons.append("missing-source-analysis-facts")
    else:
        if rewrite_status and rewrite_status != "found":
            reasons.append("missing-source-rewrite")
            if rewrite_absent_reason:
                reasons.append(rewrite_absent_reason)
        if not rewrite_evidence:
            reasons.append("missing-dse-rewrite-evidence")
        reasons.extend(f"missing-dse-impl-evidence:{kind}" for kind in missing_impl)
        if not reasons:
            status = "matched"
        else:
            status = "impl-ir-incomplete"
    return {
        "model": "llvm-pass-impl-ir-intent-check-v1",
        "status": status,
        "intent_shape": "dse-analysis-facts",
        "expected_predicate": True,
        "expected_rewrite": not blockers and not missing_source,
        "analysis_fact_kinds": kinds,
        "required_analysis_facts": required,
        "missing_source_analysis_facts": missing_source,
        "source_analysis_fact_blockers": blockers,
        "analysis_fact_impl_ir_evidence": fact_evidence,
        "rewrite_needles": DSE_REWRITE_NEEDLES,
        "rewrite_evidence": rewrite_evidence,
        "missing_impl_ir_evidence": missing_impl,
        "reasons": reasons,
    }


def global_safety_evidence_for(finding: dict[str, Any]) -> list[dict[str, Any]]:
    for container_name in ("source_intent", "source_intent_graph"):
        container = finding.get(container_name)
        if not isinstance(container, dict):
            continue
        records = container.get("safety_provenance")
        if isinstance(records, list):
            return [dict(record) for record in records if isinstance(record, dict)]
    return []


GLOBALOPT_REQUIRED_SAFETY_FACTS = ("initializer-dead", "local-linkage", "no-uses")
GLOBALOPT_SAFETY_FACT_NEEDLES = {
    "initializer-dead": ["isGlobalInitializerDead"],
    "local-linkage": ["hasLocalLinkage"],
    "no-uses": ["use_empty"],
}
GLOBALOPT_REPLACEMENT_KIND = "default-null-initializer"
GLOBALOPT_REPLACEMENT_NEEDLES = {
    "null_factory": ["getNullValue"],
    "value_type": ["getValueType"],
}
GLOBALOPT_REPLACEMENT_FLOW_CONTRACTS = [
    {
        "name": "value-type-to-null-factory",
        "producer_needles": ["getValueType"],
        "consumer_needles": ["getNullValue"],
        "consumer_arg_index": 0,
        "required": True,
    },
    {
        "name": "null-factory-to-set-initializer",
        "producer_needles": ["getNullValue"],
        "consumer_needles": ["setInitializer"],
        "consumer_arg_index": 1,
        "fallback_any_arg": True,
        "required": True,
    },
]
GLOBALOPT_REPLACEMENT_FLOW_COMPAT_KEYS = {
    "value-type-to-null-factory": "value_type_to_null_factory",
    "null-factory-to-set-initializer": "null_factory_to_set_initializer",
}


def global_required_safety_facts_for(finding: dict[str, Any]) -> list[str]:
    for container_name in ("source_intent_graph", "source_intent"):
        container = finding.get(container_name)
        if not isinstance(container, dict):
            continue
        facts = container.get("required_safety_facts")
        if isinstance(facts, list):
            values = sorted({str(fact) for fact in facts if isinstance(fact, str) and fact})
            if values:
                return values
    return list(GLOBALOPT_REQUIRED_SAFETY_FACTS)


def global_source_safety_missing_facts_for(finding: dict[str, Any]) -> list[str]:
    for container_name in ("source_intent_graph", "source_intent"):
        container = finding.get(container_name)
        if not isinstance(container, dict):
            continue
        missing = container.get("missing_safety_facts")
        if isinstance(missing, list):
            values = sorted({str(fact) for fact in missing if isinstance(fact, str) and fact})
            if values:
                return values
    return []


def global_source_safety_status_for(finding: dict[str, Any]) -> str:
    for container_name in ("source_intent_graph", "source_intent"):
        container = finding.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in ("safety_provenance_status", "safety_status"):
            status = container.get(key)
            if isinstance(status, str) and status:
                return status
    return ""


def global_safety_ir_evidence_for(
    slice_record: dict[str, Any],
    required_facts: list[str],
) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {}
    for fact in required_facts:
        needles = GLOBALOPT_SAFETY_FACT_NEEDLES.get(fact, [fact])
        evidence[fact] = pass_impl_ir_call_evidence(slice_record, needles)
    return evidence


def global_replacement_provenance_for(finding: dict[str, Any]) -> dict[str, str]:
    graph = finding.get("source_intent_graph")
    graph = graph if isinstance(graph, dict) else {}
    source_intent = finding.get("source_intent")
    source_intent = source_intent if isinstance(source_intent, dict) else {}
    rewrite = source_intent.get("rewrite")
    rewrite = rewrite if isinstance(rewrite, dict) else {}
    rewrite_node: dict[str, Any] = {}
    nodes = graph.get("rewrite_nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get("action") or "") == "remove-global-initializer-if-dead-v1":
                rewrite_node = node
                break
            if not rewrite_node and (
                node.get("replacement_kind")
                or node.get("replacement_expr")
                or node.get("value_type_expr")
            ):
                rewrite_node = node
    return {
        "kind": str(
            rewrite_node.get("replacement_kind")
            or graph.get("replacement_kind")
            or rewrite.get("replacement_kind")
            or rewrite.get("replacement")
            or ""
        ),
        "expr": str(
            rewrite_node.get("replacement_expr")
            or graph.get("replacement_expr")
            or rewrite.get("replacement_expr")
            or ""
        ),
        "value_type_expr": str(
            rewrite_node.get("value_type_expr")
            or graph.get("value_type_expr")
            or rewrite.get("value_type_expr")
            or ""
        ),
    }


def global_replacement_ir_evidence_for(
    slice_record: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    return {
        key: pass_impl_ir_call_evidence(slice_record, needles)
        for key, needles in GLOBALOPT_REPLACEMENT_NEEDLES.items()
    }


def global_replacement_flow_evidence_for(
    slice_record: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    contract_result = pass_impl_ir_rewrite_flow_contract_evidence(
        slice_record,
        GLOBALOPT_REPLACEMENT_FLOW_CONTRACTS,
    )
    evidence = contract_result["rewrite_flow_evidence"]
    return {
        compat_key: evidence.get(contract_name, [])
        for contract_name, compat_key in GLOBALOPT_REPLACEMENT_FLOW_COMPAT_KEYS.items()
    }


def pass_impl_ir_globalopt_check_for(
    finding: dict[str, Any],
    slice_record: dict[str, Any],
) -> dict[str, Any]:
    base = {
        "model": "llvm-pass-impl-ir-intent-check-v1",
        "intent_shape": "global-rewrite",
        "expected_predicate": True,
        "expected_rewrite": True,
        "global_rewrite_api": "setInitializer",
    }
    rewrite_status = str(finding.get("rewrite_status") or "")
    rewrite_absent_reason = str(finding.get("rewrite_absent_reason") or "")
    rewrite_evidence = pass_impl_ir_call_evidence(slice_record, ["setInitializer"])
    safety_evidence = global_safety_evidence_for(finding)
    required_safety_facts = global_required_safety_facts_for(finding)
    source_missing_safety_facts = global_source_safety_missing_facts_for(finding)
    source_safety_status = global_source_safety_status_for(finding)
    safety_ir_evidence = global_safety_ir_evidence_for(slice_record, required_safety_facts)
    missing_ir_safety_facts = [
        fact for fact in required_safety_facts if not safety_ir_evidence.get(fact)
    ]
    replacement = global_replacement_provenance_for(finding)
    replacement_kind = replacement["kind"]
    replacement_expr = replacement["expr"]
    value_type_expr = replacement["value_type_expr"]
    replacement_ir_evidence = global_replacement_ir_evidence_for(slice_record)
    missing_replacement_evidence = [
        key for key in ("null_factory", "value_type")
        if not replacement_ir_evidence.get(key)
    ]
    rewrite_flow_result = pass_impl_ir_rewrite_flow_contract_evidence(
        slice_record,
        GLOBALOPT_REPLACEMENT_FLOW_CONTRACTS,
    )
    rewrite_flow_evidence = rewrite_flow_result["rewrite_flow_evidence"]
    missing_rewrite_flows = rewrite_flow_result["missing_rewrite_flows"]
    replacement_flow_evidence = global_replacement_flow_evidence_for(slice_record)
    reasons: list[str] = []
    if rewrite_status and rewrite_status != "found":
        reasons.append("missing-source-rewrite")
        if rewrite_absent_reason:
            reasons.append(rewrite_absent_reason)
        status = "source-incomplete"
    elif source_safety_status == "incomplete" or source_missing_safety_facts:
        reasons.extend(
            f"missing-source-safety-fact:{fact}" for fact in source_missing_safety_facts
        )
        if not source_missing_safety_facts:
            reasons.append("missing-source-safety-provenance")
        status = "source-incomplete"
    elif not replacement_kind:
        reasons.append("missing-source-replacement-provenance")
        status = "source-incomplete"
    elif replacement_kind != GLOBALOPT_REPLACEMENT_KIND:
        reasons.append("unsupported-global-replacement-kind")
        status = "mismatch"
    else:
        if not rewrite_evidence:
            reasons.append("missing-rewrite-evidence")
        reasons.extend(
            f"missing-global-safety-evidence:{fact}" for fact in missing_ir_safety_facts
        )
        reasons.extend(
            f"missing-global-replacement-evidence:{key.replace('_', '-')}"
            for key in missing_replacement_evidence
        )
        reasons.extend(
            f"missing-global-replacement-flow:{key}" for key in missing_rewrite_flows
        )
        if (
            rewrite_evidence
            and not missing_ir_safety_facts
            and not missing_replacement_evidence
            and not missing_rewrite_flows
        ):
            status = "matched"
        elif (
            rewrite_evidence
            or len(missing_ir_safety_facts) < len(required_safety_facts)
            or len(missing_replacement_evidence) < len(GLOBALOPT_REPLACEMENT_NEEDLES)
            or len(missing_rewrite_flows) < len(GLOBALOPT_REPLACEMENT_FLOW_CONTRACTS)
        ):
            status = "partial"
        else:
            status = "mismatch"
    if (
        not reasons
        and rewrite_evidence
        and not missing_ir_safety_facts
        and not missing_replacement_evidence
        and not missing_rewrite_flows
    ):
        status = "matched"
    return {
        **base,
        "status": status,
        "rewrite_needles": ["setInitializer"],
        "rewrite_evidence": rewrite_evidence,
        "global_rewrite_evidence": rewrite_evidence,
        "global_safety_evidence": safety_evidence,
        "global_required_safety_facts": required_safety_facts,
        "global_safety_ir_evidence": safety_ir_evidence,
        "global_replacement_kind": replacement_kind,
        "global_replacement_expr": replacement_expr,
        "global_value_type_expr": value_type_expr,
        "global_replacement_ir_evidence": replacement_ir_evidence,
        "rewrite_flow_evidence": rewrite_flow_evidence,
        "global_replacement_flow_evidence": replacement_flow_evidence,
        "reasons": reasons,
    }


def pass_impl_ir_intent_check_for(finding: dict[str, Any]) -> dict[str, Any]:
    marker = str(finding.get("marker") or "")
    slice_record = finding.get("pass_impl_ir_slice")
    base = {"model": "llvm-pass-impl-ir-intent-check-v1"}
    if not isinstance(slice_record, dict) or slice_record.get("status") != "matched":
        reason = "missing-pass-impl-ir-slice"
        if isinstance(slice_record, dict):
            reason = str(slice_record.get("reason") or "pass-impl-ir-slice-not-matched")
        return {**base, "status": "unsupported", "reasons": [reason]}
    if marker == "probe.globalopt.dead-initializer":
        return pass_impl_ir_globalopt_check_for(finding, slice_record)
    if marker.startswith("probe.dse."):
        return pass_impl_ir_dse_check_for(finding, slice_record)
    if not marker.startswith("probe.instcombine."):
        return {**base, "status": "unsupported", "reasons": ["unsupported-marker-family"]}

    predicate_needles = predicate_needles_for(finding)
    rewrite_needles = rewrite_needles_for(finding)
    rewrite_status = str(finding.get("rewrite_status") or "")
    rewrite_absent_reason = str(finding.get("rewrite_absent_reason") or "")
    intent_shape = pass_impl_ir_intent_shape(predicate_needles, rewrite_needles)
    expected_predicate = bool(predicate_needles)
    expected_rewrite = bool(rewrite_needles)
    if not predicate_needles and not rewrite_needles:
        return {
            **base,
            "status": "unsupported",
            "intent_shape": intent_shape,
            "expected_predicate": expected_predicate,
            "expected_rewrite": expected_rewrite,
            "reasons": ["unsupported-intent-shape"],
        }

    predicate_evidence = pass_impl_ir_call_evidence(slice_record, predicate_needles)
    rewrite_evidence = pass_impl_ir_call_evidence(slice_record, rewrite_needles)
    rewrite_flow_evidence: dict[str, list[dict[str, Any]]] = {}
    rewrite_operand_evidence: dict[str, list[dict[str, Any]]] = {}
    missing_rewrite_operand_flows: list[str] = []
    rewrite_binding_status = ""
    rewrite_binding_evidence: dict[str, Any] = {}
    missing_rewrite_binding = False
    rewrite_binding_mismatch = False
    if "replaceInstUsesWith" in rewrite_needles:
        rewrite_operand_evidence = instcombine_rewrite_operand_evidence_for(
            finding,
            slice_record,
        )
        for required_flow in (
            "instruction-subject-to-replace-uses",
            "replacement-to-replace-uses",
        ):
            if required_flow in rewrite_operand_evidence and not rewrite_operand_evidence[required_flow]:
                missing_rewrite_operand_flows.append(required_flow)
        binding_check = instcombine_rewrite_binding_check_for(
            finding,
            rewrite_operand_evidence,
            slice_record,
        )
        rewrite_binding_status = str(binding_check.get("status") or "")
        rewrite_binding_evidence = dict(binding_check.get("evidence") or {})
        derived_flow = instcombine_derived_rewrite_flow_evidence_for(finding, slice_record)
        if derived_flow:
            rewrite_flow_evidence["derived-builder-to-replacement"] = derived_flow
        if rewrite_binding_evidence.get("replacement_role") in {"constant-replacement", "derived-expression"}:
            missing_rewrite_operand_flows = [
                flow for flow in missing_rewrite_operand_flows
                if flow != "replacement-to-replace-uses"
            ]
        missing_rewrite_binding = rewrite_binding_status == "partial"
        rewrite_binding_mismatch = rewrite_binding_status == "mismatch"
    reasons: list[str] = []
    if predicate_needles and not predicate_evidence:
        reasons.append("missing-predicate-evidence")
    if rewrite_needles and not rewrite_evidence:
        reasons.append("missing-rewrite-evidence")
    reasons.extend(
        f"missing-rewrite-operand-flow:{flow}" for flow in missing_rewrite_operand_flows
    )
    if missing_rewrite_binding:
        reasons.append("missing-rewrite-binding-evidence")
    if rewrite_binding_mismatch:
        reasons.append("replacement-binding-mismatch")

    if predicate_evidence and rewrite_status and rewrite_status != "found":
        status = "source-incomplete"
        reasons.append("missing-source-rewrite")
        if rewrite_absent_reason:
            reasons.append(rewrite_absent_reason)
    elif predicate_evidence and not expected_rewrite:
        status = "source-incomplete"
        reasons.append("missing-source-rewrite")
    elif rewrite_binding_mismatch:
        status = "mismatch"
    elif (
        predicate_evidence
        and rewrite_evidence
        and not missing_rewrite_operand_flows
        and not missing_rewrite_binding
    ):
        status = "matched"
    elif predicate_evidence or rewrite_evidence:
        status = "partial"
    else:
        status = "mismatch"
    return {
        **base,
        "status": status,
        "intent_shape": intent_shape,
        "expected_predicate": expected_predicate,
        "expected_rewrite": expected_rewrite,
        "predicate_needles": predicate_needles,
        "rewrite_needles": rewrite_needles,
        "predicate_evidence": predicate_evidence,
        "rewrite_evidence": rewrite_evidence,
        "rewrite_flow_evidence": rewrite_flow_evidence,
        "rewrite_operand_evidence": rewrite_operand_evidence,
        "rewrite_binding_status": rewrite_binding_status,
        "rewrite_binding_evidence": rewrite_binding_evidence,
        "reasons": reasons,
    }


def pass_impl_ir_intent_check_summary(findings: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    status = collections.Counter()
    reasons = collections.Counter()
    shapes = collections.Counter()
    family_status: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for finding in findings:
        check = finding.get("pass_impl_ir_intent_check")
        if not isinstance(check, dict):
            continue
        check_status = str(check.get("status") or "unset")
        family = pass_impl_ir_marker_family(str(finding.get("marker") or ""))
        status[check_status] += 1
        family_status[family][check_status] += 1
        shapes[str(check.get("intent_shape") or "unset")] += 1
        for reason in check.get("reasons") or []:
            reasons[str(reason)] += 1
    return {
        "intent_check_status": dict(sorted(status.items())),
        "intent_check_reasons": dict(sorted(reasons.items())),
        "intent_check_shape": dict(sorted(shapes.items())),
        "intent_check_family_status": {
            family: dict(sorted(counts.items()))
            for family, counts in sorted(family_status.items())
        },
    }


def record_transaction(record: dict[str, Any]) -> dict[str, Any] | None:
    tx = record.get("optimization_transaction")
    if isinstance(tx, dict):
        return tx
    evidence = record.get("evidence")
    if isinstance(evidence, dict) and isinstance(evidence.get("optimization_transaction"), dict):
        return evidence["optimization_transaction"]
    return None


def has_slp_transaction(records: list[dict[str, Any]]) -> bool:
    return any(
        str((record_transaction(record) or {}).get("kind") or "").startswith("slp-vectorize-")
        for record in records
    )


def slp_transaction_ir_summary(enabled: bool, out_dir: Path, data: dict[str, Any] | None) -> dict[str, Any]:
    summary = data if isinstance(data, dict) else {}
    manifest = out_dir / "manifest.jsonl"
    return {
        "enabled": enabled,
        "out_dir": str(out_dir),
        "manifest": str(manifest) if manifest.exists() else "",
        "summary": str(out_dir / "summary.json") if summary else "",
        "generated": int(summary.get("generated") or 0),
        "skipped": int(summary.get("skipped") or 0),
        "graph_ir": nested_dict(summary, "graph_ir"),
        "unsupported_graph_reasons": nested_dict(summary, "unsupported_graph_reasons"),
        "ir_validation": nested_dict(summary, "ir_validation"),
    }


def transaction_graph_readiness(findings: list[dict[str, Any]]) -> dict[str, Any]:
    graph_status = collections.Counter()
    graph_consistency = collections.Counter()
    absent_reasons = collections.Counter()
    memory_contracts = collections.Counter()
    store_contracts = collections.Counter()
    samples: list[dict[str, Any]] = []
    total = 0
    for record in findings:
        tx = record_transaction(record)
        if not tx:
            continue
        total += 1
        graph = tx.get("transaction_graph")
        if isinstance(graph, dict):
            graph_status["present"] += 1
            graph_consistency[str(graph.get("consistency") or "unset")] += 1
            operands = graph.get("operands")
            if isinstance(operands, list):
                for operand in operands:
                    if isinstance(operand, dict) and operand.get("kind") == "memory-pack":
                        memory_contracts[str(operand.get("memory_contract") or "unset")] += 1
            store_sinks = graph.get("store_sinks")
            if isinstance(store_sinks, list):
                for sink in store_sinks:
                    if isinstance(sink, dict):
                        store_contracts[str(sink.get("store_contract") or "unset")] += 1
            continue
        graph_status["absent"] += 1
        reasons = tx.get("transaction_graph_absent_reasons")
        if isinstance(reasons, list) and reasons:
            reason_values = [str(item) for item in reasons if str(item)]
        else:
            reason = str(tx.get("transaction_graph_absent_reason") or "unset")
            reason_values = [reason]
        for reason in reason_values:
            absent_reasons[reason] += 1
        if len(samples) < 10:
            samples.append(
                {
                    "file": str(record.get("file") or ""),
                    "line": int(record.get("line") or 0),
                    "marker": str(record.get("marker") or ""),
                    "reasons": reason_values,
                }
            )
    return {
        "transactions": total,
        "graph_status": dict(sorted(graph_status.items())),
        "graph_consistency": dict(sorted(graph_consistency.items())),
        "absent_reasons": dict(sorted(absent_reasons.items())),
        "memory_contracts": dict(sorted(memory_contracts.items())),
        "store_contracts": dict(sorted(store_contracts.items())),
        "absent_samples": samples,
    }


def real_pass_readiness_report(
    summary: dict[str, Any],
    findings: list[dict[str, Any]],
    validated_records: list[dict[str, Any]],
    slp_ir: dict[str, Any],
) -> dict[str, Any]:
    graph = transaction_graph_readiness(findings)
    coverage = nested_dict(summary, "coverage")
    source_program_graph_contract = nested_dict(coverage, "source_program_graph_contract")
    pass_impl_ir = nested_dict(summary, "pass_impl_ir")
    modelcheck = nested_dict(summary, "modelcheck")
    intent_status = nested_dict(pass_impl_ir, "intent_check_status")
    intent_recommendation = ""
    if int(intent_status.get("source-incomplete") or 0) > 0:
        intent_recommendation = "improve source rewrite extraction for predicate-only findings"
    elif int(intent_status.get("impl-ir-incomplete") or 0) > 0:
        intent_recommendation = "add implementation IR evidence for source-derived intent facts"
    return {
        "model": "o2t-real-pass-readiness-v1",
        "sources": nested_dict(summary, "sources"),
        "findings": nested_dict(summary, "findings"),
        "intents": nested_dict(summary, "intents"),
        "source_rewrites": nested_dict(summary, "source_rewrites"),
        "pass_impl_ir": pass_impl_ir,
        "modelcheck": modelcheck,
        "transaction_graph": graph,
        "source_program_graph_contract": source_program_graph_contract,
        "slp_transaction_ir": slp_ir,
        "coverage": {
            "recommendations": nested_dict(coverage, "recommendations"),
            "next_modeling_target": str(coverage.get("next_modeling_target") or ""),
        },
        "diagnostics": {
            "validated_records": len(validated_records),
            "graph_present_transactions": int(graph.get("graph_status", {}).get("present", 0)),
            "graph_absent_transactions": int(graph.get("graph_status", {}).get("absent", 0)),
            "source_program_graph_contract_failures": int(source_program_graph_contract.get("failed") or 0),
            "source_program_graph_contract_next": str(
                nested_dict(source_program_graph_contract, "gaps").get("next_modeling_target") or ""
            ),
            "pass_impl_ir_intent_recommendation": intent_recommendation,
            "modelcheck_refuted": int(modelcheck.get("refuted") or 0),
            "modelcheck_error": int(modelcheck.get("error") or 0),
        },
    }


def format_real_pass_readiness(report: dict[str, Any]) -> str:
    sources = nested_dict(report, "sources")
    findings = nested_dict(report, "findings")
    graph = nested_dict(report, "transaction_graph")
    source_program_graph = nested_dict(report, "source_program_graph_contract")
    pass_impl_ir = nested_dict(report, "pass_impl_ir")
    modelcheck = nested_dict(report, "modelcheck")
    slp_ir = nested_dict(report, "slp_transaction_ir")
    lines = [
        "O2T Real Pass Slice Readiness",
        f"sources: selected={int(sources.get('selected') or 0)} skipped={int(sources.get('skipped') or 0)} errors={int(sources.get('errors') or 0)}",
        f"findings: {int(findings.get('total') or 0)}",
        f"transactions: {int(graph.get('transactions') or 0)}",
        "Pass implementation IR intent checks",
    ]
    intent_status = nested_dict(pass_impl_ir, "intent_check_status")
    if intent_status:
        for key, value in intent_status.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    recommendation = str(nested_dict(report, "diagnostics").get("pass_impl_ir_intent_recommendation") or "")
    if recommendation:
        lines.append(f"pass_impl_ir_next: {recommendation}")
    if modelcheck.get("enabled"):
        lines.append(
            "modelcheck: "
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
        lines.append("modelcheck components")
        lines.extend(modelcheck_component_lines(modelcheck))
        lines.append("modelcheck widths")
        lines.extend(modelcheck_width_lines(modelcheck))
        lines.append("modelcheck findings")
        lines.extend(modelcheck_finding_lines(modelcheck, 5))
    lines.append(
        "Transaction graph status",
    )
    for key, value in nested_dict(graph, "graph_status").items():
        lines.append(f"  {key}: {value}")
    if not nested_dict(graph, "graph_status"):
        lines.append("  none")
    lines.append("Graph absent reasons")
    absent = nested_dict(graph, "absent_reasons")
    if absent:
        for key, value in sorted(absent.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Memory contracts")
    memory_contracts = nested_dict(graph, "memory_contracts")
    if memory_contracts:
        for key, value in memory_contracts.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Store contracts")
    store_contracts = nested_dict(graph, "store_contracts")
    if store_contracts:
        for key, value in store_contracts.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Source program graph contract")
    status = nested_dict(source_program_graph, "status")
    if status:
        for key, value in sorted(status.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    graph_gaps = nested_dict(source_program_graph, "gaps")
    failed_checks = nested_dict(graph_gaps, "failed_checks")
    if failed_checks:
        lines.append("Graph contract failed checks")
        for key, value in sorted(failed_checks.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]:
            lines.append(f"  {key}: {value}")
    next_target = str(graph_gaps.get("next_modeling_target") or "")
    if next_target:
        lines.append(f"source_program_graph_next: {next_target}")
    if slp_ir.get("enabled"):
        lines.append("SLP transaction IR graph lowering")
        for key, value in nested_dict(slp_ir, "graph_ir").items():
            lines.append(f"  {key}: {value}")
        unsupported = nested_dict(slp_ir, "unsupported_graph_reasons")
        lines.append("Unsupported graph reasons")
        if unsupported:
            for key, value in sorted(unsupported.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]:
                lines.append(f"  {key}: {value}")
        else:
            lines.append("  none")
    return "\n".join(lines) + "\n"


def next_modeling_target(transactions: dict[str, Any], source_program_graph_contract: dict[str, Any] | None = None) -> str:
    graph_target = ""
    if isinstance(source_program_graph_contract, dict):
        graph_target = str(nested_dict(source_program_graph_contract, "gaps").get("next_modeling_target") or "")
    if graph_target:
        return graph_target
    recommendations = collections.Counter()
    for key in (
        "reduction_coverage_gaps",
        "masked_memory_coverage_gaps",
        "helper_slice_coverage_gaps",
    ):
        recommendations.update(nested_dict(nested_dict(transactions, key), "recommendations"))
    if not recommendations:
        return ""
    return sorted(recommendations.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]


def helper_slice_diagnostics(transactions: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = nested_dict(transactions, "helper_slice_coverage_gaps").get("diagnostics", [])
    if not isinstance(diagnostics, list):
        return []
    return [dict(item) for item in diagnostics[:25] if isinstance(item, dict)]


def formalization_provenance_coverage_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    status = collections.Counter()
    roles = collections.Counter()
    missing_paths = collections.Counter()
    records = data.get("records", []) if isinstance(data, dict) else []
    for record in records:
        if not isinstance(record, dict):
            continue
        verification = record.get("transaction_formalization_verification")
        if not isinstance(verification, dict):
            continue
        coverage = verification.get("provenance_coverage")
        if not isinstance(coverage, dict):
            continue
        status[str(coverage.get("status") or "absent")] += 1
        for role, count_value in (coverage.get("roles") or {}).items():
            roles[str(role)] += int(count_value)
        for path_item in coverage.get("missing_paths") or []:
            missing_paths[str(path_item)] += 1
    return {
        "status": dict(sorted(status.items())),
        "roles": dict(sorted(roles.items())),
        "missing_paths": dict(sorted(missing_paths.items())),
        "incomplete": int(status.get("incomplete", 0)),
    } if status else {}


def baseline_record(record: dict[str, Any]) -> dict[str, Any]:
    transaction_kind = str(record.get("transaction_kind") or "")
    transaction_opcode = str(record.get("transaction_opcode") or "")
    key_parts = [
        str(record.get("file") or ""),
        str(int(record.get("line") or 0)),
        str(record.get("marker") or ""),
        transaction_kind,
        transaction_opcode,
    ]
    return {
        "key": "|".join(key_parts),
        "file": key_parts[0],
        "line": int(record.get("line") or 0),
        "marker": key_parts[2],
        "proof_status": str(record.get("proof_status") or "unset"),
        "promotion_status": str(record.get("promotion_status") or "unset"),
        "recommendation": str(record.get("recommendation") or ""),
        "transaction_kind": transaction_kind,
        "transaction_opcode": transaction_opcode,
        "transaction_lowering": str(record.get("transaction_lowering") or ""),
        "transaction_consistency": str(record.get("transaction_consistency") or ""),
        "transaction_consistency_errors": [
            str(item) for item in record.get("transaction_consistency_errors", []) if str(item)
        ],
    }


def baseline_from_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    records = coverage.get("records")
    if not isinstance(records, list):
        records = []
    baseline_records = [baseline_record(record) for record in records if isinstance(record, dict)]
    return {
        "model": PASS_SOURCE_AUDIT_BASELINE_MODEL,
        "records": sorted(baseline_records, key=lambda item: str(item.get("key") or "")),
        "marker_counts": dict(sorted(collections.Counter(record.get("marker", "") for record in baseline_records).items())),
        "proof_status": dict(sorted(collections.Counter(record.get("proof_status", "") for record in baseline_records).items())),
        "transaction_lowering": dict(
            sorted(
                collections.Counter(
                    record.get("transaction_lowering", "")
                    for record in baseline_records
                    if record.get("transaction_lowering")
                ).items()
            )
        ),
    }


def modelcheck_baseline_record(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "")
    width = int(record.get("width") or 0)
    key_parts = [
        str(record.get("file") or ""),
        str(int(record.get("line") or 0)),
        str(record.get("marker") or ""),
        str(width),
        status,
    ]
    return {
        "key": "|".join(key_parts),
        "file": key_parts[0],
        "line": int(record.get("line") or 0),
        "marker": key_parts[2],
        "width": width,
        "status": status,
        "domain": str(record.get("domain") or ""),
        "reason": str(record.get("reason") or ""),
        "function": str(record.get("function") or ""),
        "source_function": str(record.get("source_function") or ""),
    }


def modelcheck_baseline(modelcheck: dict[str, Any] | None) -> dict[str, Any]:
    findings = modelcheck_findings(modelcheck or {})
    records = [
        modelcheck_baseline_record(record)
        for record in findings
        if str(record.get("status") or "") in {"refuted", "error"}
    ]
    return {
        "model": MODELCHECK_BASELINE_MODEL,
        "records": sorted(records, key=lambda item: str(item.get("key") or "")),
        "status": dict(sorted(collections.Counter(record.get("status", "") for record in records).items())),
        "marker_counts": dict(sorted(collections.Counter(record.get("marker", "") for record in records).items())),
    }


def with_modelcheck_baseline(baseline: dict[str, Any], modelcheck: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(baseline)
    out["modelcheck"] = modelcheck_baseline(modelcheck)
    return out


def load_baseline(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"model": PASS_SOURCE_AUDIT_BASELINE_MODEL, "records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"model": PASS_SOURCE_AUDIT_BASELINE_MODEL, "records": []}
    if is_model_id(data.get("model"), PASS_SOURCE_AUDIT_BASELINE_MODEL, LEGACY_PASS_SOURCE_AUDIT_BASELINE_MODEL):
        return data
    if isinstance(data.get("baseline"), dict):
        return data["baseline"]
    if isinstance(data.get("records"), list):
        return baseline_from_coverage(data)
    return {"model": PASS_SOURCE_AUDIT_BASELINE_MODEL, "records": []}


def baseline_map(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = baseline.get("records")
    if not isinstance(records, list):
        return {}
    return {
        str(record.get("key") or ""): record
        for record in records
        if isinstance(record, dict) and str(record.get("key") or "")
    }


def modelcheck_baseline_records(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    modelcheck = baseline.get("modelcheck") if isinstance(baseline.get("modelcheck"), dict) else {}
    records = modelcheck.get("records") if isinstance(modelcheck.get("records"), list) else []
    return [record for record in records if isinstance(record, dict)]


def modelcheck_duplicate_base_keys(*record_groups: list[dict[str, Any]]) -> set[str]:
    counts: collections.Counter[str] = collections.Counter()
    for records in record_groups:
        group_counts = collections.Counter(
            str(record.get("key") or "")
            for record in records
            if str(record.get("key") or "")
        )
        for key, count in group_counts.items():
            if count > 1:
                counts[key] += 1
    return set(counts)


def modelcheck_duplicate_identity_keys(
    duplicate_base_keys: set[str],
    *record_groups: list[dict[str, Any]],
) -> set[str]:
    duplicate_identity_keys: set[str] = set()
    for records in record_groups:
        group_counts = collections.Counter(
            modelcheck_baseline_identity_key(str(record.get("key") or ""), record)
            for record in records
            if str(record.get("key") or "") in duplicate_base_keys
        )
        duplicate_identity_keys.update(key for key, count in group_counts.items() if count > 1)
    return duplicate_identity_keys


def modelcheck_baseline_map(
    baseline: dict[str, Any],
    duplicate_base_keys: set[str] | None = None,
    duplicate_identity_keys: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    records = modelcheck_baseline_records(baseline)
    if duplicate_base_keys is None:
        base_counts = collections.Counter(
            str(record.get("key") or "")
            for record in records
            if str(record.get("key") or "")
        )
        duplicate_base_keys = {key for key, count in base_counts.items() if count > 1}
    if duplicate_identity_keys is None:
        duplicate_identity_keys = modelcheck_duplicate_identity_keys(duplicate_base_keys, records)
    keyed_records: list[tuple[str, dict[str, Any]]] = []
    for record in records:
        key = str(record.get("key") or "")
        if not key:
            continue
        keyed_records.append((modelcheck_baseline_identity_key(key, record) if key in duplicate_base_keys else key, record))
    identity_counts = collections.Counter(key for key, _record in keyed_records)
    identity_ordinals: collections.Counter[str] = collections.Counter()
    out: dict[str, dict[str, Any]] = {}
    for key, record in keyed_records:
        if key in duplicate_identity_keys or identity_counts[key] > 1:
            identity_ordinals[key] += 1
            key = f"{key}|occurrence={identity_ordinals[key]}"
        out[key] = record
    return out


def modelcheck_baseline_identity_key(key: str, record: dict[str, Any]) -> str:
    parts = [
        str(record.get("source_function") or ""),
        str(record.get("function") or ""),
    ]
    return key + "|" + "|".join(parts) if any(parts) else key


def compare_modelcheck_baselines(
    previous: dict[str, Any],
    current: dict[str, Any],
    baseline_present: bool,
) -> dict[str, Any]:
    previous_modelcheck = previous.get("modelcheck") if isinstance(previous.get("modelcheck"), dict) else {}
    previous_records = modelcheck_baseline_records(previous)
    current_records = modelcheck_baseline_records(current)
    duplicate_base_keys = modelcheck_duplicate_base_keys(previous_records, current_records)
    duplicate_identity_keys = modelcheck_duplicate_identity_keys(duplicate_base_keys, previous_records, current_records)
    previous_by_key = modelcheck_baseline_map(previous, duplicate_base_keys, duplicate_identity_keys)
    current_by_key = modelcheck_baseline_map(current, duplicate_base_keys, duplicate_identity_keys)
    align_legacy_modelcheck_keys(previous_by_key, current_by_key)
    modelcheck_baseline_present = baseline_present and is_model_id(
        previous_modelcheck.get("model"),
        MODELCHECK_BASELINE_MODEL,
        LEGACY_MODELCHECK_BASELINE_MODEL,
    )
    if not modelcheck_baseline_present:
        return {
            "model": "o2t-modelcheck-baseline-diff-v1",
            "baseline_present": False,
            "summary": {
                "previous_records": len(previous_by_key),
                "current_records": len(current_by_key),
                "new": 0,
                "resolved": 0,
                "changed": 0,
                "new_refuted": 0,
                "new_error": 0,
                "resolved_refuted": 0,
                "resolved_error": 0,
            },
            "new": [],
            "resolved": [],
            "changed": [],
        }
    new = [current_by_key[key] for key in sorted(set(current_by_key) - set(previous_by_key))]
    resolved = [previous_by_key[key] for key in sorted(set(previous_by_key) - set(current_by_key))]
    changed: list[dict[str, Any]] = []
    fields = ["domain", "reason", "function", "source_function"]
    for key in sorted(set(previous_by_key) & set(current_by_key)):
        before = previous_by_key[key]
        after = current_by_key[key]
        changes = {
            field: {"before": before.get(field), "after": after.get(field)}
            for field in fields
            if before.get(field) != after.get(field)
        }
        if changes:
            changed.append({"key": key, "before": before, "after": after, "changes": changes})
    return {
        "model": "o2t-modelcheck-baseline-diff-v1",
        "baseline_present": True,
        "summary": {
            "previous_records": len(previous_by_key),
            "current_records": len(current_by_key),
            "new": len(new),
            "resolved": len(resolved),
            "changed": len(changed),
            "new_refuted": sum(1 for record in new if record.get("status") == "refuted"),
            "new_error": sum(1 for record in new if record.get("status") == "error"),
            "resolved_refuted": sum(1 for record in resolved if record.get("status") == "refuted"),
            "resolved_error": sum(1 for record in resolved if record.get("status") == "error"),
        },
        "new": new,
        "resolved": resolved,
        "changed": changed,
    }


def align_legacy_modelcheck_keys(
    previous_by_key: dict[str, dict[str, Any]],
    current_by_key: dict[str, dict[str, Any]],
) -> None:
    for key, previous_record in list(previous_by_key.items()):
        if key in current_by_key or key.count("|") != 4:
            continue
        identity_key = modelcheck_baseline_identity_key(key, previous_record)
        current_record = current_by_key.get(identity_key)
        if current_record is not None:
            current_by_key[key] = current_by_key.pop(identity_key)
            continue
        candidates = [
            (candidate_key, value)
            for candidate_key, value in current_by_key.items()
            if candidate_key.startswith(key + "|")
        ]
        if len(candidates) == 1:
            candidate_key, value = candidates[0]
            current_by_key[key] = current_by_key.pop(candidate_key, value)


def compare_baselines(previous: dict[str, Any], current: dict[str, Any], baseline_present: bool) -> dict[str, Any]:
    previous_by_key = baseline_map(previous)
    current_by_key = baseline_map(current)
    if not baseline_present:
        return {
            "model": "o2t-pass-source-audit-baseline-diff-v1",
            "baseline_present": False,
            "summary": {
                "previous_records": 0,
                "current_records": len(current_by_key),
                "new": 0,
                "resolved": 0,
                "changed": 0,
                "new_unsupported": 0,
                "new_fallback_transactions": 0,
            },
            "new": [],
            "resolved": [],
            "changed": [],
            "modelcheck": compare_modelcheck_baselines(previous, current, baseline_present),
        }
    new = [current_by_key[key] for key in sorted(set(current_by_key) - set(previous_by_key))]
    resolved = [previous_by_key[key] for key in sorted(set(previous_by_key) - set(current_by_key))]
    changed: list[dict[str, Any]] = []
    fields = [
        "proof_status",
        "promotion_status",
        "recommendation",
        "transaction_lowering",
        "transaction_consistency",
        "transaction_consistency_errors",
    ]
    for key in sorted(set(previous_by_key) & set(current_by_key)):
        before = previous_by_key[key]
        after = current_by_key[key]
        changes = {
            field: {"before": before.get(field), "after": after.get(field)}
            for field in fields
            if before.get(field) != after.get(field)
        }
        if changes:
            changed.append({"key": key, "before": before, "after": after, "changes": changes})
    new_unsupported = sum(1 for record in new if record.get("proof_status") == "unsupported")
    new_fallback_transactions = sum(1 for record in new if record.get("transaction_lowering") == "fallback")
    return {
        "model": "o2t-pass-source-audit-baseline-diff-v1",
        "baseline_present": baseline_present,
        "summary": {
            "previous_records": len(previous_by_key),
            "current_records": len(current_by_key),
            "new": len(new),
            "resolved": len(resolved),
            "changed": len(changed),
            "new_unsupported": new_unsupported,
            "new_fallback_transactions": new_fallback_transactions,
        },
        "new": new,
        "resolved": resolved,
        "changed": changed,
        "modelcheck": compare_modelcheck_baselines(previous, current, baseline_present),
    }


def format_modelcheck_baseline_finding(record: dict[str, Any]) -> str:
    width = int(record.get("width") or 0)
    width_text = f"@{width}b " if width else ""
    domain = str(record.get("domain") or "")
    domain_text = f"{domain} " if domain else ""
    return (
        f"  {record.get('status')} {width_text}{domain_text}"
        f"{record.get('marker')} {record.get('file')}:{record.get('line')} ({record.get('reason')})"
    )


def append_limited_modelcheck_baseline_findings(
    lines: list[str],
    records: list[dict[str, Any]],
    limit: int = 10,
) -> None:
    for record in records[:limit]:
        lines.append(format_modelcheck_baseline_finding(record))
    omitted = len(records) - limit
    if omitted > 0:
        lines.append(f"  ... {omitted} more")
    if not records:
        lines.append("  none")


def format_baseline_diff(diff: dict[str, Any]) -> str:
    summary = nested_dict(diff, "summary")
    modelcheck = nested_dict(diff, "modelcheck")
    modelcheck_summary = nested_dict(modelcheck, "summary")
    lines = [
        "O2T Pass Source Audit Baseline Diff",
        f"baseline_present: {str(bool(diff.get('baseline_present'))).lower()}",
        "records: "
        + " ".join(
            [
                f"previous={int(summary.get('previous_records') or 0)}",
                f"current={int(summary.get('current_records') or 0)}",
                f"new={int(summary.get('new') or 0)}",
                f"resolved={int(summary.get('resolved') or 0)}",
                f"changed={int(summary.get('changed') or 0)}",
            ]
        ),
        f"new_unsupported: {int(summary.get('new_unsupported') or 0)}",
        f"new_fallback_transactions: {int(summary.get('new_fallback_transactions') or 0)}",
        "modelcheck: "
        + " ".join(
            [
                f"baseline_present={str(bool(modelcheck.get('baseline_present'))).lower()}",
                f"previous={int(modelcheck_summary.get('previous_records') or 0)}",
                f"current={int(modelcheck_summary.get('current_records') or 0)}",
                f"new={int(modelcheck_summary.get('new') or 0)}",
                f"resolved={int(modelcheck_summary.get('resolved') or 0)}",
                f"changed={int(modelcheck_summary.get('changed') or 0)}",
                f"new_refuted={int(modelcheck_summary.get('new_refuted') or 0)}",
                f"new_error={int(modelcheck_summary.get('new_error') or 0)}",
                f"resolved_refuted={int(modelcheck_summary.get('resolved_refuted') or 0)}",
                f"resolved_error={int(modelcheck_summary.get('resolved_error') or 0)}",
            ]
        ),
    ]
    new_records = [record for record in diff.get("new", []) if isinstance(record, dict)]
    unsupported = [record for record in new_records if record.get("proof_status") == "unsupported"]
    fallback = [record for record in new_records if record.get("transaction_lowering") == "fallback"]
    lines.append("Top new unsupported")
    for record in unsupported[:10]:
        lines.append(f"  {record.get('marker')} {record.get('file')}:{record.get('line')}")
    omitted_unsupported = len(unsupported) - 10
    if omitted_unsupported > 0:
        lines.append(f"  ... {omitted_unsupported} more")
    if not unsupported:
        lines.append("  none")
    lines.append("Top new fallback transactions")
    for record in fallback[:10]:
        lines.append(
            "  "
            + " ".join(
                [
                    str(record.get("marker") or ""),
                    str(record.get("transaction_kind") or ""),
                    str(record.get("transaction_opcode") or ""),
                    f"{record.get('file')}:{record.get('line')}",
                ]
            )
        )
    omitted_fallback = len(fallback) - 10
    if omitted_fallback > 0:
        lines.append(f"  ... {omitted_fallback} more")
    if not fallback:
        lines.append("  none")
    modelcheck_new = [record for record in modelcheck.get("new", []) if isinstance(record, dict)]
    lines.append("Top new modelcheck findings")
    append_limited_modelcheck_baseline_findings(lines, modelcheck_new)
    modelcheck_resolved = [record for record in modelcheck.get("resolved", []) if isinstance(record, dict)]
    lines.append("Top resolved modelcheck findings")
    append_limited_modelcheck_baseline_findings(lines, modelcheck_resolved)
    modelcheck_changed = [record for record in modelcheck.get("changed", []) if isinstance(record, dict)]
    lines.append("Changed modelcheck findings")
    for item in modelcheck_changed[:10]:
        changes = nested_dict(item, "changes")
        changed_fields = []
        for field in ("domain", "reason", "function", "source_function"):
            value = nested_dict(changes, field)
            if value:
                changed_fields.append(f"{field}:{value.get('before')}->{value.get('after')}")
        after = nested_dict(item, "after")
        marker = str(after.get("marker") or nested_dict(item, "before").get("marker") or "")
        source_function = str(after.get("source_function") or nested_dict(item, "before").get("source_function") or "")
        detail = " ".join(changed_fields) if changed_fields else "changed"
        lines.append(f"  {marker} {source_function} {detail}".rstrip())
    omitted_changed = len(modelcheck_changed) - 10
    if omitted_changed > 0:
        lines.append(f"  ... {omitted_changed} more")
    if not modelcheck_changed:
        lines.append("  none")
    lines.append("Changed recommendations")
    changed_recommendations = [
        item for item in diff.get("changed", [])
        if isinstance(item, dict) and "recommendation" in nested_dict(item, "changes")
    ]
    for item in changed_recommendations[:10]:
        changes = nested_dict(item, "changes")
        recommendation = nested_dict(changes, "recommendation")
        lines.append(f"  {item.get('key')}: {recommendation.get('before')} -> {recommendation.get('after')}")
    omitted_recommendations = len(changed_recommendations) - 10
    if omitted_recommendations > 0:
        lines.append(f"  ... {omitted_recommendations} more")
    if not changed_recommendations:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def run_summary(
    args: argparse.Namespace,
    manifest: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    validated_records: list[dict[str, Any]],
    coverage: dict[str, Any],
    modelcheck: dict[str, Any],
    commands: list[list[str]],
    budget_violations: list[dict[str, Any]],
    baseline: dict[str, Any],
    baseline_diff: dict[str, Any],
    slp_ir: dict[str, Any],
) -> dict[str, Any]:
    coverage_summary = nested_dict(coverage, "summary")
    transactions = nested_dict(coverage_summary, "optimization_transactions")
    source_program_graph_contract = nested_dict(coverage_summary, "source_program_graph_contract")
    provenance_coverage = nested_dict(coverage_summary, "transaction_formal_provenance_coverage")
    recommendation_counts = nested_dict(coverage_summary, "recommendation")
    proof_counts = count(validated_records, "proof_status")
    promotion_counts = count(validated_records, "promotion_status")
    marker_counts = collections.Counter(str(record.get("marker") or "unset") for record in findings)
    file_counts = collections.Counter(str(record.get("file") or "unset") for record in findings)
    pass_counts = collections.Counter(str(record.get("pass") or "unset") for record in findings)
    return {
        "sources": {
            "total": len(manifest),
            "selected": manifest_count(manifest, "selected"),
            "skipped": manifest_count(manifest, "skipped"),
            "errors": manifest_count(manifest, "error"),
            "reasons": count(manifest, "reason"),
        },
        "findings": {
            "total": len(findings),
            "by_pass": dict(sorted(pass_counts.items())),
            "by_marker": dict(sorted(marker_counts.items())),
            "by_file": dict(sorted(file_counts.items())),
        },
        "source_rewrites": rewrite_status_summary(findings),
        "pass_impl_ir": {
            "slice_status": pass_impl_ir_slice_counts(findings),
            **pass_impl_ir_intent_check_summary(findings),
        },
        "intents": {
            "total": len(validated_records),
            "proof_status": proof_counts,
            "promotion_status": promotion_counts,
        },
        "coverage": {
            "recommendations": recommendation_counts,
            "next_modeling_target": next_modeling_target(transactions, source_program_graph_contract),
            "helper_slice_diagnostics": helper_slice_diagnostics(transactions),
            "optimization_transactions": transactions,
            "source_program_graph_contract": source_program_graph_contract,
            "transaction_formal_provenance_coverage": provenance_coverage,
        },
        "modelcheck": {
            "enabled": bool(modelcheck),
            "summary": str(modelcheck.get("summary") or ""),
            "records": int(modelcheck.get("records") or 0),
            "generated": int(modelcheck.get("generated") or 0),
            "proved": int(modelcheck.get("proved") or 0),
            "refuted": int(modelcheck.get("refuted") or 0),
            "unsupported": int(modelcheck.get("unsupported") or 0),
            "skipped": int(modelcheck.get("skipped") or 0),
            "error": int(modelcheck.get("error") or 0),
            "ok": bool(modelcheck.get("ok")) if modelcheck else False,
            "engine": str(modelcheck.get("engine") or ""),
            "width_mode": str(modelcheck.get("width_mode") or ""),
            "selected_widths": modelcheck.get("selected_widths") if isinstance(modelcheck.get("selected_widths"), list) else [],
            "widths": modelcheck.get("widths") if isinstance(modelcheck.get("widths"), dict) else {},
            "components": modelcheck.get("components") if isinstance(modelcheck.get("components"), list) else [],
            "findings": modelcheck_findings(modelcheck),
        },
        "filters": {
            "passes": [item.strip() for item in str(args.passes or "").split(",") if item.strip()],
            "markers": list(args.marker),
            "marker_prefixes": list(args.marker_prefix),
            "include": list(args.include),
            "exclude": list(args.exclude),
            "intent_min_confidence": args.intent_min_confidence,
        },
        "tools": {
            "ast_miner": str(args.ast_miner),
            "ir_miner": str(args.ir_miner),
            "ir_source_wrapper": str(args.ir_source_wrapper),
            "intent_inferer": str(args.intent_inferer),
            "intent_validator": str(args.intent_validator),
            "coverage_auditor": str(args.coverage_auditor),
            "source_slice_contract_verifier": str(args.source_slice_contract_verifier),
            "transaction_formalization_verifier": str(args.transaction_formalization_verifier),
            "slp_transaction_ir_emitter": str(args.slp_transaction_ir_emitter),
            "modelcheck_intent_checker": str(args.modelcheck_intent_checker),
            "modelcheck_cfg_checker": str(args.modelcheck_cfg_checker),
            "modelcheck_memory_checker": str(args.modelcheck_memory_checker),
            "modelcheck_licm_checker": str(args.modelcheck_licm_checker),
            "modelcheck_globalopt_checker": str(args.modelcheck_globalopt_checker),
            "modelcheck_dce_checker": str(args.modelcheck_dce_checker),
            "modelcheck_slp_checker": str(args.modelcheck_slp_checker),
            "llvm_as": str(args.llvm_as or ""),
            "z3": str(args.z3),
        },
        "slp_transaction_ir": slp_ir,
        "commands": len(commands),
        "budget_violations": budget_violations,
        "baseline": baseline,
        "baseline_diff": nested_dict(baseline_diff, "summary"),
        "modelcheck_baseline_diff": nested_dict(baseline_diff, "modelcheck"),
    }


def format_run_summary(summary: dict[str, Any]) -> str:
    sources = nested_dict(summary, "sources")
    findings = nested_dict(summary, "findings")
    source_rewrites = nested_dict(summary, "source_rewrites")
    pass_impl_ir = nested_dict(summary, "pass_impl_ir")
    intents = nested_dict(summary, "intents")
    modelcheck = nested_dict(summary, "modelcheck")
    coverage = nested_dict(summary, "coverage")
    recommendations = nested_dict(coverage, "recommendations")
    provenance_coverage = nested_dict(coverage, "transaction_formal_provenance_coverage")
    lines = [
        "O2T Pass Source Audit Run Summary",
        f"sources: selected={int(sources.get('selected') or 0)} skipped={int(sources.get('skipped') or 0)} errors={int(sources.get('errors') or 0)}",
        f"findings: {int(findings.get('total') or 0)}",
        f"intents: {int(intents.get('total') or 0)}",
        "Source rewrite extraction",
    ]
    rewrite_status = nested_dict(source_rewrites, "rewrite_status")
    if rewrite_status:
        for key, value in rewrite_status.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    rewrite_reasons = nested_dict(source_rewrites, "rewrite_absent_reason")
    lines.append("Source rewrite absent reasons")
    if rewrite_reasons:
        for key, value in sorted(rewrite_reasons.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.extend([
        "Pass implementation IR slices",
    ])
    slice_status = nested_dict(pass_impl_ir, "slice_status")
    if slice_status:
        for key, value in slice_status.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    intent_check_status = nested_dict(pass_impl_ir, "intent_check_status")
    lines.append("Pass implementation IR intent checks")
    if intent_check_status:
        for key, value in intent_check_status.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    intent_check_reasons = nested_dict(pass_impl_ir, "intent_check_reasons")
    lines.append("Pass implementation IR intent check reasons")
    if intent_check_reasons:
        for key, value in sorted(intent_check_reasons.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.extend([
        "Proof status",
    ])
    for key, value in nested_dict(intents, "proof_status").items():
        lines.append(f"  {key}: {value}")
    lines.append("Promotion status")
    for key, value in nested_dict(intents, "promotion_status").items():
        lines.append(f"  {key}: {value}")
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
    lines.append("Top recommendations")
    if recommendations:
        for key, value in sorted(recommendations.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    next_target = str(coverage.get("next_modeling_target") or "none")
    lines.append(f"next_modeling_target: {next_target}")
    helper_diagnostics = coverage.get("helper_slice_diagnostics", [])
    lines.append("Top helper slice diagnostics")
    if isinstance(helper_diagnostics, list) and helper_diagnostics:
        for diagnostic in helper_diagnostics[:10]:
            if not isinstance(diagnostic, dict):
                continue
            lines.append(
                "  "
                + " ".join(
                    [
                        str(diagnostic.get("marker") or ""),
                        f"{diagnostic.get('file', '')}:{int(diagnostic.get('line') or 0)}",
                        f"helper={diagnostic.get('helper', '')}",
                        f"role={diagnostic.get('role', '')}",
                        f"reason={diagnostic.get('reason', '')}",
                    ]
                )
            )
    else:
        lines.append("  none")
    if provenance_coverage:
        status = nested_dict(provenance_coverage, "status")
        lines.append(
            "formal_provenance_coverage: "
            + (", ".join(f"{key}={value}" for key, value in status.items()) if status else "none")
            + f" incomplete={int(provenance_coverage.get('incomplete') or 0)}"
        )
    slp_ir = nested_dict(summary, "slp_transaction_ir")
    if slp_ir.get("enabled"):
        validation = nested_dict(slp_ir, "ir_validation")
        lines.append("SLP transaction IR")
        lines.append(
            "  "
            + " ".join(
                [
                    f"generated={int(slp_ir.get('generated') or 0)}",
                    f"skipped={int(slp_ir.get('skipped') or 0)}",
                    f"validation_passed={int(validation.get('passed') or 0)}",
                    f"validation_failed={int(validation.get('failed') or 0)}",
                    f"validation_skipped={int(validation.get('skipped') or 0)}",
                ]
            )
        )
        if slp_ir.get("manifest"):
            lines.append(f"  manifest: {slp_ir.get('manifest')}")
    baseline_diff = nested_dict(summary, "baseline_diff")
    if baseline_diff:
        lines.append(
            "baseline_diff: "
            + " ".join(
                [
                    f"new={int(baseline_diff.get('new') or 0)}",
                    f"resolved={int(baseline_diff.get('resolved') or 0)}",
                    f"changed={int(baseline_diff.get('changed') or 0)}",
                    f"new_unsupported={int(baseline_diff.get('new_unsupported') or 0)}",
                    f"new_fallback_transactions={int(baseline_diff.get('new_fallback_transactions') or 0)}",
                ]
            )
        )
    violations = summary.get("budget_violations")
    lines.append("Budget violations")
    if isinstance(violations, list) and violations:
        for violation in violations:
            if isinstance(violation, dict):
                lines.append(
                    f"  {violation.get('budget')}: actual={violation.get('actual')} limit={violation.get('limit')}"
                )
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def budget_violations(
    args: argparse.Namespace,
    manifest: list[dict[str, Any]],
    validated_records: list[dict[str, Any]],
    coverage: dict[str, Any],
    baseline_diff: dict[str, Any],
    modelcheck: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    proof_counts = collections.Counter(str(record.get("proof_status") or "unset") for record in validated_records)
    coverage_summary = nested_dict(coverage, "summary")
    optimization_transactions = nested_dict(coverage_summary, "optimization_transactions")
    provenance_coverage = nested_dict(coverage_summary, "transaction_formal_provenance_coverage")
    checks: list[tuple[str, int, int | None, str]] = [
        ("min-proved", int(proof_counts.get("proved", 0)), args.min_proved, "min"),
        ("max-unsupported", int(proof_counts.get("unsupported", 0)), args.max_unsupported, "max"),
        (
            "max-proof-failures",
            int(proof_counts.get("failed", 0)) + int(proof_counts.get("error", 0)),
            args.max_proof_failures,
            "max",
        ),
        (
            "max-fallback-transactions",
            int(optimization_transactions.get("fallback") or 0),
            args.max_fallback_transactions,
            "max",
        ),
        ("max-mining-errors", manifest_count(manifest, "error"), args.max_mining_errors, "max"),
        (
            "max-new-unsupported",
            int(nested_dict(baseline_diff, "summary").get("new_unsupported") or 0),
            args.max_new_unsupported,
            "max",
        ),
        (
            "max-new-fallback-transactions",
            int(nested_dict(baseline_diff, "summary").get("new_fallback_transactions") or 0),
            args.max_new_fallback_transactions,
            "max",
        ),
        (
            "max-incomplete-formal-provenance",
            int(provenance_coverage.get("incomplete") or 0),
            args.max_incomplete_formal_provenance,
            "max",
        ),
        (
            "max-modelcheck-refuted",
            int((modelcheck or {}).get("refuted") or 0),
            args.max_modelcheck_refuted,
            "max",
        ),
        (
            "max-modelcheck-errors",
            int((modelcheck or {}).get("error") or 0),
            args.max_modelcheck_errors,
            "max",
        ),
        (
            "max-new-modelcheck-refuted",
            int(nested_dict(nested_dict(baseline_diff, "modelcheck"), "summary").get("new_refuted") or 0),
            args.max_new_modelcheck_refuted,
            "max",
        ),
        (
            "max-new-modelcheck-errors",
            int(nested_dict(nested_dict(baseline_diff, "modelcheck"), "summary").get("new_error") or 0),
            args.max_new_modelcheck_errors,
            "max",
        ),
    ]
    violations: list[dict[str, Any]] = []
    for name, actual, limit, mode in checks:
        if limit is None:
            continue
        failed = actual < limit if mode == "min" else actual > limit
        if failed:
            violations.append({"budget": name, "actual": actual, "limit": limit})
    return violations


def intent_summary(records: list[dict[str, Any]]) -> str:
    lines = ["O2T Pass Source Intent Summary", f"candidates: {len(records)}", "Proof status"]
    for key, value in count(records, "proof_status").items():
        lines.append(f"  {key}: {value}")
    lines.append("Promotion status")
    for key, value in count(records, "promotion_status").items():
        lines.append(f"  {key}: {value}")
    lines.append("Markers")
    marker_counts = collections.Counter(str(record.get("marker") or "") for record in records)
    for marker, value in sorted(marker_counts.items()):
        lines.append(f"  {marker}: {value}")
    if not marker_counts:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    findings_path = args.out / "findings.json"
    source_manifest = args.out / "source-manifest.jsonl"
    intent_candidates = args.out / "intent-candidates.jsonl"
    intent_validated = args.out / "intent-validated.jsonl"
    intent_summary_path = args.out / "intent-summary.txt"
    intent_coverage = args.out / "intent-coverage.json"
    intent_coverage_report = args.out / "intent-coverage.txt"
    run_summary_path = args.out / "run-summary.json"
    run_summary_report = args.out / "run-summary.txt"
    readiness_path = args.out / "real-pass-readiness.json"
    readiness_report_path = args.out / "real-pass-readiness.txt"
    contract_verification = args.out / "source-slice-contract-verification.json"
    contract_verification_report = args.out / "source-slice-contract-verification.txt"
    formalization_verification = args.out / "transaction-formalization-verification.json"
    formalization_verification_report = args.out / "transaction-formalization-verification.txt"
    baseline_path = args.write_baseline or (args.out / "audit-baseline.json")
    baseline_diff_path = args.out / "baseline-diff.json"
    baseline_diff_report = args.out / "baseline-diff.txt"
    intent_smt = args.out / "intent-smt"
    slp_transaction_ir = args.out / "slp-transaction-ir"
    modelcheck_intents = args.out / "modelcheck-intents"
    modelcheck_scalar_summary_path = modelcheck_intents / "scalar-summary.json"
    modelcheck_cfg_source = modelcheck_intents / "cfg-source"
    modelcheck_cfg_summary_path = modelcheck_cfg_source / "cfg-summary.json"
    modelcheck_memory_source = modelcheck_intents / "memory-source"
    modelcheck_memory_summary_path = modelcheck_memory_source / "memory-summary.json"
    modelcheck_licm_source = modelcheck_intents / "licm-source"
    modelcheck_licm_summary_path = modelcheck_licm_source / "licm-summary.json"
    modelcheck_globalopt_source = modelcheck_intents / "globalopt-source"
    modelcheck_globalopt_summary_path = modelcheck_globalopt_source / "globalopt-summary.json"
    modelcheck_dce_source = modelcheck_intents / "dce-source"
    modelcheck_dce_summary_path = modelcheck_dce_source / "dce-summary.json"
    modelcheck_slp_source = modelcheck_intents / "slp-source"
    modelcheck_slp_summary_path = modelcheck_slp_source / "slp-summary.json"
    modelcheck_summary_path = modelcheck_intents / "modelcheck-summary.json"
    command_log = args.out / "commands.log"
    commands: list[list[str]] = []

    ok = True
    for path, label in [
        (args.ast_miner, "AST source miner"),
        (args.intent_inferer, "intent inferer"),
        (args.intent_validator, "intent validator"),
        (args.coverage_auditor, "intent coverage auditor"),
    ]:
        ok = require_executable(path, label) and ok
    if args.mine_pass_impl_ir:
        ok = require_executable(args.ir_miner, "pass implementation IR miner") and ok
        ok = require_file(args.ir_source_wrapper, "pass implementation IR source wrapper") and ok
    if args.verify_source_slice_contracts:
        ok = require_executable(args.source_slice_contract_verifier, "source-slice contract verifier") and ok
    if args.verify_transaction_formalization:
        ok = require_executable(args.transaction_formalization_verifier, "transaction formalization verifier") and ok
    if args.emit_slp_transaction_ir:
        ok = require_file(args.slp_transaction_ir_emitter, "SLP transaction IR emitter") and ok
    if args.modelcheck_intents:
        ok = require_file(args.modelcheck_intent_checker, "modelcheck intent checker") and ok
        ok = require_file(args.modelcheck_cfg_checker, "modelcheck cfg checker") and ok
        ok = require_file(args.modelcheck_memory_checker, "modelcheck memory checker") and ok
        ok = require_file(args.modelcheck_licm_checker, "modelcheck licm checker") and ok
        ok = require_file(args.modelcheck_globalopt_checker, "modelcheck globalopt checker") and ok
        ok = require_file(args.modelcheck_dce_checker, "modelcheck dce checker") and ok
        ok = require_file(args.modelcheck_slp_checker, "modelcheck slp checker") and ok
    if args.validate_slp_ir and args.llvm_as:
        ok = require_command(args.llvm_as, "llvm-as") and ok
    ok = require_command(args.z3, "z3") and ok
    if not args.compile_commands.exists():
        print(f"compile commands path does not exist: {args.compile_commands}", file=sys.stderr)
        ok = False
    if not ok:
        return 2

    try:
        compile_files = load_compile_files(args.compile_commands)
        candidates, manifest = source_files(args.sources)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    selected: list[Path] = []
    for path in candidates:
        keep, reason = path_selected(path, args.include, args.exclude)
        if not keep:
            manifest.append({"file": str(path), "status": "skipped", "reason": reason})
            continue
        if path not in compile_files:
            manifest.append({"file": str(path), "status": "skipped", "reason": "missing-compile-command"})
            continue
        selected.append(path)

    all_findings: list[dict[str, Any]] = []
    compile_dir = compile_commands_dir(args.compile_commands)
    for path in selected:
        mine_command = [
            str(args.ast_miner),
            "--format",
            "json",
            "--registry",
            str(args.registry),
            "--semantic-registry",
            str(args.semantic_registry),
            "--guard-semantics",
            str(args.guard_semantics),
            "-p",
            str(compile_dir),
            str(path),
        ]
        commands.append(mine_command)
        mined = run(mine_command)
        if mined.returncode != 0:
            manifest.append(
                {
                    "file": str(path),
                    "status": "error",
                    "reason": "ast-miner-failed",
                    "message": mined.stderr.strip(),
                }
            )
            if args.require_clean_mining:
                write_jsonl(source_manifest, manifest)
                write_command_log(command_log, commands)
                print(mined.stderr, file=sys.stderr, end="")
                return mined.returncode or 1
            continue
        try:
            file_findings = load_records_text(mined.stdout)
        except json.JSONDecodeError as exc:
            manifest.append({"file": str(path), "status": "error", "reason": "invalid-ast-json", "message": str(exc)})
            if args.require_clean_mining:
                write_jsonl(source_manifest, manifest)
                write_command_log(command_log, commands)
                return 1
            continue
        selected_record: dict[str, Any] = {
            "file": str(path),
            "status": "selected",
            "findings": len(file_findings),
        }
        if args.mine_pass_impl_ir:
            ir_out_dir = args.out / "pass-impl-ir" / path.stem
            ir_command = [
                sys.executable,
                str(args.ir_source_wrapper),
                str(path),
                "--compile-commands",
                str(args.compile_commands),
                "--out-dir",
                str(ir_out_dir),
                "--ir-miner",
                str(args.ir_miner),
            ]
            commands.append(ir_command)
            mined_ir = run(ir_command)
            if mined_ir.returncode != 0:
                selected_record["pass_impl_ir"] = "error"
                selected_record["pass_impl_ir_reason"] = "pass-impl-ir-miner-failed"
                selected_record["pass_impl_ir_message"] = mined_ir.stderr.strip()
                if args.require_clean_mining:
                    manifest.append(selected_record)
                    write_jsonl(source_manifest, manifest)
                    write_command_log(command_log, commands)
                    print(mined_ir.stderr, file=sys.stderr, end="")
                    return mined_ir.returncode or 1
            else:
                try:
                    ir_graph = json.loads(mined_ir.stdout)
                except json.JSONDecodeError as exc:
                    selected_record["pass_impl_ir"] = "error"
                    selected_record["pass_impl_ir_reason"] = "invalid-pass-impl-ir-json"
                    selected_record["pass_impl_ir_message"] = str(exc)
                    if args.require_clean_mining:
                        manifest.append(selected_record)
                        write_jsonl(source_manifest, manifest)
                        write_command_log(command_log, commands)
                        return 1
                else:
                    graph_path = ir_out_dir / "graph.json"
                    write_json(graph_path, ir_graph)
                    graph_ref = relative_output_path(graph_path, args.out)
                    slices = [
                        pass_impl_ir_slice_for(finding, ir_graph, path, args.pass_impl_ir_slice_window)
                        for finding in file_findings
                    ]
                    slice_status = collections.Counter(
                        str(slice_record.get("status") or "unset")
                        for slice_record in slices
                        if isinstance(slice_record, dict)
                    )
                    selected_record["pass_impl_ir"] = "present"
                    selected_record["pass_impl_ir_graph"] = graph_ref
                    selected_record["pass_impl_ir_functions"] = len(
                        ir_graph.get("functions", [])
                        if isinstance(ir_graph.get("functions"), list)
                        else []
                    )
                    selected_record["pass_impl_ir_slice_matched"] = int(slice_status.get("matched", 0))
                    selected_record["pass_impl_ir_slice_absent"] = sum(
                        int(count_value)
                        for status, count_value in slice_status.items()
                        if status != "matched"
                    )
                    for finding, slice_record in zip(file_findings, slices):
                        finding["pass_impl_ir_graph_ref"] = graph_ref
                        finding["pass_impl_ir_slice"] = slice_record
                        finding["pass_impl_ir_intent_check"] = pass_impl_ir_intent_check_for(finding)
        manifest.append(selected_record)
        all_findings.extend(file_findings)

    if not selected:
        write_jsonl(source_manifest, manifest)
        write_command_log(command_log, commands)
        print("no source files selected from compile_commands.json", file=sys.stderr)
        return 1
    if args.require_clean_mining and any(record.get("status") == "error" for record in manifest):
        write_jsonl(source_manifest, manifest)
        write_command_log(command_log, commands)
        print("AST mining issues found", file=sys.stderr)
        return 1

    pass_filter = {item.strip() for item in str(args.passes or "").split(",") if item.strip()}
    marker_filter = {str(item) for item in args.marker if str(item)}
    marker_prefixes = [str(item) for item in args.marker_prefix if str(item)]
    findings = filter_findings_by_marker(merge_findings(all_findings, pass_filter), marker_filter, marker_prefixes)
    write_json(findings_path, findings)
    write_jsonl(source_manifest, sorted(manifest, key=lambda item: str(item.get("file") or "")))

    if args.verify_source_slice_contracts:
        verify_command = [
            str(args.source_slice_contract_verifier),
            "--findings",
            str(findings_path),
            "--out",
            str(contract_verification),
            "--report",
            str(contract_verification_report),
            "--require-clean",
        ]
        commands.append(verify_command)
        verified_contracts = run(verify_command)
        if verified_contracts.stdout:
            print(verified_contracts.stdout, end="")
        if verified_contracts.returncode != 0:
            print(verified_contracts.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return verified_contracts.returncode

    infer_command = [
        str(args.intent_inferer),
        "--findings",
        str(findings_path),
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

    if args.verify_transaction_formalization:
        formalization_command = [
            str(args.transaction_formalization_verifier),
            "--input",
            str(intent_candidates),
            "--out",
            str(formalization_verification),
            "--report",
            str(formalization_verification_report),
            "--require-clean",
        ]
        commands.append(formalization_command)
        verified_formalization = run(formalization_command)
        if verified_formalization.stdout:
            print(verified_formalization.stdout, end="")
        if verified_formalization.returncode != 0:
            print(verified_formalization.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return verified_formalization.returncode

    validate_command = [
        str(args.intent_validator),
        "--input",
        str(intent_candidates),
        "--out",
        str(intent_validated),
        "--z3",
        args.z3,
    ]
    if args.emit_smt:
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
    intent_summary_path.write_text(intent_summary(validated_records), encoding="utf-8")
    slp_ir_data: dict[str, Any] | None = None
    modelcheck_summary: dict[str, Any] = {}

    if args.modelcheck_intents:
        modelcheck_parts: list[dict[str, Any]] = []
        modelcheck_command = [
            sys.executable,
            str(args.modelcheck_intent_checker),
            "--input",
            str(intent_validated),
            "--out-dir",
            str(modelcheck_intents),
            "--engine",
            args.modelcheck_engine,
            "--unwind",
            str(args.modelcheck_unwind),
            "--timeout",
            str(args.modelcheck_timeout),
            "--widths",
            str(args.modelcheck_widths),
            "--summary-json",
            str(modelcheck_scalar_summary_path),
        ]
        commands.append(modelcheck_command)
        modelchecked = run(modelcheck_command)
        if modelchecked.stdout:
            print(modelchecked.stdout, end="")
        if modelchecked.stderr:
            print(modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_scalar_summary_path.exists():
            scalar_summary = json.loads(modelcheck_scalar_summary_path.read_text(encoding="utf-8"))
            scalar_summary["summary"] = str(modelcheck_scalar_summary_path)
        else:
            scalar_summary = modelcheck_error_summary(
                modelcheck_scalar_summary_path,
                "intent",
                len(validated_records),
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck intent checker did not write a summary",
            )
        modelcheck_parts.append(scalar_summary)

        cfg_command = [
            sys.executable,
            str(args.modelcheck_cfg_checker),
        ]
        for source in selected:
            cfg_command.extend(["--source", str(source)])
        cfg_command.extend(
            [
                "--out-dir",
                str(modelcheck_cfg_source),
                "--engine",
                args.modelcheck_engine,
                "--unwind",
                str(args.modelcheck_unwind),
                "--timeout",
                str(args.modelcheck_timeout),
                "--widths",
                str(args.modelcheck_widths),
                "--summary-json",
                str(modelcheck_cfg_summary_path),
            ]
        )
        commands.append(cfg_command)
        cfg_modelchecked = run(cfg_command)
        if cfg_modelchecked.stdout:
            print(cfg_modelchecked.stdout, end="")
        if cfg_modelchecked.stderr:
            print(cfg_modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_cfg_summary_path.exists():
            cfg_summary = json.loads(modelcheck_cfg_summary_path.read_text(encoding="utf-8"))
            cfg_summary["summary"] = str(modelcheck_cfg_summary_path)
        else:
            cfg_summary = modelcheck_error_summary(
                modelcheck_cfg_summary_path,
                "cfg-source",
                0,
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck cfg checker did not write a summary",
            )
        modelcheck_parts.append(cfg_summary)
        memory_command = [
            sys.executable,
            str(args.modelcheck_memory_checker),
        ]
        for source in selected:
            memory_command.extend(["--source", str(source)])
        memory_command.extend(
            [
                "--out-dir",
                str(modelcheck_memory_source),
                "--engine",
                args.modelcheck_engine,
                "--unwind",
                str(args.modelcheck_unwind),
                "--timeout",
                str(args.modelcheck_timeout),
                "--widths",
                str(args.modelcheck_widths),
                "--summary-json",
                str(modelcheck_memory_summary_path),
            ]
        )
        commands.append(memory_command)
        memory_modelchecked = run(memory_command)
        if memory_modelchecked.stdout:
            print(memory_modelchecked.stdout, end="")
        if memory_modelchecked.stderr:
            print(memory_modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_memory_summary_path.exists():
            memory_summary = json.loads(modelcheck_memory_summary_path.read_text(encoding="utf-8"))
            memory_summary["summary"] = str(modelcheck_memory_summary_path)
        else:
            memory_summary = modelcheck_error_summary(
                modelcheck_memory_summary_path,
                "memory-source",
                0,
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck memory checker did not write a summary",
            )
        modelcheck_parts.append(memory_summary)
        licm_command = [
            sys.executable,
            str(args.modelcheck_licm_checker),
        ]
        for source in selected:
            licm_command.extend(["--source", str(source)])
        licm_command.extend(
            [
                "--out-dir",
                str(modelcheck_licm_source),
                "--engine",
                args.modelcheck_engine,
                "--unwind",
                str(args.modelcheck_unwind),
                "--timeout",
                str(args.modelcheck_timeout),
                "--widths",
                str(args.modelcheck_widths),
                "--summary-json",
                str(modelcheck_licm_summary_path),
            ]
        )
        commands.append(licm_command)
        licm_modelchecked = run(licm_command)
        if licm_modelchecked.stdout:
            print(licm_modelchecked.stdout, end="")
        if licm_modelchecked.stderr:
            print(licm_modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_licm_summary_path.exists():
            licm_summary = json.loads(modelcheck_licm_summary_path.read_text(encoding="utf-8"))
            licm_summary["summary"] = str(modelcheck_licm_summary_path)
        else:
            licm_summary = modelcheck_error_summary(
                modelcheck_licm_summary_path,
                "licm-source",
                0,
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck licm checker did not write a summary",
            )
        modelcheck_parts.append(licm_summary)
        globalopt_command = [
            sys.executable,
            str(args.modelcheck_globalopt_checker),
        ]
        for source in selected:
            globalopt_command.extend(["--source", str(source)])
        globalopt_command.extend(
            [
                "--out-dir",
                str(modelcheck_globalopt_source),
                "--engine",
                args.modelcheck_engine,
                "--unwind",
                str(args.modelcheck_unwind),
                "--timeout",
                str(args.modelcheck_timeout),
                "--widths",
                str(args.modelcheck_widths),
                "--summary-json",
                str(modelcheck_globalopt_summary_path),
            ]
        )
        commands.append(globalopt_command)
        globalopt_modelchecked = run(globalopt_command)
        if globalopt_modelchecked.stdout:
            print(globalopt_modelchecked.stdout, end="")
        if globalopt_modelchecked.stderr:
            print(globalopt_modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_globalopt_summary_path.exists():
            globalopt_summary = json.loads(modelcheck_globalopt_summary_path.read_text(encoding="utf-8"))
            globalopt_summary["summary"] = str(modelcheck_globalopt_summary_path)
        else:
            globalopt_summary = modelcheck_error_summary(
                modelcheck_globalopt_summary_path,
                "globalopt-source",
                0,
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck globalopt checker did not write a summary",
            )
        modelcheck_parts.append(globalopt_summary)
        dce_command = [
            sys.executable,
            str(args.modelcheck_dce_checker),
        ]
        for source in selected:
            dce_command.extend(["--source", str(source)])
        dce_command.extend(
            [
                "--out-dir",
                str(modelcheck_dce_source),
                "--engine",
                args.modelcheck_engine,
                "--unwind",
                str(args.modelcheck_unwind),
                "--timeout",
                str(args.modelcheck_timeout),
                "--widths",
                str(args.modelcheck_widths),
                "--summary-json",
                str(modelcheck_dce_summary_path),
            ]
        )
        commands.append(dce_command)
        dce_modelchecked = run(dce_command)
        if dce_modelchecked.stdout:
            print(dce_modelchecked.stdout, end="")
        if dce_modelchecked.stderr:
            print(dce_modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_dce_summary_path.exists():
            dce_summary = json.loads(modelcheck_dce_summary_path.read_text(encoding="utf-8"))
            dce_summary["summary"] = str(modelcheck_dce_summary_path)
        else:
            dce_summary = modelcheck_error_summary(
                modelcheck_dce_summary_path,
                "dce-source",
                0,
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck dce checker did not write a summary",
            )
        modelcheck_parts.append(dce_summary)
        slp_command = [
            sys.executable,
            str(args.modelcheck_slp_checker),
        ]
        for source in selected:
            slp_command.extend(["--source", str(source)])
        slp_command.extend(
            [
                "--out-dir",
                str(modelcheck_slp_source),
                "--engine",
                args.modelcheck_engine,
                "--unwind",
                str(args.modelcheck_unwind),
                "--timeout",
                str(args.modelcheck_timeout),
                "--widths",
                str(args.modelcheck_widths),
                "--summary-json",
                str(modelcheck_slp_summary_path),
            ]
        )
        commands.append(slp_command)
        slp_modelchecked = run(slp_command)
        if slp_modelchecked.stdout:
            print(slp_modelchecked.stdout, end="")
        if slp_modelchecked.stderr:
            print(slp_modelchecked.stderr, file=sys.stderr, end="")
        if modelcheck_slp_summary_path.exists():
            slp_summary = json.loads(modelcheck_slp_summary_path.read_text(encoding="utf-8"))
            slp_summary["summary"] = str(modelcheck_slp_summary_path)
        else:
            slp_summary = modelcheck_error_summary(
                modelcheck_slp_summary_path,
                "slp-source",
                0,
                args.modelcheck_engine,
                args.modelcheck_widths,
                "modelcheck slp checker did not write a summary",
            )
        modelcheck_parts.append(slp_summary)
        modelcheck_summary = merge_modelcheck_summaries(
            modelcheck_summary_path,
            modelcheck_parts,
            args.modelcheck_widths,
        )
        write_json(modelcheck_summary_path, modelcheck_summary)

    if args.emit_slp_transaction_ir and has_slp_transaction(validated_records):
        slp_summary_json = slp_transaction_ir / "summary.json"
        slp_unsupported_jsonl = slp_transaction_ir / "unsupported.jsonl"
        slp_ir_command = [
            sys.executable,
            str(args.slp_transaction_ir_emitter),
            "--input",
            str(intent_validated),
            "--out-dir",
            str(slp_transaction_ir),
            "--summary-json",
            str(slp_summary_json),
            "--unsupported-jsonl",
            str(slp_unsupported_jsonl),
        ]
        if args.validate_slp_ir:
            slp_ir_command.append("--validate-ir")
        if args.llvm_as:
            slp_ir_command.extend(["--llvm-as", str(args.llvm_as)])
        commands.append(slp_ir_command)
        emitted_slp_ir = run(slp_ir_command)
        if emitted_slp_ir.stdout:
            print(emitted_slp_ir.stdout, end="")
        if emitted_slp_ir.returncode != 0:
            print(emitted_slp_ir.stderr, file=sys.stderr, end="")
            write_command_log(command_log, commands)
            return emitted_slp_ir.returncode
        if slp_summary_json.exists():
            slp_ir_data = json.loads(slp_summary_json.read_text(encoding="utf-8"))

    audit_command = [
        str(args.coverage_auditor),
        "--validated",
        str(intent_validated),
        "--intent-registry",
        str(args.intent_registry),
        "--semantic-facts",
        str(args.semantic_registry),
        "--guard-semantics",
        str(args.guard_semantics),
        "--out",
        str(intent_coverage),
        "--report",
        str(intent_coverage_report),
    ]
    commands.append(audit_command)
    audited = run(audit_command)
    if audited.stdout:
        print(audited.stdout, end="")
    if audited.returncode != 0:
        print(audited.stderr, file=sys.stderr, end="")
        write_command_log(command_log, commands)
        return audited.returncode

    coverage = json.loads(intent_coverage.read_text(encoding="utf-8")) if intent_coverage.exists() else {}
    formal_provenance_summary = formalization_provenance_coverage_summary(formalization_verification)
    if formal_provenance_summary:
        coverage.setdefault("summary", {})["transaction_formal_provenance_coverage"] = formal_provenance_summary
        write_json(intent_coverage, coverage)
    current_baseline = with_modelcheck_baseline(baseline_from_coverage(coverage), modelcheck_summary)
    previous_baseline = load_baseline(args.baseline)
    baseline_diff = compare_baselines(previous_baseline, current_baseline, args.baseline is not None)
    write_json(baseline_path, current_baseline)
    write_json(baseline_diff_path, baseline_diff)
    baseline_diff_report.write_text(format_baseline_diff(baseline_diff), encoding="utf-8")
    violations = budget_violations(args, manifest, validated_records, coverage, baseline_diff, modelcheck_summary)
    slp_ir = slp_transaction_ir_summary(args.emit_slp_transaction_ir, slp_transaction_ir, slp_ir_data)
    summary = run_summary(
        args,
        manifest,
        findings,
        validated_records,
        coverage,
        modelcheck_summary,
        commands,
        violations,
        current_baseline,
        baseline_diff,
        slp_ir,
    )
    write_json(run_summary_path, summary)
    run_summary_report.write_text(format_run_summary(summary), encoding="utf-8")
    readiness = real_pass_readiness_report(summary, findings, validated_records, slp_ir)
    write_json(readiness_path, readiness)
    readiness_report_path.write_text(format_real_pass_readiness(readiness), encoding="utf-8")
    write_command_log(command_log, commands)
    print(f"sources: {source_manifest}")
    print(f"findings: {findings_path}")
    print(f"intent_candidates: {intent_candidates}")
    print(f"intent_validated: {intent_validated}")
    print(f"intent_summary: {intent_summary_path}")
    print(f"intent_coverage: {intent_coverage}")
    print(f"intent_coverage_report: {intent_coverage_report}")
    if contract_verification.exists():
        print(f"source_slice_contract_verification: {contract_verification}")
    if contract_verification_report.exists():
        print(f"source_slice_contract_verification_report: {contract_verification_report}")
    if formalization_verification.exists():
        print(f"transaction_formalization_verification: {formalization_verification}")
    if formalization_verification_report.exists():
        print(f"transaction_formalization_verification_report: {formalization_verification_report}")
    if (slp_transaction_ir / "manifest.jsonl").exists():
        print(f"slp_transaction_ir: {slp_transaction_ir}")
    if modelcheck_summary_path.exists():
        print(f"modelcheck_intents: {modelcheck_summary_path}")
    print(f"run_summary: {run_summary_path}")
    print(f"run_summary_report: {run_summary_report}")
    print(f"real_pass_readiness: {readiness_path}")
    print(f"real_pass_readiness_report: {readiness_report_path}")
    print(f"audit_baseline: {baseline_path}")
    print(f"baseline_diff: {baseline_diff_path}")
    print(f"baseline_diff_report: {baseline_diff_report}")
    if intent_smt.exists():
        print(f"intent_smt: {intent_smt}")
    if violations:
        for violation in violations:
            print(
                f"budget violation: {violation['budget']} actual={violation['actual']} limit={violation['limit']}",
                file=sys.stderr,
            )
        return 1
    return 0 if validated.returncode == 0 else validated.returncode


if __name__ == "__main__":
    raise SystemExit(main())
