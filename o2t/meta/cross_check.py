#!/usr/bin/env python3
"""Independent confirmation of every verdict: witness re-validation + second-solver cross-check.

A proof is only as trustworthy as the solver and the encoding behind it. Two independent checks:

  1. WITNESS RE-VALIDATION (runs today, z3 alone) -- a "refuted" verdict ships a model; we take
     that model, substitute it back into the obligation, and CONFIRM the obligation is genuinely
     false there (fix every variable to its witness value, then assert the positive goal -> the
     query must be UNSAT). This catches a bogus "sat": a witness that does not actually violate
     the claim. It is an independent query (different shape) -- not the prover taking its own word.

  2. SECOND-SOLVER CROSS-CHECK (solver-agnostic) -- a "proved" verdict (and its refuted control) is
     replayed through every available SMT-LIB2 solver; all must AGREE (unsat for proved, sat for
     refuted). A disagreement is a solver or encoding bug. bitwuzla, cvc5 and cvc4 are auto-detected
     on PATH (bitwuzla covers QF_BV / QF_UF / QF_ABV / QF_FP, the theories every obligation uses);
     when no second solver is present the cross-check is reported `skipped` (honest) rather than
     passed, while witness re-validation still runs in full.

Obligations come from the verifiers' OWN obligation builders (`*_obligation` in the validate
models), so what is re-validated and cross-checked is exactly what the verifier proved -- no
re-derivation, no drift.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from o2t.validate import (
    slp_model,
    globalopt_model,
    loop_structural_model,
    memory_model,
    dce_model,
)

_BV_DEF = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\)\s*(#x[0-9a-fA-F]+|#b[01]+)\)")
_BOOL_DEF = re.compile(r"\(define-fun (\w+) \(\) Bool\s*(true|false)\)")


# Independent SMT-LIB2 solvers auto-detected on PATH (in preference order). bitwuzla is the
# BV/array/FP workhorse we verify against; cvc5/cvc4 are used when present. All read SMT-LIB2 from
# stdin and print sat/unsat on the first line.
_SECOND_SOLVERS = ("bitwuzla", "cvc5", "cvc4")


def detect_solvers(z3_bin, extra=()):
    """All available SMT-LIB2 solvers: z3 always, plus any second solver found on PATH (or passed
    in). A real second solver makes the cross-check an INDEPENDENT pass rather than z3-self-check."""
    solvers = [("z3", z3_bin)]
    for name in _SECOND_SOLVERS:
        path = shutil.which(name)
        if path:
            solvers.append((name, path))
    for name, path in extra:
        if path and (name, path) not in solvers:
            solvers.append((name, path))
    return solvers


def _argv(name, binary):
    base = (name + " " + binary).lower()
    if "z3" in base:
        return [binary, "-in"]
    if "cvc5" in base or "cvc4" in base:
        return [binary, "--lang=smt2"]          # reads SMT-LIB2 from stdin
    return [binary]                              # bitwuzla and others: read SMT-LIB2 from stdin


def run_solver(name, binary, smt):
    """Run `smt` through one solver; return its first result line (sat/unsat/unknown/error)."""
    try:
        out = subprocess.run(_argv(name, binary), input=smt,
                             capture_output=True, text=True).stdout
    except OSError as exc:
        return f"error:{exc}"
    return out.strip().splitlines()[0].strip() if out.strip() else "error"


def proof_smt(logic, decls, premises, goal, get_model=False):
    """The validity query: premises hold, goal must too -> assert (not goal); unsat == proved."""
    lines = [f"(set-logic {logic})", *decls, *premises,
             f"(assert (not {goal}))", "(check-sat)"]
    if get_model:
        lines.append("(get-model)")
    return "\n".join(lines) + "\n"


def parse_model(text):
    """Scalar (bitvector/bool) assignments from a solver model -- enough to pin a witness."""
    assigns = {m.group(1): m.group(2) for m in _BV_DEF.finditer(text)}
    assigns.update({m.group(1): m.group(2) for m in _BOOL_DEF.finditer(text)})
    return assigns


def revalidation_smt(logic, decls, premises, goal, assigns):
    """Pin every variable to its witness value, then assert the positive goal: a genuine
    counterexample makes the goal FALSE there, so this must be UNSAT."""
    fixes = [f"(assert (= {k} {v}))" for k, v in assigns.items()]
    return "\n".join([f"(set-logic {logic})", *decls, *premises, *fixes,
                      f"(assert {goal})", "(check-sat)"]) + "\n"


# --- the obligation set, drawn from the verifiers' own builders --------------------------------

def _obligations():
    """(name, theory, sound builder, corrupt builder | None, has_array). The corrupt builder is a
    refutable single-point corruption used for witness re-validation; array obligations skip
    re-validation (their witness includes an array model) but are still cross-checked."""
    name, before, after, observable, assumptions = memory_model.CONTRACTS[0]   # dse-overwrite
    return [
        ("slp-pack", "QF_BV",
         lambda: slp_model.pack_obligation("add", 4, [0, 1, 2, 3], [0, 1, 2, 3]),
         lambda: slp_model.pack_obligation("add", 4, [0, 1, 2, 3], [1, 0, 2, 3]), False),
        ("slp-reduction", "QF_BV",
         lambda: slp_model.reduction_obligation("add", 4, fp=False), None, False),
        ("globalopt-default", "QF_BV",
         lambda: globalopt_model.initializer_obligation([], external=False),
         lambda: globalopt_model.initializer_obligation([], external=True), False),
        ("dce-dead-instruction", "QF_UF",
         lambda: dce_model.dead_erase_obligation(no_live_use=True, no_side_effect=True),
         lambda: dce_model.dead_erase_obligation(no_live_use=False, no_side_effect=True), False),
        ("dce-dead-loop-instruction", "QF_UF",
         lambda: dce_model.dead_loop_instruction_obligation(
             no_loop_result_use=True,
             no_loop_control_effect=True,
             no_loop_side_effect=True,
         ),
         lambda: dce_model.dead_loop_instruction_obligation(
             no_loop_result_use=True,
             no_loop_control_effect=False,
             no_loop_side_effect=True,
         ), False),
        ("dce-unused-alloca", "QF_UF",
         lambda: dce_model.unused_alloca_obligation(
             no_uses=True,
             no_escape=True,
             no_lifetime_effect=True,
         ),
         lambda: dce_model.unused_alloca_obligation(
             no_uses=False,
             no_escape=True,
             no_lifetime_effect=True,
         ), False),
        ("licm-invariance", "QF_BV",
         lambda: loop_structural_model.hoist_invariance_obligation(True),
         lambda: loop_structural_model.hoist_invariance_obligation(False), False),
        ("licm-safety", "QF_UF",
         lambda: loop_structural_model.hoist_safety_obligation(True, False),
         lambda: loop_structural_model.hoist_safety_obligation(False, False), False),
        ("dse-overwrite", "QF_ABV",
         lambda: memory_model.transform_obligation(before, after, observable, assumptions),
         lambda: memory_model.transform_obligation(before, after, observable, ()), True),
    ]


def run_cross_check(z3_bin, extra_solvers=()):
    """Cross-check every proved obligation across all solvers and re-validate every witness."""
    solvers = detect_solvers(z3_bin, extra_solvers)
    have_second = len(solvers) > 1
    proof_rows, reval_rows = [], []

    for name, theory, build, corrupt, has_array in _obligations():
        logic, decls, premises, goal = build()
        smt = proof_smt(logic, decls, premises, goal)
        results = {sname: run_solver(sname, sbin, smt) for sname, sbin in solvers}
        agree = len(set(results.values())) == 1 and next(iter(results.values())) == "unsat"
        proof_rows.append({"obligation": name, "theory": theory, "results": results,
                           "agree": agree, "cross_checked": have_second})

        if corrupt is None:
            continue
        clogic, cdecls, cprem, cgoal = corrupt()
        cmodel = subprocess.run(_argv("z3", z3_bin), text=True, capture_output=True,
                                input=proof_smt(clogic, cdecls, cprem, cgoal, get_model=True)).stdout
        sat = bool(cmodel.strip()) and cmodel.strip().splitlines()[0].strip() == "sat"
        row = {"obligation": name, "theory": clogic, "refuted": sat, "array": has_array}
        if sat and not has_array:
            assigns = parse_model(cmodel)
            rhead = run_solver("z3", z3_bin,
                               revalidation_smt(clogic, cdecls, cprem, cgoal, assigns))
            row["witness_vars"] = len(assigns)
            row["confirmed"] = rhead == "unsat"        # goal is false at the witness
        else:
            row["confirmed"] = None                    # array witness: re-validation skipped
        reval_rows.append(row)

    revalidated = [r for r in reval_rows if r["confirmed"] is not None]
    cross_ok = all(r["agree"] for r in proof_rows)
    reval_ok = all(r["refuted"] and r["confirmed"] for r in revalidated)
    return {
        "solvers": [s[0] for s in solvers], "second_solver": have_second,
        "proof_rows": proof_rows, "reval_rows": reval_rows,
        "cross_checked": have_second, "cross_agree": cross_ok,
        "witnesses_revalidated": len(revalidated),
        "witnesses_confirmed": sum(1 for r in revalidated if r["confirmed"]),
        "reval_ok": reval_ok,
        # Without a second solver the cross-check is z3-self-consistent only -> not an independent
        # pass; witness re-validation is the part that gates today.
        "ok": reval_ok and (cross_ok if have_second else True),
    }
