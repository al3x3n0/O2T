#!/usr/bin/env python3
"""Emit targeted LLVM IR cases from mined SLP optimization transactions."""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_LANES = {2, 4, 8, 16, 32, 64}
SUPPORTED_BASE_LANES = {2, 4, 8}
BINOP_OPS = {"add", "sub", "mul", "xor", "or", "and"}
GRAPH_NODE_KINDS = {"binop", "icmp", "select", "shuffle", "cast", "extract", "insert"}
GRAPH_ICMP_PREDICATES = {"eq", "ne", "ugt", "uge", "ult", "ule", "sgt", "sge", "slt", "sle"}
GRAPH_CAST_OPS = {"zext", "sext", "trunc"}
GRAPH_OPERAND_KINDS = {"node", "pack", "memory-pack", "const"}
MINMAX_PREDICATES = {
    "smin": "slt",
    "smax": "sgt",
    "umin": "ult",
    "umax": "ugt",
}
REDUCE_INTRINSICS = {
    "add": "add",
    "mul": "mul",
    "and": "and",
    "or": "or",
    "xor": "xor",
    "smin": "smin",
    "smax": "smax",
    "umin": "umin",
    "umax": "umax",
}


@dataclass(frozen=True)
class GraphValue:
    ty: str
    value: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--unsupported-jsonl", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--validate-ir", action="store_true")
    parser.add_argument("--llvm-as", type=Path)
    parser.add_argument("--verify-formalization", action="store_true")
    parser.add_argument(
        "--formalization-verifier",
        type=Path,
        default=ROOT / "tools" / "cv-verify-transaction-formalization.py",
    )
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
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


def marker_filename(marker: str) -> str:
    return marker.replace("probe.", "").replace(".", "_").replace("-", "_")


def safe_part(value: Any) -> str:
    text = str(value or "unknown").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def record_transaction(record: dict[str, Any]) -> dict[str, Any] | None:
    tx = record.get("optimization_transaction")
    if isinstance(tx, dict):
        return tx
    evidence = record.get("evidence")
    if isinstance(evidence, dict) and isinstance(evidence.get("optimization_transaction"), dict):
        return evidence["optimization_transaction"]
    return None


def transaction_lanes(tx: dict[str, Any]) -> int:
    if tx.get("scalable") is True:
        return int(tx.get("base_lanes") or tx.get("lanes") or 4)
    return int(tx.get("lanes") or tx.get("reduction_lanes") or 4)


def lane_map(tx: dict[str, Any], lanes: int) -> list[int]:
    mapping = tx.get("lane_mapping")
    values = mapping.get("map") if isinstance(mapping, dict) else None
    if isinstance(values, list) and len(values) == lanes and all(isinstance(v, int) for v in values):
        return [int(v) for v in values]
    return list(range(lanes))


def result_lane_map(tx: dict[str, Any], lanes: int) -> list[int]:
    mapping = tx.get("result_lane_mapping")
    values = mapping.get("map") if isinstance(mapping, dict) else None
    if isinstance(values, list) and len(values) == lanes and all(isinstance(v, int) for v in values):
        return [int(v) for v in values]
    return list(range(lanes))


def validate_transaction(tx: dict[str, Any]) -> str:
    kind = str(tx.get("kind") or "")
    if kind not in {"slp-vectorize-binop", "slp-vectorize-minmax", "slp-vectorize-reduction"}:
        return "unsupported-transaction-kind"
    if tx.get("consistency") != "ok":
        errors = tx.get("consistency_errors")
        if not errors and isinstance(tx.get("unsupported_reduction_reasons"), list):
            errors = tx.get("unsupported_reduction_reasons")
        return "failed-consistency" + (":" + ",".join(map(str, errors)) if errors else "")
    lanes = transaction_lanes(tx)
    if tx.get("scalable") is True:
        if lanes not in SUPPORTED_BASE_LANES:
            return f"unsupported-scalable-base-lanes:{lanes}"
    elif lanes not in SUPPORTED_LANES:
        return f"unsupported-lane-count:{lanes}"
    opcode = str(tx.get("reduction_opcode") or tx.get("opcode") or "")
    if kind in {"slp-vectorize-binop", "slp-vectorize-minmax"} and opcode not in BINOP_OPS | set(MINMAX_PREDICATES):
        return f"unsupported-opcode:{opcode}"
    if kind == "slp-vectorize-reduction" and opcode not in REDUCE_INTRINSICS:
        return f"unsupported-reduction-opcode:{opcode}"
    return ""


def scalar_args(prefix: str, lanes: int) -> str:
    return ", ".join(f"i32 %{prefix}{lane}" for lane in range(lanes))


