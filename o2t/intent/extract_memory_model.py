#!/usr/bin/env python3
"""Recover a memory transform's op-sequence from pass SOURCE, then discharge it (theory of arrays).

The theory-of-arrays verifier (`validate/memory_model.py`) proves CANONICAL contracts. This
lifts the same obligations from a DSE/store-forwarding pass's C++: it reads each fold function,
recognizes the rewrite (`deleteDeadInstruction`/`eraseFromParent` -> remove a store;
`replaceAllUsesWith(load, storedValue(store))` -> forward), recovers the memory operands from
the typed signature, and -- crucially -- turns the fold's OWN legality guards into the SMT
assumptions: `isOverwrite`/`fullyOverwrites` -> the dead store aliases its killer (`eq`),
`isNoAlias`/`!mayAlias` -> `ne`. It then proves the transform sound UNDER those guards, and a
fold whose guards are INSUFFICIENT (e.g. removes a store without establishing an overwrite) is
REFUTED with a concrete colliding-address witness -- catching an unsound pass from its source.

A function with no recognized rewrite (a reject/query helper) is declared `not-a-transform`.
"""

from __future__ import annotations

import re

from o2t.mine.pass_scev import split_functions, strip_comments
from o2t.validate import memory_model as mm

_SIG_RE = re.compile(r"^([A-Za-z_][\w\s:*&<>]*?\b\w+)\s*\(([^;{}]*)\)\s*$", re.S)
_PARAM_RE = re.compile(r"\b(StoreInst|LoadInst|Instruction)\b[\s*&]+(\w+)")
_RM_RE = re.compile(r"deleteDeadInstruction\(\s*&?(\w+)\s*\)|(\w+)\s*->\s*eraseFromParent|"
                    r"(\w+)\s*\.\s*eraseFromParent")
_FWD_RE = re.compile(r"replaceAllUsesWith\(\s*&?(\w+)\s*,\s*storedValue\(\s*&?(\w+)")
_OVERWRITE_RE = re.compile(r"\b(?:isOverwrite|fullyOverwrites)\(\s*&?(\w+)\s*,\s*&?(\w+)")
_NOALIAS_RE = re.compile(r"\b(?:isNoAlias|isMustNotAlias)\(\s*&?(\w+)\s*,\s*&?(\w+)")
_NOTMAYALIAS_RE = re.compile(r"!\s*mayAlias\(\s*&?(\w+)\s*,\s*&?(\w+)")


def _p(name):
    return f"{name}_p"      # symbolic address of a memory operand


def _v(name):
    return f"{name}_v"      # symbolic stored value


def _store_params(signature):
    return _PARAM_RE.findall(signature)


def _aliases(body, a, b, regexes):
    """True if any of `regexes` relates {a, b} (order-insensitive) in the guard body."""
    pair = {a, b}
    return any({m.group(1), m.group(2)} == pair for rx in regexes for m in rx.finditer(body))


def recognize_memory_fold(func_name, signature, body):
    """Lift one fold function to a memory-transform model {kind, before, after, observable,
    assumptions}, or None if it performs no recognized memory rewrite."""
    params = _store_params(signature)
    stores = [n for t, n in params if t in ("StoreInst", "Instruction")]
    loads = [n for t, n in params if t == "LoadInst"]

    fwd = _FWD_RE.search(body)
    if fwd:
        load, store = fwd.group(1), fwd.group(2)
        other = next((s for s in stores if s != store), None)
        before = [mm._store(_p(store), _v(store))]
        after = [mm._store(_p(store), _v(store))]
        if other:                                  # an intervening store the fold reasons about
            before.append(mm._store(_p(other), _v(other)))
            after.append(mm._store(_p(other), _v(other)))
        before.append(mm._load(f"{load}_r", _p(store)))     # the load reads the store's pointer
        after.append(mm._bind(f"{load}_r", _v(store)))      # ...forwarded to the stored value
        assumptions = []
        if other and (_aliases(body, other, store, [_NOALIAS_RE, _NOTMAYALIAS_RE])):
            assumptions.append({"op": "ne", "args": [_p(other), _p(store)]})
        return {"kind": "store-forward", "before": before, "after": after,
                "observable": f"load:{load}_r", "assumptions": assumptions,
                "guards": {"no_alias": bool(assumptions)}}

    rm = _RM_RE.search(body)
    if rm:
        removed = rm.group(1) or rm.group(2) or rm.group(3)
        killing = next((s for s in stores if s != removed), None)
        if killing is None:
            return None
        assumptions = []
        overwrite = _aliases(body, removed, killing, [_OVERWRITE_RE])
        if overwrite:                              # killing store overwrites the dead store
            assumptions.append({"op": "eq", "args": [_p(removed), _p(killing)]})
        before = [mm._store(_p(removed), _v(removed)), mm._store(_p(killing), _v(killing))]
        after = [mm._store(_p(killing), _v(killing))]
        return {"kind": "dse-remove", "before": before, "after": after,
                "observable": "memory", "assumptions": assumptions,
                "guards": {"overwrite": overwrite}}
    return None


def mine_source(source_text):
    """name -> recognized model (or None) for every function in the source."""
    out = {}
    funcs = split_functions(source_text)
    # Re-derive each function's signature from the source (split_functions returns bodies).
    sigs = _signatures(source_text)
    for name, body in funcs.items():
        out[name] = recognize_memory_fold(name, sigs.get(name, ""), body)
    return out


_FUNC_SIG_RE = re.compile(r"\b(\w+)\s*\(([^;{}]*)\)\s*\{", re.S)


def _signatures(source_text):
    return {m.group(1): m.group(2) for m in _FUNC_SIG_RE.finditer(strip_comments(source_text))}


def verify_source(z3_bin, source_text):
    """Mine every fold and discharge it. Returns per-function verdicts: proved | refuted |
    not-a-transform (no rewrite) | error."""
    results = []
    for name, model in mine_source(source_text).items():
        if model is None:
            results.append({"function": name, "status": "not-a-transform"})
            continue
        status, info = mm.prove_memory_transform(
            z3_bin, model["before"], model["after"], model["observable"], model["assumptions"])
        results.append({"function": name, "status": status, "kind": model["kind"],
                        "guards": model["guards"], "witness": info.get("witness")})
    return results
