#!/usr/bin/env python3
"""Deep formal verification of LICM-style loop hoisting (the loop-structural family).

Hoisting a computation out of a loop (into the preheader) is the value-changing loop-structural
transform. It is sound only under TWO legality conditions, and this module proves each with
two-sided teeth:

  * INVARIANCE -- the hoisted op's operands must be loop-invariant, else the preheader computes a
    stale value. Model the op as `a + k` where `k` is either a loop-invariant constant
    (invariant) or the loop counter `i` (variant); the hoisted copy uses the entry value of `k`.
    Invariant -> every iteration matches the hoisted value (proved); variant -> some iteration
    differs (REFUTED with witness `i != 0`).

  * SAFETY -- a potentially-trapping op (e.g. `sdiv`) may be hoisted only if it is GUARANTEED to
    execute in the original (dominates all exits / the loop runs and it is unguarded) OR is
    SPECULATABLE (proven non-trapping). The preheader runs the op unconditionally, so hoisting an
    op that the original might skip (loop runs zero times, or the op sits behind a guard)
    INTRODUCES a trap. Soundness is `hoisted-traps => original-traps`. Guaranteed or speculatable
    -> proved; trapping-and-neither -> REFUTED with a witness (trap with the original not
    executing).

This is exactly the `isLoopInvariant` + (`isSafeToSpeculativelyExecute` ||
`isGuaranteedToExecute`) guard real LICM uses; a hoist missing it is unsound, refuted here.
"""

from __future__ import annotations

import subprocess

BV = "(_ BitVec 32)"


def _check(z3_bin, logic, decls, constraints, goal_negation):
    smt = "\n".join([f"(set-logic {logic})", *decls, *constraints,
                     f"(assert {goal_negation})", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"model": out}
    return "error", {"reason": head}


def prove_hoist_invariance(z3_bin, invariant, width=32):
    """Prove hoisting preserves the computed value. `a + k`, hoisted with k's entry value (0).
    invariant -> k is a constant (proved); not invariant -> k is the counter i (refuted). Holds
    at any bit `width`."""
    logic, decls, premises, goal = hoist_invariance_obligation(invariant, width)
    return _check(z3_bin, logic, decls, premises, f"(not {goal})")


def hoist_invariance_obligation(invariant, width=32):
    """The hoist-value-preservation obligation as (logic, decls, premises, goal)."""
    bv = f"(_ BitVec {width})"
    five = "#x" + "5".rjust(width // 4, "0")
    zero = "#x" + "0" * (width // 4)
    decls = [f"(declare-const a {bv})", f"(declare-const i {bv})"]
    if invariant:
        inloop = f"(bvadd a {five})"             # loop-invariant constant operand
        hoist = f"(bvadd a {five})"
    else:
        inloop = "(bvadd a i)"                    # operand varies with the iteration
        hoist = f"(bvadd a {zero})"               # preheader copy uses the entry value (i=0)
    # soundness: in-loop value == hoisted value for the observed iteration.
    return "QF_BV", decls, [], f"(= {inloop} {hoist})"


def prove_hoist_safety(z3_bin, guaranteed, speculatable):
    """Prove hoisting introduces no new trap: hoisted-traps => original-traps. `orig_exec` is
    whether the op runs in the original; `trap` its trap condition. guaranteed -> orig_exec holds;
    speculatable -> trap cannot hold."""
    logic, decls, premises, goal = hoist_safety_obligation(guaranteed, speculatable)
    return _check(z3_bin, logic, decls, premises, f"(not {goal})")


def hoist_safety_obligation(guaranteed, speculatable):
    """The trap-safety obligation as (logic, decls, premises, goal). Premises are the established
    legality facts (guaranteed-to-execute / speculatable)."""
    decls = ["(declare-const trap Bool)", "(declare-const orig_exec Bool)"]
    premises = []
    if guaranteed:
        premises.append("(assert orig_exec)")           # op dominates exits / always executes
    if speculatable:
        premises.append("(assert (not trap))")           # op proven non-trapping
    # hoisted runs the op unconditionally (trap == hoist_trap); original traps iff it executes.
    return "QF_UF", decls, premises, "(=> trap (and orig_exec trap))"


# Canonical contracts. kind selects the prover; sound hoists prove, illegal ones refute (teeth).
LOOP_STRUCTURAL_CONTRACTS = {
    "hoist-invariant-operand": dict(kind="invariance", invariant=True, expect="proved"),
    "hoist-guaranteed-execute": dict(kind="safety", guaranteed=True, speculatable=False,
                                     expect="proved"),
    "hoist-speculatable": dict(kind="safety", guaranteed=False, speculatable=True,
                               expect="proved"),
    # TEETH: a varying operand hoisted -> the preheader value is stale.
    "hoist-variant-operand": dict(kind="invariance", invariant=False, expect="refuted"),
    # TEETH: a trapping op that is neither guaranteed nor speculatable -> a new trap.
    "hoist-trapping-not-guaranteed": dict(kind="safety", guaranteed=False, speculatable=False,
                                          expect="refuted"),
}


def run_contracts(z3_bin, contracts=LOOP_STRUCTURAL_CONTRACTS):
    """Discharge every contract; return {name: {status, expect, ok, witness}}."""
    results = {}
    for name, c in contracts.items():
        if c["kind"] == "invariance":
            status, info = prove_hoist_invariance(z3_bin, c["invariant"])
        else:
            status, info = prove_hoist_safety(z3_bin, c["guaranteed"], c["speculatable"])
        results[name] = {"status": status, "expect": c["expect"],
                         "ok": status == c["expect"], "witness": bool(info.get("model"))}
    return results
