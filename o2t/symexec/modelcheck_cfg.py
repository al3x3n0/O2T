#!/usr/bin/env python3
"""Modelcheck source-recovered SimplifyCFG diamond-to-select folds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from o2t.intent.extract_cfg_model import recognize_ifconversion_fold
from o2t.mine.pass_scev import split_functions
from o2t.symexec.modelcheck_intents import run_intent_modelcheck

MARKER = "probe.simplifycfg.diamond"


def var(name: str) -> dict[str, Any]:
    return {"op": "var", "name": name}


def bvconst(value: int, bits: int = 32) -> dict[str, Any]:
    return {"op": "bvconst", "bits": bits, "value": value}


def branch_condition(negated: bool = False) -> dict[str, Any]:
    return {"op": "eq" if negated else "ne", "args": [var("cond"), bvconst(0)]}


def role_value(role: str) -> dict[str, Any]:
    if role == "then":
        return var("then_value")
    if role == "else":
        return var("else_value")
    raise ValueError(f"unknown CFG value role: {role}")


def formal_for_ifconversion(match: dict[str, Any]) -> dict[str, Any]:
    """Build the source-specific CFG obligation for one mined select binding."""
    return {
        "domain": "cfg-bv32",
        "equivalence": "reachable-result",
        "variables": ["cond", "then_value", "else_value"],
        "poison_variables": [],
        "variable_bits": {"cond": 32, "then_value": 32, "else_value": 32},
        "refinement": "equality",
        "before": {
            "op": "ite",
            "args": [branch_condition(False), var("then_value"), var("else_value")],
        },
        "after": {
            "op": "ite",
            "args": [
                branch_condition(bool(match["cond_negated"])),
                role_value(str(match["true_src"])),
                role_value(str(match["false_src"])),
            ],
        },
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


def select_line(source_text: str, name: str) -> int:
    extent = _function_extent(source_text, name)
    if extent is None:
        return 0
    start, end = extent
    select_offset = source_text.find("CreateSelect", start, end)
    location = select_offset if select_offset >= 0 else start
    return source_text.count("\n", 0, location) + 1


def source_records(source: Path) -> list[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for name, body in split_functions(text).items():
        match = recognize_ifconversion_fold(body)
        if match is None:
            continue
        records.append(
            {
                "marker": MARKER,
                "file": str(source),
                "line": select_line(text, name),
                "pass": "simplifycfg",
                "source_function": name,
                "proof_status": "modelcheck-candidate",
                "intent_candidate": {"formal": formal_for_ifconversion(match)},
                "cfg_ifconversion": {
                    "cond_negated": bool(match["cond_negated"]),
                    "true_src": str(match["true_src"]),
                    "false_src": str(match["false_src"]),
                },
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


def run_cfg_source_modelcheck(
    sources: list[Path],
    out_dir: Path,
    engine: str = "auto",
    unwind: int = 8,
    timeout_s: int = 30,
    widths: str = "native",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = records_for_sources(sources)
    input_path = out_dir / "cfg-source-intents.jsonl"
    write_jsonl(input_path, records)
    summary = run_intent_modelcheck(input_path, out_dir, engine, unwind, timeout_s, widths)
    summary.update(
        {
            "model": "o2t-modelcheck-cfg-source-summary-v1",
            "source_kind": "cfg-source",
            "sources": [str(source) for source in sources],
            "transforms": len(records),
            "input": str(input_path),
        }
    )
    return summary
