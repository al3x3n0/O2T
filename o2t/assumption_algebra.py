#!/usr/bin/env python3
"""Normalize and check formal assumptions before SMT lowering."""

from __future__ import annotations

from typing import Any


MASK32 = (1 << 32) - 1


def _key(assumption: dict[str, Any]) -> tuple[Any, ...]:
    op = assumption.get("op")
    name = assumption.get("name")
    if op == "cmp":
        return (op, name, assumption.get("predicate"), assumption.get("value"))
    if op == "known-bits":
        return (op, name, assumption.get("zero_mask", 0), assumption.get("one_mask", 0))
    if op == "not-eq":
        return (op, name, assumption.get("value"))
    if op == "power-of-two":
        return (op, name, assumption.get("nonzero"))
    if op in ("addr-diseq", "mask-pair"):
        # Symmetric two-operand facts: order-normalize so `X&Y==0` and `Y&X==0` share one key
        # (else distinct pairs would all collide on `(op, None)` and be wrongly deduped).
        left = assumption.get("left")
        right = assumption.get("right")
        if isinstance(left, str) and isinstance(right, str) and right < left:
            left, right = right, left
        return (op, left, right)
    if op == "rel":
        # A relational guard between two operands (`isKnownNonEqual(A,B)`, a dominating icmp).
        # It has no `name`, so keying on `(op, name)` would collapse EVERY rel fact onto
        # `("rel", None)` and silently drop all but the first -- dropping a value-relevant
        # precondition (soundness hole). Key on the predicate and BOTH operands instead; the
        # comparison is ordered (slt/sgt), so do NOT order-normalize the pair.
        return (op, assumption.get("predicate"), assumption.get("left"), assumption.get("right"))
    return (op, name)


def _canonical(assumption: dict[str, Any]) -> dict[str, Any]:
    return dict(assumption)


def _derived(name: str, op: str, **fields: Any) -> dict[str, Any]:
    out = {"op": op, "name": name}
    out.update(fields)
    return out


def normalize_assumptions(assumptions: Any) -> dict[str, Any]:
    """Return normalized assumptions, derived implications, and contradictions.

    This module intentionally mirrors the formal IR assumption shapes. It only
    reasons about facts that are already in the registry/formal IR vocabulary.
    """
    if assumptions is None:
        assumptions = []
    if not isinstance(assumptions, list):
        return {"assumptions": assumptions, "derived": [], "contradictions": ["formal assumptions must be an array"]}

    by_name: dict[str, dict[str, Any]] = {}
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    known_bits_index: dict[str, int] = {}
    contradictions: list[str] = []
    derived: list[dict[str, Any]] = []

    def state_for(name: str) -> dict[str, Any]:
        return by_name.setdefault(
            name,
            {
                "not_eq_zero": False,
                "known_zero_mask": 0,
                "known_one_mask": 0,
                "power_of_two": False,
                "cmp": [],
            },
        )

    for raw in assumptions:
        if not isinstance(raw, dict):
            normalized.append(raw)
            continue
        assumption = _canonical(raw)
        key = _key(assumption)
        if key in seen:
            continue
        seen.add(key)
        name = assumption.get("name")
        if (
            assumption.get("op") == "known-bits"
            and isinstance(name, str)
            and isinstance(assumption.get("zero_mask", 0), int)
            and isinstance(assumption.get("one_mask", 0), int)
            and not (assumption.get("zero_mask", 0) & assumption.get("one_mask", 0))
            and name in known_bits_index
        ):
            existing = normalized[known_bits_index[name]]
            zero_mask = (existing.get("zero_mask", 0) | assumption.get("zero_mask", 0)) & MASK32
            one_mask = (existing.get("one_mask", 0) | assumption.get("one_mask", 0)) & MASK32
            if zero_mask or "zero_mask" in existing or "zero_mask" in assumption:
                existing["zero_mask"] = zero_mask
            if one_mask or "one_mask" in existing or "one_mask" in assumption:
                existing["one_mask"] = one_mask
            state = state_for(name)
            state["known_zero_mask"] |= assumption.get("zero_mask", 0) & MASK32
            state["known_one_mask"] |= assumption.get("one_mask", 0) & MASK32
            if state["known_zero_mask"] & state["known_one_mask"]:
                contradictions.append(f"{name}: known-bits facts conflict")
            continue
        normalized.append(assumption)

        if not isinstance(name, str):
            continue
        state = state_for(name)
        op = assumption.get("op")
        if op == "not-eq" and assumption.get("value") == 0:
            state["not_eq_zero"] = True
        elif op == "known-bits":
            known_bits_index.setdefault(name, len(normalized) - 1)
            zero_mask = assumption.get("zero_mask", 0)
            one_mask = assumption.get("one_mask", 0)
            if isinstance(zero_mask, int) and isinstance(one_mask, int):
                if zero_mask & one_mask:
                    continue
                state["known_zero_mask"] |= zero_mask & MASK32
                state["known_one_mask"] |= one_mask & MASK32
                if state["known_zero_mask"] & state["known_one_mask"]:
                    contradictions.append(f"{name}: known-bits facts conflict")
        elif op == "power-of-two" and assumption.get("nonzero") is True:
            state["power_of_two"] = True
        elif op == "cmp":
            state["cmp"].append(assumption)

    for name, state in sorted(by_name.items()):
        zero_mask = int(state["known_zero_mask"])
        one_mask = int(state["known_one_mask"])
        if state["power_of_two"]:
            fact = _derived(name, "not-eq", value=0, reason="power-of-two-nonzero")
            if fact not in normalized and fact not in derived:
                derived.append(fact)
        if one_mask:
            fact = _derived(name, "not-eq", value=0, reason="known-one-bits")
            if fact not in normalized and fact not in derived:
                derived.append(fact)
        for cmp_assumption in state["cmp"]:
            predicate = cmp_assumption.get("predicate")
            value = cmp_assumption.get("value")
            if value == 0 and predicate == "sgt":
                for fact in (
                    _derived(name, "cmp", predicate="sge", value=0, reason="sgt-zero"),
                    _derived(name, "not-eq", value=0, reason="sgt-zero"),
                ):
                    if fact not in normalized and fact not in derived:
                        derived.append(fact)
            if predicate == "eq" and value == 0 and state["not_eq_zero"]:
                contradictions.append(f"{name}: eq zero conflicts with not-eq zero")
            if predicate == "eq" and value == 0 and state["power_of_two"]:
                contradictions.append(f"{name}: eq zero conflicts with power-of-two")
        if zero_mask == MASK32 and state["not_eq_zero"]:
            contradictions.append(f"{name}: known all-zero bits conflict with not-eq zero")
        if zero_mask == MASK32 and state["power_of_two"]:
            contradictions.append(f"{name}: known all-zero bits conflict with power-of-two")

    return {
        "assumptions": normalized,
        "derived": derived,
        "contradictions": sorted(set(contradictions)),
    }
