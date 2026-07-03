#!/usr/bin/env python3
"""Regression fixture for ambiguous scalar identity text mining."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    return parser.parse_args()


def run_miner(repo: Path, source: Path) -> list[dict[str, object]]:
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / "cv-mine-pass-source.py"),
            "--registry",
            str(repo / "constraints" / "pass_constraints.json"),
            str(source),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"source miner returned {result.returncode}")
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    return [entry for entry in data if isinstance(entry, dict)]


def markers_on_line(findings: list[dict[str, object]], line: int) -> set[str]:
    return {str(entry.get("marker")) for entry in findings if entry.get("line") == line}


def main() -> int:
    args = parse_args()
    campaign = run_miner(args.repo, args.repo / "tests" / "fixtures" / "campaign_snippet.cpp")
    assert markers_on_line(campaign, 4) == {"probe.instcombine.add-zero"}

    scalar_more = run_miner(args.repo, args.repo / "tests" / "fixtures" / "scalar_more_ops_snippet.cpp")
    assert "probe.instcombine.sub-zero" in markers_on_line(scalar_more, 23)
    assert "probe.instcombine.or-zero" in markers_on_line(scalar_more, 30)
    assert "probe.instcombine.and-allones" in markers_on_line(scalar_more, 37)
    assert "probe.instcombine.and-self" in markers_on_line(scalar_more, 44)
    assert "probe.instcombine.add-zero" not in markers_on_line(scalar_more, 23)
    assert "probe.instcombine.add-zero" not in markers_on_line(scalar_more, 30)
    assert "probe.instcombine.and-self" not in markers_on_line(scalar_more, 37)
    assert "probe.instcombine.and-allones" not in markers_on_line(scalar_more, 44)

    # xor-self recall: the standard PatternMatch self-idiom `m_[c_]Xor(m_Value(X), m_Deferred(X))`
    # is mined as xor-self (recall), while a general two-operand xor is NOT (precision) -- the
    # m_Deferred self-indicator disambiguates. Real InstCombine writes `X ^ X` this way, not as
    # a `Op0 == Op1` pointer comparison.
    xor_self = run_miner(args.repo, args.repo / "tests" / "fixtures" / "xor_self_deferred_snippet.cpp")
    assert "probe.instcombine.xor-self" in markers_on_line(xor_self, 8), markers_on_line(xor_self, 8)
    assert "probe.instcombine.xor-self" in markers_on_line(xor_self, 14), markers_on_line(xor_self, 14)
    assert "probe.instcombine.xor-self" not in markers_on_line(xor_self, 20), \
        ("general xor falsely mined as xor-self", markers_on_line(xor_self, 20))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
