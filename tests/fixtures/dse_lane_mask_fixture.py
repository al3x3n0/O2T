#!/usr/bin/env python3
"""Unit checks for shared DSE four-lane partial-overwrite masks."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sys.path.insert(0, str(args.repo / "tools"))
    import cv_analysis_facts as facts

    configs = load_module("cv_constraints_to_configs", args.repo / "tools" / "cv-constraints-to-configs.py")
    formal = load_module("cv_formal_check_config", args.repo / "tools" / "cv-formal-check-config.py")

    expected = {
        "lanes-0-of-4": (0b0001, 4),
        "lanes-2-of-4": (0b0100, 4),
        "lanes-0-1-of-4": (0b0011, 4),
        "lanes-1-3-of-4": (0b1010, 4),
        "lanes-3-1-of-4": (0b1010, 4),
        "lanes-0-1-2-of-4": (0b0111, 4),
        "lanes-0-2-3-of-4": (0b1101, 4),
        "lanes-0-of-2": (0b01, 2),
        "lanes-1-of-2": (0b10, 2),
        "lanes-0-2-of-3": (0b101, 3),
        "lanes-1-3-5-of-8": (0b00101010, 8),
    }
    for name, (bits, width) in expected.items():
        assert facts.dse_lane_mask_bits(name) == bits
        assert configs.dse_lane_mask_bits(name) == bits
        normalized = facts.dse_lane_mask_name(bits, width)
        assert normalized
        decoded_name, lanes = formal.dse_partial_mask_for_config({"const_a": bits, "const_b": width})
        assert decoded_name == normalized
        assert lanes == {lane for lane in range(width) if bits & (1 << lane)}

    for invalid in [
        "",
        "lanes--of-4",
        "lanes-0-0-of-4",
        "lanes-4-of-4",
        "lanes-0-1-2-3-of-4",
        "lanes-0-of-1",
        "lanes-0-1-of-2",
        "lanes-0-1-2-3-4-5-6-7-of-8",
    ]:
        assert facts.dse_lane_mask_bits(invalid) is None
        assert configs.dse_lane_mask_bits(invalid) is None

    assert facts.dse_lane_mask_name(0) == ""
    assert facts.dse_lane_mask_name(0xF) == ""
    assert facts.dse_lane_mask_name(0b00101010, 8) == "lanes-1-3-5-of-8"
    assert formal.dse_partial_mask_for_config({"const_a": 0}) == ("lanes-0-1-of-4", {0, 1})
    assert formal.dse_partial_mask_for_config({"const_a": 0xF}) == ("lanes-0-1-of-4", {0, 1})
    assert formal.dse_partial_mask_for_config({"const_a": 0b00101010, "const_b": 8}) == (
        "lanes-1-3-5-of-8",
        {1, 3, 5},
    )

    unsupported = configs.dse_generation_blocker(
        "probe.dse.overwritten-store",
        [{"kind": "memory.overwrite.partial.fixed-byte-mask", "byte_mask": "lanes-0-1-2-3-of-4"}],
    )
    assert unsupported == "unsupported-partial-overwrite-byte-mask"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