def graph_operands(tx: dict[str, Any]) -> list[str]:
    graph = tx.get("transaction_graph")
    operands = graph.get("operands") if isinstance(graph, dict) else []
    names = [
        safe_part(operand.get("name"))
        for operand in operands
        if isinstance(operand, dict) and str(operand.get("name") or "") and operand.get("kind") != "memory-pack"
    ]
    if isinstance(graph, dict):
        return names
    return names or ["a", "b"]


def graph_function_args(tx: dict[str, Any], lanes: int) -> str:
    graph = tx.get("transaction_graph")
    operands = graph.get("operands") if isinstance(graph, dict) else []
    store_sinks = graph.get("store_sinks") if isinstance(graph, dict) else []
    args = [scalar_args(name, lanes) for name in graph_operands(tx)]
    bases: list[str] = []
    for operand in operands:
        if not isinstance(operand, dict) or operand.get("kind") != "memory-pack":
            continue
        base = safe_part(operand.get("base") or operand.get("name"))
        if base and base not in bases:
            bases.append(base)
    if isinstance(store_sinks, list):
        for sink in store_sinks:
            if not isinstance(sink, dict):
                continue
            base = safe_part(sink.get("base"))
            if base and base not in bases:
                bases.append(base)
    args.extend(f"ptr %{base}" for base in bases)
    return ", ".join(arg for arg in args if arg)


def function_args(lanes: int, binary: bool) -> str:
    lhs = scalar_args("a", lanes)
    if not binary:
        return lhs
    rhs = scalar_args("b", lanes)
    return lhs + ", " + rhs


def vector_type(lanes: int, scalable: bool) -> str:
    return vector_type_bits(lanes, scalable, 32)


def vector_type_bits(lanes: int, scalable: bool, bits: int) -> str:
    return f"<vscale x {lanes} x i{bits}>" if scalable else f"<{lanes} x i{bits}>"


def scalar_type_from_vector(ty: str) -> str:
    match = re.search(r"x i(\d+)>$", ty)
    if match:
        return f"i{match.group(1)}"
    raise ValueError(f"expected vector type: {ty}")


def vector_element_bits(ty: str) -> int:
    match = re.search(r"x i(\d+)>$", ty)
    if match:
        return int(match.group(1))
    raise ValueError(f"expected vector type: {ty}")


def is_vector_type(ty: str) -> bool:
    return ty.startswith("<") and ty.endswith(">")


def zero_vector(lanes: int, scalable: bool) -> str:
    return "zeroinitializer"


def insert_pack(lines: list[str], name: str, prefix: str, mapping: list[int], lanes: int, scalable: bool) -> None:
    vty = vector_type(lanes, scalable)
    current = "poison"
    for vector_lane, scalar_lane in enumerate(mapping):
        out = f"%{name}.{vector_lane}"
        lines.append(f"  {out} = insertelement {vty} {current}, i32 %{prefix}{scalar_lane}, i32 {vector_lane}")
        current = out
    lines.append(f"  %{name} = add {vty} {current}, {zero_vector(lanes, scalable)}")


def emit_memory_pack(lines: list[str], name: str, operand: dict[str, Any], lanes: int, scalable: bool) -> None:
    vty = vector_type(lanes, scalable)
    base = safe_part(operand.get("base") or operand.get("name"))
    terms = {
        int(term["lane"]): int(term["index"])
        for term in operand.get("address_terms", [])
        if isinstance(term, dict) and isinstance(term.get("lane"), int) and isinstance(term.get("index"), int)
    }
    loaded_by_lane: dict[int, str] = {}
    for lane in range(lanes):
        index = terms[lane]
        gep = f"{name}.gep.{lane}"
        load = f"{name}.load.{lane}"
        lines.append(f"  %{gep} = getelementptr i32, ptr %{base}, i32 {index}")
        lines.append(f"  %{load} = load i32, ptr %{gep}")
        loaded_by_lane[lane] = load
    current = "poison"
    for vector_lane, scalar_lane in enumerate(graph_lane_mapping(operand, lanes)):
        out = f"{name}.{vector_lane}"
        lines.append(f"  %{out} = insertelement {vty} {current}, i32 %{loaded_by_lane[scalar_lane]}, i32 {vector_lane}")
        current = out
    lines.append(f"  %{name} = add {vty} {current}, {zero_vector(lanes, scalable)}")


def emit_store_sink(lines: list[str], name: str, sink: dict[str, Any], vector_value: str, lanes: int, scalable: bool) -> None:
    vty = vector_type(lanes, scalable)
    base = safe_part(sink.get("base"))
    terms = {
        int(term["lane"]): int(term["index"])
        for term in sink.get("store_address_terms", [])
        if isinstance(term, dict) and isinstance(term.get("lane"), int) and isinstance(term.get("index"), int)
    }
    for lane in range(lanes):
        extract = f"{name}.extract.{lane}"
        gep = f"{name}.gep.{lane}"
        lines.append(f"  %{extract} = extractelement {vty} %{vector_value}, i32 {lane}")
        lines.append(f"  %{gep} = getelementptr i32, ptr %{base}, i32 {terms[lane]}")
        lines.append(f"  store i32 %{extract}, ptr %{gep}")


