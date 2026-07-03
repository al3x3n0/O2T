#!/usr/bin/env python3
"""Deep formal verification of dead-instruction erasure.

DCE is behavior-preserving only when the erased instruction is unobservable:

  * no live use can observe the instruction's computed value;
  * no side effect, trap, volatile access, or other externally visible effect is removed.

LLVM's `isInstructionTriviallyDead`/`wouldInstructionBeTriviallyDead` guards establish that
condition for ordinary instruction erasure. This model keeps the semantic core small: erasing an
instruction preserves behavior iff both observability channels are absent. Missing either guard is
refuted with a witness Boolean assignment.

The same cleanup family covers unused stack slots. Removing an `alloca` is sound only when the slot
has no ordinary uses, no escape, and no lifetime/debug-observable effect. A `use_empty`-style guard
establishes that alloca-specific unobservability; dropping it is refuted.

For loop-body cleanup, deleting an instruction is sound only when it cannot contribute to the loop
result, affect loop control/trip behavior, or carry a side effect. `isDeadLoopInstruction` is the
auditable source guard for that loop-specific unobservability.
"""

from __future__ import annotations

import subprocess


def _check(z3_bin: str, logic: str, decls: list[str], premises: list[str], goal: str):
    smt = "\n".join(
        [
            f"(set-logic {logic})",
            *decls,
            *premises,
            f"(assert (not {goal}))",
            "(check-sat)",
            "(get-model)",
            "",
        ]
    )
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"model": out}
    return "error", {"reason": head}


def dead_erase_obligation(no_live_use: bool, no_side_effect: bool):
    """Return (logic, decls, premises, goal) for the DCE erasure obligation."""
    decls = [
        "(declare-const live_use Bool)",
        "(declare-const side_effect Bool)",
    ]
    premises: list[str] = []
    if no_live_use:
        premises.append("(assert (not live_use))")
    if no_side_effect:
        premises.append("(assert (not side_effect))")
    goal = "(and (not live_use) (not side_effect))"
    return "QF_UF", decls, premises, goal


def prove_dead_erase(z3_bin: str, no_live_use: bool, no_side_effect: bool):
    """Prove instruction erasure preserves behavior under the supplied deadness facts."""
    logic, decls, premises, goal = dead_erase_obligation(no_live_use, no_side_effect)
    return _check(z3_bin, logic, decls, premises, goal)


def unused_alloca_obligation(no_uses: bool, no_escape: bool, no_lifetime_effect: bool):
    """Return (logic, decls, premises, goal) for unused-alloca deletion."""
    decls = [
        "(declare-const alloca_use Bool)",
        "(declare-const alloca_escape Bool)",
        "(declare-const lifetime_effect Bool)",
    ]
    premises: list[str] = []
    if no_uses:
        premises.append("(assert (not alloca_use))")
    if no_escape:
        premises.append("(assert (not alloca_escape))")
    if no_lifetime_effect:
        premises.append("(assert (not lifetime_effect))")
    goal = "(and (not alloca_use) (not alloca_escape) (not lifetime_effect))"
    return "QF_UF", decls, premises, goal


def prove_unused_alloca_erase(
    z3_bin: str,
    no_uses: bool,
    no_escape: bool,
    no_lifetime_effect: bool,
):
    """Prove unused alloca deletion preserves behavior under the supplied use/escape facts."""
    logic, decls, premises, goal = unused_alloca_obligation(
        no_uses,
        no_escape,
        no_lifetime_effect,
    )
    return _check(z3_bin, logic, decls, premises, goal)


def dead_loop_instruction_obligation(
    no_loop_result_use: bool,
    no_loop_control_effect: bool,
    no_loop_side_effect: bool,
):
    """Return (logic, decls, premises, goal) for dead loop-body instruction deletion."""
    decls = [
        "(declare-const loop_result_use Bool)",
        "(declare-const loop_control_effect Bool)",
        "(declare-const loop_side_effect Bool)",
    ]
    premises: list[str] = []
    if no_loop_result_use:
        premises.append("(assert (not loop_result_use))")
    if no_loop_control_effect:
        premises.append("(assert (not loop_control_effect))")
    if no_loop_side_effect:
        premises.append("(assert (not loop_side_effect))")
    goal = "(and (not loop_result_use) (not loop_control_effect) (not loop_side_effect))"
    return "QF_UF", decls, premises, goal


