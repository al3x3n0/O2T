#!/usr/bin/env python3
"""Validate semantic facts registry coverage and schema consistency."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cv_semantic_facts import validate_semantic_facts


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantic-facts", type=Path, default=ROOT / "constraints" / "semantic_facts.json")
    parser.add_argument("--pass-constraints", type=Path)
    parser.add_argument("--optimization-intents", type=Path)
    return parser.parse_args()


def load_array(path: Path, label: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{label} must be a JSON array")
    return [record for record in data if isinstance(record, dict)]


def marker_set(records: list[dict[str, Any]]) -> set[str]:
    return {str(record["marker"]) for record in records if isinstance(record.get("marker"), str)}


def marker_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(record["marker"]): record
        for record in records
        if isinstance(record.get("marker"), str)
    }


def expected_shapes(marker: str, record: dict[str, Any]) -> set[str]:
    kind = str(record.get("predicate_kind") or record.get("category") or "")
    if marker == "probe.globalopt.dead-initializer" or kind == "global":
        return {"global"}
    if kind in {"cfg", "terminator"}:
        return {"cfg"}
    if kind == "memory":
        return {"memory"}
    if kind == "loop":
        return {"loop"}
    if kind == "vector":
        return {"fixed-vector", "scalable-vector"}
    if kind in {"scalar", "matcher", "equality", "legality"}:
        return {"scalar"}
    if marker.startswith("probe.vector.scalable."):
        return {"scalable-vector"}
    if marker.startswith("probe.vector."):
        return {"fixed-vector"}
    return set()


def semantic_index(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for record in records:
        marker = record.get("marker")
        if not isinstance(marker, str) or not marker:
            continue
        if marker in seen:
            duplicates.append(marker)
            continue
        facts = record.get("semantic_facts")
        seen[marker] = facts if isinstance(facts, dict) else {}
    return seen, duplicates


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    try:
        semantic_records = load_array(args.semantic_facts, "semantic facts registry")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    semantics, duplicates = semantic_index(semantic_records)
    for marker in duplicates:
        errors.append(f"duplicate semantic marker: {marker}")
    for record in semantic_records:
        marker = record.get("marker")
        if not isinstance(marker, str) or not marker:
            errors.append("semantic record missing marker")
            continue
        facts = record.get("semantic_facts")
        ok, message = validate_semantic_facts(facts)
        if not ok:
            errors.append(f"{marker}: {message}")
            continue
        expected = semantics.get(marker, {})
        if any(facts.get(field) != expected.get(field) for field in facts):
            errors.append(f"{marker}: semantic_facts do not match marker")

    pass_records: dict[str, dict[str, Any]] = {}
    if args.pass_constraints:
        try:
            loaded_pass_records = load_array(args.pass_constraints, "pass constraints registry")
            pass_records = marker_map(loaded_pass_records)
            pass_markers = set(pass_records)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        missing = sorted(pass_markers - set(semantics))
        extra = sorted(set(semantics) - pass_markers)
        for marker in missing:
            errors.append(f"missing semantic facts for pass marker: {marker}")
        for marker in extra:
            errors.append(f"semantic facts without pass constraint: {marker}")

    intent_records: dict[str, dict[str, Any]] = {}
    if args.optimization_intents:
        try:
            loaded_intent_records = load_array(args.optimization_intents, "optimization intents registry")
            intent_records = marker_map(loaded_intent_records)
            intent_markers = set(intent_records)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        missing = sorted(intent_markers - set(semantics))
        for marker in missing:
            errors.append(f"missing semantic facts for intent marker: {marker}")

    for marker, facts in semantics.items():
        expected = expected_shapes(marker, pass_records.get(marker, intent_records.get(marker, {})))
        if expected and facts.get("shape") not in expected:
            errors.append(
                f"{marker}: semantic_facts shape {facts.get('shape')} does not match marker"
            )

    summary = {
        "semantic_facts": len(semantics),
        "errors": len(errors),
    }
    print(json.dumps(summary, sort_keys=True))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
