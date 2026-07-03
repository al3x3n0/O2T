#!/usr/bin/env python3
"""Orchestrator-driven SWEEP: route a broad, multi-family pass-set through the front door and
roll the per-pass verdicts into a coverage matrix.

The orchestrator (`classify -> plan -> dispatch`) handles one pass at a time. This sweeps a
curated set of representative pass sources spanning every modeled family and aggregates:

  * which families the front door actually exercises end to end;
  * which DEEP verifiers each family dispatches (scev-intent, symexec, memory-source/model,
    slp-source/model, cfg-shape, ...);
  * where the TEETH fire -- planted-unsound or under-guarded sources the front door refutes;
  * honestly, where coverage is still advisory (a known gap returns inconclusive/planned).

A source can score into several families; only the PRIMARY family's strategies are treated as
authoritative for that source (a secondary-family dispatch on a source that isn't really that
family is recorded separately as cross-family advisory, never as the headline). This keeps the
sweep's roll-up faithful: a source's headline is decided by the checks for the family it IS.
"""

from __future__ import annotations

from dataclasses import dataclass

from o2t.orchestrate.classify import FAMILIES
from o2t.orchestrate.run import orchestrate

# family -> the verification strategy ids that family OWNS (its authoritative checks).
_FAM_STRATS = {f.name: set(f.strategies) for f in FAMILIES}
_POSITIVE = {"proved", "sound", "validated"}


@dataclass(frozen=True)
class SweepCase:
    """One curated pass source and the headline verdict the front door should reach for it."""
    source: str                 # fixture filename under tests/fixtures
    pass_name: str | None       # name hint (and canonical pass for name-driven strategies)
    family: str                 # expected PRIMARY family
    expect: str                 # "proved" | "refuted" | "advisory" (headline verdict-kind)
    note: str                   # what this case demonstrates
    requires: str | None = None  # ctx capability gate ("ast-miner"); if absent -> advisory


# The sweep manifest: representative sources across families, mixing sound, planted/under-guarded
# unsound (teeth), and a known-gap advisory. Kept light enough to run as a fixture.
MANIFEST: tuple[SweepCase, ...] = (
    SweepCase("loop_pass_scev.cpp", None, "loop-scev-recurrence", "proved",
              "SCEV recurrence intent proved from source"),
    SweepCase("intent_inference_snippet.cpp", "instcombine", "peephole", "proved",
              "peephole fold cascade sound (symbolic execution)", requires="ast-miner"),
    SweepCase("vector_pass_snippet.cpp", "slp-vectorizer", "vectorize-slp", "proved",
              "deep SLP lane-mapping + reduction contracts proved"),
    SweepCase("llvm_pass_snippet.cpp", "simplifycfg", "cfg", "proved",
              "diamond -> select if-conversion proved value-equivalent"),
    SweepCase("dse_memory_folds.cpp", "dse", "memory-dse", "refuted",
              "TEETH: planted store removed without an overwrite guard -> refuted from source"),
    SweepCase("third_party_dse_like_pass.cpp", "dse", "memory-dse", "refuted",
              "TEETH: under-guarded DSE fold (noalias-with-one-inst) refuted from source"),
    SweepCase("slp_reduction_folds.cpp", "slp-vectorizer", "vectorize-slp", "refuted",
              "TEETH: FP reduction emitted without a fast-math guard -> refuted from source"),
    SweepCase("slp_pack_folds.cpp", "slp-vectorizer", "vectorize-slp", "refuted",
              "TEETH: pack whose extract lanes != insert lanes -> refuted from source"),
    SweepCase("third_party_globalopt_like_pass.cpp", "globalopt", "global", "proved",
              "GlobalOpt dead-initializer defaulting proved observationally behavior-preserving"),
    SweepCase("global_dead_initializer_unsafe_snippet.cpp", "globalopt", "global", "refuted",
              "TEETH: initializer defaulted with no linkage/use guard -> refuted from source"),
    SweepCase("dce_dead_instruction_sound.cpp", "dce", "cleanup-dce", "proved",
              "DCE dead-instruction erasures proved under trivially-dead guards"),
    SweepCase("dce_dead_instruction_folds.cpp", "dce", "cleanup-dce", "refuted",
              "TEETH: unguarded instruction erase may remove a live use or side effect"),
    SweepCase("licm_hoist_sound.cpp", "licm", "loop-structural", "proved",
              "LICM hoist proved sound (loop-invariant + speculatable/guaranteed)"),
    SweepCase("licm_hoist_folds.cpp", "licm", "loop-structural", "refuted",
              "TEETH: hoist guarded only by loop-invariance (trapping op) -> refuted from source"),
    SweepCase("cfg_ifconv_sound.cpp", "simplifycfg", "cfg", "proved",
              "if-conversion select binding proved equivalent to the diamond (from source)"),
    SweepCase("cfg_ifconv_folds.cpp", "simplifycfg", "cfg", "refuted",
              "TEETH: select operands swapped without negating the condition -> refuted from source"),
    SweepCase("third_party_mem2reg_like_pass.cpp", "mem2reg", "promotion", "proved",
              "Mem2Reg promotion proved: real opt SSA+phi returns the memory form's value (multi-block)"),
)


