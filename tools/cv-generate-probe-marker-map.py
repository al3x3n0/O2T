#!/usr/bin/env python3
"""Generate C++ probe marker metadata from marker_config_map.json."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKERS = ROOT / "constraints" / "marker_config_map.json"
DEFAULT_OUT = ROOT / "include" / "o2t" / "GeneratedProbeMarkerMap.h"

ACRONYMS = {
    "abs": "Abs",
    "cfg": "cfg",
    "dce": "dce",
    "dse": "dse",
    "licm": "licm",
    "mem2reg": "mem2reg",
    "smax": "SMax",
    "smin": "SMin",
    "umax": "UMax",
    "umin": "UMin",
}

SPECIAL_WORDS = {
    "allones": "AllOnes",
}

GROUP_PREFIX = {
    "globalopt": "global",
}


def cpp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def field_part(part: str, first: bool) -> str:
    if first:
        return GROUP_PREFIX.get(part, part)
    if part in SPECIAL_WORDS:
        return SPECIAL_WORDS[part]
    if part in ACRONYMS:
        return ACRONYMS[part]
    return part[:1].upper() + part[1:]


def coverage_field(marker: str) -> str:
    payload = marker.removeprefix("probe.")
    group, _, name = payload.partition(".")
    prefix = GROUP_PREFIX.get(group, group)
    parts = [prefix, *[part for part in re.split(r"[.-]", name) if part]]
    return "".join(field_part(part, index == 0) for index, part in enumerate(parts))


def load_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    entries = [entry for entry in data if isinstance(entry, dict) and isinstance(entry.get("marker"), str)]
    if len({entry["marker"] for entry in entries}) != len(entries):
        raise ValueError(f"{path} contains duplicate marker entries")
    return entries


def generate(entries: list[dict[str, Any]]) -> str:
    lines = [
        "#pragma once",
        "",
        '#include "o2t/PassProbes.h"',
        "",
        "#include <array>",
        "#include <cstdint>",
        "",
        "namespace cv {",
        "",
        "struct ProbeMarkerMetadata {",
        "  const char *marker;",
        "  const char *group;",
        "  const char *configPatchJson;",
        "  bool PassProbeCoverage::*coverage;",
        "};",
        "",
        "inline constexpr std::array<ProbeMarkerMetadata, " + str(len(entries)) + ">",
        "    kProbeMarkerMetadata{{",
    ]
    for entry in entries:
        marker = str(entry["marker"])
        group = str(entry.get("group") or "")
        config = entry.get("config")
        if not isinstance(config, dict):
            raise ValueError(f"marker {marker} must have a config object")
        if any(not isinstance(value, int) for value in config.values()):
            raise ValueError(f"marker {marker} config values must be integers")
        patch_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
        field = coverage_field(marker)
        lines.append(
            "        {"
            + cpp_string(marker)
            + ", "
            + cpp_string(group)
            + ", "
            + cpp_string(patch_json)
            + ", &PassProbeCoverage::"
            + field
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
    parser.add_argument("--markers", type=Path, default=DEFAULT_MARKERS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rendered = generate(load_entries(args.markers))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
