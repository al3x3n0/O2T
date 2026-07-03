#!/usr/bin/env python3
"""Counterexample witnesses for refuted transforms -- the CEGAR half of the loop.

When prove_mined REFUTES a transform (output-not-preserved), z3 has shown the simulation
relation is not inductive, but that is an abstract verdict. This turns it into a CONCRETE,
MINIMIZED miscompiling input: a specific parameter assignment and trip count for which the
source and the (claimed) optimization compute different results.

We do not trust z3's inductive-step model directly (it may assign an UNREACHABLE pre-state).
Instead we forward-execute BOTH recurrence systems from their true initial values over a sweep
of edge/parameter values and increasing trip counts, and return the first divergence -- which is
therefore reachable and minimal in trip count. SSA phi-loops are parallel: each step evaluates
deltas against the iteration-start state.
"""

from __future__ import annotations

import itertools

WIDTH = 32
MASK = (1 << WIDTH) - 1
# Edge values first, small-first, so the returned witness has small parameters.
SWEEP = [0, 1, 2, 3, 7, 16, (1 << (WIDTH - 1)) - 1, 1 << (WIDTH - 1), MASK - 1, MASK]


def _signed(x):
    x &= MASK
    return x - (1 << WIDTH) if x >> (WIDTH - 1) else x


def _ev(node, env):
    o = node["op"]
    if o == "var":
        return env[node["name"]] & MASK
    if o == "bvconst":
        return node["value"] & MASK
    a = [_ev(x, env) for x in node["args"]]
    if o == "bvadd":
        return (a[0] + a[1]) & MASK
    if o == "bvsub":
        return (a[0] - a[1]) & MASK
    if o == "bvmul":
        return (a[0] * a[1]) & MASK
    if o == "eq":
        return 1 if a[0] == a[1] else 0
    if o == "ne":
        return 1 if a[0] != a[1] else 0
    if o == "ite":
        return a[1] if a[0] else a[2]
    raise ValueError(o)


def _run(accs, output, cvals, n):
    """Forward-execute the multi-accumulator recurrence n steps; return the output's value.
    Parallel phi semantics: every delta reads the iteration-start state."""
    state = {name: _ev(init, cvals) for name, init, _ in accs}
    for i in range(n):
        env = {**cvals, "i": i & MASK, **state}
        state = {name: (state[name] + _ev(delta, env)) & MASK for name, init, delta in accs}
    return state[output]


def find_witness(before, after, consts, max_n=12):
    """A concrete (params, trip_count) where source != optimized, minimal in trip count and with
    small parameters -- or None if no divergence within the search bounds (honest: not a proof of
    equivalence, just an inconclusive search)."""
    a_accs, a_out, _ = before
    b_accs, b_out, _ = after
    if not a_out or not b_out:
        return None
    for n in range(0, max_n + 1):                      # minimal trip count first
        for combo in itertools.product(SWEEP, repeat=len(consts)):  # small params first
            cv = dict(zip(consts, combo))
            try:
                va = _run(a_accs, a_out[0], cv, n)
                vb = _run(b_accs, b_out[0], cv, n)
            except (KeyError, ValueError):
                return None                            # unmodelled op/var -> cannot witness
            if va != vb:
                return {"params": {k: _signed(v) for k, v in cv.items()},
                        "trip_count": n,
                        "source": _signed(va), "optimized": _signed(vb)}
    return None
