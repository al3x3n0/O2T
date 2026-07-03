#!/usr/bin/env python3
"""Mine an SLP vectorizer's source for reduction shapes and discharge them (deep model).

Reads a vectorizer pass `.cpp`, recovers each horizontal-reduction fold's operation, whether it
is floating-point, and its fast-math/reassoc guard, then proves it: integer reductions are
sound (associative); an FP reduction is `reassoc-allowed` only if the fold checks fast-math,
otherwise REFUTED (the vector tree-reduce reassociates and changes the result). Needs z3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.intent.extract_slp_model import verify_source  # noqa: E402

DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "slp_reduction_folds.cpp"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = verify_source(z3, args.source.read_text())
    transforms = [r for r in results if r["status"] != "not-a-transform"]
    decided = {"proved", "reassoc-allowed", "refuted"}
    proved = [r for r in transforms if r["status"] in ("proved", "reassoc-allowed")]
    refuted = [r for r in transforms if r["status"] == "refuted"]
    ok = bool(transforms) and all(r["status"] in decided for r in transforms) \
        and all(r.get("witness") for r in refuted)
    report = {"results": results, "transforms": len(transforms),
              "proved": len(proved), "refuted": len(refuted), "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"transforms": len(transforms), "proved": len(proved),
                      "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for r in results:
        detail = r.get("reduction") or (f"pack {r['op']} ext={r['ext_lanes']}" if r.get("kind") == "pack" else "")
        print(f"  [{r['status']:16}] {r['function']} {detail}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
