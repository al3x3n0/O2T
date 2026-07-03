#!/usr/bin/env python3
"""Meta-verification: audit what every "proved" verdict actually MEANS.

A passing SMT proof is only worth what its obligation is worth. Two ways a proof can be hollow:

  1. VACUOUS PREMISES -- if a contract's assumptions are jointly unsatisfiable (a contradictory
     guard), `assume(False) => anything` proves trivially. We check each assumption set is SAT.

  2. NO TEETH -- if the obligation is a tautology that does not depend on the transform being
     correct, it proves no matter what. We test this by MUTATION: apply every single-point
     corruption of the proved transform (swap a lane, drop a guard, flip a condition, make an
     op non-associative, expose an initializer) and require EACH mutant to be REFUTED with a
     witness. A mutant that still proves is a SURVIVOR -- the obligation did not constrain that
     point, so the original proof was (at that point) vacuous and the verifier's teeth have a gap.

So this is the proof-about-the-proofs: it certifies that across all families, a "proved" verdict
is non-vacuous (its premises are consistent) and load-bearing (corrupting the transform breaks
it). The auditor has its own two-sided teeth: a deliberately tautological obligation has NO
surviving-mutant-free kill set, which `mutation_kill` flags.
"""

from __future__ import annotations

import subprocess

from o2t.validate import slp_model, globalopt_model, loop_structural_model, dce_model
from o2t.validate import memory_model
from o2t.validate.cfg_shape import prove_if_conversion
from o2t.intent.extract_cfg_model import _PARAMS as CFG_PARAMS, _DIAMOND as CFG_DIAMOND


def _killed(status, info):
    """A mutant is KILLED iff the verifier refutes it with a concrete witness. Verifiers report
    the witness as either raw `model` text (slp/globalopt/loop/cfg) or a parsed `witness` dict
    (memory) -- a kill requires the refutation to actually carry a counterexample."""
    return status == "refuted" and bool(info.get("model") or info.get("witness"))


def mutation_kill(label, family, original, mutants):
    """Run a proved contract's original prover plus its mutated variants. `original`/`mutants`
    are zero-arg callables returning (status, info). Returns one audit row: the original must
    still prove, and every mutant must be killed (refuted+witness); a surviving mutant is a gap."""
    o_status, _ = original()
    rows = []
    for m_label, m_call in mutants:
        m_status, m_info = m_call()
        rows.append({"mutation": m_label, "status": m_status, "killed": _killed(m_status, m_info)})
    survivors = [r["mutation"] for r in rows if not r["killed"]]
    return {"family": family, "contract": label, "original_proved": o_status == "proved",
            "mutants": rows, "survivors": survivors,
            "ok": o_status == "proved" and not survivors}


# --- premise satisfiability (anti-vacuity of assumptions) ----------------------------------

