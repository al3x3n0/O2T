#!/usr/bin/env python3
"""Mine a DCE-like pass's source for dead-instruction erasures and discharge them.

Reads a pass `.cpp`, recovers each instruction deletion fold, and checks whether it established a
trivially-dead guard before erasing the instruction. Guarded folds prove; bare deletion is refuted
with a witness. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.intent.extract_dce_model import verify_source  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "dce_dead_instruction_folds.cpp"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--z3-bin", default="z3")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = verify_source(z3, args.source.read_text(encoding="utf-8"))
    transforms = [result for result in results if result["status"] != "not-a-transform"]
    proved = [result for result in transforms if result["status"] == "proved"]
    refuted = [result for result in transforms if result["status"] == "refuted"]
    ok = bool(transforms) and all(result["status"] in ("proved", "refuted") for result in transforms) \
        and all(result.get("witness") for result in refuted)
    report = {
        "results": results,
        "transforms": len(transforms),
        "proved": len(proved),
        "refuted": len(refuted),
        "ok": ok,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"transforms": len(transforms), "proved": len(proved),
                      "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for result in results:
        if result["status"] != "not-a-transform":
            print(f"  [{result['status']:8}] {result['function']} ({result.get('reason', '')})", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
