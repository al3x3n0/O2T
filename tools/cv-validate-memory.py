#!/usr/bin/env python3
"""Validate memory transforms over a theory of arrays (deep DSE / store-forwarding tier).

Proves each canonical memory contract (DSE overwrite, store-forwarding, forwarding/redundant-
load across a no-alias store) equivalent for ALL memories/addresses/values in QF_ABV. With
`--teeth`, drops each contract's no-alias side-condition and confirms the transform is then
REFUTED with a concrete colliding-address witness -- the side-condition is load-bearing.
Needs z3 only (no opt/clang).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from o2t.validate import memory_model as mm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--teeth", action="store_true", help="drop side-conditions; expect refutation")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = []
    for cid, before, after, obs, conds in mm.CONTRACTS:
        sound, _ = mm.prove_memory_transform(z3, before, after, obs, conds)
        entry = {"contract": cid, "sound": sound, "side_conditions": len(conds)}
        if conds:
            teeth, info = mm.prove_memory_transform(z3, before, after, obs, ())
            entry["drop_conditions"] = teeth
            entry["witness"] = info.get("witness")
        results.append(entry)

    # Byte-granular DSE: a FULL overwrite proves; a PARTIAL one is refuted (a byte survives).
    byte_results = []
    for cid, ds, ko, ks in mm.BYTE_CONTRACTS:
        before, after = mm.byte_dse_case(ds, ko, ks)
        status, info = mm.prove_byte_transform(z3, before, after)
        expected = "proved" if mm.overwrite_covers(ds, ko, ks) else "refuted"
        byte_results.append({"contract": cid, "status": status, "expected": expected,
                             "as_expected": status == expected, "witness": info.get("witness")})

    # CFG-shaped (path-sensitive): DSE across a diamond (all-paths overwrite) + store sinking;
    # a one-path overwrite is refuted (the dead store survives on the other path).
    cfg_results = []
    for cid, before, after, obs, conds, expect_sound in mm.CFG_CONTRACTS:
        status, info = mm.prove_cfg_transform(z3, before, after, obs, conds)
        expected = "proved" if expect_sound else "refuted"
        cfg_results.append({"contract": cid, "status": status, "expected": expected,
                            "as_expected": status == expected, "witness": info.get("witness")})

    # Atomics/ordering: a transform must preserve the observable sync-snapshot sequence -- an
    # atomic store is not eliminable, and ops cannot be reordered across a barrier.
    ord_results = []
    for cid, before, after, conds, expect_sound in mm.ORDERING_CONTRACTS:
        status, info = mm.prove_ordering_transform(z3, before, after, conds)
        expected = "proved" if expect_sound else "refuted"
        ord_results.append({"contract": cid, "status": status, "expected": expected,
                            "as_expected": status == expected,
                            "witness": info.get("witness") or info.get("reason")})

    sound_ok = all(r["sound"] == "proved" for r in results)
    teeth_ok = all(r.get("drop_conditions") == "refuted" and r.get("witness")
                   for r in results if r["side_conditions"])
    byte_ok = all(r["as_expected"] and (r["status"] != "refuted" or r["witness"]) for r in byte_results)
    cfg_ok = all(r["as_expected"] and (r["status"] != "refuted" or r["witness"]) for r in cfg_results)
    ord_ok = all(r["as_expected"] for r in ord_results)
    ok = sound_ok and teeth_ok and byte_ok and cfg_ok and ord_ok
    report = {"results": results, "byte_results": byte_results, "cfg_results": cfg_results,
              "ordering_results": ord_results, "ok": ok, "sound_ok": sound_ok,
              "teeth_ok": teeth_ok, "byte_ok": byte_ok, "cfg_ok": cfg_ok, "ord_ok": ord_ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    total = len(results) + len(byte_results) + len(cfg_results) + len(ord_results)
    print(json.dumps({"contracts": total, "ok": ok}, sort_keys=True))
    for r in results:
        teeth = f" | drop-conds={r['drop_conditions']}" if r["side_conditions"] else ""
        print(f"  [{r['sound']:8}] {r['contract']}{teeth}", file=sys.stderr)
    for r in byte_results:
        print(f"  [{r['status']:8}] {r['contract']} (byte; expected {r['expected']})", file=sys.stderr)
    for r in cfg_results:
        print(f"  [{r['status']:8}] {r['contract']} (cfg; expected {r['expected']})", file=sys.stderr)
    for r in ord_results:
        print(f"  [{r['status']:8}] {r['contract']} (atomics; expected {r['expected']})", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