def shuffle_if_needed(lines: list[str], source: str, result: str, mapping: list[int], lanes: int, scalable: bool) -> str:
    if mapping == list(range(lanes)):
        return source
    vty = vector_type(lanes, scalable)
    mask_ty = vector_type(lanes, scalable)
    mask = ", ".join(f"i32 {lane}" for lane in mapping)
    lines.append(f"  %{result} = shufflevector {vty} %{source}, {vty} poison, {mask_ty} <{mask}>")
    return result


def emit_binop(lines: list[str], tx: dict[str, Any], lanes: int, scalable: bool) -> str:
    opcode = str(tx.get("opcode") or "add")
    insert_pack(lines, "lhs", "a", lane_map(tx, lanes), lanes, scalable)
    insert_pack(lines, "rhs", "b", lane_map(tx, lanes), lanes, scalable)
    vty = vector_type(lanes, scalable)
    if opcode in MINMAX_PREDICATES:
        pred = MINMAX_PREDICATES[opcode]
        lines.append(f"  %cmp = icmp {pred} {vty} %lhs, %rhs")
        lines.append(f"  %op.vec = select {vector_type(lanes, scalable).replace('i32', 'i1')} %cmp, {vty} %lhs, {vty} %rhs")
    else:
        lines.append(f"  %op.vec = {opcode} {vty} %lhs, %rhs")
    return shuffle_if_needed(lines, "op.vec", "result.vec", result_lane_map(tx, lanes), lanes, scalable)


def emit_reduction(lines: list[str], tx: dict[str, Any], lanes: int, scalable: bool) -> str:
    opcode = str(tx.get("reduction_opcode") or tx.get("opcode") or "add")
    insert_pack(lines, "red.input", "a", lane_map(tx, lanes), lanes, scalable)
    if scalable:
        suffix = f"nxv{lanes}i32"
    else:
        suffix = f"v{lanes}i32"
    intrinsic = f"llvm.vector.reduce.{REDUCE_INTRINSICS[opcode]}.{suffix}"
    lines.append(f"  %result = call i32 @{intrinsic}({vector_type(lanes, scalable)} %red.input)")
    lines.append("  ret i32 %result")
    return intrinsic


def declarations(tx: dict[str, Any], lanes: int, scalable: bool) -> list[str]:
    if tx.get("kind") != "slp-vectorize-reduction":
        return []
    opcode = str(tx.get("reduction_opcode") or tx.get("opcode") or "add")
    suffix = f"nxv{lanes}i32" if scalable else f"v{lanes}i32"
    intrinsic = f"llvm.vector.reduce.{REDUCE_INTRINSICS[opcode]}.{suffix}"
    return [f"declare i32 @{intrinsic}({vector_type(lanes, scalable)})"]


def graph_lane_mapping(value: dict[str, Any], lanes: int) -> list[int]:
    mapping = value.get("mapping") if isinstance(value.get("mapping"), dict) else value.get("lane_mapping")
    values = mapping.get("map") if isinstance(mapping, dict) else None
    if isinstance(values, list) and len(values) == lanes and all(isinstance(v, int) for v in values):
        return [int(v) for v in values]
    return list(range(lanes))


def graph_output_mapping(graph: dict[str, Any], lanes: int) -> list[int]:
    outputs = graph.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        mapping = outputs[0].get("result_lane_mapping")
        values = mapping.get("map") if isinstance(mapping, dict) else None
        if isinstance(values, list) and len(values) == lanes and all(isinstance(v, int) for v in values):
            return [int(v) for v in values]
    return list(range(lanes))


def graph_root_node(graph: dict[str, Any]) -> str:
    outputs = graph.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        return str(outputs[0].get("node") or "")
    nodes = graph.get("nodes")
    if isinstance(nodes, list) and nodes and isinstance(nodes[-1], dict):
        return str(nodes[-1].get("id") or "")
    return ""


