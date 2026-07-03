#!/usr/bin/env python3
"""Classify an LLVM pass from its source: what KIND of transform does it implement?

O2T has many verifiers (loop translation validation, peephole symbolic execution, DSE
memory-fact audit, GlobalOpt witnesses, SLP transaction checks, ...), but no single front
door. This module reads a pass's C++ source, scores it against the known transform
families by the idioms it uses, and returns the ranked families -- which the scheduler
(`plan.py`) turns into a per-pass check plan.

Classification is by SOURCE FEATURE (regex over the text -- no build needed) plus an
optional `pass_name` hint. Every family lists the verification STRATEGIES that apply, so
the orchestrator can dispatch to the right harness for each pass it is handed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Family:
    """A transform family: the idioms that identify it and the checks that verify it."""
    name: str
    description: str
    signals: tuple[tuple[str, int], ...]    # (regex, weight) -- presence counts toward the score
    strategies: tuple[str, ...]             # verification strategy ids (see plan.STRATEGIES)
    pass_names: tuple[str, ...] = ()         # canonical `opt -passes=` names in this family


# Transform families, ordered most-specific first. Signals are weighted: a strongly
# discriminating idiom (e.g. `getAddRecExpr`) outweighs a generic one (e.g. `Loop`).
FAMILIES: tuple[Family, ...] = (
    Family(
        name="loop-scev-recurrence",
        description="Loop transforms phrased in Scalar Evolution recurrences "
                    "(induction-variable simplification, strength reduction).",
        signals=(
            (r"\bgetAddRecExpr\b", 5), (r"\bgetMulExpr\b", 4), (r"\bgetSCEV\b", 3),
            (r"\bScalarEvolution\b", 2), (r"\bgetBackedgeTakenCount\b", 4),
            (r"\bcreateAddRecFromPHI\b", 4), (r"\bgetInductionVariable\b", 3),
        ),
        strategies=("scev-intent", "translation-validation"),
        pass_names=("indvars", "loop-reduce", "lsr"),
    ),
    Family(
        name="loop-structural",
        description="Loop transforms that move/clone/restructure code without an SCEV "
                    "recurrence rewrite (LICM, rotate, unswitch, simplify).",
        signals=(
            (r"\bisLoopInvariant\b", 4), (r"\bmakeLoopInvariant\b", 4), (r"\bhoist\b", 3),
            (r"\bsink\w*\b", 2), (r"\bLoopInfo\b", 2), (r"\bgetLoopFor\b", 2),
            (r"\bgetExitBlock\b", 2), (r"\bunswitch\w*\b", 4), (r"\brotateLoop\b", 4),
        ),
        strategies=("licm-source", "licm-model", "loop-cfg-ir", "loop-induction",
                    "loop-simulation", "loop-rotate-ir", "loop-multiexit", "loop-nested",
                    "translation-validation"),
        pass_names=("licm", "loop-rotate", "simple-loop-unswitch", "loop-instsimplify",
                    "loop-simplify"),
    ),
    Family(
        name="peephole",
        description="Local instruction-combining / simplification: PatternMatch matchers "
                    "rewritten via the builder (InstCombine, InstSimplify).",
        signals=(
            (r"\breplaceInstUsesWith\b", 4), (r"\bm_[A-Z]\w+\s*\(", 2), (r"\bmatch\s*\(", 2),
            (r"\bBuilder\.\s*Create\w+", 2), (r"\bSimplify\w+Inst\b", 3),
        ),
        strategies=("symexec-fold-cascade", "instcombine-ir", "reassociate-ir", "early-cse-ir",
                    "symexec-real-pass", "modelcheck-real-pass", "klee-symexec"),
        pass_names=("instcombine", "instsimplify", "aggressive-instcombine", "reassociate",
                    "early-cse", "gvn"),
    ),
    Family(
        name="memory-dse",
        description="Dead/overlapping store elimination over MemorySSA with overwrite and "
                    "removability legality.",
        signals=(
            (r"\bisOverwrite\b", 5), (r"\bisRemovable\b", 4), (r"\bMemorySSA\b", 3),
            (r"\bMemoryLocation\b", 2), (r"\bgetClobbering\w*\b", 3), (r"\bMemoryDef\b", 2),
        ),
        strategies=("memory-source", "memory-model", "dse-ir", "dse-facts"),
        pass_names=("dse",),
    ),
    Family(
        name="global",
        description="Whole-module global-variable optimization (dead initializer removal, "
                    "linkage/use reasoning).",
        signals=(
            (r"\bisGlobalInitializerDead\b", 5), (r"\bsetInitializer\b", 4),
            (r"\bGlobalVariable\b", 3), (r"\bhasLocalLinkage\b", 3),
            (r"\bremoveDeadConstantUsers\b", 3),
        ),
        strategies=("globalopt-source", "globalopt-model", "globalopt-witness"),
        pass_names=("globalopt",),
    ),
    Family(
        name="cleanup-dce",
        description="Instruction cleanup / dead code elimination guarded by trivial-deadness.",
        signals=(
            (r"\bisInstructionTriviallyDead\b", 5),
            (r"\bwouldInstructionBeTriviallyDead\b", 5),
            (r"\bisDeadLoopInstruction\b", 5),
            (r"\bRecursivelyDeleteTriviallyDeadInstructions\b", 5),
            (r"\bdeleteDeadInstruction\b", 4),
            (r"\bAllocaInst\b", 3),
            (r"\buse_empty\s*\(", 2),
            (r"\buser_empty\s*\(", 2),
            (r"\bhasNUses\s*\(\s*0\s*\)", 2),
            (r"\busers\s*\(\s*\)\s*\.\s*empty\s*\(", 2),
            (r"!\s*(?:\(\s*)?(?:[A-Za-z_]\w*\s*(?:->|\.)\s*)?hasNUsesOrMore\s*\(\s*1\s*\)", 2),
            (r"\beraseFromParent\s*\(", 3),
        ),
        strategies=("dce-source", "dce-model"),
        pass_names=("dce", "adce", "bdce"),
    ),
    Family(
        name="vectorize-slp",
        description="SLP / loop vectorization: pack scalars into vector ops with a cost model.",
        signals=(
            (r"\bTreeEntry\b", 4), (r"\bvectorizeTree\b", 5), (r"\bisValidElementType\b", 3),
            (r"\bShuffleVectorInst\b", 2), (r"\bgetVectorElementType\b", 2),
            (r"\bm_SplatOrPoison\b", 3),
        ),
        strategies=("slp-source", "slp-model", "slp-ir", "slp-transaction"),
        pass_names=("slp-vectorizer", "loop-vectorize"),
    ),
    Family(
        name="promotion",
        description="Memory-to-register promotion: SSA construction over allocas (mem2reg, SROA) "
                    "with phi placement.",
        signals=(
            (r"\bPromoteMemToReg\b", 5), (r"\bisAllocaPromotable\b", 5),
            (r"\brewriteSingleStoreAlloca\b", 4), (r"\bIDFCalculator\b", 4),
            (r"\bAllocaInst\b", 2), (r"\bDenseMap<.*PHINode\b", 3),
        ),
        strategies=("mem2reg-ir",),
        pass_names=("mem2reg", "sroa"),
    ),
    Family(
        name="cfg",
        description="Control-flow simplification: branch/switch folding, block merging, "
                    "unreachable removal.",
        signals=(
            (r"\bMergeBlockIntoPredecessor\b", 4), (r"\bgetSinglePredecessor\b", 3),
            (r"\bUnreachableInst\b", 3), (r"\bSwitchInst\b", 2), (r"\bsimplifyCFG\b", 4),
            (r"\bConstantFoldTerminator\b", 3), (r"\bFoldTwoEntryPHINode\b", 5),
            (r"\bgetIncomingValueForBlock\b", 4), (r"\bgetCondition\b", 2),
            (r"\bCreateSelect\b", 2),
        ),
        strategies=("cfg-source", "cfg-shape"),
        pass_names=("simplifycfg",),
    ),
)

# Pass name -> family (for the name hint, and to recover the canonical family when the
# source is opaque). Built from each family's `pass_names`.
_NAME_TO_FAMILY = {n: f.name for f in FAMILIES for n in f.pass_names}


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


@dataclass
class Classification:
    pass_name: str | None
    scores: dict[str, int] = field(default_factory=dict)     # family -> score
    primary: str | None = None
    families: list[str] = field(default_factory=list)        # all families scoring above threshold
    strategies: list[str] = field(default_factory=list)      # union of applicable strategies
    signal_hits: dict[str, list[str]] = field(default_factory=dict)


def classify(source_text: str = "", pass_name: str | None = None,
             threshold: int = 3) -> Classification:
    """Score `source_text` (and the optional `pass_name` hint) against the families.

    A family is RETAINED when its score >= `threshold`; the highest-scoring is `primary`.
    The name hint adds a strong bias toward its canonical family, so a known pass classifies
    even from a stub source. Returns a `Classification` (families ranked, strategies unioned).
    """
    text = _strip_comments(source_text or "")
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for fam in FAMILIES:
        score, seen = 0, []
        for pattern, weight in fam.signals:
            n = len(re.findall(pattern, text))
            if n:
                score += weight * min(n, 3)          # cap per-signal contribution (no runaway)
                seen.append(pattern)
        if score:
            scores[fam.name] = score
            hits[fam.name] = seen

    # Name hint: a strong, decisive bias toward the named pass's family.
    hinted = _NAME_TO_FAMILY.get((pass_name or "").strip().lower())
    if hinted:
        scores[hinted] = scores.get(hinted, 0) + 100

    retained = sorted((f for f, s in scores.items() if s >= threshold),
                      key=lambda f: scores[f], reverse=True)
    primary = retained[0] if retained else None
    by_name = {f.name: f for f in FAMILIES}
    strategies: list[str] = []
    for f in retained:
        for s in by_name[f].strategies:
            if s not in strategies:
                strategies.append(s)
    return Classification(pass_name=pass_name, scores=scores, primary=primary,
                          families=retained, strategies=strategies, signal_hits=hits)
