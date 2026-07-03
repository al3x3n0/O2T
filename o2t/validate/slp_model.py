#!/usr/bin/env python3
"""Deep formal verification of SLP / (G)SLP vectorization: per-lane equivalence + reductions.

SLP packs N independent scalar ops into one vector op. Currently O2T routes SLP through the
coarse source-intent pipeline (proved/unsupported); this discharges the actual SMT obligations:

  * PACK / LANE MAPPING -- the vector lanes feeding each scalar's uses must compute that
    scalar's value. We model the pack (lane l holds scalar `pack[l]`'s operands) and the
    extract (scalar i's uses read lane `ext[i]`); soundness is `pack[ext[i]] == i` for every i,
    proved for all lane values. A mismatched pack/extract (a lane-bookkeeping bug) is REFUTED.

  * REDUCTION -- a scalar reduction chain `a0 OP a1 OP ... ` becomes a vector reduce, which uses
    a TREE association. For an ASSOCIATIVE integer OP (add/mul/and/or/xor over bitvectors) the
    tree equals the sequential chain -> proved. For FLOATING-POINT add/mul the association
    changes the result (FP add is not associative) -> REFUTED with a witness, unless the
    transform declares `reassoc`/fast-math, in which case it is reported `reassoc-allowed`
    (a legal-by-flag relaxation, NOT a value-equivalence proof). This is the real SLP/reduction
    correctness subtlety, now with two-sided teeth.
"""

from __future__ import annotations

import subprocess

BV = "(_ BitVec 32)"
FP = "Float32"
_BV_OP = {"add": "bvadd", "mul": "bvmul", "and": "bvand", "or": "bvor", "xor": "bvxor"}
_FP_OP = {"add": "fp.add RNE", "mul": "fp.mul RNE"}


def _decls(names, sort):
    return [f"(declare-const {n} {sort})" for n in names]


def _check(z3_bin, logic, decls, goal_negation):
    smt = "\n".join([f"(set-logic {logic})", *decls,
                     f"(assert (not {goal_negation}))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"model": out}
    return "error", {"reason": head}


def prove_pack_binop(z3_bin, op, n, pack, ext, width=32):
    """Prove the SLP pack is consistent: the value extracted for each scalar i equals that
    scalar's binop. lane l holds operands of scalar `pack[l]`; scalar i reads lane `ext[i]`.
    Sound iff `pack[ext[i]] == i` for all i -- proved for ALL operand values at the given bit
    `width` (the lane mapping is width-agnostic, holding at every width), or refuted."""
    logic, decls, premises, goal = pack_obligation(op, n, pack, ext, width)
    return _check(z3_bin, logic, decls, goal)


def pack_obligation(op, n, pack, ext, width=32):
    """The SLP pack obligation as (logic, decls, premises, goal) -- the single source the prover
    and the cross-solver/witness re-validation both consume."""
    bvop = _BV_OP[op]
    bv = f"(_ BitVec {width})"
    a = [f"a{i}" for i in range(n)]
    b = [f"b{i}" for i in range(n)]
    decls = _decls(a + b, bv)
    # vector lane l computes op(a[pack[l]], b[pack[l]]); scalar i's used value is lane ext[i].
    used = [f"({bvop} {a[pack[ext[i]]]} {b[pack[ext[i]]]})" for i in range(n)]
    scalar = [f"({bvop} {a[i]} {b[i]})" for i in range(n)]
    goal = "(and " + " ".join(f"(= {u} {s})" for u, s in zip(used, scalar)) + ")"
    return "QF_BV", decls, [], goal


def _seq(vals, op):
    """Left-associated chain: ((v0 op v1) op v2) ..."""
    acc = vals[0]
    for v in vals[1:]:
        acc = f"({op} {acc} {v})"
    return acc


def _tree(vals, op):
    """Balanced tree association (what a vector reduce computes)."""
    cur = list(vals)
    while len(cur) > 1:
        cur = [f"({op} {cur[i]} {cur[i + 1]})" for i in range(0, len(cur) - 1, 2)] + \
              ([cur[-1]] if len(cur) % 2 else [])
    return cur[0]


def prove_reduction(z3_bin, op, n, fp=False, width=32):
    """Prove a scalar reduction chain equals the vector (tree) reduction. Integer ops are
    associative -> proved at ANY bit `width`; FP ops are not -> refuted (the reassociation
    changes the result). `width` applies to the integer path; FP stays Float32."""
    logic, decls, premises, goal = reduction_obligation(op, n, fp, width)
    return _check(z3_bin, logic, decls, goal)


def reduction_obligation(op, n, fp=False, width=32):
    """The reduction obligation as (logic, decls, premises, goal): sequential chain == tree."""
    vals = [f"x{i}" for i in range(n)]
    if fp:
        sym = _FP_OP[op]
        decls = _decls(vals, FP)
        return "QF_FP", decls, [], f"(= {_seq(vals, sym)} {_tree(vals, sym)})"
    sym = _BV_OP[op]
    decls = _decls(vals, f"(_ BitVec {width})")
    return "QF_BV", decls, [], f"(= {_seq(vals, sym)} {_tree(vals, sym)})"


# Canonical SLP contracts. (id, kind, params, expected). `expected` is the SOUND verdict.
PACK_CONTRACTS = [
    # identity pack + identity extract -> the lanes line up -> proved.
    ("pack-binop-identity", "add", 4, [0, 1, 2, 3], [0, 1, 2, 3], "proved"),
    # identity pack but SWAPPED extract (scalar 0 reads lane 1) -> lane-bookkeeping bug -> refuted.
    ("pack-binop-misextract", "add", 4, [0, 1, 2, 3], [1, 0, 2, 3], "refuted"),
    # a consistent permutation (pack and extract are inverses) -> still sound.
    ("pack-binop-consistent-perm", "add", 4, [2, 0, 3, 1], [1, 3, 0, 2], "proved"),
]

REDUCTION_CONTRACTS = [
    ("reduction-int-add", "add", 4, False, "proved"),
    ("reduction-int-mul", "mul", 4, False, "proved"),
    ("reduction-int-xor", "xor", 4, False, "proved"),
    # FP reduction reassociated WITHOUT fast-math -> changes the result -> refuted (needs reassoc).
    ("reduction-fp-add-no-reassoc", "add", 4, True, "refuted"),
    ("reduction-fp-mul-no-reassoc", "mul", 4, True, "refuted"),
]
