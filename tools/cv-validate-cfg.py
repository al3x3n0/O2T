#!/usr/bin/env python3
"""Validate SimplifyCFG diamond->select if-conversion against REAL `opt` output.

Runs `opt -passes=simplifycfg` on a diamond .ll and proves each resulting `select` is
value-equivalent to the source merge-phi for all inputs (Z3). `--mutate` swaps a select's
operands to confirm a miscompiled if-conversion would be REFUTED (teeth). Verdicts per
function: proved | refuted | unsupported.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import cfg_shape as cfg  # noqa: E402

DEFAULT_LL = ROOT / "tests" / "fixtures" / "cfg_diamond.ll"
_FN_RE = re.compile(r"define\b[^@]*@(\w+)\s*\(")


def _mutate(opt_text):
    """Swap the operands of the FIRST select -- simulate a miscompiled if-conversion."""
    return re.sub(r"(select i1 [^,]+,\s*\w+\s+)(\S+)(,\s*\w+\s+)(\S+)",
                  r"\1\4\3\2", opt_text, count=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_LL)
    ap.add_argument("--passes", default="simplifycfg")        # accepted for orchestrator symmetry
    ap.add_argument("--mutate", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    opt = shutil.which(args.opt_bin) or _fallback("opt")
    if z3 is None or opt is None:
        out = {"status": "skipped", "reason": "z3 or opt not found"}
        print(json.dumps(out))
        return 0

    src = args.source.read_text()
    opt_text = cfg.run_simplifycfg(src, opt) or ""
    if args.mutate:
        opt_text = _mutate(opt_text)
    results = [cfg.validate_simplifycfg(z3, opt_text, src, m.group(1))
               for m in _FN_RE.finditer(src)]
    proved = [r for r in results if r["status"] == "proved"]
    refuted = [r for r in results if r["status"] == "refuted"]
    # Soundness: with no mutation every if-conversion must prove; with --mutate at least one
    # must be refuted (the teeth).
    ok = (bool(refuted) if args.mutate else (bool(proved) and not refuted))
    report = {"results": results, "proved": len(proved), "refuted": len(refuted),
              "mutated": args.mutate, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"proved": len(proved), "refuted": len(refuted), "ok": ok}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']:11}] {r.get('function')}", file=sys.stderr)
    return 0 if ok else 1


_HOMEBREW = Path("/opt/homebrew/opt/llvm@18/bin")


def _fallback(tool):
    c = _HOMEBREW / tool
    return str(c) if c.exists() else None


if __name__ == "__main__":
    sys.exit(main())
