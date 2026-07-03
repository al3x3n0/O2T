#!/usr/bin/env python3
"""Generate C++ source marker pattern metadata from pass constraints."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cv_source_marker_rules import (
    load_json_array,
    scalar_identity_default_patterns,
    source_pattern_entries,
    source_rule_matches,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "constraints" / "pass_constraints.json"
DEFAULT_IDIOMS = ROOT / "constraints" / "llvm_idioms.json"
DEFAULT_OUT = ROOT / "include" / "o2t" / "GeneratedSourceMarkerPatterns.h"


def cpp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate(entries: list[dict[str, str]]) -> str:
    lines = [
        "#pragma once",
        "",
        '#include "llvm/ADT/StringRef.h"',
        "",
        "#include <array>",
        "#include <cctype>",
        "#include <string>",
        "",
        "namespace cv {",
        "",
        "struct SourceMarkerPattern {",
        "  const char *marker;",
        "  const char *pattern;",
        "  const char *requiredTokens;",
        "  const char *forbiddenTokens;",
        "};",
        "",
        "inline constexpr std::array<SourceMarkerPattern, " + str(len(entries)) + ">",
        "    kSourceMarkerPatterns{{",
    ]
    for entry in entries:
        lines.append(
            "        {"
            + cpp_string(entry["marker"])
            + ", "
            + cpp_string(entry["pattern"])
            + ", "
            + cpp_string(entry["required"])
            + ", "
            + cpp_string(entry["forbidden"])
            + "},"
        )
    lines.extend(
        [
            "    }};",
            "",
            "inline bool generatedSourceTokensAllPresent(llvm::StringRef Text,",
            "                                                llvm::StringRef Tokens) {",
            "  while (!Tokens.empty()) {",
            "    auto Split = Tokens.split('\\t');",
            "    if (!Split.first.empty() && !Text.contains(Split.first)) {",
            "      return false;",
            "    }",
            "    Tokens = Split.second;",
            "  }",
            "  return true;",
            "}",
            "",
            "inline bool generatedSourceTokensAnyPresent(llvm::StringRef Text,",
            "                                               llvm::StringRef Tokens) {",
            "  while (!Tokens.empty()) {",
            "    auto Split = Tokens.split('\\t');",
            "    if (!Split.first.empty() && Text.contains(Split.first)) {",
            "      return true;",
            "    }",
            "    Tokens = Split.second;",
            "  }",
            "  return false;",
            "}",
            "",
            "inline std::string generatedSourceCompact(llvm::StringRef Text) {",
            "  std::string Result;",
            "  Result.reserve(Text.size());",
            "  for (char C : Text) {",
            "    if (!std::isspace(static_cast<unsigned char>(C))) {",
            "      Result.push_back(C);",
            "    }",
            "  }",
            "  return Result;",
            "}",
            "",
            "inline std::string generatedSourceCompactTokens(llvm::StringRef Tokens) {",
            "  std::string Result;",
            "  bool First = true;",
            "  while (!Tokens.empty()) {",
            "    auto Split = Tokens.split('\\t');",
            "    if (!First) {",
            "      Result.push_back('\\t');",
            "    }",
            "    First = false;",
            "    Result += generatedSourceCompact(Split.first);",
            "    Tokens = Split.second;",
            "  }",
            "  return Result;",
            "}",
            "",
            "inline std::string markerForGeneratedSourceText(llvm::StringRef Text) {",
            "  const std::string CompactTextStorage = generatedSourceCompact(Text);",
            "  const llvm::StringRef CompactText(CompactTextStorage);",
            "  for (const SourceMarkerPattern &Entry : kSourceMarkerPatterns) {",
            "    const std::string CompactPatternStorage =",
            "        generatedSourceCompact(Entry.pattern);",
            "    const std::string CompactRequiredStorage =",
            "        generatedSourceCompactTokens(Entry.requiredTokens);",
            "    const std::string CompactForbiddenStorage =",
            "        generatedSourceCompactTokens(Entry.forbiddenTokens);",
            "    if ((Text.contains(Entry.pattern) ||",
            "         (!CompactPatternStorage.empty() &&",
            "          CompactText.contains(CompactPatternStorage))) &&",
            "        (generatedSourceTokensAllPresent(Text, Entry.requiredTokens) ||",
            "         generatedSourceTokensAllPresent(CompactText, CompactRequiredStorage)) &&",
            "        !generatedSourceTokensAnyPresent(Text, Entry.forbiddenTokens) &&",
            "        !generatedSourceTokensAnyPresent(CompactText, CompactForbiddenStorage)) {",
            "      return Entry.marker;",
            "    }",
            "  }",
            "  return \"\";",
            "}",
            "",
            "} // namespace cv",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--idioms", type=Path, default=DEFAULT_IDIOMS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rendered = generate(source_pattern_entries(args.registry, args.idioms))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