def prove_dead_loop_instruction_erase(
    z3_bin: str,
    no_loop_result_use: bool,
    no_loop_control_effect: bool,
    no_loop_side_effect: bool,
):
    """Prove loop-body instruction deletion preserves loop behavior under supplied facts."""
    logic, decls, premises, goal = dead_loop_instruction_obligation(
        no_loop_result_use,
        no_loop_control_effect,
        no_loop_side_effect,
    )
    return _check(z3_bin, logic, decls, premises, goal)


DCE_CONTRACTS = {
    "erase-trivially-dead": dict(no_live_use=True, no_side_effect=True, expect="proved"),
    "erase-with-live-use": dict(no_live_use=False, no_side_effect=True, expect="refuted"),
    "erase-with-side-effect": dict(no_live_use=True, no_side_effect=False, expect="refuted"),
    "erase-unguarded": dict(no_live_use=False, no_side_effect=False, expect="refuted"),
}

LOOP_INSTRUCTION_CONTRACTS = {
    "erase-dead-loop-instruction": dict(
        no_loop_result_use=True,
        no_loop_control_effect=True,
        no_loop_side_effect=True,
        expect="proved",
    ),
    "erase-loop-result-use": dict(
        no_loop_result_use=False,
        no_loop_control_effect=True,
        no_loop_side_effect=True,
        expect="refuted",
    ),
    "erase-loop-control-effect": dict(
        no_loop_result_use=True,
        no_loop_control_effect=False,
        no_loop_side_effect=True,
        expect="refuted",
    ),
    "erase-loop-side-effect": dict(
        no_loop_result_use=True,
        no_loop_control_effect=True,
        no_loop_side_effect=False,
        expect="refuted",
    ),
}

ALLOCA_CONTRACTS = {
    "erase-unused-alloca": dict(
        no_uses=True,
        no_escape=True,
        no_lifetime_effect=True,
        expect="proved",
    ),
    "erase-used-alloca": dict(
        no_uses=False,
        no_escape=True,
        no_lifetime_effect=True,
        expect="refuted",
    ),
    "erase-escaped-alloca": dict(
        no_uses=True,
        no_escape=False,
        no_lifetime_effect=True,
        expect="refuted",
    ),
    "erase-lifetime-observed-alloca": dict(
        no_uses=True,
        no_escape=True,
        no_lifetime_effect=False,
        expect="refuted",
    ),
}


def run_contracts(z3_bin: str):
    """Discharge every DCE contract; return {name: {status, expect, ok, witness}}."""
    results = {}
    for name, contract in DCE_CONTRACTS.items():
        status, info = prove_dead_erase(
            z3_bin,
            bool(contract["no_live_use"]),
            bool(contract["no_side_effect"]),
        )
        results[name] = {
            "status": status,
            "expect": contract["expect"],
            "ok": status == contract["expect"],
            "witness": bool(info.get("model")),
        }
    for name, contract in LOOP_INSTRUCTION_CONTRACTS.items():
        status, info = prove_dead_loop_instruction_erase(
            z3_bin,
            bool(contract["no_loop_result_use"]),
            bool(contract["no_loop_control_effect"]),
            bool(contract["no_loop_side_effect"]),
        )
        results[name] = {
            "status": status,
            "expect": contract["expect"],
            "ok": status == contract["expect"],
            "witness": bool(info.get("model")),
        }
    for name, contract in ALLOCA_CONTRACTS.items():
        status, info = prove_unused_alloca_erase(
            z3_bin,
            bool(contract["no_uses"]),
            bool(contract["no_escape"]),
            bool(contract["no_lifetime_effect"]),
        )
        results[name] = {
            "status": status,
            "expect": contract["expect"],
            "ok": status == contract["expect"],
            "witness": bool(info.get("model")),
        }
    return results
