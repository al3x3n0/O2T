#!/usr/bin/env python3
"""Single source of truth for LLVM ValueTracking analysis facts.

A fold's legality often rests on a ValueTracking query (`isKnownToBeAPowerOfTwo`,
`isKnownNonNegative`, `MaskedValueIsZero`, ...). Two provers consume such a fact:

  * the intent-validation pipeline -- `intent/infer.py` lifts the fact to a formal
    *assumption* object and `formal_ir.assumption_to_smt` lowers it to SMT;
  * the symexec cascade discharge -- `intent/extract_pass_model.predicate_to_guard`
    turns the fold's guard into a path condition for `cv-symexec-pass`.

This module is the ONE place that (a) maps a ValueTracking predicate to the
canonical assumption vocabulary both pipelines already use, and (b) lowers a
scalar value-fact assumption to its SMT fragment. `formal_ir.assumption_to_smt`
delegates its scalar cases here, and `predicate_to_guard` lowers facts here too,
so the two provers cannot drift -- they share one encoder, pinned by a test.

The assumption vocabulary mirrors `intent/infer.py`:
    {"op": "power-of-two", "name": N, "nonzero": True}
    {"op": "cmp", "predicate": "sge"|"sgt"|"slt", "name": N, "value": 0}
    {"op": "not-eq", "name": N, "value": 0}
    {"op": "known-bits", "name": N, "zero_mask": M}
"""

from __future__ import annotations

import re
from typing import Any

MASK32 = (1 << 32) - 1

# Scalar value-fact assumption ops this module owns the SMT encoding for. Other
# ops (`not-poison`, `rel`, `addr-diseq`) need solver/poison context and stay in
# formal_ir.
SCALAR_ASSUMPTION_OPS = {"power-of-two", "known-bits", "cmp", "not-eq"}

_CMP_SMT = {
    "eq": "=", "sgt": "bvsgt", "sge": "bvsge", "slt": "bvslt", "sle": "bvsle",
    "ugt": "bvugt", "uge": "bvuge", "ult": "bvult", "ule": "bvule",
}

# Recognizers -- one regex per ValueTracking predicate family. The first operand
# is the subject value; a power-of-two `OrZero` second arg admits zero.
_POW2_RE = re.compile(r"\bisKnown(?:ToBeA)?PowerOf(?:Two)?\(\s*&?(\w+)\s*(?:,\s*(\w+))?")
_MASK_RE = re.compile(r"\b(?:MaskedValueIsZero|haveNoCommonBitsSet)\(\s*&?(\w+)\s*,\s*&?(\w+)")
_SIGN_RE = {
    "isKnownNonNegative": ("cmp", "sge"),
    "isKnownPositive": ("cmp", "sgt"),
    "isKnownNegative": ("cmp", "slt"),
    "isKnownNonZero": ("not-eq", None),
}
# Inline mask-test guards -- the forms folds write WITHOUT calling a helper (stratum A of
# docs/roadmap-vocabulary-strata.md). Anchored so it matches only a pure `(A & B) == D` clause:
#   (X & C) == 0   -> the C bits of X are known ZERO  (= MaskedValueIsZero(X, C), literal C)
#   (X & C) == C   -> the C bits of X are known ONE   (the one-mask direction; no ValueTracking helper)
#   (X & Y) == 0   -> X, Y share no set bits           (= haveNoCommonBitsSet(X, Y), SSA masks)
# Operands may be in either order. A literal mask yields the known-bits fact the SMT already
# discharges (scalar_assumption_smt handles both masks); two SSA operands with `== 0` yield the
# relational mask-pair. Every other shape (a non-0/non-mask RHS, `!=`, a relational one-mask) DECLINES.
_INLINE_MASK_RE = re.compile(r"^\s*\(?\s*(\w+)\s*&\s*(\w+)\s*\)?\s*==\s*(\w+)\s*$")


def _hexlit(value: int) -> str:
    return f"#x{value & MASK32:08x}"


def mask_pair_smt(left: str, right: str) -> str:
    """SMT for a two-operand disjointness fact `(X & Y) == 0` (`haveNoCommonBitsSet` /
    `MaskedValueIsZero` with an SSA mask). Owned here so the formal-IR prover and the symexec
    guard path share ONE encoding and cannot drift, exactly like the scalar facts above."""
    return f"(= (bvand {left} {right}) #x00000000)"


def _smt_and(clauses: list[str]) -> str:
    """Match formal_ir.smt_and's shape so delegated SMT is byte-identical."""
    if not clauses:
        return "true"
    if len(clauses) == 1:
        return clauses[0]
    return f"(and {' '.join(clauses)})"


def _mask_operand(tok: str):
    """A second ValueTracking argument is an SSA value or a literal mask."""
    tok = tok.strip()
    if re.fullmatch(r"-?\d+", tok):
        return ("lit", int(tok))
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", tok):
        return ("lit", int(tok, 16))
    return ("var", tok)


