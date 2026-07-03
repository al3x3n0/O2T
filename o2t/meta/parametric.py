#!/usr/bin/env python3
"""Generalize the deep contracts: re-prove them across a grid of bit WIDTHS and lane counts N.

A contract proved at i32 / n=4 is a single instance. The multiwidth track (`prove/multiwidth.py`)
already lifts scalar identities to i8/i16/i32/i64 but explicitly defers the structural deep
contracts. This closes that: every width-parametric deep contract (SLP pack + reduction, GlobalOpt
dead-initializer, LICM hoist-invariance, DSE memory) is re-discharged at WIDTHS = {8,16,32,64},
the width-insensitive cleanup-DCE contracts are replayed at the same width buckets, and the
arity-parametric SLP contracts also at N = {2,4,8,16}. For EACH point we check both directions --
the sound contract still PROVES and its single-point corruption still REFUTES -- so the universal
claim ("holds at every width / every n") is backed by proof, not one sample, and the teeth are
shown to bite at every width. A point where a proof fails or a corruption survives is a real
soundness finding (a width/arity-specific bug).
"""

from __future__ import annotations

from o2t.validate import (
    slp_model,
    globalopt_model,
    loop_structural_model,
    memory_model,
    dce_model,
)

WIDTHS = (8, 16, 32, 64)
NS = (2, 4, 8, 16)


def _swap01(seq):
    out = list(seq)
    out[0], out[1] = out[1], out[0]
    return out


def _grid_slp(z3_bin):
    """SLP pack + integer reduction across widths AND lane counts; each must prove, its
    single-lane corruption must refute."""
    rows = []
    for w in WIDTHS:
        for n in NS:
            ident = list(range(n))
            prove = slp_model.prove_pack_binop(z3_bin, "add", n, ident, ident, width=w)[0]
            refute = slp_model.prove_pack_binop(z3_bin, "add", n, ident, _swap01(ident), width=w)[0]
            red = slp_model.prove_reduction(z3_bin, "add", n, fp=False, width=w)[0]
            rows.append({"contract": "slp-pack", "width": w, "n": n,
                         "proved": prove == "proved", "teeth": refute == "refuted",
                         "ok": prove == "proved" and refute == "refuted"})
            rows.append({"contract": "slp-reduction", "width": w, "n": n,
                         "proved": red == "proved", "teeth": None, "ok": red == "proved"})
    return rows


def _grid_width_only(z3_bin):
    """GlobalOpt, LICM invariance, and DSE memory across widths (arity is fixed for these)."""
    rows = []
    name, before, after, observable, assumptions = memory_model.CONTRACTS[0]   # dse-overwrite
    for w in WIDTHS:
        g_p = globalopt_model.prove_initializer_default(z3_bin, [], external=False, width=w)[0]
        g_t = globalopt_model.prove_initializer_default(z3_bin, [], external=True, width=w)[0]
        rows.append({"contract": "globalopt-default", "width": w, "n": None,
                     "proved": g_p == "proved", "teeth": g_t == "refuted",
                     "ok": g_p == "proved" and g_t == "refuted"})
        l_p = loop_structural_model.prove_hoist_invariance(z3_bin, True, width=w)[0]
        l_t = loop_structural_model.prove_hoist_invariance(z3_bin, False, width=w)[0]
        rows.append({"contract": "licm-invariance", "width": w, "n": None,
                     "proved": l_p == "proved", "teeth": l_t == "refuted",
                     "ok": l_p == "proved" and l_t == "refuted"})
        m_p = memory_model.prove_memory_transform(z3_bin, before, after, observable, assumptions, width=w)[0]
        m_t = memory_model.prove_memory_transform(z3_bin, before, after, observable, (), width=w)[0]
        rows.append({"contract": "dse-overwrite", "width": w, "n": None,
                     "proved": m_p == "proved", "teeth": m_t == "refuted",
                     "ok": m_p == "proved" and m_t == "refuted"})
    return rows


def _grid_cleanup_dce(z3_bin):
    """DCE cleanup obligations are Boolean/structural, but replay them in every width bucket so
    the parametric matrix tracks cleanup coverage alongside width-sensitive contracts."""
    rows = []
    for w in WIDTHS:
        d_p = dce_model.prove_dead_erase(z3_bin, no_live_use=True, no_side_effect=True)[0]
        d_t = dce_model.prove_dead_erase(z3_bin, no_live_use=False, no_side_effect=True)[0]
        rows.append({"contract": "dce-dead-instruction", "width": w, "n": None,
                     "proved": d_p == "proved", "teeth": d_t == "refuted",
                     "ok": d_p == "proved" and d_t == "refuted"})
        l_p = dce_model.prove_dead_loop_instruction_erase(
            z3_bin, True, True, True)[0]
        l_t = dce_model.prove_dead_loop_instruction_erase(
            z3_bin, True, False, True)[0]
        rows.append({"contract": "dce-dead-loop-instruction", "width": w, "n": None,
                     "proved": l_p == "proved", "teeth": l_t == "refuted",
                     "ok": l_p == "proved" and l_t == "refuted"})
        a_p = dce_model.prove_unused_alloca_erase(
            z3_bin, True, True, True)[0]
        a_t = dce_model.prove_unused_alloca_erase(
            z3_bin, False, True, True)[0]
        rows.append({"contract": "dce-unused-alloca", "width": w, "n": None,
                     "proved": a_p == "proved", "teeth": a_t == "refuted",
                     "ok": a_p == "proved" and a_t == "refuted"})
    return rows


def run_parametric(z3_bin):
    """Sweep the full width x n grid. Returns rows + a roll-up of proofs/teeth that held."""
    rows = _grid_slp(z3_bin) + _grid_width_only(z3_bin) + _grid_cleanup_dce(z3_bin)
    failures = [r for r in rows if not r["ok"]]
    contracts = sorted({r["contract"] for r in rows})
    proofs = sum(1 for r in rows if r["proved"])
    teeth = sum(1 for r in rows if r["teeth"])
    return {
        "rows": rows, "contracts": contracts,
        "widths": list(WIDTHS), "lane_counts": list(NS),
        "points": len(rows), "proofs_held": proofs, "teeth_bit": teeth,
        "failures": failures, "ok": not failures,
    }