def validate_memory_pack_operand(operand: dict[str, Any], lanes: int) -> str:
    name = str(operand.get("name") or "unset")
    memory_contract = str(operand.get("memory_contract") or "unset")
    if memory_contract not in {"contiguous-load-pack-v1", "static-gather-pack-v1"}:
        return f"unsupported-memory-pack-contract:{memory_contract}"
    if operand.get("memory_safety_status") != "complete":
        return f"unsupported-memory-pack-safety:{operand.get('memory_safety_status') or 'unset'}"
    if int(operand.get("element_bits") or 0) != 32:
        return f"unsupported-memory-pack-element-bits:{operand.get('element_bits') or 'unset'}"
    if not str(operand.get("base") or ""):
        return f"unsupported-memory-pack-base:{name}"
    terms = operand.get("address_terms")
    if not isinstance(terms, list) or len(terms) != lanes:
        return f"unsupported-memory-pack-address-terms:{name}"
    bases = {str(term.get("base") or "") for term in terms if isinstance(term, dict)}
    if bases != {str(operand.get("base") or "")}:
        return f"unsupported-memory-pack-mixed-base:{name}"
    seen_lanes: set[int] = set()
    for term in terms:
        if not isinstance(term, dict) or term.get("kind") != "static":
            return f"unsupported-memory-pack-address-kind:{name}"
        lane = term.get("lane")
        index = term.get("index")
        if not isinstance(lane, int) or not isinstance(index, int):
            return f"unsupported-memory-pack-address-index:{name}"
        seen_lanes.add(lane)
    if seen_lanes != set(range(lanes)):
        return f"unsupported-memory-pack-lanes:{name}"
    side_conditions = operand.get("memory_side_conditions")
    if isinstance(side_conditions, dict):
        for key in ("stable_base", "non_volatile", "non_atomic", "no_intervening_store", "no_unknown_memory_effects"):
            if side_conditions.get(key) is not True:
                return f"unsupported-memory-pack-side-condition:{key}"
    return ""


def validate_store_sink(sink: dict[str, Any], lanes: int, node_ids: set[str]) -> str:
    name = str(sink.get("base") or "unset")
    store_contract = str(sink.get("store_contract") or "unset")
    if store_contract not in {"contiguous-store-pack-v1", "static-scatter-store-pack-v1"}:
        return f"unsupported-store-sink-contract:{store_contract}"
    if sink.get("store_safety_status") != "complete":
        return f"unsupported-store-sink-safety:{sink.get('store_safety_status') or 'unset'}"
    if int(sink.get("element_bits") or 0) != 32:
        return f"unsupported-store-sink-element-bits:{sink.get('element_bits') or 'unset'}"
    if not str(sink.get("base") or ""):
        return "unsupported-store-sink-base:unset"
    if str(sink.get("node") or "") not in node_ids:
        return f"unsupported-store-sink-node:{sink.get('node') or 'unset'}"
    terms = sink.get("store_address_terms")
    if not isinstance(terms, list) or len(terms) != lanes:
        return f"unsupported-store-sink-address-terms:{name}"
    bases = {str(term.get("base") or "") for term in terms if isinstance(term, dict)}
    if bases != {str(sink.get("base") or "")}:
        return f"unsupported-store-sink-mixed-base:{name}"
    seen_lanes: set[int] = set()
    for term in terms:
        if not isinstance(term, dict) or term.get("kind") != "static":
            return f"unsupported-store-sink-address-kind:{name}"
        lane = term.get("lane")
        index = term.get("index")
        if not isinstance(lane, int) or not isinstance(index, int):
            return f"unsupported-store-sink-address-index:{name}"
        seen_lanes.add(lane)
    if seen_lanes != set(range(lanes)):
        return f"unsupported-store-sink-lanes:{name}"
    side_conditions = sink.get("store_side_conditions")
    if isinstance(side_conditions, dict):
        for key in ("stable_base", "non_volatile", "non_atomic", "no_intervening_store", "no_unknown_memory_effects"):
            if side_conditions.get(key) is not True:
                return f"unsupported-store-sink-side-condition:{key}"
    return ""


