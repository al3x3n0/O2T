#!/usr/bin/env python3
"""Discharge the deep DCE dead-instruction erasure contracts.

Proves that erasing a trivially-dead instruction preserves behavior, and refutes erasure when a
live use or side effect may be removed. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate.dce_model import run_contracts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--z3-bin", default="z3")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = run_contracts(z3)
    ok = all(result["ok"] for result in results.values())
    refuted = [name for name, result in results.items() if result["status"] == "refuted"]
    proved = [name for name, result in results.items() if result["status"] == "proved"]
    report = {
        "results": results,
        "contracts": len(results),
        "proved": len(proved),
        "refuted": len(refuted),
        "ok": ok,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"contracts": len(results), "proved": len(proved),
                      "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for name, result in results.items():
        mark = "ok " if result["ok"] else "!! "
        print(f"  {mark}[{result['status']:8}] {name} (expect {result['expect']})", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
