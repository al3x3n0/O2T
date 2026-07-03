#!/usr/bin/env python3
"""Modelcheck source-recovered DSE/store-forward memory transforms."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from o2t.intent import extract_memory_model as EM
from o2t.symexec import modelcheck as M
from o2t.symexec.modelcheck_intents import (
    SUPPORTED_WIDTHS,
    UnsupportedIntent,
    actionable_finding,
    parse_widths,
    safe_ident,
)

KIND_MARKERS = {
    "dse-remove": "probe.dse.overwritten-store",
    "store-forward": "probe.mem2reg.store-load-forward",
}


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
        source_text.find("deleteDeadInstruction", start, end),
        source_text.find("eraseFromParent", start, end),
        source_text.find("replaceAllUsesWith", start, end),
    ]
    location = next((candidate for candidate in candidates if candidate >= 0), start)
    return source_text.count("\n", 0, location) + 1


def _collect_ops(ops: list[dict[str, Any]], addrs: set[str], vals: set[str], loads: set[str]) -> None:
    for op in ops:
        kind = str(op.get("op") or "")
        if "addr" in op:
            addrs.add(str(op["addr"]))
        if kind == "store":
            vals.add(str(op["val"]))
        elif kind == "load":
            loads.add(str(op["name"]))
        elif kind == "bind":
            loads.add(str(op["name"]))
            src = str(op["src"])
            if src not in loads:
                vals.add(src)


def model_symbols(model: dict[str, Any]) -> tuple[list[str], list[str]]:
    addrs: set[str] = set()
    vals: set[str] = set()
    loads: set[str] = set()
    _collect_ops(model.get("before") or [], addrs, vals, loads)
    _collect_ops(model.get("after") or [], addrs, vals, loads)
    for assumption in model.get("assumptions") or []:
        for arg in assumption.get("args") or []:
            addrs.add(str(arg))
    return sorted(addrs), sorted(vals)


def source_records(source: Path) -> list[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for name, model in EM.mine_source(text).items():
        if model is None:
            continue
        kind = str(model.get("kind") or "")
        records.append(
            {
                "marker": KIND_MARKERS.get(kind, "probe.dse.overwritten-store"),
                "file": str(source),
                "line": rewrite_line(text, name),
                "pass": "dse",
                "source_function": name,
                "kind": kind,
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


def _symbol(name: str) -> str:
    return safe_ident(name, "sym")


def memory_domain(width: int | None = None) -> str:
    return f"memory-bv{width or 32}"


def _mem0_expr(addr: str, base_symbols: list[str]) -> str:
    expr = "Mem0_default"
    for symbol in reversed(base_symbols):
        expr = f"cv_ite(cv_eq({addr}, {symbol}), Mem0_{symbol}, {expr})"
    return expr


def _load_expr(stores: list[tuple[str, str]], addr: str, base_symbols: list[str]) -> str:
    expr = _mem0_expr(addr, base_symbols)
    for store_addr, store_val in reversed(stores):
        expr = f"cv_ite(cv_eq({addr}, {store_addr}), {store_val}, {expr})"
    return expr


def _execute_ops(
    ops: list[dict[str, Any]],
    observable_addr: str | None,
    base_symbols: list[str],
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    stores: list[tuple[str, str]] = []
    loads: dict[str, str] = {}
    for op in ops:
        kind = str(op.get("op") or "")
        if kind == "store":
            stores.append((_symbol(str(op["addr"])), _symbol(str(op["val"]))))
        elif kind == "load":
            loads[str(op["name"])] = _load_expr(stores, _symbol(str(op["addr"])), base_symbols)
        elif kind == "bind":
            src = str(op["src"])
            loads[str(op["name"])] = loads.get(src, _symbol(src))
        else:
            raise UnsupportedIntent(f"unsupported-memory-op:{kind or 'unset'}")
    if observable_addr is not None:
        loads["__observable_memory"] = _load_expr(stores, observable_addr, base_symbols)
    return stores, loads


def _assumption_cpp(assumption: dict[str, Any]) -> str:
    op = str(assumption.get("op") or "")
    args = assumption.get("args")
    if not isinstance(args, list) or len(args) != 2:
        raise UnsupportedIntent("unsupported-memory-assumption")
    left, right = _symbol(str(args[0])), _symbol(str(args[1]))
    if op == "eq":
        return f"((cv_eq({left}, {right}).bits & 1U) != 0U)"
    if op == "ne":
        return f"((cv_eq({left}, {right}).bits & 1U) == 0U)"
    raise UnsupportedIntent(f"unsupported-memory-assumption:{op or 'unset'}")


def check_function_name(index: int, record: dict[str, Any], width: int, include_width: bool = False) -> str:
    marker = safe_ident(str(record.get("marker") or "memory"), "memory")
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
        raise UnsupportedIntent("missing-memory-model")
    addrs, vals = model_symbols(model)
    observable = str(model.get("observable") or "")
    observable_addr = "observer" if observable == "memory" else None
    function = check_function_name(index, record, width, include_width)
    lines = [
        '#include "modelcheck_llvm.h"',
        "",
        f'extern "C" void {function}() {{',
    ]
    for name in addrs + vals:
        lines.append(f"  Value {_symbol(name)} = cv_any_bv({width}U);")
    if observable_addr is not None:
        lines.append(f"  Value {observable_addr} = cv_any_bv({width}U);")
    base_symbols = [_symbol(name) for name in addrs]
    if observable_addr is not None:
        base_symbols.append(observable_addr)
    lines.append(f"  Value Mem0_default = cv_any_bv({width}U);")
    for name in addrs:
        lines.append(f"  Value Mem0_{_symbol(name)} = cv_any_bv({width}U);")
    if observable_addr is not None:
        lines.append(f"  Value Mem0_{observable_addr} = cv_any_bv({width}U);")
    for assumption in model.get("assumptions") or []:
        lines.append(f"  CV_ASSUME({_assumption_cpp(assumption)});")

    before_stores, before_loads = _execute_ops(model.get("before") or [], observable_addr, base_symbols)
    after_stores, after_loads = _execute_ops(model.get("after") or [], observable_addr, base_symbols)
    _ = before_stores, after_stores
    if observable == "memory":
        before = before_loads["__observable_memory"]
        after = after_loads["__observable_memory"]
    elif observable.startswith("load:"):
        name = observable.split(":", 1)[1]
        if name not in before_loads or name not in after_loads:
            raise UnsupportedIntent("unsupported-memory-observable")
        before = before_loads[name]
        after = after_loads[name]
    else:
        raise UnsupportedIntent(f"unsupported-memory-observable:{observable or 'unset'}")
    lines.append(f"  Value before = {before};")
    lines.append(f"  Value after = {after};")
    lines.append(f'  cv_assert_equivalent(before, after, "{function} memory equivalence");')
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
        "domain": memory_domain(width),
    }
    if width is not None:
        result["width"] = width
    return result


def run_memory_source_modelcheck(
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
    input_path = out_dir / "memory-source-models.jsonl"
    write_jsonl(input_path, records)
    engine_path, engine_name = M.resolve_engine(engine)
    results: list[dict[str, Any]] = []
    try:
        selected_widths = parse_widths(widths)
    except UnsupportedIntent as exc:
        selected_widths = []
        results.append({"record_index": -1, "marker": "", "file": "", "line": 0,
                        "status": "error", "reason": str(exc), "domain": memory_domain()})
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
                "domain": memory_domain(width),
                "source_function": str(record.get("source_function") or ""),
                "kind": str(record.get("kind") or ""),
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
        "model": "o2t-modelcheck-memory-source-summary-v1",
        "source_kind": "memory-source",
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
