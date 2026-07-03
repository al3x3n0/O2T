#!/usr/bin/env python3
"""Verification STRATEGIES and the per-pass check plan.

`classify.py` says what family a pass is; this module maps each family's strategy ids to a
concrete, runnable check: which O2T tool to invoke, what binaries it needs, whether it runs
on the pass SOURCE or on a test .ll through a real `opt` pass, and how to read its verdict.
`plan_for` turns a classification into an ordered list of `PlannedCheck`s, each marked
feasible (prerequisites met) or skipped-with-reason -- so the scheduler dispatches only what
the environment supports, and says honestly why it skipped the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
DEFAULT_LOOP_LL = ROOT / "tests" / "fixtures" / "translation_validation.ll"


@dataclass(frozen=True)
class Strategy:
    sid: str
    label: str
    tool: str                       # tool filename under tools/
    needs: tuple[str, ...]          # required resolved binaries in the run context
    target: str                     # "source" (the pass .cpp) or "pass-runner" (opt -passes)
    note: str = ""
    canonical_pass: str = ""         # a fixed `opt` pass this strategy validates (always runnable)


# The verification surface, keyed by the strategy ids the families reference. A strategy is
# RUNNABLE here only if its tool + prerequisites are wired; otherwise it is planned and
# reported as "no-runner" so coverage gaps are explicit, never silent.
STRATEGIES: dict[str, Strategy] = {
    "scev-intent": Strategy(
        "scev-intent", "SCEV intent recovery (source → recurrence → Z3 proof)",
        "cv-mine-pass-scev.py", ("z3",), "source"),
    "symexec-fold-cascade": Strategy(
        "symexec-fold-cascade", "Peephole fold-cascade symbolic execution (source)",
        "cv-extract-pass-model.py", ("z3", "ast-miner"), "source"),
    # Closed-loop InstCombine translation validation: prove the LITERAL `opt -passes=instcombine`
    # output (translate before/after scalar IR to SMT, prove the returned value equal for all
    # inputs) -- closes the contract<->IR gap for peephole. Validates a fixed pass (z3+opt).
    "instcombine-ir": Strategy(
        "instcombine-ir", "InstCombine translation validation on real opt output (scalar IR->SMT)",
        "cv-validate-instcombine-ir.py", ("z3", "opt"), "pass-runner", canonical_pass="instcombine"),
    # Symbolic execution of the REAL compiled C++ of pass folds: enumerate the pass's actual
    # control-flow paths and prove each rewrite refines the input under the facts its branches
    # established (an under-guarded fold is refuted). Verifies the implementation, not a model.
    "symexec-real-pass": Strategy(
        "symexec-real-pass", "Symbolic execution of the real fold C++ (per-path refinement)",
        "cv-symexec-real-pass.py", ("z3", "clang"), "canonical"),
    # Optional bounded model-checking cross-check: run a real fold harness under CBMC/ESBMC with
    # nondet inputs + query outcomes and assert the same poison-aware refinement property.
    "modelcheck-real-pass": Strategy(
        "modelcheck-real-pass", "CBMC/ESBMC bounded model check of the real fold C++",
        "cv-modelcheck-real-pass.py", ("model-checker",), "canonical"),
    # KLEE-driven symbolic execution: true symbolic branching over input shape x guard outcomes
    # (forks on `&&` short-circuits and the input dispatch), per-path refinement. Needs KLEE.
    "klee-symexec": Strategy(
        "klee-symexec", "KLEE-driven symbolic execution of the real fold (per-path refinement)",
        "cv-klee-symexec-pass.py", ("z3", "klee"), "canonical"),
    # The same scalar IR->SMT translation validator generalizes to any value-preserving scalar
    # pass: prove the LITERAL `opt -passes=<P>` output keeps each function's returned value.
    "reassociate-ir": Strategy(
        "reassociate-ir", "Reassociate translation validation on real opt output (scalar IR->SMT)",
        "cv-validate-scalar-tv.py", ("z3", "opt"), "pass-runner", canonical_pass="reassociate"),
    "early-cse-ir": Strategy(
        "early-cse-ir", "EarlyCSE translation validation on real opt output (scalar IR->SMT)",
        "cv-validate-scalar-tv.py", ("z3", "opt"), "pass-runner", canonical_pass="early-cse"),
    "translation-validation": Strategy(
        "translation-validation", "Closed-loop translation validation (real opt output)",
        "cv-translation-validate.py", ("z3", "opt"), "pass-runner"),
    # Source-driven deep LICM/loop-structural verification: recover the pass's OWN hoist folds +
    # the legality they establish (invariance / speculatable / guaranteed) and prove each
    # (refuting a hoist guarded only by loop-invariance -- a stale value or a new trap).
    "licm-source": Strategy(
        "licm-source", "Mine the pass's hoist folds and prove them (invariance + safety)",
        "cv-mine-licm-pass.py", ("z3",), "source"),
    # Deep loop-structural verification: proves the canonical hoist contracts (invariance +
    # trap-safety) with teeth. Validates fixed contracts, so feasible from z3 alone ("canonical").
    "licm-model": Strategy(
        "licm-model", "LICM hoist legality proof (loop-invariance + trap-safety, with teeth)",
        "cv-validate-licm.py", ("z3",), "canonical"),
    # BOUNDED closed-loop TV for loop-CFG transforms: fully unroll a constant-trip loop with and
    # without the transform (loop-rotate / unswitch) and prove the acyclic forms equal -- the
    # transform preserved the computation for that trip count. Validates a fixed pass (z3+opt).
    "loop-cfg-ir": Strategy(
        "loop-cfg-ir", "Loop-rotate/unswitch bounded translation validation (unroll + prove)",
        "cv-validate-loop-cfg-ir.py", ("z3", "opt"), "pass-runner", canonical_pass="loop-rotate"),
    # UNBOUNDED loop equivalence: prove a structure-preserving body fold keeps the loop's value for
    # ALL trip counts by induction over the loop-carried state (init/guard/step/result). No unrolling.
    "loop-induction": Strategy(
        "loop-induction", "Unbounded loop equivalence by induction (all trip counts)",
        "cv-validate-loop-induction.py", ("z3", "opt"), "pass-runner", canonical_pass="instcombine"),
    # Simulation-relation loop equivalence: prove structurally-DIFFERENT loops (reshaped state)
    # equal for all trip counts via an inductive relation R. Canonical contracts, z3-only.
    "loop-simulation": Strategy(
        "loop-simulation", "Simulation-relation loop equivalence (reshaped state, with teeth)",
        "cv-validate-loop-simulation.py", ("z3",), "canonical"),
    # UNBOUNDED loop-rotate validation: reconstruct + self-verify a canonical model from the real
    # rotated IR (guard motion) and prove it equivalent to the original for all trip counts.
    "loop-rotate-ir": Strategy(
        "loop-rotate-ir", "Loop-rotate unbounded validation (guard-motion, self-verified)",
        "cv-validate-loop-rotate.py", ("z3", "opt"), "pass-runner", canonical_pass="loop-rotate"),
    # UNBOUNDED multi-exit loop equivalence: model the ordered exits + step and prove two such
    # loops equal for all trip counts (per-exit decision/result + step). Canonical, z3-only.
    "loop-multiexit": Strategy(
        "loop-multiexit", "Multi-exit loop equivalence (ordered exits + step, with teeth)",
        "cv-validate-loop-multiexit.py", ("z3",), "canonical"),
    # UNBOUNDED nested loop equivalence: prove the inner loops define the same transition, then the
    # outer loops with the inner abstracted as one uninterpreted function (compositional, QF_UFBV).
    "loop-nested": Strategy(
        "loop-nested", "Nested loop equivalence (compositional: inner summary + outer UF)",
        "cv-validate-loop-nested.py", ("z3",), "canonical"),
    # Source-driven deep memory verification: recover the pass's OWN memory transforms + guards
    # and prove each sound over a theory of arrays (refuting a fold with insufficient guards).
    "memory-source": Strategy(
        "memory-source", "Mine the pass's memory transforms and prove them (theory of arrays)",
        "cv-mine-memory-pass.py", ("z3",), "source"),
    # Source-driven SLP verification: recover the pass's OWN reduction shapes + fast-math guards
    # and prove each (refuting an FP reduction emitted without a reassoc guard).
    "slp-source": Strategy(
        "slp-source", "Mine the pass's SLP reductions and prove them (deep reduction model)",
        "cv-mine-slp-pass.py", ("z3",), "source"),
    # Deep SLP verification: proves the canonical lane-mapping + reduction contracts (a
    # mismatched pack or an FP reduction without fast-math is refuted). z3-only (target "canonical").
    "slp-model": Strategy(
        "slp-model", "SLP lane-mapping + reduction associativity proof (incl. FP teeth)",
        "cv-validate-slp.py", ("z3",), "canonical"),
    # Closed-loop SLP translation validation: prove the LITERAL `opt -passes=slp-vectorizer` output
    # (model memory as cells, prove each output cell's value equals the scalar version's for all
    # inputs) -- closes the contract<->IR gap for vectorization. Validates a fixed pass (z3+opt).
    "slp-ir": Strategy(
        "slp-ir", "SLP translation validation on real opt output (vector IR->SMT, per-cell)",
        "cv-validate-slp-ir.py", ("z3", "opt"), "pass-runner", canonical_pass="slp-vectorizer"),
    # Deep theory-of-arrays memory verification: proves the canonical DSE / store-forwarding /
    # redundant-load contracts for ALL memories/addresses/values (QF_ABV), with alias teeth. It
    # validates fixed contracts, so it is feasible from z3 alone (target "canonical").
    "memory-model": Strategy(
        "memory-model", "Theory-of-arrays memory transform proof (DSE/forwarding, alias-sensitive)",
        "cv-validate-memory.py", ("z3",), "canonical"),
    # These route through the generic source-intent -> formal-obligation -> Z3 pipeline
    # (cv-infer-optimization-intent | cv-validate-intent-candidates), which models DSE memory
    # facts, GlobalOpt dead-initializer contracts, and SLP transaction graphs.
    "dse-facts": Strategy(
        "dse-facts", "DSE memory-fact audit (overwrite/removability legality)",
        "cv-infer-optimization-intent.py", ("z3",), "source"),
    "globalopt-witness": Strategy(
        "globalopt-witness", "GlobalOpt dead-initializer witness contract",
        "cv-infer-optimization-intent.py", ("z3",), "source"),
    # Source-driven deep GlobalOpt verification: recover the pass's OWN initializer-defaulting
    # folds + the auditable legality they establish (internal linkage / no observing use) and
    # prove each (refuting a fold that defaults an externally-visible or possibly-loaded global).
    "globalopt-source": Strategy(
        "globalopt-source", "Mine the pass's dead-initializer folds and prove them (semantic)",
        "cv-mine-globalopt-pass.py", ("z3",), "source"),
    # Deep GlobalOpt verification: proves the canonical dead-initializer defaulting contracts --
    # defaulting preserves every observable load -- with read-before-store / external-linkage
    # teeth. Validates fixed contracts, so it is feasible from z3 alone (target "canonical").
    "globalopt-model": Strategy(
        "globalopt-model", "GlobalOpt dead-initializer semantic proof (observability, with teeth)",
        "cv-validate-globalopt.py", ("z3",), "canonical"),
    # Source-driven DCE verification: recover the pass's OWN instruction-erasure folds and the
    # deadness facts they establish, then prove erasure is unobservable (refuting bare erases).
    "dce-source": Strategy(
        "dce-source", "Mine the pass's dead-instruction erasures and prove them",
        "cv-mine-dce-pass.py", ("z3",), "source"),
    # Deep DCE verification: proves the canonical dead-instruction erasure contract -- no live
    # use and no side effect -- with live-use / side-effect teeth. z3-only.
    "dce-model": Strategy(
        "dce-model", "DCE erasure semantic proof (no live use / no effects, with teeth)",
        "cv-validate-dce.py", ("z3",), "canonical"),
    "slp-transaction": Strategy(
        "slp-transaction", "SLP transaction-graph intent validation",
        "cv-infer-optimization-intent.py", ("z3",), "source"),
    # Closed-loop DSE translation validation: prove the LITERAL `opt -passes=dse` output (parse
    # the real surviving instructions, prove final memory preserved over a theory of arrays) --
    # closes the contract<->IR gap for DSE. Validates a fixed pass, feasible from z3+opt alone.
    "dse-ir": Strategy(
        "dse-ir", "DSE translation validation on real opt output (theory of arrays)",
        "cv-validate-dse-ir.py", ("z3", "opt"), "pass-runner", canonical_pass="dse"),
    "cfg-shape": Strategy(
        "cfg-shape", "SimplifyCFG diamond→select if-conversion contract (real opt output)",
        "cv-validate-cfg.py", ("z3", "opt"), "pass-runner", canonical_pass="simplifycfg"),
    # Source-driven CFG verification: recover the pass's OWN if-conversion folds (how each
    # CreateSelect binds the branch condition + then/else values) and prove each (refuting a
    # fold that swaps the select operands without negating the condition).
    "cfg-source": Strategy(
        "cfg-source", "Mine the pass's if-conversion folds and prove them (diamond→select)",
        "cv-mine-cfg-pass.py", ("z3",), "source"),
    # Closed-loop Mem2Reg translation validation: prove the LITERAL `opt -passes=mem2reg` output
    # (symbolically execute the memory before and the SSA+phi after over the shared CFG, prove the
    # returns equal) -- the first multi-block + phi validator. Validates a fixed pass (z3+opt).
    "mem2reg-ir": Strategy(
        "mem2reg-ir", "Mem2Reg translation validation on real opt output (multi-block + phi)",
        "cv-validate-mem2reg-ir.py", ("z3", "opt"), "pass-runner", canonical_pass="mem2reg"),
}


@dataclass
class PlannedCheck:
    strategy: str
    label: str
    feasible: bool
    target: str
    reason: str = ""                # why skipped, when not feasible


def plan_for(classification, ctx: dict, has_source: bool = True) -> list[PlannedCheck]:
    """Turn a `Classification` into an ordered, de-duplicated list of `PlannedCheck`s, each
    judged feasible against the resolved binaries in `ctx` (z3/opt/clang/ast-miner), whether a
    pass SOURCE was supplied (for `source` strategies), and a runnable pass name (for
    `pass-runner` strategies)."""
    checks: list[PlannedCheck] = []
    for sid in classification.strategies:
        strat = STRATEGIES.get(sid)
        if strat is None:
            continue
        missing = [n for n in strat.needs if not ctx.get(n)]
        feasible, reason = True, ""
        if not strat.tool:
            feasible, reason = False, strat.note or "no runner"
        elif strat.note:
            feasible, reason = False, strat.note
        elif strat.target == "source" and not has_source:
            feasible, reason = False, "no pass source supplied"
        # target "canonical" validates fixed contracts -> needs only its `needs`, no source.
        elif missing:
            feasible, reason = False, f"missing: {', '.join(missing)}"
        elif strat.target == "pass-runner" and not strat.canonical_pass \
                and not _runnable_pass(classification, ctx):
            feasible, reason = False, "pass not runnable via opt (unknown/unbuilt pass name)"
        checks.append(PlannedCheck(strat.sid, strat.label, feasible, strat.target, reason))
    return checks


def _runnable_pass(classification, ctx: dict) -> bool:
    """A `pass-runner` strategy needs a pass name `opt` understands. Standard passes (from the
    family's `pass_names`) qualify; a custom/unbuilt pass would need a build (out of scope)."""
    from o2t.orchestrate.classify import FAMILIES
    name = (classification.pass_name or "").strip().lower()
    known = {n for f in FAMILIES for n in f.pass_names}
    return bool(name and name in known) or bool(ctx.get("force_pass_runner"))
