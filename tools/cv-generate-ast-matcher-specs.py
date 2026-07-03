#!/usr/bin/env python3
"""Generate simple C++ AST matcher registration metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cv_ast_mining_metadata import ast_matcher_spec_entries


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "constraints" / "pass_constraints.json"
DEFAULT_OUT = ROOT / "include" / "o2t" / "GeneratedAstMatcherSpecs.h"

def cpp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate(entries: list[dict[str, str]]) -> str:
    lines = [
        "#pragma once",
        "",
        "#include <array>",
        "",
        "namespace cv {",
        "",
        "enum class AstMatcherKind {",
        "  FunctionCall,",
        "  MemberCall,",
        "  MemberCallUIntArg,",
        "  MemberRangeEmpty,",
        "  NegatedMemberCallUIntArg,",
        "  TypeName,",
        "  BinaryEquality,",
        "  NestedFunctionCall,",
        "};",
        "",
        "struct AstMatcherSpec {",
        "  const char *bindName;",
        "  const char *marker;",
        "  AstMatcherKind kind;",
        "  const char *name;",
        "  const char *nestedName;",
        "};",
        "",
        "inline constexpr std::array<AstMatcherSpec, " + str(len(entries)) + ">",
        "    kAstMatcherSpecs{{",
    ]
    kind_names = {
        "function-call": "AstMatcherKind::FunctionCall",
        "member-call": "AstMatcherKind::MemberCall",
        "member-call-uint-arg": "AstMatcherKind::MemberCallUIntArg",
        "member-range-empty": "AstMatcherKind::MemberRangeEmpty",
        "negated-member-call-uint-arg": "AstMatcherKind::NegatedMemberCallUIntArg",
        "type-name": "AstMatcherKind::TypeName",
        "binary-equality": "AstMatcherKind::BinaryEquality",
        "nested-function-call": "AstMatcherKind::NestedFunctionCall",
    }
    for entry in entries:
        lines.append(
            "        {"
            + cpp_string(entry["bind"])
            + ", "
            + cpp_string(entry["marker"])
            + ", "
            + kind_names[entry["kind"]]
            + ", "
            + cpp_string(entry["name"])
            + ", "
            + cpp_string(entry["nested_name"])
            + "},"
        )
    lines.extend(
        [
            "    }};",
            "",
            "} // namespace cv",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rendered = generate(ast_matcher_spec_entries(args.registry))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
