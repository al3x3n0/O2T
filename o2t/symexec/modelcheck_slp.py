#!/usr/bin/env python3
"""Modelcheck source-recovered SLP pack and reduction folds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from o2t.intent import extract_slp_model as ES
from o2t.mine.pass_scev import split_functions
from o2t.symexec import modelcheck as M
from o2t.symexec.modelcheck_intents import (
    SUPPORTED_WIDTHS,
    UnsupportedIntent,
    actionable_finding,
    parse_widths,
    safe_ident,
)
from o2t.validate import slp_model as SLP

PACK_MARKER = "probe.slp.vectorize-binop"
REDUCTION_MARKER = "probe.slp.vectorize-reduction"
BV_DOMAIN = "vector-bv32xN"
FP_DOMAIN = "vector-fp32xN"


def slp_domain(record: dict[str, Any] | None = None, width: int | None = None) -> str:
    if isinstance(record, dict) and str(record.get("domain") or "") == FP_DOMAIN:
        return FP_DOMAIN
    return f"vector-bv{width or 32}xN"


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


def rewrite_line(source_text: str, name: str, kind: str) -> int:
    extent = _function_extent(source_text, name)
    if extent is None:
        return 0
    start, end = extent
    tokens = (
        ["ExtractElement", "InsertElement", "CreateAdd", "CreateMul"]
        if kind == "pack"
        else ["CreateAddReduce", "CreateMulReduce", "CreateFAddReduce", "CreateFMulReduce", "vector_reduce"]
    )
    candidates = [source_text.find(token, start, end) for token in tokens]
    location = next((candidate for candidate in candidates if candidate >= 0), start)
    return source_text.count("\n", 0, location) + 1


def _pack_lane_to_scalar(insert_lanes: list[int]) -> list[int]:
    pack = [0] * len(insert_lanes)
    for scalar, lane in enumerate(insert_lanes):
        pack[lane] = scalar
    return pack


def source_records(source: Path) -> list[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for name, body in split_functions(text).items():
        pack = ES.recognize_pack_fold(body)
        reduction = ES.recognize_reduction_fold(body)
        if pack is not None and reduction is None:
            records.append(
                {
                    "marker": PACK_MARKER,
                    "file": str(source),
                    "line": rewrite_line(text, name, "pack"),
                    "pass": "slp-vectorizer",
                    "source_function": name,
                    "kind": "pack",
                    "domain": BV_DOMAIN,
                    "model": pack,
                    "proof_status": "modelcheck-candidate",
                }
            )
            continue
        if reduction is None:
            continue
        is_fp = bool(reduction.get("is_fp"))
        records.append(
            {
                "marker": REDUCTION_MARKER,
                "file": str(source),
                "line": rewrite_line(text, name, "reduction"),
                "pass": "slp-vectorizer",
                "source_function": name,
                "kind": "reduction",
                "domain": FP_DOMAIN if is_fp else BV_DOMAIN,
                "model": reduction,
                "status_detail": "reassoc-allowed" if is_fp and bool(reduction.get("reassoc_guard")) else "",
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
    marker = safe_ident(str(record.get("marker") or "slp"), "slp")
    source_function = safe_ident(str(record.get("source_function") or ""), "")
    if source_function:
        marker = f"{marker}_{source_function}"
    suffix = f"_w{width}" if include_width else ""
    return f"check_{index:04d}_{marker}{suffix}"


def _bv_binop(op: str, left: str, right: str) -> str:
    table = {
        "add": f"cv_bvadd({left}, {right})",
        "mul": f"cv_bvmul({left}, {right})",
        "and": f"cv_bvand({left}, {right})",
        "or": f"cv_bvor({left}, {right})",
        "xor": f"cv_bvxor({left}, {right})",
    }
    if op not in table:
        raise UnsupportedIntent(f"unsupported-slp-op:{op or 'unset'}")
    return table[op]


def _seq_expr(values: list[str], op: str) -> str:
    acc = values[0]
    for value in values[1:]:
        acc = _bv_binop(op, acc, value)
    return acc


def _tree_expr(values: list[str], op: str) -> str:
    cur = list(values)
    while len(cur) > 1:
        cur = [
            _bv_binop(op, cur[index], cur[index + 1])
            for index in range(0, len(cur) - 1, 2)
        ] + ([cur[-1]] if len(cur) % 2 else [])
    return cur[0]


def _fp_op_symbol(op: str) -> str:
    if op == "add":
        return "+"
    if op == "mul":
        return "*"
    raise UnsupportedIntent(f"unsupported-slp-fp-op:{op or 'unset'}")


def _fp_seq_expr(values: list[str], op: str) -> str:
    sym = _fp_op_symbol(op)
    acc = values[0]
    for value in values[1:]:
        acc = f"({acc} {sym} {value})"
    return acc


def _fp_tree_expr(values: list[str], op: str) -> str:
    cur = list(values)
    sym = _fp_op_symbol(op)
    while len(cur) > 1:
        cur = [
            f"({cur[index]} {sym} {cur[index + 1]})"
            for index in range(0, len(cur) - 1, 2)
        ] + ([cur[-1]] if len(cur) % 2 else [])
    return cur[0]


def _pack_body(model: dict[str, Any], width: int, function: str) -> list[str]:
    lanes = int(model.get("n") or 0)
    if lanes < 2:
        raise UnsupportedIntent("unsupported-slp-pack-lanes")
    insert_lanes = model.get("insert_lanes")
    ext_lanes = model.get("ext_lanes")
    if not isinstance(insert_lanes, list) or not isinstance(ext_lanes, list) or len(insert_lanes) != lanes or len(ext_lanes) != lanes:
        raise UnsupportedIntent("unsupported-slp-pack-lanes")
    pack = _pack_lane_to_scalar([int(lane) for lane in insert_lanes])
    ext = [int(lane) for lane in ext_lanes]
    op = str(model.get("op") or "add")
    lines: list[str] = []
    for lane in range(lanes):
        lines.append(f"  Value a{lane} = cv_any_bv({width}U);")
        lines.append(f"  Value b{lane} = cv_any_bv({width}U);")
    for scalar in range(lanes):
        source_scalar = pack[ext[scalar]]
        before = _bv_binop(op, f"a{scalar}", f"b{scalar}")
        after = _bv_binop(op, f"a{source_scalar}", f"b{source_scalar}")
        lines.append(f"  Value before{scalar} = {before};")
        lines.append(f"  Value after{scalar} = {after};")
        lines.append(f'  cv_assert_equivalent(before{scalar}, after{scalar}, "{function} SLP lane {scalar}");')
    return lines


def _reduction_bv_body(model: dict[str, Any], width: int, function: str) -> list[str]:
    op = str(model.get("base_op") or "")
    if op not in SLP._BV_OP:
        raise UnsupportedIntent(f"unsupported-slp-reduction-op:{op or 'unset'}")
    lanes = 4
    values = [f"x{lane}" for lane in range(lanes)]
    lines = [f"  Value {name} = cv_any_bv({width}U);" for name in values]
    lines.append(f"  Value before = {_seq_expr(values, op)};")
    lines.append(f"  Value after = {_tree_expr(values, op)};")
    lines.append(f'  cv_assert_equivalent(before, after, "{function} SLP integer reduction");')
    return lines


def _reduction_fp_body(model: dict[str, Any], function: str) -> list[str]:
    op = str(model.get("base_op") or "")
    if bool(model.get("reassoc_guard")):
        return [f'  CV_ASSERT(true, "{function} SLP FP reassoc allowed");']
    values = [f"x{lane}" for lane in range(4)]
    lines = [
        "  auto cv_any_float = []() { union { uint32_t u; float f; } bits; bits.u = nondet_uint(); return bits.f; };"
    ]
    lines.extend(f"  float {name} = cv_any_float();" for name in values)
    lines.append(f"  float before = {_fp_seq_expr(values, op)};")
    lines.append(f"  float after = {_fp_tree_expr(values, op)};")
    lines.append(f'  CV_ASSERT(before == after, "{function} SLP FP reduction reassociation");')
    return lines


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
        raise UnsupportedIntent("missing-slp-model")
    function = check_function_name(index, record, width, include_width)
    kind = str(record.get("kind") or "")
    lines = [
        '#include "modelcheck_llvm.h"',
        "",
        f'extern "C" void {function}() {{',
    ]
    if kind == "pack":
        lines.extend(_pack_body(model, width, function))
    elif kind == "reduction" and bool(model.get("is_fp")):
        lines.extend(_reduction_fp_body(model, function))
    elif kind == "reduction":
        lines.extend(_reduction_bv_body(model, width, function))
    else:
        raise UnsupportedIntent(f"unsupported-slp-kind:{kind or 'unset'}")
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
        "domain": slp_domain(record, width),
    }
    if width is not None:
        result["width"] = width
    return result


def run_slp_source_modelcheck(
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
    input_path = out_dir / "slp-source-models.jsonl"
    write_jsonl(input_path, records)
    engine_path, engine_name = M.resolve_engine(engine)
    results: list[dict[str, Any]] = []
    try:
        selected_widths = parse_widths(widths)
    except UnsupportedIntent as exc:
        selected_widths = []
        results.append({"record_index": -1, "marker": "", "file": "", "line": 0,
                        "status": "error", "reason": str(exc), "domain": slp_domain()})
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
                "domain": slp_domain(record, width),
                "source_function": str(record.get("source_function") or ""),
                "kind": str(record.get("kind") or ""),
                "status_detail": str(record.get("status_detail") or ""),
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
        "model": "o2t-modelcheck-slp-source-summary-v1",
        "source_kind": "slp-source",
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
