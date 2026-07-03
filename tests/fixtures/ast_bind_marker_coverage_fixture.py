#!/usr/bin/env python3
"""Verify AST matcher bind registration matches generated marker metadata."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def matching_paren(text: str, open_index: int) -> int:
    assert text[open_index] == "("
    depth = 1
    index = open_index + 1
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
        elif in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 1
        elif in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif in_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                in_char = False
        elif char == "/" and next_char == "/":
            in_line_comment = True
            index += 1
        elif char == "/" and next_char == "*":
            in_block_comment = True
            index += 1
        elif char == '"':
            in_string = True
        elif char == "'":
            in_char = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError(f"unclosed parenthesis at byte offset {open_index}")


def top_level_args(call_body: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    in_string = False
    in_char = False
    escaped = False
    for index, char in enumerate(call_body):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif in_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                in_char = False
        elif char == '"':
            in_string = True
        elif char == "'":
            in_char = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(call_body[start:index].strip())
            start = index + 1
    args.append(call_body[start:].strip())
    return args


def cpp_string_literal(value: str) -> str | None:
    value = value.strip()
    if not value.startswith('"'):
        return None
    index = 1
    escaped = False
    chars: list[str] = []
    while index < len(value):
        char = value[index]
        if escaped:
            chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return "".join(chars)
        else:
            chars.append(char)
        index += 1
    return None


def registered_if_condition_binds(source: str) -> set[str]:
    binds: set[str] = set()
    needle = "ifWithCondition"
    index = 0
    while True:
        found = source.find(needle, index)
        if found == -1:
            return binds
        open_index = source.find("(", found + len(needle))
        if open_index == -1:
            return binds
        close_index = matching_paren(source, open_index)
        args = top_level_args(source[open_index + 1 : close_index])
        if len(args) >= 2:
            bind = cpp_string_literal(args[1])
            if bind:
                binds.add(bind)
        index = close_index + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo
    bind_generator = load_module(
        repo / "tools" / "cv-generate-ast-bind-marker-map.py",
        "cv_generate_ast_bind_marker_map",
    )
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
    entries = bind_generator.bind_marker_entries(repo / "constraints" / "pass_constraints.json")
    by_bind = {entry["bind"]: entry for entry in entries}
    manual_registered = registered_if_condition_binds(
        (repo / "tools" / "cv-mine-pass-source-ast.cpp").read_text(encoding="utf-8")
    )
    matcher_specs = matcher_generator.ast_matcher_spec_entries(
        repo / "constraints" / "pass_constraints.json"
    )
    generated_registered = {entry["bind"] for entry in matcher_specs}
    registered = manual_registered | generated_registered

    unknown_registered = sorted(registered - set(by_bind))
    assert not unknown_registered, "registered AST binds missing metadata: " + ", ".join(
        unknown_registered
    )
    assert generated_registered <= set(by_bind)
    assert not manual_registered

    expected_ast = {
        entry["bind"] for entry in entries if entry.get("registration", "ast") == "ast"
    }
    missing_registered = sorted(expected_ast - registered)
    assert not missing_registered, "generated AST binds not registered: " + ", ".join(
        missing_registered
    )

    source_entries = source_generator.source_pattern_entries(
        repo / "constraints" / "pass_constraints.json",
        repo / "constraints" / "llvm_idioms.json",
    )
    source_markers = {entry["marker"] for entry in source_entries}
    registry_entries = ast_metadata.load_registry_entries(
        repo / "constraints" / "pass_constraints.json"
    )
    covered_markers = {entry["marker"] for entry in entries} | source_markers
    source_minable_without_coverage = sorted(
        str(entry["marker"])
        for entry in registry_entries
        if isinstance(entry.get("marker"), str)
        and entry.get("predicate_kind") != "transaction"
        and isinstance(entry.get("source_patterns"), list)
        and entry["source_patterns"]
        and entry["marker"] not in covered_markers
    )
    assert not source_minable_without_coverage, (
        "registry source-minable markers missing AST/text coverage: "
        + ", ".join(source_minable_without_coverage)
    )
    fallback_without_source = sorted(
        entry["marker"]
        for entry in entries
        if entry.get("registration") == "text-fallback"
        and entry["marker"] not in source_markers
    )
    assert not fallback_without_source, (
        "text-fallback AST binds missing source marker coverage: "
        + ", ".join(fallback_without_source)
    )

    assert "vector-scalable-add-zero" not in registered
    assert by_bind["vector-scalable-add-zero"]["registration"] == "text-fallback"
    assert by_bind["vector-add-zero"]["registration"] == "ast"
    assert "vector-add-zero" in registered
    assert "vector-add-zero" in generated_registered
    assert "vector-mul-one" in generated_registered
    assert "vector-xor-self" in generated_registered
    assert "m-zero" in registered
    assert "unary-update" not in registered
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
