#!/usr/bin/env python3
"""Formal memory-transform verification over a THEORY OF ARRAYS (the deep DSE tier).

The DSE / store-forwarding / redundant-load family was previously checked only through the
coarse source-intent pipeline (proved/unsupported). This module discharges those transforms
*deeply*: it models memory as an SMT array `Mem : Addr -> Word`, symbolically executes a
BEFORE and an AFTER straight-line op sequence from the SAME initial memory, and proves the
OBSERVABLE (a returned load value, or the final memory) equal for ALL memories, addresses,
and values -- in QF_ABV, which Z3 decides exactly (no bounded sweep).

The interesting content is ALIASING: a store/load transform is sound only under a no-alias
side-condition (`q != p`). The verifier proves it sound UNDER that assumption and REFUTES it
WITHOUT -- emitting a concrete witness memory state where the addresses collide. So the
side-condition is shown load-bearing, the same two-sided teeth the rest of O2T carries, now
for real read/write memory rather than uninterpreted loads.

Addresses and words are 32-bit; memory is word-addressed `(Array (_ BitVec 32) (_ BitVec 32))`.
Byte-level overlap is a future refinement (see the partial-overwrite note in the DSE ledger).
"""

from __future__ import annotations

import re
import subprocess

ADDR, WORD = "(_ BitVec 32)", "(_ BitVec 32)"


class _Sym:
    """Collects the symbolic constants an op sequence references (addresses, values)."""

    def __init__(self):
        self.addrs: set[str] = set()
        self.vals: set[str] = set()


def _collect(ops, sym: _Sym):
    bound = {o["name"] for o in ops if o["op"] in ("load", "bind")}
    for o in ops:
        if "addr" in o:
            sym.addrs.add(o["addr"])
        if o["op"] == "store":
            sym.vals.add(o["val"])
        if o["op"] == "bind" and o["src"] not in bound:
            sym.vals.add(o["src"])           # a forwarded literal value (not a prior load name)


def _exec(ops, mem0: str):
    """Symbolically execute a straight-line op sequence from SMT array term `mem0`.
    `store`/`load` thread the array; `bind name src` records `name := <src's term>` where src
    is a prior load name (the forwarded load) or a value symbol (the forwarded constant).
    Returns (final_mem_term, {bound_name: value_term})."""
    mem = mem0
    loads: dict[str, str] = {}
    for o in ops:
        if o["op"] == "store":
            mem = f"(store {mem} {o['addr']} {o['val']})"
        elif o["op"] == "load":
            loads[o["name"]] = f"(select {mem} {o['addr']})"
        elif o["op"] == "bind":
            loads[o["name"]] = loads.get(o["src"], o["src"])   # forwarded load term, or a value
        else:
            raise ValueError(f"unknown memory op {o['op']}")
    return mem, loads


_REL = {"eq": "=", "ne": "(distinct"}


def _assumption_smt(a: dict) -> str:
    x, y = a["args"]
    if a["op"] == "eq":
        return f"(= {x} {y})"
    if a["op"] == "ne":
        return f"(distinct {x} {y})"
    raise ValueError(f"unknown assumption {a['op']}")