def primary_checks(pass_report: dict) -> list[dict]:
    """The checks belonging to the pass's PRIMARY family -- its authoritative verdicts."""
    strats = _FAM_STRATS.get(pass_report.get("primary_family"), set())
    return [c for c in pass_report.get("checks", []) if c["strategy"] in strats]


def secondary_checks(pass_report: dict) -> list[dict]:
    """Checks dispatched for a RETAINED non-primary family -- cross-family advisory only."""
    strats = _FAM_STRATS.get(pass_report.get("primary_family"), set())
    return [c for c in pass_report.get("checks", []) if c["strategy"] not in strats]


def headline(primary: list[dict]) -> str:
    """The source's headline verdict-kind from its primary-family checks. A refutation (teeth)
    dominates; otherwise a positive proof; otherwise advisory (only inconclusive/planned)."""
    verdicts = [c.get("verdict") for c in primary]
    if any(v == "refuted" for v in verdicts):
        return "refuted"
    if any(v in _POSITIVE for v in verdicts):
        return "proved"
    return "advisory"


def effective_expect(case: SweepCase, ctx: dict) -> str:
    """A case whose required capability is absent is honestly downgraded to advisory (the deep
    verifier cannot run), so the sweep never reports a gap as a pass."""
    if case.requires and not ctx.get(case.requires):
        return "advisory"
    return case.expect


def run_sweep(ctx: dict, manifest: tuple[SweepCase, ...] = MANIFEST, fixtures_dir=None) -> dict:
    """Route every manifest case through the orchestrator and aggregate a coverage report."""
    from pathlib import Path
    fx = Path(fixtures_dir) if fixtures_dir else \
        Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    inputs = [{"source": str(fx / c.source), "pass_name": c.pass_name} for c in manifest]
    rep = orchestrate(inputs, ctx)

    rows = []
    for case, p in zip(manifest, rep["passes"]):
        pc = primary_checks(p)
        observed = headline(pc)
        expect = effective_expect(case, ctx)
        rows.append({
            "source": case.source, "expected_family": case.family,
            "primary_family": p.get("primary_family"),
            "family_ok": p.get("primary_family") == case.family,
            "expect": expect, "observed": observed, "ok": observed == expect,
            "primary": [{"strategy": c["strategy"], "verdict": c.get("verdict")} for c in pc],
            "secondary": [{"strategy": c["strategy"], "verdict": c.get("verdict")}
                          for c in secondary_checks(p)],
            "note": case.note,
        })

    families = sorted({r["primary_family"] for r in rows if r["primary_family"]})
    deep = sorted({c["strategy"] for r in rows for c in r["primary"]
                   if c["verdict"] in _POSITIVE or c["verdict"] == "refuted"})
    teeth = [r["source"] for r in rows if r["observed"] == "refuted"]
    gaps = [r["source"] for r in rows if r["observed"] == "advisory"]
    summary = {
        "cases": len(rows),
        "families_exercised": families,
        "deep_verifiers_dispatched": deep,
        "teeth_fired": teeth,
        "advisory_gaps": gaps,
        "family_routing_ok": all(r["family_ok"] for r in rows),
        "all_ok": all(r["ok"] and r["family_ok"] for r in rows),
    }
    return {"rows": rows, "summary": summary}
