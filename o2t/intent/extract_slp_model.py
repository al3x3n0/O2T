#!/usr/bin/env python3
"""Recover SLP reduction shapes from pass SOURCE, then discharge them (deep reduction model).

The deep SLP verifier (`validate/slp_model.py`) proves CANONICAL contracts. This lifts the same
obligation from a vectorizer's C++: it finds each fold that emits a horizontal reduction
(`CreateAddReduce`, `CreateFAddReduce`, `vector_reduce_*`, ...), recovers the reduction OPERATION
and whether it is FLOATING-POINT, and detects the fold's OWN legality guard -- a fast-math /
`allowReassoc()` check. It then discharges:

  * integer reduction               -> proved (associative);
  * FP reduction WITH a reassoc/fast-math guard  -> `reassoc-allowed` (legal by flag);
  * FP reduction WITHOUT that guard  -> REFUTED -- the vector tree-reduce reassociates the
    additions and changes the result, so the vectorizer is unsound, caught from its source.

A function emitting no reduction is `not-a-transform`; a reduction op outside the modeled set
is `unsupported-op`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from o2t.mine.pass_scev import split_functions
from o2t.validate import slp_model as slp

ROOT = Path(__file__).resolve().parents[2]
_IDIOMS = json.loads((ROOT / "constraints" / "llvm_idioms.json").read_text())
# reduction-creating call/intrinsic token -> operation name (fadd/add/mul/...).
TOKEN_OP = {tok: r["operation"] for r in _IDIOMS.get("reductions", []) for tok in r["tokens"]}
_FP_OPS = {"fadd", "fmul"}
# a fast-math / reassociation guard in the fold body.
_REASSOC_RE = re.compile(r"\ballowReassoc\b|\bisFast\b|\bhasAllowReassoc\b|"
                         r"\bgetFastMathFlags\b|\bsetFast\b|\bAllowReassoc\b")

# pack/lane-mapping idioms: insert a scalar at a lane, extract a lane, the element-wise binop.
_INSERT_RE = re.compile(r"(?:Create|Builder\.\s*Create)?InsertElement\s*\([^;]*?,\s*(\d+)\s*\)")
_EXTRACT_RE = re.compile(r"(?:Create|Builder\.\s*Create)?ExtractElement\s*\([^;]*?,\s*(\d+)\s*\)")
# element-wise vector binop emitted for the pack (operation name -> deep-model base op).
_PACK_BINOP = {"CreateAdd": "add", "CreateSub": "add", "CreateMul": "mul",
               "CreateAnd": "and", "CreateOr": "or", "CreateXor": "xor",
               "CreateFAdd": "add", "CreateFMul": "mul"}
_PACK_BINOP_RE = re.compile(r"\b(" + "|".join(_PACK_BINOP) + r")\s*\(")


def recognize_pack_fold(body):
    """Recover a binop pack's lane mapping from source, or None.

    A pack builds vector operands with InsertElement (scalar k's operand -> lane
    `insert_lanes[k]`, in source order) and routes each scalar's uses to ExtractElement
    (scalar k reads lane `ext_lanes[k]`). We take the first n inserts as the (shared) operand
    pack order and the n extracts as the use lanes. Soundness (checked downstream) is that each
    scalar extracts from the lane its operands were packed into."""
    inserts = [int(m.group(1)) for m in _INSERT_RE.finditer(body)]
    ext_lanes = [int(m.group(1)) for m in _EXTRACT_RE.finditer(body)]
    n = len(ext_lanes)
    if n < 2 or len(inserts) < n:
        return None
    insert_lanes = inserts[:n]                 # first operand vector's lane assignment
    if sorted(insert_lanes) != list(range(n)) or sorted(ext_lanes) != list(range(n)):
        return None                            # not a clean lane permutation -> out of model
    bm = _PACK_BINOP_RE.search(body)
    op = _PACK_BINOP[bm.group(1)] if bm else "add"
    return {"n": n, "insert_lanes": insert_lanes, "ext_lanes": ext_lanes, "op": op}


def recognize_reduction_fold(body):
    """Recover {operation, base_op, is_fp, reassoc_guard} for a reduction-emitting fold, or None."""
    call = next((tok for tok in TOKEN_OP if re.search(r"\b" + re.escape(tok) + r"\s*\(", body)), None)
    if call is None:
        return None
    operation = TOKEN_OP[call]
    is_fp = operation in _FP_OPS
    base_op = operation[1:] if is_fp and operation.startswith("f") else operation
    return {"operation": operation, "base_op": base_op, "is_fp": is_fp,
            "reassoc_guard": bool(_REASSOC_RE.search(body)), "call": call}


def verify_pack_fold(z3_bin, m):
    """Discharge a recovered pack lane mapping with the deep pack model. `insert_lanes[k]` is the
    lane scalar k's operands are packed into (pack is its inverse); `ext_lanes[k]` the lane
    scalar k extracts. Sound iff each scalar extracts the lane it was packed into."""
    n = m["n"]
    pack = [0] * n
    for k, lane in enumerate(m["insert_lanes"]):
        pack[lane] = k                         # lane -> scalar (inverse of insert order)
    ext = list(m["ext_lanes"])                 # scalar -> lane it reads
    status, info = slp.prove_pack_binop(z3_bin, m["op"], n, pack, ext)
    return status, bool(info.get("model"))


def verify_source(z3_bin, source_text, n=4):
    """Mine each SLP fold (reduction or binop pack) and discharge it. Per-function verdicts:
    proved | reassoc-allowed | refuted | unsupported-op | not-a-transform."""
    results = []
    for name, body in split_functions(source_text).items():
        pk = recognize_pack_fold(body)
        if pk is not None and recognize_reduction_fold(body) is None:
            status, witness = verify_pack_fold(z3_bin, pk)
            entry = {"function": name, "kind": "pack", "op": pk["op"], "lanes": pk["n"],
                     "insert_lanes": pk["insert_lanes"], "ext_lanes": pk["ext_lanes"],
                     "status": status}
            if status == "refuted":
                entry["witness"] = witness
            results.append(entry)
            continue
        m = recognize_reduction_fold(body)
        if m is None:
            results.append({"function": name, "status": "not-a-transform"})
            continue
        if m["base_op"] not in slp._BV_OP:
            results.append({"function": name, "status": "unsupported-op",
                            "reduction": m["operation"]})
            continue
        entry = {"function": name, "reduction": m["operation"], "fp": m["is_fp"],
                 "reassoc_guard": m["reassoc_guard"]}
        if not m["is_fp"]:
            entry["status"] = slp.prove_reduction(z3_bin, m["base_op"], n, fp=False)[0]
        elif m["reassoc_guard"]:
            entry["status"] = "reassoc-allowed"        # FP reassociation permitted by fast-math
        else:
            status, info = slp.prove_reduction(z3_bin, m["base_op"], n, fp=True)
            entry["status"] = status                   # refuted: FP reassoc without fast-math
            entry["witness"] = bool(info.get("model"))
        results.append(entry)
    return results