def prove_memory_transform(z3_bin, before, after, observable="memory", assumptions=(), width=32):
    """Prove BEFORE and AFTER agree on `observable` for all memories/addresses/values, under
    `assumptions` (alias facts). `observable` is "memory" (final store state, via array
    extensionality) or "load:<name>" (a bound load value). `width` is the address/word bit width
    (the theory-of-arrays argument holds at every width). Returns (status, info) with status
    in {proved, refuted, error}; a refutation carries a concrete Z3 model."""
    built = transform_obligation(before, after, observable, assumptions, width)
    if built[0] == "error":
        return "error", {"reason": built[1]}
    _logic, decls, asserts, goal = built
    smt = "\n".join(["(set-logic QF_ABV)", *decls, *asserts,
                     f"(assert (not {goal}))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"witness": _parse_model(out)}
    return "error", {"reason": head}


def transform_obligation(before, after, observable="memory", assumptions=(), width=32):
    """The memory-transform obligation as (logic, decls, premise-asserts, goal). On a bad
    observable returns ("error", reason). Single source for the prover and the cross-solver check."""
    addr = word = f"(_ BitVec {width})"
    sym = _Sym()
    _collect(before, sym)
    _collect(after, sym)
    decls = ["(declare-const Mem0 (Array " + addr + " " + word + "))"]
    decls += [f"(declare-const {a} {addr})" for a in sorted(sym.addrs)]
    decls += [f"(declare-const {v} {word})" for v in sorted(sym.vals)]
    asserts = [f"(assert {_assumption_smt(a)})" for a in assumptions]
    bmem, bloads = _exec(before, "Mem0")
    amem, aloads = _exec(after, "Mem0")
    if observable == "memory":
        b, a = bmem, amem                                  # array extensionality decides equality
    elif observable.startswith("load:"):
        name = observable.split(":", 1)[1]
        if name not in bloads or name not in aloads:
            return "error", f"load {name} not bound on both sides"
        b, a = bloads[name], aloads[name]
    else:
        return "error", f"bad observable {observable}"
    return "QF_ABV", decls, asserts, f"(= {b} {a})"


_DEF_RE = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\)\s*#x([0-9a-fA-F]+)\)")


def _parse_model(text):
    return {name: int(val, 16) for name, val in _DEF_RE.findall(text)}


# --- shared op constructors -------------------------------------------------------------
def _store(addr, val):
    return {"op": "store", "addr": addr, "val": val}


def _load(name, addr):
    return {"op": "load", "name": name, "addr": addr}


def _bind(name, src):
    return {"op": "bind", "name": name, "src": src}


# --- CFG-shaped memory flow: branches, all-paths overwrite, store sinking ---------------
def branch(cond, then_ops, else_ops):
    return {"op": "branch", "cond": cond, "then": then_ops, "else": else_ops}


def store_sel(addr, cond, val_then, val_else):
    """A store whose value is `ite(cond, val_then, val_else)` -- the result of sinking two
    conditional stores to the same address into one."""
    return {"op": "store", "addr": addr, "sel": [cond, val_then, val_else]}


def _val_term(o):
    if "sel" in o:
        c, vt, ve = o["sel"]
        return f"(ite {c} {vt} {ve})"
    return o["val"]


def _cexec(ops, mem0):
    """Execute a CFG-shaped op sequence: a `branch` forks the memory and merges with `ite`,
    so the final memory is path-sensitive. Returns (final_mem, {load: value})."""
    mem, loads = mem0, {}
    for o in ops:
        if o["op"] == "store":
            mem = f"(store {mem} {o['addr']} {_val_term(o)})"
        elif o["op"] == "branch":
            tmem, _ = _cexec(o["then"], mem)
            emem, _ = _cexec(o["else"], mem)
            mem = f"(ite {o['cond']} {tmem} {emem})"
        elif o["op"] == "load":
            loads[o["name"]] = f"(select {mem} {o['addr']})"
        elif o["op"] == "bind":
            loads[o["name"]] = loads.get(o["src"], o["src"])
    return mem, loads


def _collect_cfg(ops, addrs, vals, conds):
    bound = {o["name"] for o in ops if o["op"] in ("load", "bind")}
    for o in ops:
        if "addr" in o:
            addrs.add(o["addr"])
        if o["op"] == "store" and "sel" in o:
            conds.add(o["sel"][0])
            vals.update(o["sel"][1:])
        elif o["op"] == "store":
            vals.add(o["val"])
        elif o["op"] == "branch":
            conds.add(o["cond"])
            _collect_cfg(o["then"], addrs, vals, conds)
            _collect_cfg(o["else"], addrs, vals, conds)
        elif o["op"] == "bind" and o["src"] not in bound:
            vals.add(o["src"])


def prove_cfg_transform(z3_bin, before, after, observable="memory", assumptions=()):
    """Prove a CFG-shaped memory transform (path-sensitive) equal on `observable` for all
    memories, addresses, values, and branch conditions. Used for DSE across a diamond (a store
    dead because overwritten on EVERY path) and store sinking (conditional stores -> one
    select-valued store). Returns (status, info)."""
    addrs, vals, conds = set(), set(), set()
    _collect_cfg(before, addrs, vals, conds)
    _collect_cfg(after, addrs, vals, conds)
    decls = ["(declare-const Mem0 (Array " + ADDR + " " + WORD + "))"]
    decls += [f"(declare-const {a} {ADDR})" for a in sorted(addrs)]
    decls += [f"(declare-const {v} {WORD})" for v in sorted(vals)]
    decls += [f"(declare-const {c} Bool)" for c in sorted(conds)]
    asserts = [f"(assert {_assumption_smt(a)})" for a in assumptions]
    bmem, bloads = _cexec(before, "Mem0")
    amem, aloads = _cexec(after, "Mem0")
    if observable == "memory":
        b, a = bmem, amem
    else:
        name = observable.split(":", 1)[1]
        b, a = bloads[name], aloads[name]
    smt = "\n".join(["(set-logic ALL)", *decls, *asserts,
                     f"(assert (not (= {b} {a})))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"witness": _parse_model(out)}
    return "error", {"reason": head}


# CFG-shaped memory contracts. (id, before, after, observable, assumptions, expect-sound).
def _cfg_contracts():
    p, q = "p", "q"
    # DSE across a diamond: a store dead because BOTH paths overwrite the same address.
    dse_full = ([_store(p, "v0"), branch("c", [_store(p, "v1")], [_store(p, "v2")])],
                [branch("c", [_store(p, "v1")], [_store(p, "v2")])])
    # UNSOUND: the else path overwrites a DIFFERENT address, so the dead store survives there.
    dse_partial = ([_store(p, "v0"), branch("c", [_store(p, "v1")], [_store(q, "v2")])],
                   [branch("c", [_store(p, "v1")], [_store(q, "v2")])])
    # Store sinking: two conditional stores to the same address -> one select-valued store.
    sink = ([branch("c", [_store(p, "v1")], [_store(p, "v2")])],
            [store_sel(p, "c", "v1", "v2")])
    return [
        ("dse-across-diamond-allpaths", *dse_full, "memory", (), True),
        ("dse-across-diamond-onepath",  *dse_partial, "memory", ({"op": "ne", "args": [p, q]},), False),
        ("store-sink-conditional",      *sink, "memory", (), True),
    ]


CFG_CONTRACTS = _cfg_contracts()


# --- atomics / ordering: observable sync points -----------------------------------------
def store_sync(addr, val):
    """A synchronizing store (atomic >= monotonic, or volatile): its effect is OBSERVABLE to
    other threads at this program point, so the memory state here is part of the trace."""
    return {"op": "store", "addr": addr, "val": val, "sync": True}


def fence():
    """A fence / sync barrier: an observable point with no store of its own."""
    return {"op": "fence"}


def _oexec(ops, mem0):
    """Execute a straight-line sequence, returning (final_mem, [snapshot_terms]) where a
    snapshot is the memory array at each synchronizing op -- the inter-thread-observable points."""
    mem, snaps = mem0, []
    for o in ops:
        if o["op"] == "store":
            mem = f"(store {mem} {o['addr']} {_val_term(o)})"
            if o.get("sync"):
                snaps.append(mem)              # the store's effect is observable here
        elif o["op"] == "fence":
            snaps.append(mem)                  # the memory is observable at the barrier
    return mem, snaps


def prove_ordering_transform(z3_bin, before, after, assumptions=()):
    """Prove an atomic/ordering-aware transform sound: it must preserve BOTH the final memory
    AND the SEQUENCE of memory snapshots at synchronizing (atomic/volatile/fence) points. So an
    atomic store cannot be eliminated (a snapshot would vanish) and ops cannot be reordered
    across a barrier (the snapshot there would change) -- conservatively treating any sync op as
    a full barrier (sound; the per-ordering relaxation is a refinement). Returns (status, info)."""
    bm, bs = _oexec(before, "Mem0")
    am, as_ = _oexec(after, "Mem0")
    if len(bs) != len(as_):
        return "refuted", {"reason": "observable sync-event count changed (atomic op added/removed)"}
    addrs, vals, conds = set(), set(), set()
    _collect_cfg(before, addrs, vals, conds)
    _collect_cfg(after, addrs, vals, conds)
    decls = ["(declare-const Mem0 (Array " + ADDR + " " + WORD + "))"]
    decls += [f"(declare-const {a} {ADDR})" for a in sorted(addrs)]
    decls += [f"(declare-const {v} {WORD})" for v in sorted(vals)]
    asserts = [f"(assert {_assumption_smt(a)})" for a in assumptions]
    eqs = [f"(= {bm} {am})"] + [f"(= {x} {y})" for x, y in zip(bs, as_)]
    conj = eqs[0] if len(eqs) == 1 else "(and " + " ".join(eqs) + ")"
    smt = "\n".join(["(set-logic QF_ABV)", *decls, *asserts,
                     f"(assert (not {conj}))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"witness": _parse_model(out)}
    return "error", {"reason": head}


def _ordering_contracts():
    p, q = "p", "q"
    ne_pq = ({"op": "ne", "args": [p, q]},)
    return [
        # a NON-atomic dead store overwritten by a later store -> eliminable.
        ("dse-nonatomic-overwrite",
         [_store(p, "v0"), _store(p, "v1")], [_store(p, "v1")], (), True),
        # an ATOMIC store cannot be eliminated even if "overwritten": its sync event vanishes.
        ("dse-atomic-store-not-eliminable",
         [store_sync(p, "v0"), _store(p, "v1")], [_store(p, "v1")], (), False),
        # reordering a store across a RELEASE (sync) store changes the memory seen at the barrier.
        ("reorder-store-across-sync",
         [_store(q, "w"), store_sync(p, "v")], [store_sync(p, "v"), _store(q, "w")], ne_pq, False),
        # reordering two non-aliasing NON-atomic stores is invisible -> legal.
        ("reorder-nonatomic-nonalias",
         [_store(p, "v"), _store(q, "w")], [_store(q, "w"), _store(p, "v")], ne_pq, True),
    ]


ORDERING_CONTRACTS = _ordering_contracts()


# --- byte-level memory: partial vs full overwrite ---------------------------------------
BYTE = "(_ BitVec 8)"


def _bexec(ops, mem0):
    """Execute byte-granular stores over a byte array `Mem : (BitVec 32) -> (BitVec 8)`.
    A `bstore` writes `size` consecutive bytes (`{vals}_0 .. {vals}_{size-1}`) at `base+offset`."""
    mem = mem0
    for o in ops:
        for i in range(o["size"]):
            addr = f"(bvadd {o['base']} (_ bv{o['offset'] + i} 32))"
            mem = f"(store {mem} {addr} {o['vals']}_{i})"
    return mem


def bstore(base, offset, size, vals):
    return {"op": "bstore", "base": base, "offset": offset, "size": size, "vals": vals}


def prove_byte_transform(z3_bin, before, after, assumptions=()):
    """Prove two byte-store sequences leave the FINAL byte memory equal for all memories,
    base addresses, and byte values (QF_ABV over a byte array). Used to distinguish a sound
    FULL overwrite (the dead store's bytes are all rewritten) from an unsound PARTIAL one (some
    byte survives -> refuted with a witness)."""
    bases, vals = set(), set()
    for o in before + after:
        bases.add(o["base"])
        vals.update(f"{o['vals']}_{i}" for i in range(o["size"]))
    decls = ["(declare-const Mem0 (Array (_ BitVec 32) " + BYTE + "))"]
    decls += [f"(declare-const {b} (_ BitVec 32))" for b in sorted(bases)]
    decls += [f"(declare-const {v} {BYTE})" for v in sorted(vals)]
    asserts = [f"(assert {_assumption_smt(a)})" for a in assumptions]
    b, a = _bexec(before, "Mem0"), _bexec(after, "Mem0")
    smt = "\n".join(["(set-logic QF_ABV)", *decls, *asserts,
                     f"(assert (not (= {b} {a})))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"witness": _parse_model(out)}
    return "error", {"reason": head}


def overwrite_covers(dead_size, kill_offset, kill_size):
    """Does the killing store's byte range [kill_offset, kill_offset+kill_size) cover the dead
    store's [0, dead_size)? (Both relative to the shared base.) Full overwrite iff True."""
    return kill_offset <= 0 and kill_offset + kill_size >= dead_size


# DSE at byte granularity: `bstore Dead[0,ds); bstore Kill[off,ks)` -> drop Dead. Sound iff the
# kill range COVERS the dead range; a partial overwrite leaves a surviving byte (unsound).
BYTE_CONTRACTS = [
    ("dse-full-overwrite-same",      4, 0, 4),     # exact cover -> sound
    ("dse-full-overwrite-wider",     4, 0, 8),     # kill wider -> sound
    ("dse-partial-overwrite-short",  4, 0, 2),     # kill too short -> UNSOUND (bytes 2,3 survive)
    ("dse-partial-overwrite-offset", 4, 2, 4),     # kill shifted -> UNSOUND (bytes 0,1 survive)
]


def byte_dse_case(dead_size, kill_offset, kill_size):
    """(before, after) op sequences for removing a dead store overwritten (maybe partially) by
    a later store to the same base."""
    before = [bstore("p", 0, dead_size, "d"), bstore("p", kill_offset, kill_size, "k")]
    after = [bstore("p", kill_offset, kill_size, "k")]
    return before, after


# --- canonical memory transforms (the deep DSE/forwarding contracts) --------------------


# Each contract: (id, before, after, observable, side_conditions). The SOUND verdict requires
# the side conditions; `teeth` re-runs the SAME pair with them DROPPED, which must REFUTE --
# proving the aliasing/no-intervening-access fact is load-bearing, with a witness memory state.
CONTRACTS = [
    # Dead-store elimination: `store p,v1; store q,v2` -> drop the first. Sound iff p aliases q
    # (the second overwrites the first); without that, p keeps v1 in BEFORE but not in AFTER.
    ("dse-overwrite",
     [_store("p", "v1"), _store("q", "v2")], [_store("q", "v2")], "memory",
     ({"op": "eq", "args": ["p", "q"]},)),
    # Store-to-load forwarding: `store p,v; r=load p` -> `r := v`. Unconditional (select-store).
    ("store-forward",
     [_store("p", "v"), _load("r", "p")], [_store("p", "v"), _bind("r", "v")], "load:r", ()),
    # Forwarding ACROSS an intervening store to q: `store p,v; store q,w; r=load p` -> `r := v`.
    # Sound iff q != p (no alias); if q==p the load returns w, not v.
    ("store-forward-across-noalias",
     [_store("p", "v"), _store("q", "w"), _load("r", "p")],
     [_store("p", "v"), _store("q", "w"), _bind("r", "v")], "load:r",
     ({"op": "ne", "args": ["q", "p"]},)),
    # Redundant load elimination across an intervening store: `r1=load p; store q,w; r2=load p`
    # -> `r2 := r1`. Sound iff q != p.
    ("redundant-load-across-noalias",
     [_load("r1", "p"), _store("q", "w"), _load("r2", "p")],
     [_load("r1", "p"), _store("q", "w"), _bind("r2", "r1")], "load:r2",
     ({"op": "ne", "args": ["q", "p"]},)),
]

