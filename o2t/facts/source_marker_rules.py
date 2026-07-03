#!/usr/bin/env python3
"""Shared generated source-marker matching rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SKIP_SOURCE_PATTERNS = {
    # Equality-only predicates are the xor-self textual idiom. The and-self
    # marker still gets operation-specific matcher patterns from llvm_idioms.
    "probe.instcombine.and-self": {"m_Deferred(", "Op0 == Op1", "LHS == RHS"},
}


def load_json_array(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [entry for entry in data if isinstance(entry, dict)]


def operation_matcher_patterns(idioms_path: Path) -> dict[str, list[str]]:
    data = json.loads(idioms_path.read_text(encoding="utf-8"))
    operations = data.get("operations")
    if not isinstance(operations, list):
        raise ValueError(f"{idioms_path} must contain an operations array")
    result: dict[str, list[str]] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        name = operation.get("operation")
        matchers = operation.get("matchers")
        if not isinstance(name, str) or not isinstance(matchers, list):
            continue
        result[name] = [f"{matcher}(" for matcher in matchers if isinstance(matcher, str)]
    return result


def scalar_operation_for_entry(entry: dict[str, Any]) -> str:
    marker = entry.get("marker")
    constraints = entry.get("constraints")
    if not isinstance(marker, str) or not marker.startswith("probe.instcombine."):
        return ""
    if not isinstance(constraints, dict):
        return ""
    opcode = constraints.get("instruction.opcode")
    return opcode if isinstance(opcode, str) else ""


def scalar_identity_default_patterns(registry: list[dict[str, Any]]) -> dict[str, str]:
    defaults: dict[str, str] = {}
    candidates: dict[str, list[str]] = {}
    for entry in registry:
        marker = entry.get("marker")
        constraints = entry.get("constraints")
        patterns = entry.get("source_patterns")
        if (
            not isinstance(marker, str)
            or not isinstance(constraints, dict)
            or not isinstance(patterns, list)
            or constraints.get("rhs.kind") != "constant"
        ):
            continue
        identity_patterns = [
            pattern
            for pattern in patterns
            if isinstance(pattern, str)
            and pattern.startswith("m_")
            and pattern.endswith("(")
            and len(patterns) == 1
        ]
        for pattern in identity_patterns:
            candidates.setdefault(pattern, []).append(marker)
    for pattern, markers in candidates.items():
        if len(markers) == 1:
            defaults[pattern] = markers[0]
    return defaults


def exposes_operation_matcher(entry: dict[str, Any], operation_patterns: dict[str, list[str]]) -> bool:
    operation = scalar_operation_for_entry(entry)
    patterns = entry.get("source_patterns")
    if not operation or not isinstance(patterns, list):
        return False
    operation_prefixes = operation_patterns.get(operation, [])
    return any(
        isinstance(pattern, str) and pattern in operation_prefixes
        for pattern in patterns
    )


def source_pattern_entries(registry_path: Path, idioms_path: Path) -> list[dict[str, str]]:
    registry = load_json_array(registry_path)
    operation_patterns = operation_matcher_patterns(idioms_path)
    scalar_identity_defaults = scalar_identity_default_patterns(registry)
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(marker: str, pattern: str, required: str = "", forbidden: str = "") -> None:
        key = (marker, pattern, required, forbidden)
        if key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "marker": marker,
                "pattern": pattern,
                "required": required,
                "forbidden": forbidden,
            }
        )

    def context_for_rule(marker: str, pattern: str) -> tuple[str, str]:
        if marker == "probe.instcombine.and-allones" and pattern in {"m_And(", "m_c_And("}:
            return "m_AllOnes(", ""
        if marker == "probe.instcombine.and-self" and pattern in {"m_And(", "m_c_And("}:
            return "", "m_AllOnes("
        if marker == "probe.instcombine.xor-self" and pattern in {"m_Xor(", "m_c_Xor("}:
            # X ^ X is matched as `m_[c_]Xor(m_Value(X), m_Deferred(X))`; require the m_Deferred
            # self-indicator so a general two-operand xor does not falsely match xor-self.
            return "m_Deferred(", ""
        if marker == "probe.cleanup.unused-alloca":
            if pattern == "hasNUsesOrMore(1)":
                return "!\tif", ""
            return "if", ""
        return "", ""

    for entry in registry:
        marker = entry.get("marker")
        if not isinstance(marker, str):
            continue
        if entry.get("predicate_kind") == "transaction":
            continue
        for pattern in entry.get("source_patterns", []):
            if isinstance(pattern, str) and pattern:
                if pattern in SKIP_SOURCE_PATTERNS.get(marker, set()):
                    continue
                if scalar_identity_defaults.get(pattern, marker) != marker:
                    continue
                required, forbidden = context_for_rule(marker, pattern)
                add(marker, pattern, required, forbidden)
        operation = scalar_operation_for_entry(entry)
        if operation and (
            entry.get("predicate_kind") == "matcher"
            or exposes_operation_matcher(entry, operation_patterns)
        ):
            for pattern in operation_patterns.get(operation, []):
                required, forbidden = context_for_rule(marker, pattern)
                add(marker, pattern, required, forbidden)

    def priority(item: dict[str, str]) -> tuple[int, int, str, str]:
        if scalar_identity_defaults.get(item["pattern"]) == item["marker"]:
            family_priority = 20
        elif item["required"] or item["forbidden"]:
            family_priority = 0
        else:
            family_priority = 10
        return (family_priority, -len(item["pattern"]), item["marker"], item["pattern"])

    return sorted(entries, key=priority)


def source_tokens_all_present(text: str, tokens: str) -> bool:
    return all(not token or token in text for token in tokens.split("\t"))


def source_tokens_any_present(text: str, tokens: str) -> bool:
    return any(token and token in text for token in tokens.split("\t"))


def compact_source_text(text: str) -> str:
    return "".join(char for char in text if not char.isspace())


def compact_source_tokens(tokens: str) -> str:
    return "\t".join(compact_source_text(token) for token in tokens.split("\t"))


def source_rule_matches(text: str, rule: dict[str, str]) -> bool:
    pattern = rule["pattern"]
    compact_text = compact_source_text(text)
    compact_pattern = compact_source_text(pattern)
    required = rule.get("required", "")
    forbidden = rule.get("forbidden", "")
    return (
        (pattern in text or (bool(compact_pattern) and compact_pattern in compact_text))
        and (
            source_tokens_all_present(text, required)
            or source_tokens_all_present(compact_text, compact_source_tokens(required))
        )
        and not source_tokens_any_present(text, forbidden)
        and not source_tokens_any_present(compact_text, compact_source_tokens(forbidden))
    )
