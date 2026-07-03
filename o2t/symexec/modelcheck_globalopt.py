#!/usr/bin/env python3
"""Modelcheck source-recovered GlobalOpt dead-initializer defaulting."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from o2t.intent import extract_globalopt_model as EG
from o2t.mine.pass_scev import split_functions
from o2t.symexec import modelcheck as M
from o2t.symexec.modelcheck_intents import (
    SUPPORTED_WIDTHS,
    UnsupportedIntent,
    actionable_finding,
    parse_widths,
    safe_ident,
)

DOMAIN = "global-initializer-observable-v1"
MARKER = "probe.globalopt.dead-initializer"


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
        source_text.find("setInitializer", start, end),
        source_text.find("zeroinitializer", start, end),
        source_text.find("ConstantAggregateZero", start, end),
    ]
    location = next((candidate for candidate in candidates if candidate >= 0), start)
    return source_text.count("\n", 0, location) + 1


def obligation_for_model(model: dict[str, Any]) -> tuple[str, str]:
    if not bool(model.get("local_linkage")):
        return "external-observable", "external-linkage-observable"
    if not bool(model.get("use_empty")):
        return "read-before-store", "load-may-observe-initializer"
    return "unobservable", "internal-and-use-empty"


def source_records(source: Path) -> list[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for name, body in split_functions(text).items():
        model = EG.recognize_initializer_default(body)
        if model is None:
            continue
        obligation, reason = obligation_for_model(model)
        records.append(
            {
                "marker": MARKER,
                "file": str(source),
                "line": rewrite_line(text, name),
                "pass": "globalopt",
                "source_function": name,
                "kind": "dead-initializer-default",
                "domain": DOMAIN,
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
    marker = safe_ident(str(record.get("marker") or "globalopt"), "globalopt")
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
        raise UnsupportedIntent("missing-globalopt-model")
    function = check_function_name(index, record, width, include_width)
    observable = not bool(model.get("local_linkage")) or not bool(model.get("use_empty"))
    lines = [
        '#include "modelcheck_llvm.h"',
        "",
        f'extern "C" void {function}() {{',
        f"  Value init = cv_any_bv({width}U);",
        f"  Value zero = cv_value_w(0ULL, {width}U);",
    ]
    if observable:
        lines.extend(
            [
                "  Value before_observation = init;",
                "  Value after_observation = zero;",
                f'  cv_assert_equivalent(before_observation, after_observation, "{function} initializer observability");',
            ]
        )
    else:
        lines.append(f'  CV_ASSERT(true, "{function} initializer unobservable");')
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
        "domain": DOMAIN,
    }
    if width is not None:
        result["width"] = width
    return result


def run_globalopt_source_modelcheck(
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
    input_path = out_dir / "globalopt-source-models.jsonl"
    write_jsonl(input_path, records)
    engine_path, engine_name = M.resolve_engine(engine)
    results: list[dict[str, Any]] = []
    try:
        selected_widths = parse_widths(widths)
    except UnsupportedIntent as exc:
        selected_widths = []
        results.append({"record_index": -1, "marker": "", "file": "", "line": 0,
                        "status": "error", "reason": str(exc), "domain": DOMAIN})
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
                "domain": DOMAIN,
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
    counts = {status: sum(1 for item in results if item.get("status") == status)
              for status in ("proved", "refuted", "unsupported", "skipped", "error")}
    width_rollup: dict[str, dict[str, int]] = {}
    for item in results:
        key = str(item.get("width") or "none")
        bucket = width_rollup.setdefault(key, {status: 0 for status in ("proved", "refuted", "unsupported", "skipped", "error")})
        status = str(item.get("status") or "")
        if status in bucket:
            bucket[status] += 1
    findings = [
        finding
        for item in results
        if (finding := actionable_finding(item)) is not None
    ]
    return {
        "model": "o2t-modelcheck-globalopt-source-summary-v1",
        "source_kind": "globalopt-source",
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
