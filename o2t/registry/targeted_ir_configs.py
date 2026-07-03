"""Shared targeted IR generator config mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from o2t.registry.optimization_registry import marker_config_entries, marker_config_patch


KEY_ORDER = [
    "arith_opcode",
    "rhs_mode",
    "extra_opcode",
    "predicate",
    "shape",
    "feature_bits",
    "memory_shape",
    "pointer_mode",
    "store_mode",
    "load_use_mode",
    "loop_shape",
    "loop_trip_mode",
    "induction_mode",
    "loop_use_mode",
    "vector_shape",
    "global_shape",
    "const_a",
    "const_b",
]


DEFAULT_CONFIG = {
    "arith_opcode": 0,
    "rhs_mode": 0,
    "extra_opcode": 0,
    "predicate": 0,
    "shape": 0,
    "feature_bits": 0,
    "memory_shape": 0,
    "pointer_mode": 0,
    "store_mode": 0,
    "load_use_mode": 0,
    "loop_shape": 0,
    "loop_trip_mode": 0,
    "induction_mode": 0,
    "loop_use_mode": 0,
    "vector_shape": 0,
    "global_shape": 0,
    "const_a": 0,
    "const_b": 0,
}

MARKER_CONFIGS: dict[str, dict[str, int]] = {
    str(entry["marker"]): {str(key): int(value) for key, value in entry["config"].items()}
    for entry in marker_config_entries()
    if isinstance(entry.get("config"), dict)
}


def marker_filename(marker: str) -> str:
    return marker.replace("probe.", "").replace(".", "_").replace("-", "_")


def config_from_patch(patch: dict[str, int]) -> dict[str, int]:
    config = dict(DEFAULT_CONFIG)
    config.update(patch)
    return config


def config_for_marker(marker: str) -> dict[str, int] | None:
    patch = marker_config_patch(marker)
    return config_from_patch(patch) if patch is not None else None


def config_for_constraints(constraints: dict[str, Any]) -> dict[str, int] | None:
    if constraints.get("instruction.opcode") == "add" and constraints.get("rhs.value") == 0:
        return config_from_patch({"arith_opcode": 0, "rhs_mode": 0})
    if constraints.get("instruction.opcode") == "mul" and constraints.get("rhs.value") == 1:
        return config_from_patch({"arith_opcode": 2, "rhs_mode": 1})
    if constraints.get("instruction.opcode") == "xor" and (
        constraints.get("lhs") == "same-value" or constraints.get("rhs") == "same-value"
    ):
        return config_from_patch({"extra_opcode": 3})
    if constraints.get("instruction.is_dead") is True:
        return config_from_patch({"extra_opcode": 4})
    if constraints.get("block.reachable") is False:
        return config_from_patch({"shape": 3})
    if constraints.get("cfg.shape") == "diamond":
        return config_from_patch({"shape": 1})
    if constraints.get("cfg.shape") == "nested-branch":
        return config_from_patch({"shape": 2})
    if constraints.get("cfg.shape") == "switch-like-chain":
        return config_from_patch({"shape": 4})
    if constraints.get("memory.alloca") == "promotable":
        return config_from_patch({"memory_shape": 1})
    if constraints.get("memory.store_load_forward") is True:
        return config_from_patch({"memory_shape": 1})
    if constraints.get("memory.store") == "dead":
        return config_from_patch({"memory_shape": 3})
    if constraints.get("memory.store") == "overwritten":
        return config_from_patch({"memory_shape": 4})
    if constraints.get("memory.load") == "redundant":
        return config_from_patch({"memory_shape": 2})
    if constraints.get("memory.alloca") == "unused":
        return config_from_patch({"memory_shape": 5})
    if constraints.get("loop.shape") == "canonical":
        return config_from_patch({"loop_shape": 1})
    if constraints.get("loop.induction") == "phi":
        return config_from_patch({"loop_shape": 1})
    if constraints.get("loop.trip_count") == "simple":
        return config_from_patch({"loop_shape": 1})
    if constraints.get("loop.invariant") is True:
        return config_from_patch({"loop_shape": 3})
    if constraints.get("loop.body_instruction") == "dead":
        return config_from_patch({"loop_shape": 4})
    if constraints.get("loop.exit") == "early":
        return config_from_patch({"loop_shape": 2})
    if constraints.get("vector.scalable") is True:
        scalable_markers = {
            "add": 13,
            "mul": 14,
            "xor": 15,
            "sub": 16,
            "or": 17,
            "and": 18,
        }
        opcode = constraints.get("vector.opcode")
        if opcode in scalable_markers:
            return config_from_patch({"vector_shape": scalable_markers[opcode]})
        if constraints.get("vector.reduction") == "add-zero":
            return config_from_patch({"vector_shape": 19})
    if constraints.get("vector.opcode") == "add" and constraints.get("vector.rhs") == "zero-splat":
        return config_from_patch({"vector_shape": 1})
    if constraints.get("vector.opcode") == "mul" and constraints.get("vector.rhs") == "one-splat":
        return config_from_patch({"vector_shape": 2})
    if constraints.get("vector.opcode") == "xor" and constraints.get("vector.operands") == "same-value":
        return config_from_patch({"vector_shape": 3})
    if constraints.get("vector.shuffle") == "identity":
        return config_from_patch({"vector_shape": 4})
    if constraints.get("vector.shuffle") == "splat":
        return config_from_patch({"vector_shape": 5})
    if constraints.get("vector.extract_insert") == "same-lane":
        return config_from_patch({"vector_shape": 6})
    if constraints.get("vector.reduction") == "add-zero":
        return config_from_patch({"vector_shape": 7})
    if constraints.get("vector.opcode") == "sub" and constraints.get("vector.rhs") == "zero-splat":
        return config_from_patch({"vector_shape": 8})
    if constraints.get("vector.opcode") == "or" and constraints.get("vector.rhs") == "zero-splat":
        return config_from_patch({"vector_shape": 9})
    if constraints.get("vector.opcode") == "and" and constraints.get("vector.rhs") == "allones-splat":
        return config_from_patch({"vector_shape": 10})
    if constraints.get("vector.insert_extract") == "identity":
        return config_from_patch({"vector_shape": 11})
    if constraints.get("vector.reduction") == "add-single-lane":
        return config_from_patch({"vector_shape": 12})
    fixed_minmax = {
        "smin": 20,
        "smax": 21,
        "umin": 22,
        "umax": 23,
        "abs": 24,
    }
    opcode = constraints.get("vector.opcode")
    if opcode in fixed_minmax:
        return config_from_patch({"vector_shape": fixed_minmax[opcode]})
    fixed_identities = {
        "signed-min": 20,
        "signed-max": 21,
        "unsigned-min": 22,
        "unsigned-max": 23,
        "absolute-value": 24,
    }
    identity = constraints.get("vector.identity")
    if identity in fixed_identities:
        return config_from_patch({"vector_shape": fixed_identities[identity]})
    return None


def config_for_record(record: dict[str, Any]) -> dict[str, int] | None:
    marker = record.get("marker")
    if isinstance(marker, str):
        config = config_for_marker(marker)
        if config is not None:
            return config
    constraints = record.get("constraints")
    return config_for_constraints(constraints) if isinstance(constraints, dict) else None


def write_config(path: Path, config: dict[str, int]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for key in KEY_ORDER:
            output.write(f"{key}={config[key]}\n")
