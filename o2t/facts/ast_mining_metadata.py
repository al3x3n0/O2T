#!/usr/bin/env python3
"""Shared registry-derived AST mining metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCALAR_MATCHER_BIND_BY_OPCODE_VALUE = {
    ("add", 0): "m-zero",
    ("sub", 0): "m-sub",
    ("mul", 1): "m-one",
    ("or", 0): "m-or",
    ("and", -1): "m-allones",
}

SCALAR_EQUALITY_BIND_BY_OPCODE = {
    "and": "m-and",
    "xor": "xor-self",
}

EMIT_FAMILY_ORDER = {
    "vector": 0,
    "vector-scalable": 1,
    "scalar": 2,
    "other": 3,
}

SCALAR_EMIT_ORDER = {
    "m-zero": 0,
    "m-sub": 1,
    "m-one": 2,
    "m-or": 3,
    "m-allones": 4,
    "m-and": 5,
    "xor-self": 6,
}

SCALAR_MATCHER_NAME_BY_BIND = {
    "m-zero": "m_Zero",
    "m-sub": "m_Sub",
    "m-one": "m_One",
    "m-or": "m_Or",
    "m-allones": "m_AllOnes",
    "m-and": "m_And",
}

def load_registry_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [entry for entry in data if isinstance(entry, dict)]


def ast_mining_hints(entry: dict[str, Any]) -> dict[str, Any]:
    hints = entry.get("ast_mining")
    return hints if isinstance(hints, dict) else {}


def scalar_bind_for_entry(entry: dict[str, Any]) -> str:
    constraints = entry.get("constraints")
    if not isinstance(constraints, dict):
        return ""
    opcode = constraints.get("instruction.opcode")
    if not isinstance(opcode, str):
        return ""
    rhs_value = constraints.get("rhs.value")
    bind = SCALAR_MATCHER_BIND_BY_OPCODE_VALUE.get((opcode, rhs_value))
    if bind:
        return bind
    if constraints.get("lhs") == "same-value" and constraints.get("rhs") == "same-value":
        return SCALAR_EQUALITY_BIND_BY_OPCODE.get(opcode, "")
    return ""


def vector_bind_for_marker(marker: str) -> str:
    if not marker.startswith("probe.vector."):
        return ""
    return "vector-" + marker.removeprefix("probe.vector.").replace(".", "-")


def bind_for_entry(entry: dict[str, Any]) -> str:
    hinted_bind = ast_mining_hints(entry).get("bind")
    if isinstance(hinted_bind, str) and hinted_bind:
        return hinted_bind
    marker = entry.get("marker")
    if not isinstance(marker, str):
        return ""
    if marker.startswith("probe.vector."):
        return vector_bind_for_marker(marker)
    if marker.startswith("probe.instcombine."):
        bind = scalar_bind_for_entry(entry)
        if bind:
            return bind
    return ""


def emit_sort_key(entry: dict[str, str]) -> tuple[int, int]:
    bind = entry["bind"]
    marker = entry["marker"]
    if marker.startswith("probe.vector.scalable."):
        family = "vector-scalable"
    elif marker.startswith("probe.vector."):
        family = "vector"
    elif bind.startswith("m-") or bind == "xor-self":
        family = "scalar"
    else:
        family = "other"
    family_order = EMIT_FAMILY_ORDER[family]
    if family == "scalar":
        return (family_order, SCALAR_EMIT_ORDER[bind])
    return (family_order, int(entry["registry_index"]))


def ast_bind_entries(registry_path: Path) -> list[dict[str, str]]:
    entries = [
        {
            "bind": bind_for_entry(entry),
            "marker": str(entry["marker"]),
            "registration": str(ast_mining_hints(entry).get("registration") or "ast"),
            "registry_index": str(index),
        }
        for index, entry in enumerate(load_registry_entries(registry_path))
        if isinstance(entry.get("marker"), str) and bind_for_entry(entry)
    ]
    entries = sorted(entries, key=emit_sort_key)
    if len({entry["bind"] for entry in entries}) != len(entries):
        raise ValueError("duplicate AST bind names")
    return entries


def qualified_function_names(name: str) -> list[str]:
    if name.startswith("m_") or name.startswith("::"):
        return [name]
    return [name, "::llvm::" + name]


def function_names_from_source_patterns(entry: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for pattern in entry.get("source_patterns", []):
        if not isinstance(pattern, str):
            continue
        token = pattern[:-1] if pattern.endswith("(") else pattern
        if not token or not (token[0].isalpha() or token[0] == "_"):
            continue
        if not all(char.isalnum() or char == "_" for char in token):
            continue
        if token == "m_Deferred" or token.islower():
            continue
        names.append(token)
    return names


def nested_function_call_from_source_patterns(entry: dict[str, Any]) -> tuple[str, str]:
    for pattern in entry.get("source_patterns", []):
        if not isinstance(pattern, str):
            continue
        open_index = pattern.find("(")
        if open_index <= 0:
            continue
        outer = pattern[:open_index]
        inner_start = open_index + 1
        inner_end = inner_start
        while inner_end < len(pattern) and (
            pattern[inner_end].isalnum() or pattern[inner_end] == "_"
        ):
            inner_end += 1
        inner = pattern[inner_start:inner_end]
        if outer and inner:
            return outer, inner
    return "", ""


def matcher_kind(bind: str) -> str:
    del bind
    return "function-call"


def matcher_kind_for_entry(bind: str, entry: dict[str, Any]) -> str:
    hinted_kind = ast_mining_hints(entry).get("matcher_kind")
    if isinstance(hinted_kind, str) and hinted_kind:
        return hinted_kind
    if bind == "xor-self":
        return "binary-equality"
    return matcher_kind(bind)


def matcher_names_for_entry(bind: str, entry: dict[str, Any]) -> list[str]:
    hinted_names = ast_mining_hints(entry).get("matcher_names")
    if isinstance(hinted_names, list):
        return [name for name in hinted_names if isinstance(name, str) and name]
    if bind in SCALAR_MATCHER_NAME_BY_BIND:
        return [SCALAR_MATCHER_NAME_BY_BIND[bind]]
    return function_names_from_source_patterns(entry)


def unused_alloca_extra_specs(bind: str, marker: str) -> list[dict[str, str]]:
    return [
        {
            "bind": bind,
            "marker": marker,
            "kind": "member-call-uint-arg",
            "name": "hasNUses",
            "nested_name": "0",
        },
        {
            "bind": bind,
            "marker": marker,
            "kind": "member-range-empty",
            "name": "users",
            "nested_name": "empty",
        },
        {
            "bind": bind,
            "marker": marker,
            "kind": "negated-member-call-uint-arg",
            "name": "hasNUsesOrMore",
            "nested_name": "1",
        },
    ]


def ast_matcher_spec_entries(registry_path: Path) -> list[dict[str, str]]:
    registry_by_marker = {
        str(entry["marker"]): entry
        for entry in load_registry_entries(registry_path)
        if isinstance(entry.get("marker"), str)
    }
    entries: list[dict[str, str]] = []
    for bind_entry in ast_bind_entries(registry_path):
        bind = bind_entry["bind"]
        marker = bind_entry["marker"]
        if bind_entry.get("registration") != "ast":
            continue
        registry_entry = registry_by_marker.get(marker, {})
        kind = matcher_kind_for_entry(bind, registry_entry)
        outer, inner = nested_function_call_from_source_patterns(registry_entry)
        if kind == "function-call" and outer and inner:
            entries.append(
                {
                    "bind": bind,
                    "marker": marker,
                    "kind": "nested-function-call",
                    "name": outer,
                    "nested_name": inner,
                }
            )
            continue
        if bind == "unused-alloca":
            entries.extend(unused_alloca_extra_specs(bind, marker))
        if kind == "binary-equality":
            entries.append(
                {
                    "bind": bind,
                    "marker": marker,
                    "kind": kind,
                    "name": "",
                    "nested_name": "",
                }
            )
            if bind == "xor-self":
                entries.append(
                    {
                        "bind": bind,
                        "marker": marker,
                        "kind": "function-call",
                        "name": "m_Xor",
                        "nested_name": "",
                    }
                )
            continue
        names = matcher_names_for_entry(bind, registry_entry)
        if kind == "function-call":
            expanded: list[str] = []
            for name in names:
                expanded.extend(qualified_function_names(name))
            names = expanded
        for name in names:
            entries.append(
                {
                    "bind": bind,
                    "marker": marker,
                    "kind": kind,
                    "name": name,
                    "nested_name": "",
                }
            )
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for entry in entries:
        key = (entry["bind"], entry["kind"], entry["name"], entry["nested_name"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped
