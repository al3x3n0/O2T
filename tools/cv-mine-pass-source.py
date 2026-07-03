#!/usr/bin/env python3
"""Mine LLVM-like pass sources for optimization predicate candidates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from cv_optimization_registry import (
    reduction_operation_for_token,
    reduction_tokens,
    vector_emission_tokens,
    vector_operation_for_token,
)
from cv_semantic_facts import DEFAULT_REGISTRY as DEFAULT_SEMANTIC_REGISTRY
from cv_semantic_facts import semantic_facts_for_marker
from cv_source_marker_rules import source_pattern_entries, source_rule_matches


DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "constraints" / "pass_constraints.json"
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc"}
SLP_EMISSION_TOKENS = vector_emission_tokens()
SLP_REDUCTION_TOKENS = reduction_tokens()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--semantic-registry", type=Path, default=DEFAULT_SEMANTIC_REGISTRY)
    parser.add_argument("--format", choices=["json", "jsonl"], default="json")
    parser.add_argument("--require-marker", action="append", default=[])
    parser.add_argument("--context", type=int, default=2)
    return parser.parse_args()


def load_registry(path: Path) -> list[dict[str, Any]]:
    with path.open() as input_file:
        registry = json.load(input_file)
    if not isinstance(registry, list):
        raise ValueError("constraint registry must be a JSON array")
    return registry


def source_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix in SOURCE_SUFFIXES:
                    files.append(candidate)
            continue
        raise FileNotFoundError(path)
    return sorted(files)


def pattern_matches(line: str, pattern: str) -> bool:
    if pattern.isidentifier():
        return re.search(rf"\b{re.escape(pattern)}\b", line) is not None
    return pattern in line


def infer_pass(path: Path, entry: dict[str, Any]) -> str:
    text = str(path).lower()
    if "instcombine" in text:
        return "instcombine"
    if "simplifycfg" in text:
        return "simplifycfg"
    if "/dce" in text or "dce" in path.name.lower():
        return "dce"
    return str(entry.get("pass", "unknown"))


def context_lines(lines: list[str], index: int, radius: int) -> list[str]:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return [line.rstrip("\n") for line in lines[start:end]]


def make_finding(
    path: Path,
    line_number: int,
    line: str,
    entry: dict[str, Any],
    pattern: str,
    context: list[str],
    semantic_registry: Path,
) -> dict[str, Any]:
    marker = str(entry["marker"])
    finding = {
        "file": str(path),
        "line": line_number,
        "marker": marker,
        "pass": infer_pass(path, entry),
        "predicate_kind": entry.get("predicate_kind", "unknown"),
        "matched_pattern": pattern,
        "source": line.strip(),
        "constraints": entry.get("constraints", {}),
        "suggestion": f'Wrap predicate with CV_PASS_PROBE_IF("{marker}", <predicate>)',
        "context": context,
    }
    semantic_facts = semantic_facts_for_marker(marker, semantic_registry)
    if semantic_facts:
        finding["semantic_facts"] = semantic_facts
    return finding


def slp_transaction_opcode(text: str) -> str | None:
    lowered = text.lower()
    opcode = reduction_operation_for_token(text) or vector_operation_for_token(text)
    if opcode:
        return opcode
    if "instruction::add" in lowered or "opcode == add" in lowered:
        return "add"
    if "instruction::sub" in lowered or "opcode == sub" in lowered:
        return "sub"
    if "instruction::mul" in lowered or "opcode == mul" in lowered:
        return "mul"
    if "instruction::xor" in lowered or "opcode == xor" in lowered:
        return "xor"
    if "instruction::or" in lowered or "opcode == or" in lowered:
        return "or"
    if "instruction::and" in lowered or "opcode == and" in lowered:
        return "and"
    return None


def slp_reduction_text(text: str) -> bool:
    lowered = text.lower()
    return "reduce" in lowered and any(token.lower() in lowered for token in SLP_REDUCTION_TOKENS)


def slp_transaction_kind(opcode: str, text: str = "") -> str:
    if opcode in {"add", "mul", "and", "or", "xor", "smin", "smax", "umin", "umax", "fadd", "fmul"} and slp_reduction_text(text):
        return "slp-vectorize-reduction"
    return "slp-vectorize-minmax" if opcode in {"smin", "smax", "umin", "umax"} else "slp-vectorize-binop"


def slp_reduction_unsupported_reasons(text: str) -> list[str]:
    lowered = text.lower()
    reasons: list[str] = []
    width_info = slp_reduction_width_info(text)
    if any(token in lowered for token in ("createzext", "createsext", "createzextortrunc", "zext", "sext")) and width_info.get("status") != "complete":
        reasons.append(str(width_info.get("unsupported_reason") or "unsupported-reduction-ambiguous-width"))
    if any(token in lowered for token in ("createtrunc", "trunc")) and width_info.get("status") != "complete":
        reasons.append(str(width_info.get("unsupported_reason") or "unsupported-reduction-ambiguous-width"))
    return reasons


def slp_fp_reduction_policy(lines: list[str], opcode: str, lane_mapping: dict[str, Any]) -> dict[str, Any]:
    if opcode not in {"fadd", "fmul"}:
        return {}
    evidence = source_records_for_tokens(
        lines,
        (
            "AllowReassoc",
            "hasAllowReassoc",
            "setAllowReassoc",
            "FastMathFlags",
            "setFastMathFlags",
            "reassoc",
            "fast",
            "setFast",
            "unordered",
            "Unordered",
            "isOrdered",
            "IsOrdered",
        ),
    )
    if not evidence:
        return {}
    evidence_text = "\n".join(str(item.get("source") or "") for item in evidence if isinstance(item, dict)).lower()
    if "reassoc" in evidence_text:
        semantics = "relaxed-reassoc"
    elif "unordered" in evidence_text or "isordered" in evidence_text:
        semantics = "unordered-fp-reduction"
    else:
        semantics = "fast-math-fp-reduction"
    return {
        "kind": "fp-reduction-policy",
        "semantics": semantics,
        "operation": opcode,
        "element_type": "fp32",
        "lane_mapping": dict(lane_mapping),
        "evidence": evidence,
    }


def bit_width_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    symbol_bits: dict[str, int] = {}
    seen_records: set[tuple[int, str, str, int, str]] = set()

    def role_for_name(name: str) -> str:
        lowered = name.lower()
        if any(token in lowered for token in ("input", "narrow", "scalar", "lane", "orig")):
            return "input"
        if any(token in lowered for token in ("wide", "accum", "zext", "sext", "extended")):
            return "accumulator"
        if any(token in lowered for token in ("result", "trunc")):
            return "result"
        return "unknown"

    def add_record(index: int, line: str, kind: str, role: str, bits: int, symbol: str = "") -> None:
        key = (index + 1, kind, role, bits, symbol)
        if key in seen_records:
            return
        seen_records.add(key)
        record: dict[str, Any] = {
            "line": index + 1,
            "source": line.strip(),
            "kind": kind,
            "role": role,
            "bits": bits,
        }
        if symbol:
            record["symbol"] = symbol
        records.append(record)

    patterns = [
        re.compile(r"\b(?:Type::getInt|IntegerType::get\s*\([^,]+,\s*|i)(8|16|32|64)(?:Ty)?\b", re.IGNORECASE),
        re.compile(r"\b(?:input_bits|accumulator_bits|result_bits|bits|bitwidth)\s*=\s*(8|16|32|64)\b", re.IGNORECASE),
        re.compile(r"\bget(?:Scalar|Primitive)SizeInBits\s*\([^)]*\)\s*==\s*(8|16|32|64)\b", re.IGNORECASE),
    ]
    constant_patterns = [
        re.compile(r"\b(?:const\s+)?(?:unsigned|int|size_t|auto)\s+([A-Za-z_]\w*)\s*=\s*(8|16|32|64)\b"),
        re.compile(r"\b([A-Za-z_]\w*)\s*=\s*(8|16|32|64)\b"),
    ]
    type_alias_pattern = re.compile(
        r"\b(?:Type|IntegerType|auto)\s*\*?\s*([A-Za-z_]\w*)\s*=\s*(?:Type::getInt|IntegerType::get\s*\([^,]+,\s*|i)(8|16|32|64)(?:Ty)?\b",
        re.IGNORECASE,
    )
    type_alias_symbol_pattern = re.compile(
        r"\b(?:Type|IntegerType|auto)\s*\*?\s*([A-Za-z_]\w*)\s*=\s*IntegerType::get\s*\([^,]+,\s*([A-Za-z_]\w*)\s*\)",
        re.IGNORECASE,
    )
    for index, line in enumerate(text.splitlines()):
        lowered = line.lower()
        role = ""
        if any(token in lowered for token in ("input", "narrow", "scalar")):
            role = "input"
        if any(token in lowered for token in ("wide", "accum", "zext", "sext", "extended")):
            role = "accumulator"
        if any(token in lowered for token in ("result", "trunc")):
            role = "result"
        for pattern in constant_patterns:
            for match in pattern.finditer(line):
                symbol = match.group(1)
                bits = int(match.group(2))
                symbol_bits[symbol] = bits
                add_record(index, line, "width-constant", role_for_name(symbol), bits, symbol)
        for match in type_alias_pattern.finditer(line):
            symbol = match.group(1)
            bits = int(match.group(2))
            symbol_bits[symbol] = bits
            add_record(index, line, "type-alias-width", role_for_name(symbol), bits, symbol)
        for match in type_alias_symbol_pattern.finditer(line):
            symbol = match.group(1)
            source_symbol = match.group(2)
            if source_symbol in symbol_bits:
                bits = symbol_bits[source_symbol]
                symbol_bits[symbol] = bits
                add_record(index, line, "type-alias-width", role_for_name(symbol), bits, symbol)
        for pattern in patterns:
            for match in pattern.finditer(line):
                width = int(match.group(1))
                add_record(index, line, "width-expression", role or "unknown", width)
        ext_match = re.search(r"Create(?:ZExt|SExt|ZExtOrTrunc)\s*\(.*,\s*([A-Za-z_]\w*)\s*\)", line)
        if ext_match and ext_match.group(1) in symbol_bits:
            add_record(index, line, "extension-target-width", "accumulator", symbol_bits[ext_match.group(1)], ext_match.group(1))
        trunc_match = re.search(r"CreateTrunc\s*\(.*,\s*([A-Za-z_]\w*)\s*\)", line)
        if trunc_match and trunc_match.group(1) in symbol_bits:
            add_record(index, line, "trunc-target-width", "result", symbol_bits[trunc_match.group(1)], trunc_match.group(1))
    return records


def slp_reduction_width_info(text: str) -> dict[str, Any]:
    lowered = text.lower()
    if not any(token in lowered for token in ("createzext", "createsext", "zext", "sext")):
        return {}
    records = bit_width_records(text)
    role_widths = {
        role: sorted({int(record["bits"]) for record in records if record.get("role") == role})
        for role in ("input", "accumulator", "result")
    }
    conflicting_roles = [role for role, widths in role_widths.items() if len(widths) > 1]
    if conflicting_roles:
        return {
            "status": "conflicting",
            "unsupported_reason": "unsupported-reduction-conflicting-width",
            "width_provenance": records,
        }
    numbers = sorted({int(record["bits"]) for record in records})
    input_bits = role_widths["input"][0] if role_widths["input"] else 0
    accumulator_bits = role_widths["accumulator"][0] if role_widths["accumulator"] else 0
    result_bits = role_widths["result"][0] if role_widths["result"] else 0
    if not input_bits or not accumulator_bits:
        if len(numbers) == 2:
            input_bits = input_bits or numbers[0]
            accumulator_bits = accumulator_bits or numbers[1]
        else:
            return {
                "status": "ambiguous",
                "unsupported_reason": "unsupported-reduction-ambiguous-width",
                "width_provenance": records,
            }
    if accumulator_bits < input_bits or (result_bits and result_bits > accumulator_bits):
        return {
            "status": "conflicting",
            "unsupported_reason": "unsupported-reduction-conflicting-width",
            "width_provenance": records,
        }
    if not result_bits:
        result_bits = input_bits if any(token in lowered for token in ("createtrunc", "trunc")) else accumulator_bits
    if not input_bits or not accumulator_bits:
        return {
            "status": "ambiguous",
            "unsupported_reason": "unsupported-reduction-ambiguous-width",
            "width_provenance": records,
        }
    extend_kind = "sext" if any(token in lowered for token in ("createsext", "sext")) else "zext"
    return {
        "status": "complete",
        "input_bits": input_bits,
        "accumulator_bits": accumulator_bits,
        "result_bits": result_bits,
        "extend_kind": extend_kind,
        "width_provenance": records,
    }


def slp_scalable_info(text: str, default_base_lanes: int) -> dict[str, Any]:
    lowered = text.lower()
    if not any(token in lowered for token in ("getscalable", "isscalable", "scalable")):
        return {"scalable": False}
    base_lanes = default_base_lanes
    records: list[dict[str, Any]] = []
    patterns = [
        re.compile(r"ElementCount::getScalable\s*\(\s*(\d+)\s*\)"),
        re.compile(r"base_lanes\s*=\s*(\d+)", re.IGNORECASE),
        re.compile(r"scalable_base_lanes\s*=\s*(\d+)", re.IGNORECASE),
        re.compile(r"VectorType::get\s*\([^,]+,\s*(\d+)\s*,\s*true\s*\)"),
    ]
    for index, line in enumerate(text.splitlines()):
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                base_lanes = int(match.group(1))
                records.append(
                    {
                        "line": index + 1,
                        "source": line.strip(),
                        "kind": "scalable-base-lanes",
                        "base_lanes": base_lanes,
                    }
                )
    return {
        "scalable": True,
        "base_lanes": base_lanes,
        "vscale_values": [1, 2, 4],
        "scalable_provenance": records,
    }


def minmax_predicate_for_opcode(opcode: str) -> str:
    return {
        "smin": "slt",
        "smax": "sgt",
        "umin": "ult",
        "umax": "ugt",
    }.get(opcode, "")


def minmax_sources(lines: list[str], tokens: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if any(token in line for token in tokens):
            result.append({"line": index + 1, "source": line.strip()})
    return result


def source_records_for_tokens(lines: list[str], tokens: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if any(token in line for token in tokens):
            result.append({"line": index + 1, "source": line.strip()})
    return result


def opcode_source(text: str, role: str, line: int, function: str) -> dict[str, Any] | None:
    patterns = [
        (r"Create(Add|Mul|And|Or|Xor|SMin|SMax|UMin|UMax)Reduce\b", {"add": "add", "mul": "mul", "and": "and", "or": "or", "xor": "xor", "smin": "smin", "smax": "smax", "umin": "umin", "umax": "umax"}),
        (r"Create(SMin|SMax|UMin|UMax)\b", {"smin": "smin", "smax": "smax", "umin": "umin", "umax": "umax"}),
        (r"ICMP_(SLT|SGT|ULT|UGT)\b", {"slt": "smin", "sgt": "smax", "ult": "umin", "ugt": "umax"}),
        (r"Create(Add|Sub|Mul|Xor|Or|And)\b", {"add": "add", "sub": "sub", "mul": "mul", "xor": "xor", "or": "or", "and": "and"}),
        (r"Instruction::(Add|Sub|Mul|Xor|Or|And)\b", {"add": "add", "sub": "sub", "mul": "mul", "xor": "xor", "or": "or", "and": "and"}),
        (r"opcode\s*==\s*(add|sub|mul|xor|or|and)\b", {"add": "add", "sub": "sub", "mul": "mul", "xor": "xor", "or": "or", "and": "and"}),
    ]
    for pattern, mapping in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).lower()
            opcode = mapping.get(raw)
            if opcode:
                return {"role": role, "function": function, "line": line, "opcode": opcode, "source": text.strip()}
    return None


def slp_lane_source(lines: list[str]) -> dict[str, Any]:
    for index, line in enumerate(lines):
        match = re.search(r"Scalars\s*\[\s*(\d+)\s*\]", line)
        if match and ("struct" in line or "*" in line or "Value" in line):
            return {"lanes": int(match.group(1)), "line": index + 1, "source": line.strip(), "kind": "scalar-array-declaration"}
    max_index = -1
    source_line = ""
    source_number = 0
    for index, line in enumerate(lines):
        for match in re.finditer(r"Scalars\s*\[\s*(\d+)\s*\]", line):
            lane_index = int(match.group(1))
            if lane_index > max_index:
                max_index = lane_index
                source_line = line.strip()
                source_number = index + 1
    if max_index >= 0:
        return {"lanes": max_index + 1, "line": source_number, "source": source_line, "kind": "max-scalar-index"}
    return {"lanes": 4, "line": 0, "source": "default fixed vector width", "kind": "default"}


def inverse_permutation(mapping: list[int]) -> list[int]:
    inverse = [0] * len(mapping)
    for index, lane in enumerate(mapping):
        inverse[lane] = index
    return inverse


def make_lane_mapping(kind: str, lanes: int, mapping: list[int], line: int, source: str, source_kind: str) -> dict[str, Any]:
    result = {
        "kind": kind,
        "lanes": lanes,
        "map": mapping,
        "source": {"line": line, "source": source, "kind": source_kind},
    }
    if len(mapping) == lanes and sorted(mapping) == list(range(lanes)):
        result["inverse_map"] = inverse_permutation(mapping)
    return result


def explicit_lane_mapping(lines: list[str], lanes: int, names: tuple[str, ...], source_kind: str) -> dict[str, Any] | None:
    name_pattern = "|".join(re.escape(name) for name in names)
    initializer = re.compile(rf"\b(?:{name_pattern})\s*(?:\[\s*\d+\s*\])?\s*=\s*\{{([^}}]*)\}}")
    for index, line in enumerate(lines):
        match = initializer.search(line)
        if not match:
            continue
        values = [int(item) for item in re.findall(r"-?\d+", match.group(1))]
        kind = "permutation" if values != list(range(lanes)) else "identity"
        return make_lane_mapping(kind, lanes, values, index + 1, line.strip(), source_kind)
    return None


def slp_lane_mapping(lines: list[str], lanes: int) -> dict[str, Any]:
    explicit = explicit_lane_mapping(lines, lanes, ("LaneMap", "ReorderMask", "ShuffleMask"), "explicit-lane-map")
    if explicit:
        return explicit
    return make_lane_mapping("identity", lanes, list(range(lanes)), 0, "default identity lane mapping", "default")


def validate_lane_mapping(mapping: dict[str, Any], lanes: int) -> str | None:
    lane_map = mapping.get("map")
    if not isinstance(lane_map, list) or len(lane_map) != lanes:
        return "lane-map-size-mismatch"
    if not all(isinstance(lane, int) and 0 <= lane < lanes for lane in lane_map):
        return "invalid-lane-map"
    if sorted(lane_map) != list(range(lanes)):
        return "unsupported-lane-map-kind"
    return None


def slp_pack_source(lines: list[str], start: int, end: int, operand_index: int) -> dict[str, Any] | None:
    pattern = re.compile(rf"\bpackOperand\s*\([^,]+,\s*{operand_index}\s*\)")
    for index in range(start, end):
        if pattern.search(lines[index]):
            return {"line": index + 1, "source": lines[index].strip(), "kind": "packOperand", "operand_index": operand_index}
    return None


def slp_pack_helper_call(lines: list[str], start: int, end: int, role: str) -> dict[str, Any] | None:
    variable_names = ("LHS", "Left") if role == "lhs" else ("RHS", "Right")
    variable_pattern = "|".join(re.escape(name) for name in variable_names)
    pattern = re.compile(rf"\b(?:{variable_pattern})\b\s*=\s*([A-Za-z_]\w*)\s*\(")
    for index in range(start, end):
        match = pattern.search(lines[index])
        if match:
            return {"line": index + 1, "source": lines[index].strip(), "kind": "pack-helper-call", "function": match.group(1)}
    return None


def lane_mapping_from_helper_body(lines: list[str], function: dict[str, Any], lanes: int) -> dict[str, Any]:
    start = int(function.get("start") or 0)
    end = int(function.get("end") or len(lines))
    body = str(function.get("body") or "")
    explicit_names = (
        "LaneMap",
        "ReorderMask",
        "ShuffleMask",
        "LHSLaneMap",
        "LHSReorderMask",
        "LHSShuffleMask",
        "RHSLaneMap",
        "RHSReorderMask",
        "RHSShuffleMask",
    )
    referenced_names = tuple(name for name in explicit_names if f"{name}[" in body)
    if referenced_names:
        explicit = explicit_lane_mapping(lines, lanes, referenced_names, "helper-explicit-lane-map")
        if explicit:
            explicit["pack_builder"] = {
                "function": str(function.get("name") or ""),
                "line": start + 1,
                "source": str(function.get("signature") or ""),
                "kind": "helper-loop-map" if re.search(r"\bfor\s*\(", body) else "helper-map",
                "status": "complete" if validate_lane_mapping(explicit, lanes) is None else "incomplete",
            }
            return explicit
    scalar_indices = sorted({int(match.group(1)) for match in re.finditer(r"Scalars\s*\[\s*(\d+)\s*\]", body)})
    if scalar_indices == list(range(lanes)):
        result = make_lane_mapping("identity", lanes, list(range(lanes)), start + 1, str(function.get("signature") or ""), "helper-scalar-indexes")
        result["pack_builder"] = {
            "function": str(function.get("name") or ""),
            "line": start + 1,
            "source": str(function.get("signature") or ""),
            "kind": "helper-scalar-indexes",
            "status": "complete",
        }
        return result
    if scalar_indices:
        result = make_lane_mapping("incomplete", lanes, scalar_indices, start + 1, str(function.get("signature") or ""), "helper-partial-scalar-indexes")
        result["pack_builder"] = {
            "function": str(function.get("name") or ""),
            "line": start + 1,
            "source": str(function.get("signature") or ""),
            "kind": "helper-partial-scalar-indexes",
            "status": "incomplete",
        }
        return result
    result = make_lane_mapping("incomplete", lanes, [], start + 1, str(function.get("signature") or ""), "helper-unresolved")
    result["pack_builder"] = {
        "function": str(function.get("name") or ""),
        "line": start + 1,
        "source": str(function.get("signature") or ""),
        "kind": "helper-unresolved",
        "status": "incomplete",
    }
    return result


def slp_pack_builder_summaries(lines: list[str], lanes: int) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for function in source_function_ranges(lines):
        body = str(function.get("body") or "")
        if "Create" in body:
            continue
        if "TreeEntry" not in body or not any(token in body for token in ("packOperand", "Scalars", "LaneMap", "ReorderMask", "ShuffleMask")):
            continue
        summaries[str(function.get("name") or "")] = lane_mapping_from_helper_body(lines, function, lanes)
    return summaries


def slp_result_lane_mapping(lines: list[str], replacement: dict[str, Any], lanes: int, lane_mapping: dict[str, Any]) -> dict[str, Any] | None:
    start = int(replacement.get("start") or 0)
    end = int(replacement.get("end") or len(lines))
    body = str(replacement.get("body") or "") or "\n".join(lines[start:end])
    function_name = str(replacement.get("function") or "")
    explicit_names = ("ResultLaneMap", "ResultReorderMask", "ReplacementLaneMap", "UseLaneMap")
    referenced_names = tuple(name for name in explicit_names if f"{name}[" in body)
    if referenced_names:
        explicit = explicit_lane_mapping(lines, lanes, referenced_names, "result-explicit-lane-map")
        if explicit:
            explicit["replacement_source"] = {
                "function": function_name,
                "line": start + 1,
                "source": str(replacement.get("signature") or ""),
                "kind": "result-helper-map" if function_name != "vectorizeTree" else "direct-result-map",
                "status": "complete" if validate_lane_mapping(explicit, lanes) is None else "incomplete",
            }
            return explicit
    replaced_indices = sorted(
        {
            int(match.group(1))
            for line in lines[start:end]
            if "replaceAllUsesWith" in line
            for match in re.finditer(r"Scalars\s*\[\s*(\d+)\s*\]", line)
        }
    )
    if replaced_indices == list(range(lanes)):
        result = make_lane_mapping("identity", lanes, list(range(lanes)), start + 1, str(replacement.get("signature") or ""), "direct-result-scalar-indexes")
        result["replacement_source"] = {
            "function": function_name,
            "line": start + 1,
            "source": str(replacement.get("signature") or ""),
            "kind": "direct-result-scalar-indexes",
            "status": "complete",
        }
        return result
    if ("replaceScalarUses" in body or "replaceExternalUses" in body or function_name in {"replaceScalarUses", "replaceExternalUses"}) and function_name not in {"replacePartialUses", "partialReplace"}:
        result = dict(lane_mapping)
        result["source"] = {"line": start + 1, "source": str(replacement.get("signature") or ""), "kind": "default-result-lane-mapping"}
        result["replacement_source"] = {
            "function": function_name,
            "line": start + 1,
            "source": str(replacement.get("signature") or ""),
            "kind": "coarse-replacement-helper",
            "status": "complete",
        }
        return result
    if replaced_indices:
        result = make_lane_mapping("incomplete", lanes, replaced_indices, start + 1, str(replacement.get("signature") or ""), "partial-result-scalar-indexes")
        result["replacement_source"] = {
            "function": function_name,
            "line": start + 1,
            "source": str(replacement.get("signature") or ""),
            "kind": "partial-result-scalar-indexes",
            "status": "incomplete",
        }
        return result
    return None


def slp_scalar_lane_pairs(lhs_mapping: dict[str, Any], rhs_mapping: dict[str, Any], result_mapping: dict[str, Any], lanes: int) -> list[dict[str, int]]:
    lhs_map = lhs_mapping.get("map")
    rhs_map = rhs_mapping.get("map")
    result_map = result_mapping.get("map")
    if not isinstance(lhs_map, list) or not isinstance(rhs_map, list) or not isinstance(result_map, list):
        return []
    if len(lhs_map) != lanes or len(rhs_map) != lanes or len(result_map) != lanes:
        return []
    if not all(isinstance(item, int) for item in lhs_map + rhs_map + result_map):
        return []
    return [
        {"vector_lane": index, "result_lane": int(result_map[index]), "lhs_lane": int(lhs_map[index]), "rhs_lane": int(rhs_map[index])}
        for index in range(lanes)
    ]


def slp_operand_lane_mappings(lines: list[str], emitter: dict[str, Any], lanes: int, transaction_lane_mapping: dict[str, Any]) -> dict[str, Any]:
    start = int(emitter.get("start") or 0)
    end = int(emitter.get("end") or len(lines))
    pack_builders = slp_pack_builder_summaries(lines, lanes)
    operand_names = {
        "lhs": ("LHSLaneMap", "LHSReorderMask", "LHSShuffleMask", "LeftLaneMap", "LeftReorderMask", "LeftShuffleMask"),
        "rhs": ("RHSLaneMap", "RHSReorderMask", "RHSShuffleMask", "RightLaneMap", "RightReorderMask", "RightShuffleMask"),
    }
    operands: dict[str, Any] = {}
    for role, operand_index in (("lhs", 0), ("rhs", 1)):
        pack_source = slp_pack_source(lines, start, end, operand_index)
        if pack_source is not None:
            mapping = explicit_lane_mapping(lines, lanes, operand_names[role], f"explicit-{role}-lane-map")
            if mapping is None:
                mapping = dict(transaction_lane_mapping)
            mapping["pack_source"] = pack_source
            operands[role] = mapping
            continue
        helper_call = slp_pack_helper_call(lines, start, end, role)
        if helper_call is None:
            continue
        helper_name = str(helper_call.get("function") or "")
        helper_mapping = pack_builders.get(helper_name)
        if helper_mapping is None:
            helper_mapping = make_lane_mapping("incomplete", lanes, [], int(helper_call["line"]), str(helper_call.get("source") or ""), "helper-unresolved")
            helper_mapping["pack_builder"] = {"function": helper_name, "line": int(helper_call["line"]), "source": str(helper_call.get("source") or ""), "kind": "helper-unresolved", "status": "incomplete"}
        mapping = dict(helper_mapping)
        mapping["pack_source"] = helper_call
        operands[role] = mapping
    return operands


def slp_transaction_guard_line(lines: list[str], start: int, end: int) -> int | None:
    guard_tokens = ("allSameOpcode", "sameOpcode", "isValidElementType", "isProfitable", "getEntryCost")
    for index in range(start, end):
        line = lines[index]
        if "if" in line and any(token in line for token in guard_tokens):
            return index
    return None


def function_name_from_signature(line: str) -> str:
    stripped = line.strip()
    match = re.search(r"([A-Za-z_~][\w:~]*)\s*\([^;]*\)\s*(?:const\s*)?\{", stripped)
    if not match:
        return ""
    name = match.group(1)
    if name in {"if", "for", "while", "switch", "catch"}:
        return ""
    return name.split("::")[-1]


def source_function_ranges(lines: list[str]) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        name = function_name_from_signature(line)
        if not name:
            index += 1
            continue
        function_start = index
        depth = line.count("{") - line.count("}")
        end = index + 1
        while end < len(lines) and depth > 0:
            depth += lines[end].count("{") - lines[end].count("}")
            end += 1
        functions.append(
            {
                "name": name,
                "start": function_start,
                "end": end,
                "signature": line.strip(),
                "body": "\n".join(lines[function_start:end]),
            }
        )
        index = max(end, index + 1)
    return functions


def first_line_with_token(lines: list[str], start: int, end: int, tokens: tuple[str, ...]) -> int:
    for index in range(start, end):
        if any(token in lines[index] for token in tokens):
            return index
    return start


def role_provenance(function: dict[str, Any], role: str, line_index: int, lines: list[str]) -> dict[str, Any]:
    return {
        "role": role,
        "function": str(function["name"]),
        "line": line_index + 1,
        "source": lines[line_index].strip(),
    }


def slp_function_summaries(lines: list[str]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for function in source_function_ranges(lines):
        body = str(function["body"])
        roles: list[str] = []
        evidence: list[dict[str, Any]] = []
        start = int(function["start"])
        end = int(function["end"])
        if "TreeEntry" in body and any(token in body for token in ("Scalars", "VectorizableTree", "packOperand", "ExternalUses", "buildTree")):
            roles.append("candidate-tree")
            evidence.append(role_provenance(function, "candidate-tree", first_line_with_token(lines, start, end, ("Scalars", "VectorizableTree", "TreeEntry", "buildTree")), lines))
        if any(token in body for token in ("allSameOpcode", "sameOpcode", "isValidElementType", "canVectorize")):
            roles.append("legality")
            legality_line = first_line_with_token(lines, start, end, ("allSameOpcode", "sameOpcode", "isValidElementType", "canVectorize"))
            evidence_item = role_provenance(function, "legality", legality_line, lines)
            source_opcode = opcode_source(lines[legality_line], "legality", legality_line + 1, str(function["name"]))
            if source_opcode:
                evidence_item["opcode"] = source_opcode["opcode"]
            evidence.append(evidence_item)
        if any(token in body for token in ("getEntryCost", "TTI", "isProfitable")):
            roles.append("profitability")
            evidence.append(role_provenance(function, "profitability", first_line_with_token(lines, start, end, ("getEntryCost", "TTI", "isProfitable")), lines))
        opcode = slp_transaction_opcode(body)
        if opcode is not None and any(token in body for token in SLP_EMISSION_TOKENS):
            roles.append("vector-emission")
            emit_line = first_line_with_token(lines, start, end, SLP_EMISSION_TOKENS)
            evidence_item = role_provenance(function, "vector-emission", emit_line, lines)
            evidence_item["opcode"] = opcode
            evidence.append(evidence_item)
        if any(token in body for token in ("replaceScalarUses", "replaceExternalUses", "replaceAllUsesWith", "ExternalUses")):
            roles.append("scalar-replacement")
            evidence.append(role_provenance(function, "scalar-replacement", first_line_with_token(lines, start, end, ("replaceScalarUses", "replaceExternalUses", "replaceAllUsesWith", "ExternalUses")), lines))
        if roles:
            summaries.append(
                {
                    "function": function["name"],
                    "start": start,
                    "end": end,
                    "signature": function["signature"],
                    "body": body,
                    "roles": roles,
                    "opcode": opcode,
                    "evidence": evidence,
                }
            )
    return summaries


def first_summary_with_role(summaries: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    for summary in summaries:
        if role in summary.get("roles", []):
            return summary
    return None


def transaction_role_evidence(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_role: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        for item in summary.get("evidence", []):
            if isinstance(item, dict) and item.get("role") not in by_role:
                by_role[str(item.get("role"))] = item
    return [by_role[role] for role in ("candidate-tree", "legality", "profitability", "vector-emission", "scalar-replacement") if role in by_role]


def mine_slp_transactions(path: Path, lines: list[str], radius: int) -> list[dict[str, Any]]:
    summaries = slp_function_summaries(lines)
    candidate = first_summary_with_role(summaries, "candidate-tree")
    legality = first_summary_with_role(summaries, "legality")
    emitter = first_summary_with_role(summaries, "vector-emission")
    replacement = first_summary_with_role(summaries, "scalar-replacement")
    if not (candidate and legality and emitter and replacement):
        return []
    opcode = str(emitter.get("opcode") or "")
    if not opcode:
        return []
    transaction_kind = slp_transaction_kind(opcode, str(emitter.get("body") or ""))
    role_evidence = transaction_role_evidence(summaries)
    guard_index = next((int(item["line"]) - 1 for item in role_evidence if item.get("role") == "legality"), int(legality["start"]))
    marker = "probe.slp.vectorize-reduction" if transaction_kind == "slp-vectorize-reduction" else "probe.slp.vectorize-binop"
    functions = sorted({str(summary.get("function") or "") for summary in summaries if summary.get("function")})
    lane_source = slp_lane_source(lines)
    lanes = int(lane_source.get("lanes") or 0)
    scalable_info = slp_scalable_info("\n".join(lines), lanes or 4)
    if scalable_info.get("scalable") is True:
        lanes = int(scalable_info.get("base_lanes") or lanes or 4)
        lane_source = {
            **lane_source,
            "lanes": lanes,
            "scalable": True,
            "base_lanes": lanes,
            "vscale_values": list(scalable_info.get("vscale_values") or [1, 2, 4]),
        }
    lane_mapping = slp_lane_mapping(lines, lanes)
    operand_lane_mappings = slp_operand_lane_mappings(lines, emitter, lanes, lane_mapping)
    lhs_mapping = operand_lane_mappings.get("lhs")
    rhs_mapping = operand_lane_mappings.get("rhs")
    if transaction_kind == "slp-vectorize-reduction" and isinstance(lhs_mapping, dict):
        lane_mapping = {key: value for key, value in lhs_mapping.items() if key != "pack_source"}
    elif isinstance(lhs_mapping, dict) and isinstance(rhs_mapping, dict) and lhs_mapping.get("map") == rhs_mapping.get("map"):
        lane_mapping = {key: value for key, value in lhs_mapping.items() if key != "pack_source"}
    result_lane_mapping = slp_result_lane_mapping(lines, replacement, lanes, lane_mapping)
    scalar_lane_pairs = (
        slp_scalar_lane_pairs(lhs_mapping, rhs_mapping, result_lane_mapping, lanes)
        if isinstance(lhs_mapping, dict) and isinstance(rhs_mapping, dict) and isinstance(result_lane_mapping, dict)
        else []
    )
    opcode_sources = [
        {"role": str(item.get("role")), "function": str(item.get("function")), "line": int(item.get("line") or 0), "opcode": str(item.get("opcode")), "source": str(item.get("source") or "")}
        for item in role_evidence
        if item.get("opcode")
    ]
    reduction_opcode = opcode
    reduction_lanes = lanes
    reduction_sources: list[dict[str, Any]] = []
    reduction_result: dict[str, Any] = {}
    if transaction_kind == "slp-vectorize-reduction":
        reduction_sources = source_records_for_tokens(
            lines,
            SLP_REDUCTION_TOKENS,
        )
        reduction_result = {
            "kind": "scalar-reduction-result",
            "source": str(emitter.get("signature") or ""),
        }
    consistency_errors: list[str] = []
    for source in opcode_sources:
        if source["role"] != "vector-emission" and source["opcode"] != opcode:
            consistency_errors.append(f"opcode-mismatch:{source['role']}:{source['opcode']}!=vector-emission:{opcode}")
    supported_lanes = {2, 4, 8, 16, 32, 64}
    if scalable_info.get("scalable") is True:
        if transaction_kind not in {"slp-vectorize-reduction", "slp-vectorize-binop", "slp-vectorize-minmax"}:
            consistency_errors.append("unsupported-scalable-transaction")
        if lanes <= 0:
            consistency_errors.append("unsupported-scalable-base-lanes")
    elif lanes not in supported_lanes:
        consistency_errors.append(f"unsupported-lane-count:{lanes}")
    lane_mapping_error = validate_lane_mapping(lane_mapping, lanes)
    if lane_mapping_error:
        consistency_errors.append(lane_mapping_error)
    fp_policy = slp_fp_reduction_policy(lines, opcode, lane_mapping)
    if transaction_kind == "slp-vectorize-reduction":
        consistency_errors.extend(slp_reduction_unsupported_reasons(str(emitter.get("body") or "")))
        if opcode in {"fadd", "fmul"} and lane_mapping.get("map") != list(range(lanes)) and not fp_policy:
            consistency_errors.append("unsupported-reduction-fp-permutation")
        emitter_body_lower = str(emitter.get("body") or "").lower()
        scalable_width_info = slp_reduction_width_info(str(emitter.get("body") or ""))
        if (
            scalable_info.get("scalable") is True
            and any(token in emitter_body_lower for token in ("createzext", "createsext", "createzextortrunc", "createtrunc", "zext", "sext", "trunc"))
            and scalable_width_info.get("status") != "complete"
        ):
            consistency_errors.append("unsupported-scalable-widening-reduction")
        if reduction_opcode != opcode:
            consistency_errors.append(f"reduction-opcode-mismatch:{reduction_opcode}!={opcode}")
        if reduction_lanes != lanes:
            consistency_errors.append(f"reduction-lane-count-mismatch:{reduction_lanes}!={lanes}")
        if not reduction_sources:
            consistency_errors.append("missing-reduction-source")
        if not reduction_result:
            consistency_errors.append("missing-reduction-result")
        if not isinstance(lhs_mapping, dict):
            consistency_errors.append("missing-operand-lane-mapping")
        else:
            pack_builder = lhs_mapping.get("pack_builder")
            if isinstance(pack_builder, dict) and pack_builder.get("status") != "complete":
                consistency_errors.append("incomplete-pack-builder")
            mapping_error = validate_lane_mapping(lhs_mapping, lanes)
            if mapping_error:
                consistency_errors.append(f"lhs-{mapping_error}")
    elif not isinstance(lhs_mapping, dict) or not isinstance(rhs_mapping, dict):
        consistency_errors.append("missing-operand-lane-mapping")
    else:
        for role, mapping in (("lhs", lhs_mapping), ("rhs", rhs_mapping)):
            pack_builder = mapping.get("pack_builder")
            if isinstance(pack_builder, dict) and pack_builder.get("status") != "complete":
                consistency_errors.append("incomplete-pack-builder")
            mapping_error = validate_lane_mapping(mapping, lanes)
            if mapping_error:
                consistency_errors.append(f"{role}-{mapping_error}")
        if validate_lane_mapping(lhs_mapping, lanes) is None and validate_lane_mapping(rhs_mapping, lanes) is None and lhs_mapping.get("map") != rhs_mapping.get("map"):
            consistency_errors.append("operand-lane-map-mismatch")
    if transaction_kind == "slp-vectorize-reduction":
        result_lane_mapping = {}
    elif not isinstance(result_lane_mapping, dict):
        consistency_errors.append("missing-result-lane-mapping")
    elif isinstance(result_lane_mapping, dict):
        replacement_source = result_lane_mapping.get("replacement_source")
        if isinstance(replacement_source, dict) and replacement_source.get("status") != "complete":
            consistency_errors.append("incomplete-result-lane-mapping")
        result_mapping_error = validate_lane_mapping(result_lane_mapping, lanes)
        if result_mapping_error:
            consistency_errors.append(f"result-{result_mapping_error}")
        if (
            isinstance(lhs_mapping, dict)
            and isinstance(rhs_mapping, dict)
            and validate_lane_mapping(lhs_mapping, lanes) is None
            and validate_lane_mapping(rhs_mapping, lanes) is None
            and result_mapping_error is None
            and lhs_mapping.get("map") == rhs_mapping.get("map")
            and result_lane_mapping.get("map") != lhs_mapping.get("map")
        ):
            consistency_errors.append("unsupported-lane-pairing")
    consistency = "ok" if not consistency_errors else "failed"
    constraints = {
        "transaction.kind": transaction_kind,
        "transaction.opcode": opcode,
        "transaction.lanes": lanes,
    }
    if first_summary_with_role(summaries, "profitability"):
        constraints["transaction.profitability_guard"] = True
    if legality:
        constraints["transaction.legality_guard"] = "valid-element-type"
    transaction = {
        "model": "optimization-transaction-v1",
        "kind": transaction_kind,
        "opcode": opcode,
        "lanes": lanes,
        "root": str(emitter.get("signature") or ""),
        "functions": functions,
        "role_provenance": role_evidence,
        "opcode_sources": opcode_sources,
        "lane_source": lane_source,
        "lane_mapping": lane_mapping,
        "operand_lane_mappings": operand_lane_mappings,
        "result_lane_mapping": result_lane_mapping or {},
        "scalar_lane_pairs": scalar_lane_pairs,
        "consistency": consistency,
        "consistency_errors": consistency_errors,
        "legality": {
            "same_opcode": any("sameOpcode" in str(item.get("source")) or "allSameOpcode" in str(item.get("source")) for item in role_evidence),
            "valid_element_type": any("isValidElementType" in str(item.get("source")) for item in role_evidence),
        },
        "profitability": {
            "cost_model": first_summary_with_role(summaries, "profitability") is not None,
        },
        "actions": [
            {"kind": "pack-scalars", "source": "TreeEntry.Scalars"},
            {
                "kind": "emit-vector-reduction"
                if transaction_kind == "slp-vectorize-reduction"
                else ("emit-vector-minmax" if transaction_kind == "slp-vectorize-minmax" else "emit-vector-binop"),
                "opcode": opcode,
            },
            {"kind": "replace-scalar-uses"},
        ],
        "preserves": "scalar reduction result" if transaction_kind == "slp-vectorize-reduction" else "lane-wise scalar result",
    }
    if transaction_kind == "slp-vectorize-minmax":
        transaction["predicate"] = minmax_predicate_for_opcode(opcode)
        transaction["select_order"] = "canonical"
        transaction["compare_sources"] = minmax_sources(lines, ("ICMP_", "CreateICmp", "CmpInst::"))
        transaction["select_sources"] = minmax_sources(lines, ("CreateSelect", "SelectInst", "select"))
    if scalable_info.get("scalable") is True:
        transaction["scalable"] = True
        transaction["base_lanes"] = lanes
        transaction["vscale_values"] = list(scalable_info.get("vscale_values") or [1, 2, 4])
        transaction["scalable_provenance"] = list(scalable_info.get("scalable_provenance") or [])
    if transaction_kind == "slp-vectorize-reduction":
        width_info = slp_reduction_width_info(str(emitter.get("body") or ""))
        transaction["reduction_opcode"] = reduction_opcode
        transaction["reduction_lanes"] = reduction_lanes
        transaction["reduction_sources"] = reduction_sources
        transaction["reduction_result"] = reduction_result
        if width_info:
            transaction["reduction_width_status"] = width_info.get("status", "")
            transaction["reduction_width_provenance"] = width_info.get("width_provenance", [])
        if width_info.get("status") == "complete":
            transaction["reduction_input_bits"] = width_info["input_bits"]
            transaction["reduction_accumulator_bits"] = width_info["accumulator_bits"]
            transaction["reduction_result_bits"] = width_info["result_bits"]
            transaction["reduction_extend_kind"] = width_info["extend_kind"]
        if fp_policy:
            transaction["fp_policy"] = fp_policy
        transaction["unsupported_reduction_reasons"] = [
            error for error in consistency_errors if error.startswith("unsupported-reduction-") or error.startswith("unsupported-scalable-")
        ]
    return [
        {
            "file": str(path),
            "line": guard_index + 1,
            "marker": marker,
            "pass": "slp-vectorizer",
            "predicate_kind": "transaction",
            "matched_pattern": transaction_kind + "-transaction",
            "source": lines[guard_index].strip(),
            "predicate_source": lines[guard_index].strip(),
            "rewrite_source": (
                f"emit vector {opcode} reduction and replace scalar result"
                if transaction_kind == "slp-vectorize-reduction"
                else f"emit vector {opcode} and replace scalar uses"
            ),
            "constraints": constraints,
            "suggestion": f'Wrap transaction root with CV_PASS_PROBE_IF("{marker}", <legality>)',
            "context": context_lines(lines, guard_index, radius),
            "optimization_transaction": transaction,
        }
    ]


def mine_file(
    path: Path,
    registry_path: Path,
    registry: list[dict[str, Any]],
    radius: int,
    semantic_registry: Path,
) -> list[dict[str, Any]]:
    text = path.read_text(errors="replace")
    lines = text.splitlines(keepends=True)
    findings: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    registry_by_marker = {
        str(entry["marker"]): entry
        for entry in registry
        if isinstance(entry, dict) and isinstance(entry.get("marker"), str)
    }
    source_rules = [
        rule
        for rule in source_pattern_entries(
            registry_path,
            DEFAULT_REGISTRY.parent / "llvm_idioms.json",
        )
        if rule["marker"] in registry_by_marker
    ]

    for index, line in enumerate(lines):
        line_number = index + 1
        passes_on_line: set[str] = set()
        for rule in source_rules:
            if not source_rule_matches(line, rule):
                continue
            entry = registry_by_marker[rule["marker"]]
            key = (line_number, str(entry["marker"]))
            if key in seen:
                continue
            # One marker per PASS per line: overlapping patterns within a pass are ambiguous
            # readings of the SAME expression (`x - 0` is sub-zero, not also add-zero), so the first
            # (highest-priority) rule for that pass wins -- the deliberate disambiguation. But a line
            # can legitimately establish markers for DIFFERENT passes (e.g.
            # `if (isGlobalInitializerDead(GV) && GV->use_empty())` is both the globalopt
            # dead-initializer legality site and a cleanup use_empty check), so those co-occur rather
            # than the first shadowing the rest -- otherwise a fold-site marker surfaces only at a
            # forward declaration, where intent inference (needing a predicate site) drops it.
            pass_name = str(entry.get("pass", ""))
            if pass_name in passes_on_line:
                continue
            seen.add(key)
            passes_on_line.add(pass_name)
            findings.append(
                make_finding(
                    path,
                    line_number,
                    line,
                    entry,
                    rule["pattern"],
                    context_lines(lines, index, radius),
                    semantic_registry,
                )
            )

    findings.extend(mine_slp_transactions(path, [line.rstrip("\n") for line in lines], radius))
    return findings


def validate_required(findings: list[dict[str, Any]], required: list[str]) -> bool:
    found = {str(finding["marker"]) for finding in findings}
    missing = [marker for marker in required if marker not in found]
    if missing:
        print("missing required markers: " + ", ".join(missing), file=sys.stderr)
        return False
    return True


def main() -> int:
    args = parse_args()
    try:
        registry = load_registry(args.registry)
        files = source_files(args.paths)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    findings: list[dict[str, Any]] = []
    for path in files:
        findings.extend(
            mine_file(path, args.registry, registry, args.context, args.semantic_registry)
        )

    findings.sort(key=lambda item: (item["file"], item["line"], item["marker"]))

    if args.format == "jsonl":
        for finding in findings:
            print(json.dumps(finding, sort_keys=True))
    else:
        print(json.dumps(findings, indent=2, sort_keys=True))

    return 0 if validate_required(findings, args.require_marker) else 1


if __name__ == "__main__":
    raise SystemExit(main())
