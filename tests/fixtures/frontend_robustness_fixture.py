#!/usr/bin/env python3
"""E4: the SCEV frontend recovers the rotated/LCSSA loops the line-regex frontend cannot.

Pins the differential: over the canonical `clang -O1` shape (rotated, multi-block, LCSSA live-out)
the regex frontend recovers NONE while the SCEV frontend recovers ALL -- strict domination on the
form that matters in practice; and over a simple single-block control the regex frontend recovers,
so its rotated failures are a property of loop SHAPE, not a dead parser. Needs opt 18."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import robustness as fr  # noqa: E402


def main() -> int:
    opt = shutil.which("opt") or (str(fr._HOMEBREW_OPT) if fr._HOMEBREW_OPT.exists() else None)
    if opt is None:
        print("frontend_robustness_fixture: opt(18) not found, skipped")
        return 0

    r = fr.run(opt)

    # 1. HEADLINE: on the rotated/LCSSA benchmark the regex frontend recovers NOTHING while SCEV
    #    recovers everything -- strict domination on the real-world shape.
    rot = r["rotated"]
    assert rot["count"] >= 4, rot
    assert rot["regex_recovered"] == 0, ("regex should fail on rotated loops", rot)
    assert rot["scev_recovered"] == rot["count"], ("scev should recover all rotated loops", rot)
    assert r["strict_domination_on_rotated"], r
    # every rotated function is SCEV-recovered-where-regex-failed.
    assert len(r["scev_only_on_rotated"]) == rot["count"], r["scev_only_on_rotated"]

    # 2. CONTROL: the regex frontend DOES recover simple single-block loops -- the rotated
    #    failures are shape-specific, not a broken parser.
    assert r["regex_works_on_simple"] and r["simple"]["regex_recovered"] > 0, r["simple"]

    print(f"frontend_robustness_fixture OK: on {rot['count']} rotated/multi-block/LCSSA loops "
          f"the line-regex frontend recovers 0 while SCEV recovers all "
          f"({rot['scev_recovered']}/{rot['count']}) -- strict domination on the clang -O1 shape; "
          f"on the simple control the regex frontend recovers "
          f"{r['simple']['regex_recovered']}/{r['simple']['count']}, so the rotated failures are "
          "a property of loop shape, not a dead parser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