def _inline_mask_fact(a: str, b: str, rhs: str):
    """An inline `(A & B) == RHS` clause -> its assumption, or None (decline). A LITERAL mask with a
    known value/mask on the LHS gives an exact known-bits fact (RHS 0 -> known-zero of that mask;
    RHS == the mask -> known-one of that mask); two SSA operands with RHS 0 give the relational
    mask-pair. A non-0 / non-mask RHS, or a relational one-mask, is not a clean fact and declines."""
    ka, va = _mask_operand(a)
    kb, vb = _mask_operand(b)
    rk, rv = _mask_operand(rhs)
    if ka == "lit" and kb == "var":
        mask, val = va & MASK32, b
    elif kb == "lit" and ka == "var":
        mask, val = vb & MASK32, a
    else:
        mask = val = None
    if mask is not None and rk == "lit":
        r = rv & MASK32
        if r == 0:
            return [{"op": "known-bits", "name": val, "zero_mask": mask}]
        if r == mask:
            return [{"op": "known-bits", "name": val, "one_mask": mask}]
        return None                                   # (X & C) == D, D != 0 and D != C -> not clean
    if ka == "var" and kb == "var" and rk == "lit" and (rv & MASK32) == 0:
        return [{"op": "mask-pair", "left": a, "right": b}]
    return None


def fact_to_assumptions(clause: str) -> list[dict[str, Any]] | None:
    """Lower one ValueTracking predicate clause to canonical assumption objects.

    Returns a list of assumptions, or None if the clause carries no recognized
    value fact. Mask facts whose mask is another SSA value yield a *relational*
    `known-bits`-style assumption expressed as an extra var (handled by the
    guard-node form), so they are returned as a raw `mask` assumption the caller
    lowers with both operands in scope.
    """
    m = _POW2_RE.search(clause)
    if m:
        a = {"op": "power-of-two", "name": m.group(1), "nonzero": True}
        if (m.group(2) or "").strip() == "true":
            a["or_zero"] = True
        return [a]
    mz = _MASK_RE.search(clause)
    if mz:
        kind, operand = _mask_operand(mz.group(2))
        if kind == "lit":
            return [{"op": "known-bits", "name": mz.group(1), "zero_mask": operand}]
        # (X & Y) == 0 with Y an SSA value: a two-operand disjointness fact.
        return [{"op": "mask-pair", "left": mz.group(1), "right": operand}]
    im = _INLINE_MASK_RE.match(clause)
    if im:
        inline = _inline_mask_fact(im.group(1), im.group(2), im.group(3))
        if inline is not None:
            return inline
    for name, (op, predicate) in _SIGN_RE.items():
        if re.search(name + r"\(\s*&?(\w+)", clause):
            sm = re.search(name + r"\(\s*&?(\w+)", clause)
            if op == "not-eq":
                return [{"op": "not-eq", "name": sm.group(1), "value": 0}]
            return [{"op": "cmp", "predicate": predicate, "name": sm.group(1), "value": 0}]
    return None


def scalar_assumption_smt(assumption: dict[str, Any], var: str) -> str | None:
    """SMT fragment for a scalar value-fact assumption over the term `var`.

    `var` is the already-formed SMT term for the subject (a bare name, an
    `smt_sym`-escaped name, or a per-lane `name{lane}`); the caller owns variable
    declaration and validation. Returns None for ops this module does not encode.
    """
    op = assumption.get("op")
    if op == "power-of-two":
        nonzero = f"(not (= {var} #x00000000))"
        single_bit = f"(= (bvand {var} (bvsub {var} #x00000001)) #x00000000)"
        # OrZero variants admit zero (a power of two *or* zero) -> drop nonzero.
        if assumption.get("or_zero") is True:
            return single_bit
        return _smt_and([nonzero, single_bit])
    if op == "known-bits":
        clauses: list[str] = []
        zero_mask = assumption.get("zero_mask", 0)
        one_mask = assumption.get("one_mask", 0)
        if zero_mask:
            clauses.append(f"(= (bvand {var} {_hexlit(zero_mask)}) #x00000000)")
        if one_mask:
            clauses.append(f"(= (bvand {var} {_hexlit(one_mask)}) {_hexlit(one_mask)})")
        return _smt_and(clauses)
    if op in {"cmp", "not-eq"}:
        predicate = "ne" if op == "not-eq" else assumption.get("predicate")
        constant = _hexlit(int(assumption.get("value", 0)))
        if predicate == "eq":
            return f"(= {var} {constant})"
        if predicate == "ne":
            return f"(not (= {var} {constant}))"
        smt_op = _CMP_SMT.get(predicate)
        return f"({smt_op} {var} {constant})" if smt_op else None
    return None


def assumption_guard_smt(assumption: dict[str, Any]) -> tuple[str, list[str]] | None:
    """SMT fragment + free variable names for a fact assumption, for the symexec
    path (`predicate_to_guard`). Uses bare variable names, matching how the symexec
    model declares its operands. Returns None for an unencodable assumption."""
    if assumption.get("op") == "mask-pair":
        left, right = assumption["left"], assumption["right"]
        # (X & Y) == 0 -- the two operands share no set bits.
        return mask_pair_smt(left, right), [left, right]
    name = assumption.get("name")
    if not isinstance(name, str):
        return None
    smt = scalar_assumption_smt(assumption, name)
    return (smt, [name]) if smt is not None else None