def validate_graph(tx: dict[str, Any]) -> str:
    graph = tx.get("transaction_graph")
    if not isinstance(graph, dict):
        return "absent"
    if graph.get("consistency") != "ok":
        return "graph-consistency-not-ok"
    lanes = transaction_lanes(tx)
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return "graph-has-no-nodes"
    graph_operands_value = graph.get("operands")
    if isinstance(graph_operands_value, list):
        for operand in graph_operands_value:
            if isinstance(operand, dict) and operand.get("kind") == "memory-pack":
                reason = validate_memory_pack_operand(operand, lanes)
                if reason:
                    return reason
    node_ids = {str(node.get("id") or "") for node in nodes if isinstance(node, dict)}
    store_sinks = graph.get("store_sinks")
    if isinstance(store_sinks, list):
        for sink in store_sinks:
            if isinstance(sink, dict):
                reason = validate_store_sink(sink, lanes, node_ids)
                if reason:
                    return reason
    for node in nodes:
        if not isinstance(node, dict):
            return "invalid-graph-node"
        kind = str(node.get("kind") or "")
        if kind not in GRAPH_NODE_KINDS:
            return f"unsupported-graph-node-kind:{kind or 'unset'}"
        opcode = str(node.get("opcode") or kind)
        if kind == "binop" and opcode not in BINOP_OPS:
            return f"unsupported-graph-binop:{opcode}"
        if kind == "cast" and opcode not in GRAPH_CAST_OPS:
            return f"unsupported-graph-cast:{opcode}"
        operands = node.get("operands")
        if not isinstance(operands, list):
            return f"missing-graph-operands:{node.get('id')}"
        if kind == "binop" and len(operands) != 2:
            return f"unsupported-graph-binop-arity:{node.get('id')}"
        if kind == "icmp":
            if len(operands) != 2:
                return f"unsupported-graph-icmp-arity:{node.get('id')}"
            if str(node.get("predicate") or "") not in GRAPH_ICMP_PREDICATES:
                return f"unsupported-graph-icmp-predicate:{node.get('predicate') or 'unset'}"
        if kind == "select" and len(operands) != 3:
            return f"unsupported-graph-select-arity:{node.get('id')}"
        if kind == "shuffle":
            if len(operands) != 1:
                return f"unsupported-graph-shuffle-arity:{node.get('id')}"
            mask = node.get("mask") or node.get("base_mask")
            if not (isinstance(mask, list) and len(mask) == lanes and all(isinstance(v, int) for v in mask)):
                return f"unsupported-shuffle-mask:{node.get('id')}"
        if kind == "cast":
            if len(operands) != 1:
                return f"unsupported-graph-cast-arity:{node.get('id')}"
            if int(node.get("bits") or 0) not in {32, 64}:
                return f"unsupported-graph-cast-width:{node.get('bits') or 'unset'}"
        if kind == "extract":
            if len(operands) != 1:
                return f"unsupported-graph-extract-arity:{node.get('id')}"
            if not isinstance(node.get("index"), int):
                return f"unsupported-graph-extract-index:{node.get('id')}"
        if kind == "insert":
            if len(operands) != 2:
                return f"unsupported-graph-insert-arity:{node.get('id')}"
            if not isinstance(node.get("index"), int):
                return f"unsupported-graph-insert-index:{node.get('id')}"
        for operand in operands:
            if not isinstance(operand, dict):
                return f"invalid-graph-operand:{node.get('id')}"
            operand_kind = str(operand.get("kind") or "")
            if operand_kind not in GRAPH_OPERAND_KINDS:
                return f"unsupported-graph-operand-kind:{operand_kind or 'unset'}"
            if operand.get("kind") == "node" and str(operand.get("id") or "") not in node_ids:
                return f"unknown-graph-node-ref:{operand.get('id')}"
    if not graph_root_node(graph):
        return "missing-graph-output"
    return ""


def const_vector(value: int, lanes: int, scalable: bool, bits: int = 32) -> str:
    if value == 0:
        return "zeroinitializer"
    if scalable:
        return f"splat (i{bits} {value})"
    return "<" + ", ".join(f"i{bits} {value}" for _ in range(lanes)) + ">"


def graph_operand_value(
    lines: list[str],
    operand: dict[str, Any],
    node_values: dict[str, GraphValue],
    graph_packs: dict[str, GraphValue],
    lanes: int,
    scalable: bool,
) -> GraphValue:
    kind = str(operand.get("kind") or "")
    if kind == "node":
        value = node_values.get(str(operand.get("id") or ""))
        if value is None:
            raise ValueError(f"unknown graph node reference: {operand.get('id')}")
        return value
    if kind == "pack":
        name = safe_part(operand.get("name"))
        value = graph_packs.get(name)
        if value is None:
            raise ValueError(f"unknown graph pack reference: {name}")
        return value
    if kind == "memory-pack":
        name = safe_part(operand.get("name"))
        value = graph_packs.get(name)
        if value is None:
            raise ValueError(f"unknown graph memory-pack reference: {name}")
        return value
    if kind == "const":
        bits = int(operand.get("bits") or 32)
        if bits not in {32, 64}:
            raise ValueError(f"unsupported graph const width: {bits}")
        return GraphValue(vector_type_bits(lanes, scalable, bits), const_vector(int(operand.get("value") or 0), lanes, scalable, bits))
    raise ValueError(f"unsupported graph operand kind: {kind}")


