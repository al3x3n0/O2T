#!/usr/bin/env python3
"""Shared schema helpers for source-derived optimization semantic facts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL = "optimization-semantic-v1"
REQUIRED_FIELDS = ("model", "shape", "operation", "identity", "rewrite", "result")
DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "constraints" / "semantic_facts.json"

ALLOWED_VALUES: dict[str, set[str]] = {
    "shape": {"scalar", "cfg", "memory", "loop", "global", "fixed-vector", "scalable-vector"},
    "operation": {
        "add",
        "mul",
        "xor",
        "sub",
        "or",
        "and",
        "shl",
        "lshr",
        "ashr",
        "min",
        "max",
        "abs",
        "erase",
        "remove-unreachable",
        "simplify-branch",
        "promote-alloca",
        "forward-store",
        "remove-store",
        "remove-load",
        "remove-alloca",
        "canonicalize-loop",
        "recognize-induction",
        "derive-trip-count",
        "hoist-invariant",
        "simplify-loop-exit",
        "shuffle",
        "extract-insert",
        "insert-extract",
        "reduction",
        "reduction-add",
    },
    "identity": {
        "zero",
        "one",
        "allones",
        "same-value",
        "dead",
        "unreachable-block",
        "diamond",
        "nested-branch",
        "switch-like-chain",
        "promotable-alloca",
        "single-store",
        "dead-store",
        "overwritten-store",
        "redundant-load",
        "unused-alloca",
        "noncanonical-header",
        "induction-phi",
        "simple-trip-count",
        "loop-invariant",
        "dead-loop-instruction",
        "early-exit",
        "identity-mask",
        "splat-mask",
        "same-lane",
        "zero-vector",
        "single-live-lane",
        "signed-min",
        "signed-max",
        "unsigned-min",
        "unsigned-max",
        "absolute-value",
    },
    "rewrite": {
        "replace-with-lhs",
        "replace-with-zero",
        "remove-dead-instruction",
        "remove-block",
        "collapse-diamond",
        "collapse-nested-branch",
        "collapse-branch-chain",
        "promote-to-register",
        "replace-load-with-stored-value",
        "remove-dead-store",
        "remove-overwritten-store",
        "reuse-available-value",
        "remove-unused-alloca",
        "canonicalize-header",
        "preserve-induction-result",
        "preserve-trip-count-result",
        "hoist-invariant-op",
        "remove-dead-loop-instruction",
        "remove-global-initializer-if-dead-v1",
        "preserve-loop-exit-result",
        "preserve-vector",
        "replace-with-lane-splat",
        "replace-with-inserted-scalar",
        "reduce-to-zero",
        "replace-with-lane",
        "reduce-to-zero-splat",
        "preserve-reduction-result",
    },
    "result": {"vector", "scalar", "reachable-result", "loaded-value", "observable-result", "loop-result"},
}


@lru_cache(maxsize=8)
def load_semantic_registry(path: str = str(DEFAULT_REGISTRY)) -> dict[str, dict[str, Any]]:
    registry_path = Path(path)
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("semantic facts registry must be a JSON array")
    result: dict[str, dict[str, Any]] = {}
    for record in data:
        if not isinstance(record, dict) or not isinstance(record.get("marker"), str):
            continue
        facts = record.get("semantic_facts")
        if isinstance(facts, dict):
            result[str(record["marker"])] = dict(facts)
    return result


def semantic_facts_for_marker(marker: str, registry_path: Path | str = DEFAULT_REGISTRY) -> dict[str, Any]:
    values = load_semantic_registry(str(registry_path)).get(marker)
    if values is None:
        return {}
    return dict(values)


def validate_semantic_facts(value: Any) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "semantic_facts must be an object"
    missing = [field for field in REQUIRED_FIELDS if field not in value]
    if missing:
        return False, "semantic_facts missing fields: " + ", ".join(missing)
    if value.get("model") != MODEL:
        return False, "unsupported semantic_facts model"
    for field, allowed in ALLOWED_VALUES.items():
        field_value = value.get(field)
        if not isinstance(field_value, str) or field_value not in allowed:
            return False, f"unsupported semantic_facts {field}"
    return True, ""


def semantic_facts_valid_for_marker(marker: str, value: Any) -> tuple[bool, str]:
    ok, message = validate_semantic_facts(value)
    if not ok:
        return ok, message
    expected = semantic_facts_for_marker(marker)
    if expected and any(value.get(field) != expected.get(field) for field in REQUIRED_FIELDS):
        return False, "semantic_facts do not match marker"
    return True, ""
