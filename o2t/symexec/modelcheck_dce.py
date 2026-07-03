#!/usr/bin/env python3
"""Modelcheck source-recovered DCE dead-instruction erasures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from o2t.intent import extract_dce_model as ED
from o2t.symexec import modelcheck as M
from o2t.symexec.modelcheck_intents import (
    SUPPORTED_WIDTHS,
    UnsupportedIntent,
    actionable_finding,
    parse_widths,
    safe_ident,
)

DEAD_INSTRUCTION_DOMAIN = "dce-dead-instruction-observable-v1"
DEAD_LOOP_INSTRUCTION_DOMAIN = "dce-dead-loop-instruction-observable-v1"
UNUSED_ALLOCA_DOMAIN = "unused-alloca-observable-v1"
DEAD_INSTRUCTION_MARKER = "probe.dce.dead-instruction"
DEAD_LOOP_INSTRUCTION_MARKER = "probe.dce.dead-loop-instruction"
UNUSED_ALLOCA_MARKER = "probe.cleanup.unused-alloca"


def _function_extent(source_text: str, name: str) -> tuple[int, int] | None:
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{", re.S)
    match = pattern.search(source_text)
    if match is None:
        return None
    depth = 1
    index = match.end()
    while index < len(source_text) and depth:
        depth += {"{": 1, "}": -1}.get(source_text[index], 0)
        index += 1
    return match.start(), index


def rewrite_line(source_text: str, name: str) -> int:
    extent = _function_extent(source_text, name)
    if extent is None:
        return 0
    start, end = extent
    candidates = [
        source_text.find("eraseFromParent", start, end),
        source_text.find("deleteDeadInstruction", start, end),
        source_text.find("RecursivelyDeleteTriviallyDeadInstructions", start, end),
    ]
    location = next((candidate for candidate in candidates if candidate >= 0), start)
    return source_text.count("\n", 0, location) + 1


def obligation_for_model(model: dict[str, Any]) -> tuple[str, str]:
    if str(model.get("kind") or "") == "unused-alloca":
        if bool(model.get("unused_alloca")):
            return "alloca-unobservable", "use-empty"
        return "alloca-may-be-observable", "missing-use-empty-guard"
    if str(model.get("kind") or "") == "dead-loop-instruction":
        if bool(model.get("dead_loop_instruction")):
            return "loop-instruction-unobservable", "dead-loop-instruction"
        return "loop-instruction-may-be-observable", "missing-dead-loop-guard"
    if bool(model.get("trivially_dead")):
        return "unobservable", "trivially-dead"
    return "may-be-observable", "missing-trivially-dead-guard"


def domain_for_model(model: dict[str, Any]) -> str:
    if str(model.get("kind") or "") == "unused-alloca":
        return UNUSED_ALLOCA_DOMAIN
    if str(model.get("kind") or "") == "dead-loop-instruction":
        return DEAD_LOOP_INSTRUCTION_DOMAIN
    return DEAD_INSTRUCTION_DOMAIN


def source_records(source: Path) -> list[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for name, body in ED.split_function_texts(text).items():
        model = ED.recognize_dead_erase(body)
        if model is None:
            continue
        obligation, reason = obligation_for_model(model)
        domain = domain_for_model(model)
        records.append(
            {
                "marker": str(model.get("marker") or DEAD_INSTRUCTION_MARKER),
                "file": str(source),
                "line": rewrite_line(text, name),
                "pass": "cleanup" if str(model.get("kind") or "") == "unused-alloca" else "dce",
                "source_function": name,
                "kind": str(model.get("kind") or "dead-instruction"),
                "domain": domain,
                "obligation": obligation,
                "reason": reason,
                "model": model,
                "proof_status": "modelcheck-candidate",
            }
        )
    return records


def records_for_sources(sources: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in sources:
        records.extend(source_records(source))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def check_function_name(index: int, record: dict[str, Any], width: int, include_width: bool = False) -> str:
    marker = safe_ident(str(record.get("marker") or "dce"), "dce")
    source_function = safe_ident(str(record.get("source_function") or ""), "")
    if source_function:
        marker = f"{marker}_{source_function}"
    suffix = f"_w{width}" if include_width else ""
    return f"check_{index:04d}_{marker}{suffix}"


def harness_for_record(
    index: int,
    record: dict[str, Any],
    width: int,
    include_width: bool = False,
) -> tuple[str, str]:
    if width not in SUPPORTED_WIDTHS:
        raise UnsupportedIntent(f"unsupported-width:{width}")
    model = record.get("model")
    if not isinstance(model, dict):
        raise UnsupportedIntent("missing-dce-model")
    function = check_function_name(index, record, width, include_width)
    kind = str(model.get("kind") or "")
    guarded = bool(
        model.get("unused_alloca")
        if kind == "unused-alloca"
        else (model.get("dead_loop_instruction") if kind == "dead-loop-instruction" else model.get("trivially_dead"))
    )
    if kind == "unused-alloca":
        lines = [
            '#include "modelcheck_llvm.h"',
            "",
            f'extern "C" void {function}() {{',
            "  bool alloca_use = nondet_bool();",
            "  bool alloca_escape = nondet_bool();",
            "  bool lifetime_effect = nondet_bool();",
        ]
        if guarded:
            lines.extend(
                [
                    "  CV_ASSUME(!alloca_use);",
                    "  CV_ASSUME(!alloca_escape);",
                    "  CV_ASSUME(!lifetime_effect);",
                ]
            )
        lines.append(
            f'  CV_ASSERT(!alloca_use && !alloca_escape && !lifetime_effect, "{function} erased alloca unobservable");'
        )
        lines += ["}", "", "int main() { return 0; }", ""]
        return function, "\n".join(lines)
    if kind == "dead-loop-instruction":
        lines = [
            '#include "modelcheck_llvm.h"',
            "",
            f'extern "C" void {function}() {{',
            "  bool loop_result_use = nondet_bool();",
            "  bool loop_control_effect = nondet_bool();",
            "  bool loop_side_effect = nondet_bool();",
        ]
        if guarded:
            lines.extend(
                [
                    "  CV_ASSUME(!loop_result_use);",
                    "  CV_ASSUME(!loop_control_effect);",
                    "  CV_ASSUME(!loop_side_effect);",
                ]
            )
        lines.append(
            f'  CV_ASSERT(!loop_result_use && !loop_control_effect && !loop_side_effect, "{function} erased loop instruction unobservable");'
        )
        lines += ["}", "", "int main() { return 0; }", ""]
        return function, "\n".join(lines)
    lines = [
        '#include "modelcheck_llvm.h"',
        "",
        f'extern "C" void {function}() {{',
        "  bool live_use = nondet_bool();",
        "  bool side_effect = nondet_bool();",
    ]
    if guarded:
        lines.extend(
            [
                "  CV_ASSUME(!live_use);",
                "  CV_ASSUME(!side_effect);",
            ]
        )
    lines.append(f'  CV_ASSERT(!live_use && !side_effect, "{function} erased instruction unobservable");')
    lines += ["}", "", "int main() { return 0; }", ""]
    return function, "\n".join(lines)


def unsupported_result(index: int, record: dict[str, Any], reason: str, width: int | None = None) -> dict[str, Any]:
    result = {
        "record_index": index,
        "marker": str(record.get("marker") or ""),
        "file": str(record.get("file") or ""),
        "line": int(record.get("line") or 0),
        "status": "unsupported",
        "reason": reason,
        "source_function": str(record.get("source_function") or ""),
        "domain": str(record.get("domain") or DEAD_INSTRUCTION_DOMAIN),
    }
    if width is not None:
        result["width"] = width
    return result


def run_dce_source_modelcheck(
    sources: list[Path],
    out_dir: Path,
    engine: str = "auto",
    unwind: int = 8,
    timeout_s: int = 30,
    widths: str = "native",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    harness_dir = out_dir / "harnesses"
    harness_dir.mkdir(parents=True, exist_ok=True)
    records = records_for_sources(sources)
    input_path = out_dir / "dce-source-models.jsonl"
    write_jsonl(input_path, records)
    engine_path, engine_name = M.resolve_engine(engine)
    results: list[dict[str, Any]] = []
    try:
        selected_widths = parse_widths(widths)
    except UnsupportedIntent as exc:
        selected_widths = []
        results.append({"record_index": -1, "marker": "", "file": "", "line": 0,
                        "status": "error", "reason": str(exc), "domain": DEAD_INSTRUCTION_DOMAIN})
    work_widths = [32] if selected_widths is None else selected_widths
    for index, record in enumerate([] if not work_widths else records):
        for width in work_widths:
            try:
                function, source = harness_for_record(index, record, width, include_width=selected_widths is not None)
            except UnsupportedIntent as exc:
                results.append(unsupported_result(index, record, str(exc), width))
                continue
            harness_path = harness_dir / f"{function}.cpp"
            harness_path.write_text(source, encoding="utf-8")
            base = {
                "record_index": index,
                "marker": str(record.get("marker") or ""),
                "file": str(record.get("file") or ""),
                "line": int(record.get("line") or 0),
                "domain": str(record.get("domain") or DEAD_INSTRUCTION_DOMAIN),
                "source_function": str(record.get("source_function") or ""),
                "kind": str(record.get("kind") or ""),
                "obligation": str(record.get("obligation") or ""),
                "width": width,
                "function": function,
                "harness": str(harness_path),
            }
            if engine_path is None:
                wanted = engine if engine != "auto" else "cbmc/esbmc"
                results.append({**base, "status": "skipped", "reason": f"model checker not found: {wanted}"})
                continue
            checked = M.run_fold(harness_path, function, engine_name, engine_path, unwind, timeout_s)
            checked.update(base)
            results.append(checked)
    counts = {
        status: sum(1 for item in results if item.get("status") == status)
        for status in ("proved", "refuted", "unsupported", "skipped", "error")
    }
    findings = [
        finding
        for item in results
        if (finding := actionable_finding(item)) is not None
    ]
    width_rollup: dict[str, dict[str, int]] = {}
    for item in results:
        key = str(item.get("width") or "none")
        bucket = width_rollup.setdefault(
            key,
            {status: 0 for status in ("proved", "refuted", "unsupported", "skipped", "error")},
        )
        status = str(item.get("status") or "")
        if status in bucket:
            bucket[status] += 1
    return {
        "model": "o2t-modelcheck-dce-source-summary-v1",
        "source_kind": "dce-source",
        "sources": [str(source) for source in sources],
        "input": str(input_path),
        "out_dir": str(out_dir),
        "engine": engine_name,
        "engine_path": engine_path or "",
        "width_mode": widths,
        "selected_widths": selected_widths or [],
        "records": len(records),
        "transforms": len(records),
        "instances": len(results),
        **counts,
        "generated": sum(1 for item in results if item.get("harness")),
        "ok": counts["refuted"] == 0 and counts["error"] == 0,
        "widths": width_rollup,
        "findings": findings,
        "results": results,
    }