def type_suffix(ty: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", ty).strip("_")
    return text or "ty"


def require_vector(value: GraphValue, context: str) -> None:
    if not is_vector_type(value.ty):
        raise ValueError(f"{context} requires vector operand, got {value.ty}")


def require_same_type(lhs: GraphValue, rhs: GraphValue, context: str) -> None:
    if lhs.ty != rhs.ty:
        raise ValueError(f"{context} type mismatch: {lhs.ty} vs {rhs.ty}")


def emit_graph_ir(record: dict[str, Any], tx: dict[str, Any]) -> str:
    graph = tx["transaction_graph"]
    lanes = transaction_lanes(tx)
    scalable = tx.get("scalable") is True
    kind = str(tx.get("kind") or "")
    opcode = str(tx.get("reduction_opcode") or tx.get("opcode") or "add")
    marker = str(record.get("marker") or "probe.slp.vectorize-binop")
    lines: list[str] = [
        "; ModuleID = 'o2t-slp-transaction'",
        'source_filename = "o2t-slp-transaction.ll"',
        f"; marker={marker}",
        f"; transaction_kind={kind}",
        f"; transaction_opcode={opcode}",
        f"; transaction_lanes={lanes}",
        f"; transaction_scalable={'true' if scalable else 'false'}",
        "; ir_lowering_mode=transaction-graph",
        f"; transaction_graph_kind={graph.get('kind', '')}",
    ]
    lines.append(f"define i32 @test({graph_function_args(tx, lanes)}) {{")
    lines.append("entry:")

    graph_packs: dict[str, GraphValue] = {}
    for operand in graph.get("operands", []):
        if not isinstance(operand, dict):
            continue
        name = safe_part(operand.get("name"))
        if operand.get("kind") == "memory-pack":
            emit_memory_pack(lines, f"graph.mem.{name}", operand, lanes, scalable)
            graph_packs[name] = GraphValue(vector_type(lanes, scalable), f"%graph.mem.{name}")
        else:
            insert_pack(lines, f"graph.pack.{name}", name, graph_lane_mapping(operand, lanes), lanes, scalable)
            graph_packs[name] = GraphValue(vector_type(lanes, scalable), f"%graph.pack.{name}")

    node_values: dict[str, GraphValue] = {}
    for node in graph.get("nodes", []):
        node_id = safe_part(node.get("id"))
        kind_name = str(node.get("kind") or "")
        operands = [
            graph_operand_value(lines, operand, node_values, graph_packs, lanes, scalable)
            for operand in node.get("operands", [])
            if isinstance(operand, dict)
        ]
        if kind_name == "binop":
            opcode_name = str(node.get("opcode") or "")
            out = f"graph.{node_id}"
            require_vector(operands[0], f"graph binop {node.get('id')}")
            require_same_type(operands[0], operands[1], f"graph binop {node.get('id')}")
            lines.append(f"  %{out} = {opcode_name} {operands[0].ty} {operands[0].value}, {operands[1].value}")
            node_values[str(node.get("id") or "")] = GraphValue(operands[0].ty, f"%{out}")
        elif kind_name == "icmp":
            predicate = str(node.get("predicate") or "")
            out = f"graph.{node_id}"
            require_vector(operands[0], f"graph icmp {node.get('id')}")
            require_same_type(operands[0], operands[1], f"graph icmp {node.get('id')}")
            result_ty = vector_type_bits(lanes, scalable, 1)
            lines.append(f"  %{out} = icmp {predicate} {operands[0].ty} {operands[0].value}, {operands[1].value}")
            node_values[str(node.get("id") or "")] = GraphValue(result_ty, f"%{out}")
        elif kind_name == "select":
            out = f"graph.{node_id}"
            require_same_type(operands[1], operands[2], f"graph select {node.get('id')}")
            mask_ty = vector_type_bits(lanes, scalable, 1)
            if operands[0].ty != mask_ty:
                raise ValueError(f"graph select mask type mismatch: {operands[0].ty} vs {mask_ty}")
            lines.append(f"  %{out} = select {mask_ty} {operands[0].value}, {operands[1].ty} {operands[1].value}, {operands[2].ty} {operands[2].value}")
            node_values[str(node.get("id") or "")] = GraphValue(operands[1].ty, f"%{out}")
        elif kind_name == "shuffle":
            out = f"graph.{node_id}"
            require_vector(operands[0], f"graph shuffle {node.get('id')}")
            mask_values = [int(value) for value in (node.get("mask") or node.get("base_mask"))]
            mask = ", ".join(f"i32 {value}" for value in mask_values)
            mask_ty = vector_type(lanes, scalable)
            lines.append(f"  %{out} = shufflevector {operands[0].ty} {operands[0].value}, {operands[0].ty} poison, {mask_ty} <{mask}>")
            node_values[str(node.get("id") or "")] = GraphValue(operands[0].ty, f"%{out}")
        elif kind_name == "cast":
            out = f"graph.{node_id}"
            require_vector(operands[0], f"graph cast {node.get('id')}")
            opcode_name = str(node.get("opcode") or "")
            result_ty = vector_type_bits(lanes, scalable, int(node.get("bits") or 32))
            lines.append(f"  %{out} = {opcode_name} {operands[0].ty} {operands[0].value} to {result_ty}")
            node_values[str(node.get("id") or "")] = GraphValue(result_ty, f"%{out}")
        elif kind_name == "extract":
            out = f"graph.{node_id}"
            require_vector(operands[0], f"graph extract {node.get('id')}")
            result_ty = scalar_type_from_vector(operands[0].ty)
            lines.append(f"  %{out} = extractelement {operands[0].ty} {operands[0].value}, i32 {int(node.get('index'))}")
            node_values[str(node.get("id") or "")] = GraphValue(result_ty, f"%{out}")
        elif kind_name == "insert":
            out = f"graph.{node_id}"
            require_vector(operands[0], f"graph insert {node.get('id')}")
            element_ty = scalar_type_from_vector(operands[0].ty)
            if operands[1].ty != element_ty:
                raise ValueError(f"graph insert element type mismatch: {operands[1].ty} vs {element_ty}")
            lines.append(f"  %{out} = insertelement {operands[0].ty} {operands[0].value}, {element_ty} {operands[1].value}, i32 {int(node.get('index'))}")
            node_values[str(node.get("id") or "")] = GraphValue(operands[0].ty, f"%{out}")

    root = node_values[graph_root_node(graph)]
    require_vector(root, "graph output")
    if vector_element_bits(root.ty) != 32:
        raise ValueError(f"graph output must be i32 vector, got {root.ty}")
    result = shuffle_if_needed(lines, root.value.lstrip("%"), "graph.result.vec", graph_output_mapping(graph, lanes), lanes, scalable)
    store_sinks = graph.get("store_sinks")
    if isinstance(store_sinks, list):
        for index, sink in enumerate(store_sinks):
            if isinstance(sink, dict):
                emit_store_sink(lines, f"graph.store.{index}", sink, result, lanes, scalable)
    lines.append(f"  %result = extractelement {vector_type(lanes, scalable)} %{result}, i32 0")
    lines.append("  ret i32 %result")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_template_ir(record: dict[str, Any], tx: dict[str, Any]) -> str:
    lanes = transaction_lanes(tx)
    scalable = tx.get("scalable") is True
    kind = str(tx.get("kind") or "")
    opcode = str(tx.get("reduction_opcode") or tx.get("opcode") or "add")
    marker = str(record.get("marker") or ("probe.slp.vectorize-reduction" if kind == "slp-vectorize-reduction" else "probe.slp.vectorize-binop"))
    binary = kind != "slp-vectorize-reduction"
    lines: list[str] = [
        "; ModuleID = 'o2t-slp-transaction'",
        'source_filename = "o2t-slp-transaction.ll"',
        f"; marker={marker}",
        f"; transaction_kind={kind}",
        f"; transaction_opcode={opcode}",
        f"; transaction_lanes={lanes}",
        f"; transaction_scalable={'true' if scalable else 'false'}",
    ]
    lines.extend(declarations(tx, lanes, scalable))
    if declarations(tx, lanes, scalable):
        lines.append("")
    lines.append(f"define i32 @test({function_args(lanes, binary)}) {{")
    lines.append("entry:")
    if kind == "slp-vectorize-reduction":
        emit_reduction(lines, tx, lanes, scalable)
    else:
        result = emit_binop(lines, tx, lanes, scalable)
        lines.append(f"  %result = extractelement {vector_type(lanes, scalable)} %{result}, i32 0")
        lines.append("  ret i32 %result")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_ir(record: dict[str, Any], tx: dict[str, Any]) -> tuple[str, str, str, str]:
    reason = validate_graph(tx)
    if not reason:
        try:
            return emit_graph_ir(record, tx), "transaction-graph", "used", ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            reason = f"graph-lowering-error:{exc}"
    mode = "transaction-template"
    status = "absent" if reason == "absent" else "unsupported"
    return emit_template_ir(record, tx), mode, status, reason


def unsupported_record(record: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "marker": str(record.get("marker") or ""),
        "reason": reason,
        "source": record.get("file", record.get("pass", "")),
        "line": record.get("line"),
    }


def write_jsonl(path: Path | None, records: list[dict[str, Any]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def write_summary(path: Path | None, summary: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_formalization(args: argparse.Namespace, input_path: Path, index: int) -> None:
    out = args.out_dir / f"transaction-formalization-verification-{index}-{safe_part(input_path.stem)}.json"
    subprocess.run(
        [
            sys.executable,
            str(args.formalization_verifier),
            "--input",
            str(input_path),
            "--out",
            str(out),
            "--require-clean",
        ],
        check=True,
    )


def validate_ir(args: argparse.Namespace, ir_path: Path) -> dict[str, str]:
    if not args.validate_ir:
        return {"status": "skipped", "reason": "validation-disabled", "command": ""}
    if args.llvm_as is None:
        return {"status": "skipped", "reason": "llvm-as-not-configured", "command": ""}
    command = [str(args.llvm_as), str(ir_path)]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {"status": "failed", "reason": str(exc), "command": " ".join(command)}
    if result.returncode == 0:
        return {"status": "passed", "reason": "", "command": " ".join(command)}
    reason = result.stderr.strip() or result.stdout.strip() or f"llvm-as exited {result.returncode}"
    return {"status": "failed", "reason": reason, "command": " ".join(command)}


def validation_summary(statuses: list[str]) -> dict[str, int]:
    counts = collections.Counter(statuses)
    return {key: int(counts.get(key, 0)) for key in ("passed", "failed", "skipped")}


def main() -> int:
    args = parse_args()
    records: list[dict[str, Any]] = []
    for path in args.input:
        records.extend(load_records(path))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.jsonl"
    unsupported: list[dict[str, Any]] = []
    generated = 0
    skipped = 0
    generated_markers: list[str] = []
    validation_statuses: list[str] = []
    graph_statuses: list[str] = []
    graph_reasons: list[str] = []
    validation_failed = False

    if args.verify_formalization:
        for index, path in enumerate(args.input):
            verify_formalization(args, path, index)

    with manifest_path.open("w", encoding="utf-8") as manifest:
        for index, record in enumerate(records):
            tx = record_transaction(record)
            if tx is None:
                continue
            reason = validate_transaction(tx)
            if reason:
                skipped += 1
                unsupported.append(unsupported_record(record, reason))
                if args.strict:
                    write_jsonl(args.unsupported_jsonl, unsupported)
                    write_summary(args.summary_json, {"generated": generated, "skipped": skipped, "unsupported": unsupported})
                    return 1
                continue

            kind = str(tx.get("kind") or "")
            opcode = str(tx.get("reduction_opcode") or tx.get("opcode") or "op")
            lanes = transaction_lanes(tx)
            marker = str(record.get("marker") or ("probe.slp.vectorize-reduction" if kind == "slp-vectorize-reduction" else "probe.slp.vectorize-binop"))
            stem = f"{marker_filename(marker)}_{safe_part(kind)}_{safe_part(opcode)}_{lanes}lane_{index}"
            ir_name = f"{stem}.ll"
            ir_path = args.out_dir / ir_name
            ir_text, ir_lowering_mode, graph_ir_status, graph_ir_reason = emit_ir(record, tx)
            ir_path.write_text(ir_text, encoding="utf-8")
            ir_validation = validate_ir(args, ir_path)
            validation_statuses.append(ir_validation["status"])
            graph_statuses.append(graph_ir_status)
            if graph_ir_reason and graph_ir_status == "unsupported":
                graph_reasons.append(graph_ir_reason)
            validation_failed = validation_failed or ir_validation["status"] == "failed"

            manifest_record = {
                "case": stem,
                "marker": marker,
                "ir": ir_name,
                "ir_lowering_mode": ir_lowering_mode,
                "graph_ir_status": graph_ir_status,
                "graph_ir_reason": graph_ir_reason,
                "ir_validation_status": ir_validation["status"],
                "ir_validation_reason": ir_validation["reason"],
                "ir_validation_command": ir_validation["command"],
                "source": record.get("file", record.get("pass", "")),
                "line": record.get("line"),
                "transaction_kind": kind,
                "opcode": opcode,
                "lanes": lanes,
                "base_lanes": int(tx.get("base_lanes") or lanes),
                "scalable": tx.get("scalable") is True,
                "formal_status": record.get("proof_status", ""),
                "coverage": [marker],
            }
            manifest.write(json.dumps(manifest_record, sort_keys=True) + "\n")
            generated += 1
            generated_markers.append(marker)

    write_jsonl(args.unsupported_jsonl, unsupported)
    write_summary(
        args.summary_json,
        {
            "generated": generated,
            "skipped": skipped,
            "unsupported": unsupported,
            "generated_markers": generated_markers,
            "ir_validation": validation_summary(validation_statuses),
            "graph_ir": dict(sorted(collections.Counter(graph_statuses).items())),
            "unsupported_graph_reasons": dict(sorted(collections.Counter(graph_reasons).items())),
        },
    )
    print(f"generated {generated} SLP transaction IR case(s) in {args.out_dir}")
    if skipped:
        print(f"skipped {skipped} unsupported SLP transaction(s)", file=sys.stderr)
    if validation_failed and args.strict:
        return 1
    return 1 if generated == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
