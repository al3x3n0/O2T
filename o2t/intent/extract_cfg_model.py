#!/usr/bin/env python3
"""Recover SimplifyCFG if-conversion folds from pass SOURCE and discharge them (deep CFG model).

The deep contract (`validate/cfg_shape.py`) validates the real `opt -passes=simplifycfg` IR
OUTPUT. This recovers the same obligation from a pass's C++: it finds each fold that builds a
`CreateSelect(...)` to collapse a diamond `phi [then-val, ThenBB], [else-val, ElseBB]` over
`br cond, ThenBB, ElseBB`, and resolves how the select's three operands bind:

  * the condition operand -> the branch condition, or its negation (`CreateNot`/`CreateXor`);
  * each value operand    -> the then-block value or the else-block value
    (`getIncomingValueForBlock(ThenBB|ElseBB)`).

It then discharges the recovered binding with the SAME prover the IR contract uses
(`prove_if_conversion`): the identity binding (`select cond, then, else`) and the negate-and-swap
binding prove; a fold that swaps the value operands WITHOUT negating the condition (the select
returns the wrong arm) is REFUTED from its source with a concrete witness.
"""

from __future__ import annotations

import re

from o2t.mine.pass_scev import split_functions
from o2t.validate.cfg_shape import prove_if_conversion

_ASSIGN_RE = re.compile(r"(\w+)\s*=\s*([^;]+);")
_SELECT_RE = re.compile(r"CreateSelect\s*\(")
# symbolic params for the abstract diamond: cond (Bool), then/else values (BV32).
_PARAMS = {"%c": "Bool", "%a": "(_ BitVec 32)", "%b": "(_ BitVec 32)"}
_DIAMOND = {"cond": "%c", "then": "%a", "else": "%b"}


def _role_of_rhs(rhs):
    """Classify an assignment RHS into a role, or None."""
    if "getCondition" in rhs:
        return "cond"
    if "CreateNot" in rhs or ("CreateXor" in rhs and "true" in rhs) or "CreateICmpEQ" in rhs:
        return "not-cond"
    if "getIncomingValue" in rhs:
        arg = rhs[rhs.find("(") + 1:]
        low = arg.lower()
        if "then" in low or "true" in low:
            return "then-val"
        if "else" in low or "false" in low:
            return "else-val"
    return None


def _split_top_level(s):
    """Split a comma-separated argument list, respecting nested parentheses."""
    args, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def _resolve(token, roles):
    """Resolve a select-operand token (a variable or an inline expression) to a role."""
    token = token.strip()
    if token in roles:
        return roles[token]
    return _role_of_rhs(token)


def recognize_ifconversion_fold(body):
    """Recover {cond_negated, true_src, false_src} for a diamond->select fold, or None."""
    if not _SELECT_RE.search(body):
        return None
    roles = {}
    for name, rhs in _ASSIGN_RE.findall(body):
        role = _role_of_rhs(rhs)
        if role:
            roles[name] = role
    m = _SELECT_RE.search(body)
    args = _split_top_level(body[m.end():])
    if len(args) != 3:
        return None
    cond_role = _resolve(args[0], roles)
    true_role = _resolve(args[1], roles)
    false_role = _resolve(args[2], roles)
    if cond_role not in ("cond", "not-cond") or true_role not in ("then-val", "else-val") \
            or false_role not in ("then-val", "else-val"):
        return None
    return {"cond_negated": cond_role == "not-cond",
            "true_src": "then" if true_role == "then-val" else "else",
            "false_src": "then" if false_role == "then-val" else "else"}


def verify_source(z3_bin, source_text):
    """Mine each if-conversion fold and discharge it. Per-function verdicts:
    proved | refuted | error | not-a-transform."""
    results = []
    for name, body in split_functions(source_text).items():
        m = recognize_ifconversion_fold(body)
        if m is None:
            results.append({"function": name, "status": "not-a-transform"})
            continue
        select = {"cond": "%c", "negated": m["cond_negated"],
                  "true": "%a" if m["true_src"] == "then" else "%b",
                  "false": "%a" if m["false_src"] == "then" else "%b"}
        status, info = prove_if_conversion(z3_bin, _PARAMS, _DIAMOND, select)
        entry = {"function": name, "cond_negated": m["cond_negated"],
                 "true_src": m["true_src"], "false_src": m["false_src"], "status": status}
        if status == "refuted":
            entry["witness"] = bool(info.get("model"))
        results.append(entry)
    return results
