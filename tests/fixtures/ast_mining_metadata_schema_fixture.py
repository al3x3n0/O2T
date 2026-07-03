#!/usr/bin/env python3
"""Validate registry-owned AST mining metadata shape and generated consistency."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any


ALLOWED_AST_MINING_KEYS = {"bind", "registration", "matcher_kind", "matcher_names"}
ALLOWED_REGISTRATIONS = {"ast", "text-fallback"}
ALLOWED_MATCHER_KINDS = {
    "function-call",
    "member-call",
    "member-call-uint-arg",
    "member-range-empty",
    "negated-member-call-uint-arg",
    "type-name",
    "binary-equality",
    "nested-function-call",
}
MATCHER_KINDS_WITH_NESTED_NAME = {
    "member-call-uint-arg",
    "member-range-empty",
    "negated-member-call-uint-arg",
    "nested-function-call",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_string(value: Any, context: str) -> str:
    assert isinstance(value, str) and value, f"{context} must be a non-empty string"
    return value


def assert_string_list(value: Any, context: str) -> list[str]:
    assert isinstance(value, list), f"{context} must be a list"
    for item in value:
        assert isinstance(item, str) and item, f"{context} entries must be non-empty strings"
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo
    registry_path = repo / "constraints" / "pass_constraints.json"
    idioms_path = repo / "constraints" / "llvm_idioms.json"
    ast_metadata = load_module(
        repo / "tools" / "cv_ast_mining_metadata.py",
        "cv_ast_mining_metadata",
    )
    source_generator = load_module(
        repo / "tools" / "cv-generate-source-marker-patterns.py",
        "cv_generate_source_marker_patterns",
    )
    matcher_generator = load_module(
        repo / "tools" / "cv-generate-ast-matcher-specs.py",
        "cv_generate_ast_matcher_specs",
    )

    registry_entries = ast_metadata.load_registry_entries(registry_path)
    registry_markers = {
        entry["marker"] for entry in registry_entries if isinstance(entry.get("marker"), str)
    }
    source_markers = {
        entry["marker"]
        for entry in source_generator.source_pattern_entries(registry_path, idioms_path)
    }

    for entry in registry_entries:
        marker = entry.get("marker")
        hints = entry.get("ast_mining")
        if hints is None:
            continue
        assert isinstance(marker, str) and marker, "ast_mining entries must have a marker"
        assert isinstance(hints, dict), f"{marker}: ast_mining must be an object"
        unknown_keys = sorted(set(hints) - ALLOWED_AST_MINING_KEYS)
        assert not unknown_keys, f"{marker}: unknown ast_mining keys: {', '.join(unknown_keys)}"

        if "bind" in hints:
            assert_string(hints["bind"], f"{marker}: ast_mining.bind")
        registration = hints.get("registration", "ast")
        assert registration in ALLOWED_REGISTRATIONS, (
            f"{marker}: ast_mining.registration must be one of "
            + ", ".join(sorted(ALLOWED_REGISTRATIONS))
        )
        if registration == "text-fallback":
            assert marker in source_markers, f"{marker}: text-fallback must have source coverage"
            assert "matcher_kind" not in hints, f"{marker}: text-fallback must not set matcher_kind"
            assert "matcher_names" not in hints, f"{marker}: text-fallback must not set matcher_names"
        if "matcher_kind" in hints:
            kind = hints["matcher_kind"]
            assert kind in ALLOWED_MATCHER_KINDS, (
                f"{marker}: ast_mining.matcher_kind must be one of "
                + ", ".join(sorted(ALLOWED_MATCHER_KINDS))
            )
        if "matcher_names" in hints:
            assert_string_list(hints["matcher_names"], f"{marker}: ast_mining.matcher_names")
            assert "matcher_kind" in hints, f"{marker}: matcher_names must declare matcher_kind"
        if isinstance(marker, str) and marker.startswith("probe.vector.scalable."):
            assert registration == "text-fallback", (
                f"{marker}: scalable vector AST mining remains text-fallback"
            )

    bind_entries = ast_metadata.ast_bind_entries(registry_path)
    binds = [entry["bind"] for entry in bind_entries]
    assert len(set(binds)) == len(binds), "duplicate generated AST bind names"
    unknown_bind_markers = sorted(
        entry["marker"] for entry in bind_entries if entry["marker"] not in registry_markers
    )
    assert not unknown_bind_markers, (
        "generated AST bind entries reference unknown markers: "
        + ", ".join(unknown_bind_markers)
    )

    matcher_entries = matcher_generator.ast_matcher_spec_entries(registry_path)
    bind_set = set(binds)
    unknown_spec_binds = sorted(
        {entry["bind"] for entry in matcher_entries if entry["bind"] not in bind_set}
    )
    assert not unknown_spec_binds, (
        "generated AST matcher specs reference unknown binds: "
        + ", ".join(unknown_spec_binds)
    )
    duplicate_specs = len(
        {
            (entry["bind"], entry["kind"], entry["name"], entry["nested_name"])
            for entry in matcher_entries
        }
    ) != len(matcher_entries)
    assert not duplicate_specs, "duplicate generated AST matcher specs"

    specs_by_bind: dict[str, list[dict[str, str]]] = {}
    for spec in matcher_entries:
        bind = spec["bind"]
        marker = spec["marker"]
        kind = spec["kind"]
        name = spec["name"]
        nested_name = spec["nested_name"]
        assert marker in registry_markers, f"{bind}: matcher spec marker is not in registry"
        assert kind in ALLOWED_MATCHER_KINDS, f"{bind}: unknown matcher kind {kind}"
        if kind == "binary-equality":
            assert name == "", f"{bind}: binary-equality must not have matcher name"
        else:
            assert name, f"{bind}: non-binary matcher spec must have matcher name"
        if kind in MATCHER_KINDS_WITH_NESTED_NAME:
            assert nested_name, f"{bind}: {kind} must have nested_name"
        else:
            assert nested_name == "", f"{bind}: {kind} must not have nested_name"
        specs_by_bind.setdefault(bind, []).append(spec)

    ast_registered_binds = {
        entry["bind"] for entry in bind_entries if entry.get("registration", "ast") == "ast"
    }
    missing_specs = sorted(ast_registered_binds - set(specs_by_bind))
    assert not missing_specs, (
        "AST-registered bind entries missing matcher specs: " + ", ".join(missing_specs)
    )
    fallback_with_specs = sorted(
        entry["bind"]
        for entry in bind_entries
        if entry.get("registration") == "text-fallback" and entry["bind"] in specs_by_bind
    )
    assert not fallback_with_specs, (
        "text-fallback bind entries unexpectedly have matcher specs: "
        + ", ".join(fallback_with_specs)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
