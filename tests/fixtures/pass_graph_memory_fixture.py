#!/usr/bin/env python3
"""Memory obligations over the theory of arrays: load / store / aliasing.

O2T's scalar fragment cannot talk about memory. This adds memory-typed variables and load/store nodes
(backed by SMT arrays: mem_load -> select, mem_store -> store), so the canonical memory optimizations
become provable obligations gated by ALIASING preconditions -- must-alias (P == Q) and no-alias
(P != Q). Discharged by the same prover, cross-checked by the same independent second solver, and
diagnosed by the same abduction as scalar folds.

Pins: (1) store-to-load forwarding and must-alias forwarding prove; (2) a load past a store to a
distinct address forwards under no-alias, and is unsound without it; (3) dead-store elimination proves
as an observation (any later load agrees); (4) a stale-load rewrite refutes with a witness; (5) an
independent solver (bitwuzla) agrees on the array obligations; and (6) abduction synthesizes the
missing aliasing guard for the unguarded no-alias load.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg
from o2t import mini_alive as ma


def V(name):
    return {"op": "var", "name": name}


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_memory_fixture: z3 not found, skipped")
        return 0

    M = pg.memvar("m")
    P, Q, A, v, w = V("p"), V("q"), V("a"), V("v"), V("w")
    ld, st = pg.memload, pg.memstore

    def prove(fold):
        return ma.prove(fold, z3)

    # 1. STORE-TO-LOAD FORWARDING: reading the just-stored address returns the stored value.
    fwd = pg.memory_fold(ld(st(M, P, v), P), v, ["m", "p", "v"])
    assert prove(fwd)[0] == "proved", "store-to-load forwarding"
    # a stale-load rewrite (return the OLD memory's value) refutes with a concrete witness.
    stale = pg.memory_fold(ld(st(M, P, v), P), ld(M, P), ["m", "p", "v"])
    status, cex = prove(stale)
    assert status == "refuted" and cex, ("stale load must refute", status)

    # 2. MUST-ALIAS forwarding: forward through a DIFFERENT pointer name that must-aliases.
    ma_fwd = pg.memory_fold(ld(st(M, P, v), Q), v, ["m", "p", "q", "v"], assumptions=[pg.must_alias("p", "q")])
    assert prove(ma_fwd)[0] == "proved", "must-alias forwarding"

    # 3. NO-ALIAS load past a store: a load at a distinct address is unaffected by the store -- but ONLY
    #    under no-alias; unguarded it is unsound (Q may equal P).
    noa = pg.memory_fold(ld(st(M, P, v), Q), ld(M, Q), ["m", "p", "q", "v"], assumptions=[pg.no_alias("p", "q")])
    assert prove(noa)[0] == "proved", "no-alias load past store"
    unguarded = pg.memory_fold(ld(st(M, P, v), Q), ld(M, Q), ["m", "p", "q", "v"])
    assert prove(unguarded)[0] == "refuted", "unguarded no-alias load must refute"

    # 4. DEAD-STORE elimination, expressed as an observation: after `store P,v; store P,w`, any later
    #    load agrees with just `store P,w` -- the first store is dead.
    dse = pg.memory_fold(ld(st(st(M, P, v), P, w), A), ld(st(M, P, w), A), ["m", "p", "v", "w", "a"])
    assert prove(dse)[0] == "proved", "dead-store elimination (observed)"

    # 5. SECOND-SOLVER: an independent solver (bitwuzla) agrees on the ARRAY obligations, not just the
    #    scalar ones -- the cross-check carries over to the theory of arrays.
    if shutil.which("bitwuzla"):
        assert pg.reconcile_solver(fwd, z3)["agree"], "bitwuzla must agree on store-forwarding"
        assert pg.reconcile_solver(dse, z3)["agree"], "bitwuzla must agree on dead-store"

    # 6. ABDUCTION over aliasing: diagnosing the unguarded no-alias load synthesizes the missing
    #    isNoAlias(p, q) precondition -- precondition synthesis extends to memory aliasing guards.
    d = pg.diagnose(unguarded, z3)
    assert d["status"] == "insufficient-guard" and d["missing"] == ["isNoAlias(p, q)"], d

    print("pass_graph_memory_fixture OK: over the theory of arrays, store-to-load forwarding (plain and "
          "must-alias) and dead-store elimination prove, a no-alias load forwards only under no-alias "
          "(unguarded it refutes), a stale-load rewrite refutes with a witness, an independent solver "
          "agrees on the array obligations, and abduction synthesizes the missing isNoAlias guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
