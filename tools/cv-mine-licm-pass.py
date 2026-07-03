#!/usr/bin/env python3
"""Mine a LICM-like pass's source for hoist folds and discharge them (deep loop-structural model).

Reads a pass `.cpp`, recovers each fold that hoists an instruction out of a loop and the legality
it establishes (`isLoopInvariant` -> no stale value; `isSafeToSpeculativelyExecute` -> no trap;
`isGuaranteedToExecute` -> already ran each trip). A fold that hoists on loop-invariance alone --
no speculation/guaranteed-execution check -- is REFUTED from its source with a witness. Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.intent.extract_loop_structural_model import verify_source  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "licm_hoist_folds.cpp"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = verify_source(z3, args.source.read_text())
    transforms = [r for r in results if r["status"] != "not-a-transform"]
    proved = [r for r in transforms if r["status"] == "proved"]
    refuted = [r for r in transforms if r["status"] == "refuted"]
    ok = bool(transforms) and all(r["status"] in ("proved", "refuted") for r in transforms) \
        and all(r.get("witness") for r in refuted)
    report = {"results": results, "transforms": len(transforms),
              "proved": len(proved), "refuted": len(refuted), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"transforms": len(transforms), "proved": len(proved),
                      "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for r in results:
        if r["status"] != "not-a-transform":
            print(f"  [{r['status']:8}] {r['function']} ({r.get('reason', '')})", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
