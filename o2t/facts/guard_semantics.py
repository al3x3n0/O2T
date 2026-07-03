"""Shared guard semantics catalog helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Match


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GUARD_SEMANTICS = ROOT / "constraints" / "guard_semantics.json"


def load_guard_semantics(path: Path = DEFAULT_GUARD_SEMANTICS) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("guard semantics catalog must be an array")
    catalog: dict[str, dict[str, Any]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("guard semantics entries must be objects")
        kind = str(entry.get("kind") or "")
        if not kind:
            raise ValueError("guard semantics entry missing kind")
        catalog[kind] = dict(entry)
    return catalog


def guard_definition(kind: str, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return catalog.get(kind, catalog.get("unknown", {}))


def normalize_guard_record(
    record: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    fallback_line: Any = None,
) -> dict[str, Any]:
    kind = str(record.get("kind") or "unknown")
    definition = guard_definition(kind, catalog)
    normalized = {
        "kind": kind,
        "source": str(record.get("source") or ""),
        "line": record.get("line", fallback_line),
        "role": str(record.get("role") or definition.get("role") or "unmodeled-side-condition"),
    }
    proof_effect = record.get("proof_effect") or definition.get("proof_effect")
    if proof_effect:
        normalized["proof_effect"] = str(proof_effect)
    subject = record.get("subject")
    if subject:
        normalized["subject"] = str(subject)
    for field in ("zero_mask", "one_mask"):
        value = record.get(field)
        if isinstance(value, int):
            normalized[field] = value
    formal_effect = definition.get("formal_effect")
    if formal_effect:
        normalized["formal_effect"] = str(formal_effect)
    formal_effect_args = record.get("formal_effect_args")
    if formal_effect_args is None:
        formal_effect_args = definition.get("formal_effect_args")
    if isinstance(formal_effect_args, dict):
        normalized["formal_effect_args"] = dict(formal_effect_args)
    audit_category = definition.get("audit_category")
    if audit_category:
        normalized["audit_category"] = str(audit_category)
    return normalized


def recognizers_for(definition: dict[str, Any]) -> dict[str, Any]:
    value = definition.get("recognizers")
    return value if isinstance(value, dict) else {}


def text_patterns_for(definition: dict[str, Any]) -> list[str]:
    recognizers = recognizers_for(definition)
    patterns = recognizers.get("text_patterns")
    if not isinstance(patterns, list):
        return []
    return [pattern for pattern in patterns if isinstance(pattern, str) and pattern]


def subject_from_match(match: Match[str], subject_group: Any) -> str:
    if subject_group is None:
        return ""
    if isinstance(subject_group, str):
        try:
            return match.group(subject_group) or ""
        except IndexError:
            return ""
    if isinstance(subject_group, int):
        try:
            return match.group(subject_group) or ""
        except IndexError:
            return ""
    return ""


def text_guard_for_source(
    source: str,
    catalog: dict[str, dict[str, Any]],
    line: Any = None,
) -> dict[str, Any] | None:
    for kind, definition in catalog.items():
        recognizers = recognizers_for(definition)
        subject_group = recognizers.get("subject_group")
        for pattern in text_patterns_for(definition):
            match = re.search(pattern, source)
            if not match:
                continue
            record: dict[str, Any] = {"kind": kind, "source": source, "line": line}
            subject = subject_from_match(match, subject_group)
            if subject:
                record["subject"] = subject
            groups = match.groupdict()
            for field in ("zero_mask", "one_mask"):
                value = groups.get(field)
                if value:
                    try:
                        record[field] = int(value, 0)
                    except ValueError:
                        pass
            return normalize_guard_record(record, catalog, line)
    return None


def recognizer_summary(catalog: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    summary = {"text": [], "ast": [], "semantic_only": []}
    for kind, definition in sorted(catalog.items()):
        recognizers = recognizers_for(definition)
        has_text = bool(text_patterns_for(definition))
        has_ast = bool(recognizers.get("ast_callee"))
        if has_text:
            summary["text"].append(kind)
        if has_ast:
            summary["ast"].append(kind)
        if not has_text and not has_ast and kind != "unknown":
            summary["semantic_only"].append(kind)
    return summary
