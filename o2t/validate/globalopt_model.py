#!/usr/bin/env python3
"""Deep formal verification of GlobalOpt dead-initializer defaulting (semantic, not syntactic).

GlobalOpt rewrites an internal global's initializer to null/zero when that initializer is
"dead". The existing O2T contract checks this SYNTACTICALLY (the IR diff touches only the
initializer line). This proves the SEMANTIC obligation: defaulting the initializer preserves
ALL observable behavior.

The single observable quantity is what a load of the global can return (plus, for a
non-internal global, the initial value an EXTERNAL observer can read directly). Model the global
as one cell, run the program's accesses from two initial states -- `init` (before) and `0`
(after) -- and require every observed value to agree:

  * internal global, never loaded (`use_empty`)            -> initializer unobservable -> proved
  * internal global, every load PRECEDED by a store        -> loads see the stored value -> proved
  * internal global, a load can observe the initializer    -> before sees `init`, after sees `0`
    (read-before-store)                                       -> REFUTED with witness `init != 0`
  * non-internal global (external linkage)                 -> an external reader observes the
    initializer directly                                      -> REFUTED with witness `init != 0`

This is the real soundness condition GlobalOpt's `hasLocalLinkage() && use_empty()` guard
establishes; a fold that defaults the initializer without it is unsound, refuted here with a
concrete counterexample. Two-sided teeth over QF_BV.
"""

from __future__ import annotations

import subprocess

BV = "(_ BitVec 32)"
ZERO = "#x00000000"


def _check(z3_bin, decls, goal_negation):
    smt = "\n".join(["(set-logic QF_BV)", *decls,
                     f"(assert (not {goal_negation}))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"model": out}
    return "error", {"reason": head}


def prove_initializer_default(z3_bin, accesses, external=False, width=32):
    """Prove defaulting a global's initializer to 0 preserves every observable load.

    `accesses` is a straight-line op list over the global: ("store", value_name) | ("load",).
    `external` marks non-internal linkage (the initializer is directly observable). `width` is the
    global's bit width (the observability argument holds at every width). Returns (status, info):
    proved iff no observation can distinguish initial value `init` from `0`."""
    logic, decls, premises, goal = initializer_obligation(accesses, external, width)
    return _check(z3_bin, decls, goal)


def initializer_obligation(accesses, external=False, width=32):
    """The dead-initializer obligation as (logic, decls, premises, goal) -- single source for the
    prover and the cross-solver/witness re-validation."""
    bv = f"(_ BitVec {width})"
    zero = "#x" + "0" * (width // 4)
    decls = [f"(declare-const init {bv})"]
    before, after = "init", zero
    pairs = []                                   # (observed_before, observed_after)
    if external:
        pairs.append((before, after))            # external code reads the static initializer
    store_n = 0
    for op in accesses:
        if op[0] == "store":
            store_n += 1
            v = f"sv{store_n}"
            decls.append(f"(declare-const {v} {bv})")
            before = after = v                   # both states now hold the stored value
        elif op[0] == "load":
            pairs.append((before, after))        # this load observes the current cell
        else:
            raise ValueError(f"unknown access {op!r}")
    if not pairs:
        goal = "true"                            # nothing observable -> trivially preserved
    else:
        goal = "(and " + " ".join(f"(= {b} {a})" for b, a in pairs) + ")"
    return "QF_BV", decls, [], goal


# Canonical contracts: sound defaultings prove; unobservability-violating ones refute (teeth).
GLOBALOPT_CONTRACTS = {
    # internal global with no uses (the real `hasLocalLinkage() && use_empty()` case).
    "default-internal-use-empty": dict(accesses=[], external=False, expect="proved"),
    # internal global whose only load happens after a store -> load sees the stored value.
    "default-stored-before-read": dict(accesses=[("store", None), ("load",)],
                                       external=False, expect="proved"),
    # TEETH: a load observes the initializer before any store -> defaulting changes it.
    "default-read-before-store": dict(accesses=[("load",), ("store", None)],
                                      external=False, expect="refuted"),
    # TEETH: a read-only internal global -> the initializer is observed directly.
    "default-read-only": dict(accesses=[("load",)], external=False, expect="refuted"),
    # TEETH: external linkage -> an outside reader observes the initializer regardless of uses.
    "default-external-linkage": dict(accesses=[], external=True, expect="refuted"),
}


def run_contracts(z3_bin, contracts=GLOBALOPT_CONTRACTS):
    """Discharge every contract; return {name: {status, expect, ok, witness}}."""
    results = {}
    for name, c in contracts.items():
        status, info = prove_initializer_default(z3_bin, c["accesses"], c["external"])
        results[name] = {"status": status, "expect": c["expect"],
                         "ok": status == c["expect"], "witness": bool(info.get("model"))}
    return results
