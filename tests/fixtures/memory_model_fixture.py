#!/usr/bin/env python3
"""Cover the theory-of-arrays memory verifier (memory_model.py) -- the deep DSE tier.

Asserts each canonical memory transform (DSE overwrite, store-forwarding, forwarding/redundant
load across a no-alias store) PROVES for all memories/addresses/values in QF_ABV under its
no-alias side-condition, and is REFUTED with a concrete colliding-address witness when the
side-condition is DROPPED -- two-sided teeth showing the aliasing fact is load-bearing.
Needs z3 only."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import memory_model as mm


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("memory_model_fixture: z3 not found, skipped")
        return 0

    by_id = {c[0]: c for c in mm.CONTRACTS}
    assert {"dse-overwrite", "store-forward", "store-forward-across-noalias",
            "redundant-load-across-noalias"} <= set(by_id), set(by_id)

    # 1) every contract PROVES under its side-conditions (all memories/addresses/values).
    for cid, before, after, obs, conds in mm.CONTRACTS:
        status, _ = mm.prove_memory_transform(z3, before, after, obs, conds)
        assert status == "proved", (cid, status)

    # 2) the array overwrite axiom directly: store then store same addr == single store.
    s, _ = mm.prove_memory_transform(z3, [mm._store("p", "a"), mm._store("p", "b")],
                                     [mm._store("p", "b")], "memory", ())
    assert s == "proved"

    # 3) TEETH: dropping a no-alias side-condition REFUTES with a concrete witness where the
    #    addresses collide (q == p) and the observed value diverges.
    for cid in ("dse-overwrite", "store-forward-across-noalias", "redundant-load-across-noalias"):
        _, before, after, obs, conds = by_id[cid]
        assert conds, cid
        status, info = mm.prove_memory_transform(z3, before, after, obs, ())
        assert status == "refuted" and info.get("witness"), ("teeth failed", cid, status, info)

    # 4) a GENUINELY unsound transform (forward the WRONG value) is refuted even WITH the fact.
    bad = mm.prove_memory_transform(
        z3, [mm._store("p", "v"), mm._load("r", "p")],
        [mm._store("p", "v"), mm._bind("r", "w")], "load:r", ())   # claims r==w, but r==v
    assert bad[0] == "refuted" and bad[1].get("witness"), bad

    # 4b) BYTE-LEVEL overwrite: a FULL overwrite (kill range covers the dead store) PROVES; a
    #     PARTIAL overwrite (a dead byte survives) is REFUTED with a witness.
    for cid, ds, ko, ks in mm.BYTE_CONTRACTS:
        before, after = mm.byte_dse_case(ds, ko, ks)
        status, info = mm.prove_byte_transform(z3, before, after)
        expected = "proved" if mm.overwrite_covers(ds, ko, ks) else "refuted"
        assert status == expected, (cid, status, expected)
        if expected == "refuted":
            assert info.get("witness"), ("partial overwrite needs a surviving-byte witness", cid)
    # the distinction is real: same dead/kill base, kill 2 bytes shorter -> the tail survives.
    bshort = mm.byte_dse_case(4, 0, 2)
    assert mm.prove_byte_transform(z3, *bshort)[0] == "refuted", "partial overwrite must refute"
    bfull = mm.byte_dse_case(4, 0, 4)
    assert mm.prove_byte_transform(z3, *bfull)[0] == "proved", "full overwrite must prove"

    # 4c) CFG-SHAPED (path-sensitive): DSE across a diamond proves only when EVERY path
    #     overwrites the dead store; a one-path overwrite is refuted (it survives elsewhere).
    #     Store sinking (conditional stores -> one select-valued store) proves.
    for cid, before, after, obs, conds, expect_sound in mm.CFG_CONTRACTS:
        status, info = mm.prove_cfg_transform(z3, before, after, obs, conds)
        expected = "proved" if expect_sound else "refuted"
        assert status == expected, (cid, status, expected)
        if expected == "refuted":
            assert info.get("witness"), ("one-path DSE needs a surviving-store witness", cid)
    # the all-paths vs one-path distinction is real and decided by control flow.
    p = mm._store("p", "v0")
    allpaths = ([p, mm.branch("c", [mm._store("p", "v1")], [mm._store("p", "v2")])],
                [mm.branch("c", [mm._store("p", "v1")], [mm._store("p", "v2")])])
    assert mm.prove_cfg_transform(z3, *allpaths)[0] == "proved", "all-paths overwrite must prove"
    onepath = ([p, mm.branch("c", [mm._store("p", "v1")], [mm._store("q", "v2")])],
               [mm.branch("c", [mm._store("p", "v1")], [mm._store("q", "v2")])])
    assert mm.prove_cfg_transform(z3, *onepath, "memory", ({"op": "ne", "args": ["p", "q"]},))[0] \
        == "refuted", "one-path overwrite must refute"

    # 4d) ATOMICS/ORDERING: a transform must preserve the observable sync-snapshot sequence.
    #     Non-atomic DSE proves; eliminating an ATOMIC store is refuted (a sync event vanishes);
    #     reordering across a barrier is refuted; reordering non-aliasing non-atomics proves.
    for cid, before, after, conds, expect_sound in mm.ORDERING_CONTRACTS:
        status, info = mm.prove_ordering_transform(z3, before, after, conds)
        expected = "proved" if expect_sound else "refuted"
        assert status == expected, (cid, status, expected)
    # eliminating an atomic store must be caught structurally (its observable event is lost).
    elim_atomic = mm.prove_ordering_transform(
        z3, [mm.store_sync("p", "v0"), mm._store("p", "v1")], [mm._store("p", "v1")])
    assert elim_atomic[0] == "refuted" and elim_atomic[1].get("reason"), elim_atomic
    # but the SAME shape with a non-atomic store IS eliminable.
    elim_plain = mm.prove_ordering_transform(
        z3, [mm._store("p", "v0"), mm._store("p", "v1")], [mm._store("p", "v1")])
    assert elim_plain[0] == "proved", elim_plain

    # 5) the CLI runs all contracts + teeth clean.
    tool = ROOT / "tools" / "cv-validate-memory.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout

    print("memory_model_fixture OK: word/byte/CFG/atomics memory contracts proved; alias, "
          "partial-overwrite, one-path, atomic-elimination, and reorder-across-barrier all refuted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
