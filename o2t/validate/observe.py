#!/usr/bin/env python3
"""Close the source-intent <-> actual-behavior loop for recovered peephole folds.

O2T recovers a fold obligation from pass SOURCE and proves `before == after` sound. That verifies the
pass's INTENT, but never observes the pass's ACTUAL behavior: a recovery can be internally consistent
and proved yet not match what the compiled `opt` really does (an over-recovered precondition the pass
actually guards, a fold the pass does not apply on that shape, or pattern-vs-pass drift).

This module adds the missing observational link -- the peephole analogue of E1's loop translation
validation. It emits the recovered `before` as real LLVM IR (`pass_graph.to_llvm_ir`), runs the ACTUAL
`opt -passes=instcombine` (`scalar_ir.run_instcombine`), translates the optimizer's output back to an
SMT term (`scalar_ir.translate`), and classifies it against the recovered `before`/`after`:

  confirmed   -- opt's output == the recovered `after`: the pass ACTUALLY does what O2T recovered from
                 source (the loop is closed).
  not-fired   -- opt left `before` unchanged: the pass did not apply this fold on the minimal IR
                 (a specific context/one-use guard the recovery abstracted away, or an over-recovery).
  divergent   -- opt produced something != before and != after: the pass did something O2T did not
                 recover (chained folds, or a genuine recovery mismatch -- worth a look).
  unsupported -- an op has no IR lowering, or opt's output is outside the IR translator's fragment
                 (declined, never mis-classified).

The formal proof says "IF the pass does this, it is sound"; this observation says "the pass actually
does this." Together they are end-to-end. The check is value-level (poison/UB refinement is left to
scalar_ir.validate_transform, which this composes with for a full picture).
"""

from __future__ import annotations

import re
import subprocess

from o2t.facts import value_tracking as vt
from o2t.intent import pass_graph as pg
from o2t.validate import scalar_ir as si

_OPCODE_RE = re.compile(r"^\s*(?:%[\w.]+\s*=\s*)?([a-z][a-z.]*)\b")


def _assumption_premises(assumptions) -> list[str]:
    """SMT premises for a fold's recovered preconditions, over the IR param names (`%name`). A guarded
    fold's `before == after` holds only under these -- and the real opt likewise won't apply the fold
    on unconstrained inputs -- so the observational equality must be checked under them."""
    clauses: list[str] = []
    for a in assumptions or []:
        if a.get("op") == "mask-pair":
            clauses.append(vt.mask_pair_smt(f"%{a['left']}", f"%{a['right']}"))
        elif isinstance(a.get("name"), str):
            smt = vt.scalar_assumption_smt(a, f"%{a['name']}")
            if smt:
                clauses.append(smt)
    return clauses


def _body_opcodes(ir: str, fn: str) -> list[str] | None:
    """The sorted multiset of instruction opcodes in `fn`'s body (SSA names ignored) -- a robust
    'did opt change the instruction structure' signal that does not depend on value numbering."""
    body = si._function_body(ir, fn)
    if body is None:
        return None
    ops: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or re.fullmatch(r"[\w.]+:", line):
            continue
        m = _OPCODE_RE.match(line)
        if m:
            ops.append(m.group(1))
    return sorted(ops)


def _terms_equal(z3_bin: str, params: dict, t1: str, t2: str, premises=()) -> bool:
    """True iff two SMT bitvector terms are equal for all inputs SATISFYING `premises` (declared from
    `params`). `premises => t1 == t2` is valid iff `premises and t1 != t2` is unsat."""
    decls = [f"(declare-const {name} (_ BitVec {w}))" for name, w in sorted(params.items())]
    asserts = [f"(assert {c})" for c in premises] + [f"(assert (not (= {t1} {t2})))"]
    smt = "\n".join(["(set-logic QF_BV)", *decls, *asserts, "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return bool(out) and out.splitlines()[0].strip() == "unsat"


def observe_fold(pair: dict, z3_bin: str, opt_bin: str = "opt", fn: str = "f") -> dict:
    """Run the REAL optimizer on a recovered fold's `before` and classify the actual transform against
    the recovered `after`. Returns {status, ...} with status in {confirmed, not-fired, divergent,
    unsupported, error}. `pair` is a recover_pair/recover_folds obligation."""
    before_ir = pg.to_llvm_ir(pair, "before", fn)
    after_ir = pg.to_llvm_ir(pair, "after", fn)
    if before_ir is None or after_ir is None:
        return {"status": "unsupported", "reason": "no IR lowering for before/after"}
    opt_ir = si.run_instcombine(before_ir, opt_bin)
    if opt_ir is None:
        return {"status": "error", "reason": "opt failed"}
    try:
        pb, before_ret, wb, _, _ = si.translate(before_ir, fn)
        _, after_ret, wa, _, _ = si.translate(after_ir, fn)
        po, obs_ret, wo, _, _ = si.translate(opt_ir, fn)
    except si.Unsupported as exc:
        return {"status": "unsupported", "reason": str(exc)}
    if po != pb or wo != wb or wa != wb:
        return {"status": "unsupported", "reason": "opt changed the signature/return width"}
    # `before == after` is already proven, so VALUE-equality alone cannot tell "opt fired" from "opt
    # left it": both are value-equal to `after`. A structural opcode diff supplies the fired signal;
    # value-equality of opt's output with the recovered `after` supplies the correctness signal.
    premises = _assumption_premises(pair.get("assumptions"))
    fired = _body_opcodes(before_ir, fn) != _body_opcodes(opt_ir, fn)
    matches_after = _terms_equal(z3_bin, pb, obs_ret, after_ret, premises)
    if matches_after:
        return {"status": "confirmed" if fired else "not-fired", "observed": opt_ir}
    # opt transformed `before` into something NOT value-equal to the recovered `after` -- since
    # `before == after` is proven, this means opt's output != before either: a real discrepancy (an
    # opt miscompile, or an IR-lowering/recovery mismatch). The teeth of the observational loop.
    return {"status": "divergent", "observed": opt_ir}
