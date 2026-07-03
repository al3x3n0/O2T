#!/usr/bin/env python3
"""Generate C++ AST bind-name marker metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cv_ast_mining_metadata import ast_bind_entries


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "constraints" / "pass_constraints.json"
DEFAULT_OUT = ROOT / "include" / "o2t" / "GeneratedAstBindMarkerMap.h"


def cpp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def bind_marker_entries(registry_path: Path) -> list[dict[str, str]]:
    return ast_bind_entries(registry_path)


def generate(entries: list[dict[str, str]]) -> str:
    lines = [
        "#pragma once",
        "",
        "#include <array>",
        "",
        "namespace cv {",
        "",
        "struct AstBindMarkerMetadata {",
        "  const char *bindName;",
        "  const char *marker;",
        "};",
        "",
        "inline constexpr std::array<AstBindMarkerMetadata, " + str(len(entries)) + ">",
        "    kAstBindMarkerMetadata{{",
    ]
    for entry in entries:
        lines.append(
            "        {"
            + cpp_string(entry["bind"])
            + ", "
            + cpp_string(entry["marker"])
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

    rendered = generate(bind_marker_entries(args.registry))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