def assumptions_satisfiable(z3_bin, assumptions):
    """The assumption set must admit a model -- else the proof is vacuously true. Assumptions are
    eq/ne constraints over address symbols; declare them as bitvectors and check SAT."""
    syms = sorted({a for asm in assumptions for a in asm.get("args", [])})
    if not syms:
        return True
    decls = [f"(declare-const {s} (_ BitVec 32))" for s in syms]
    cons = []
    for asm in assumptions:
        a, b = asm["args"]
        op = "=" if asm["op"] == "eq" else "distinct"
        cons.append(f"(assert ({op} {a} {b}))")
    smt = "\n".join(["(set-logic QF_BV)", *decls, *cons, "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    return out.strip().splitlines()[0].strip() == "sat" if out.strip() else False


# --- per-family mutation audits ------------------------------------------------------------

def _swap(seq, i, j):
    out = list(seq)
    out[i], out[j] = out[j], out[i]
    return out


def audit_slp(z3_bin):
    """SLP pack: corrupt the lane map by one transposition -> must refute. SLP reduction: the
    proof rests on associativity, so swapping in a NON-associative op (bvsub) must refute."""
    rows = []
    for name, op, n, pack, ext, expect in slp_model.PACK_CONTRACTS:
        if expect != "proved":
            continue
        rows.append(mutation_kill(
            name, "vectorize-slp",
            lambda op=op, n=n, pack=pack, ext=ext: slp_model.prove_pack_binop(z3_bin, op, n, pack, ext),
            [(f"swap-ext[{i},{i+1}]",
              lambda op=op, n=n, pack=pack, ext=ext, i=i:
                  slp_model.prove_pack_binop(z3_bin, op, n, pack, _swap(ext, i, i + 1)))
             for i in range(n - 1)]))
    # reduction non-vacuity: associativity is load-bearing -> a non-associative op breaks it.
    for name, op, n, fp, expect in slp_model.REDUCTION_CONTRACTS:
        if expect != "proved":
            continue
        rows.append(mutation_kill(
            name, "vectorize-slp",
            lambda op=op, n=n: slp_model.prove_reduction(z3_bin, op, n, fp=False),
            [("op->bvsub(non-associative)", lambda n=n: _reduction_nonassoc(z3_bin, n))]))
    return rows


def _reduction_nonassoc(z3_bin, n):
    """A reduction over a non-associative op: sequential chain vs balanced tree must DIFFER."""
    vals = [f"x{i}" for i in range(n)]
    decls = slp_model._decls(vals, slp_model.BV)
    seq, tree = slp_model._seq(vals, "bvsub"), slp_model._tree(vals, "bvsub")
    return slp_model._check(z3_bin, "QF_BV", decls, f"(= {seq} {tree})")


def audit_globalopt(z3_bin):
    """Every proved dead-initializer defaulting must break if the initializer becomes observable
    (external linkage) or is read before any store."""
    rows = []
    for name, c in globalopt_model.GLOBALOPT_CONTRACTS.items():
        if c["expect"] != "proved":
            continue
        muts = [("external-linkage",
                 lambda c=c: globalopt_model.prove_initializer_default(z3_bin, c["accesses"], external=True)),
                ("prepend-load",
                 lambda c=c: globalopt_model.prove_initializer_default(
                     z3_bin, [("load",)] + list(c["accesses"]), external=False))]
        rows.append(mutation_kill(
            name, "global",
            lambda c=c: globalopt_model.prove_initializer_default(z3_bin, c["accesses"], c["external"]),
            muts))
    return rows


def audit_dce(z3_bin):
    """Every proved cleanup erasure must break when one observability guard is removed."""
    rows = []
    rows.append(mutation_kill(
        "erase-trivially-dead", "cleanup-dce",
        lambda: dce_model.prove_dead_erase(z3_bin, no_live_use=True, no_side_effect=True),
        [
            ("allow-live-use",
             lambda: dce_model.prove_dead_erase(z3_bin, no_live_use=False, no_side_effect=True)),
            ("allow-side-effect",
             lambda: dce_model.prove_dead_erase(z3_bin, no_live_use=True, no_side_effect=False)),
        ]))
    rows.append(mutation_kill(
        "erase-dead-loop-instruction", "cleanup-dce",
        lambda: dce_model.prove_dead_loop_instruction_erase(
            z3_bin,
            no_loop_result_use=True,
            no_loop_control_effect=True,
            no_loop_side_effect=True,
        ),
        [
            ("allow-loop-result-use",
             lambda: dce_model.prove_dead_loop_instruction_erase(
                 z3_bin, False, True, True)),
            ("allow-loop-control-effect",
             lambda: dce_model.prove_dead_loop_instruction_erase(
                 z3_bin, True, False, True)),
            ("allow-loop-side-effect",
             lambda: dce_model.prove_dead_loop_instruction_erase(
                 z3_bin, True, True, False)),
        ]))
    rows.append(mutation_kill(
        "erase-unused-alloca", "cleanup-dce",
        lambda: dce_model.prove_unused_alloca_erase(
            z3_bin,
            no_uses=True,
            no_escape=True,
            no_lifetime_effect=True,
        ),
        [
            ("allow-alloca-use",
             lambda: dce_model.prove_unused_alloca_erase(
                 z3_bin, False, True, True)),
            ("allow-alloca-escape",
             lambda: dce_model.prove_unused_alloca_erase(
                 z3_bin, True, False, True)),
            ("allow-lifetime-effect",
             lambda: dce_model.prove_unused_alloca_erase(
                 z3_bin, True, True, False)),
        ]))
    return rows


def audit_loop_structural(z3_bin):
    """Every proved hoist must break if its established legality is removed: a varying operand
    (invariance) or dropping the safety guard (trap-safety)."""
    rows = []
    for name, c in loop_structural_model.LOOP_STRUCTURAL_CONTRACTS.items():
        if c["expect"] != "proved":
            continue
        if c["kind"] == "invariance":
            rows.append(mutation_kill(
                name, "loop-structural",
                lambda c=c: loop_structural_model.prove_hoist_invariance(z3_bin, c["invariant"]),
                [("operand->variant",
                  lambda: loop_structural_model.prove_hoist_invariance(z3_bin, invariant=False))]))
        else:
            rows.append(mutation_kill(
                name, "loop-structural",
                lambda c=c: loop_structural_model.prove_hoist_safety(z3_bin, c["guaranteed"], c["speculatable"]),
                [("drop-safety-guard",
                  lambda: loop_structural_model.prove_hoist_safety(z3_bin, guaranteed=False, speculatable=False))]))
    return rows


def audit_cfg(z3_bin):
    """The proved identity if-conversion must break if the select operands are swapped without
    negating the condition, or the condition is negated without swapping operands."""
    sound = {"cond": "%c", "negated": False, "true": "%a", "false": "%b"}
    swap = {"cond": "%c", "negated": False, "true": "%b", "false": "%a"}
    flip = {"cond": "%c", "negated": True, "true": "%a", "false": "%b"}
    rows = [mutation_kill(
        "ifconv-identity", "cfg",
        lambda: prove_if_conversion(z3_bin, CFG_PARAMS, CFG_DIAMOND, sound),
        [("swap-operands-no-negate", lambda: prove_if_conversion(z3_bin, CFG_PARAMS, CFG_DIAMOND, swap)),
         ("negate-no-swap", lambda: prove_if_conversion(z3_bin, CFG_PARAMS, CFG_DIAMOND, flip))])]
    return rows


def _corrupt_forward(after, observable):
    """Rebind the observed load to a FRESH value -- a store-forwarding/redundant-load fold that
    forwards the wrong value. The genuine loaded value differs, so the contract must refute."""
    name = observable.split(":", 1)[1]
    return [memory_model._bind(name, "cc_wrong_forward") if o.get("op") == "bind" and o.get("name") == name
            else o for o in after]


def audit_memory(z3_bin):
    """Every memory contract: (a) its assumption set must be SAT (no contradictory guard), and
    (b) it must have LOAD-BEARING teeth -- dropping an alias guard refutes an alias-guarded contract,
    and forwarding the wrong value refutes a store-forwarding/redundant-load contract (so even the
    unconditional `store-forward` is exercised, not silently skipped)."""
    rows, premise = [], []
    for name, before, after, observable, assumptions in memory_model.CONTRACTS:
        sat = assumptions_satisfiable(z3_bin, assumptions)
        premise.append({"contract": name, "assumptions": len(assumptions),
                        "satisfiable": sat, "ok": sat})
        muts = []
        if assumptions:
            muts.append(("drop-alias-guard",
                         lambda before=before, after=after, observable=observable:
                             memory_model.prove_memory_transform(z3_bin, before, after, observable, ())))
        if observable.startswith("load:"):
            wrong = _corrupt_forward(after, observable)
            muts.append(("forward-wrong-value",
                         lambda before=before, wrong=wrong, observable=observable, assumptions=assumptions:
                             memory_model.prove_memory_transform(z3_bin, before, wrong, observable, assumptions)))
        if not muts:
            continue
        rows.append(mutation_kill(
            name, "memory-dse",
            lambda before=before, after=after, observable=observable, assumptions=assumptions:
                memory_model.prove_memory_transform(z3_bin, before, after, observable, assumptions),
            muts))
    return rows, premise


def audit_byte_memory(z3_bin):
    """Byte-granular DSE: a FULL overwrite proves, and SHIFTING the killing store one byte later (so
    byte 0 of the dead store is no longer covered) must refute with a surviving-byte witness. This
    audits `BYTE_CONTRACTS` -- the partial-overwrite soundness boundary the word model can't see."""
    rows = []
    for name, dead_size, kill_offset, kill_size in memory_model.BYTE_CONTRACTS:
        if not memory_model.overwrite_covers(dead_size, kill_offset, kill_size):
            continue                       # partial-overwrite entries are the unsound controls
        before, after = memory_model.byte_dse_case(dead_size, kill_offset, kill_size)
        # shift the kill one byte later: it can no longer cover byte 0, so a byte survives (unsound).
        shifted_b, shifted_a = memory_model.byte_dse_case(dead_size, kill_offset + 1, kill_size)
        assert not memory_model.overwrite_covers(dead_size, kill_offset + 1, kill_size)
        rows.append(mutation_kill(
            name, "memory-dse-byte",
            lambda before=before, after=after: memory_model.prove_byte_transform(z3_bin, before, after),
            [("shift-kill-offset",
              lambda shifted_b=shifted_b, shifted_a=shifted_a:
                  memory_model.prove_byte_transform(z3_bin, shifted_b, shifted_a))]))
    return rows


def run_audit(z3_bin):
    """Audit every family. Returns mutation rows, premise-SAT rows, and a roll-up."""
    mem_rows, premise_rows = audit_memory(z3_bin)
    rows = (audit_slp(z3_bin) + audit_globalopt(z3_bin) + audit_dce(z3_bin)
            + audit_loop_structural(z3_bin)
            + audit_cfg(z3_bin) + mem_rows + audit_byte_memory(z3_bin))
    total_mutants = sum(len(r["mutants"]) for r in rows)
    killed = sum(1 for r in rows for m in r["mutants"] if m["killed"])
    survivors = [(r["family"], r["contract"], m)
                 for r in rows for m in r["survivors"] for _ in [0]]
    premise_bad = [p for p in premise_rows if not p["ok"]]
    ok = all(r["ok"] for r in rows) and not premise_bad
    return {
        "rows": rows, "premise_checks": premise_rows,
        "families": sorted({r["family"] for r in rows}),
        "contracts_audited": len(rows), "mutants": total_mutants, "killed": killed,
        "survivors": [{"family": f, "contract": c, "mutation": m} for f, c, m in survivors],
        "premises_satisfiable": not premise_bad, "ok": ok,
    }
