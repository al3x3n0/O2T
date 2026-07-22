# Verify the Pass, Not the Pair: Source-Intent Recovery and All-Trip-Count Validation for LLVM

> **Status**: working prose draft (v1, 2026-07-20), written from
> [arxiv-outline.md](arxiv-outline.md). Every quantitative claim is gated by the fixture named in
> [claim-fixture-map.md](claim-fixture-map.md); measured results live in
> [e6-passir-corpus.md](e6-passir-corpus.md) and [e7-ablation.md](e7-ablation.md). Numbers in this
> draft are those of the 445-fixture gate at the time of writing.

## Abstract

Tools like Alive2 verify compiler optimizations one IR *pair* at a time: they prove that a given
rewrite of a given input is sound, not that the *pass* that emits it is. We present O2T, a system
that verifies LLVM optimization passes **from their source**. For peephole passes, O2T recovers a
pass's intent *structurally* — its `PatternMatch` matcher trees, `IRBuilder` rewrite dataflow,
function-level path conditions, interprocedural helper guards, loops over operand lists, and the
caller contracts carried by function signatures — into formal obligations discharged by an SMT
solver; anything outside the modeled fragment is declined, never silently mis-modeled. Because a
mis-*recovery* is as dangerous as a mis-*compile*, O2T defends the recovery itself with a
cross-check stack that shrinks the trusted base: symbolic-versus-exhaustive-concrete
reconciliation, an independent second solver, width- and arity-parametric corroboration,
compilation of the verbatim source rewrite through an independent shim, re-checkable proof
certificates, and precondition abduction. O2T's own tokenizer and hand-parser are themselves
removed from the trusted base by a Clang-AST front-end that recovers obligations from the real
C++ compiler's AST — byte-identically to the regex path, and now at full shape parity with it,
including the two-icmp caller contract recovered verbatim against the real LLVM 18 headers. In a
seeded-misrecovery ablation over seven corruption
classes, **no corruption escapes the stack**, and two layers are shown to be uniquely
load-bearing. On an unmodified 38,267-line corpus of upstream LLVM 18 InstCombine and
InstructionSimplify source, O2T proves twelve fold obligations verbatim — including both arms of
`foldIsPowerOf2OrZero`, genuine bit-counting theorems — with **zero false proofs and zero false
refutations** across six measured runs. For loop passes, whose effect spans unboundedly many
iterations, O2T lifts intent into a recurrence DSL and proves transforms for *all* trip counts via
an integer-ring discharge (sound for every bitwidth by the homomorphism ℤ → ℤ/2ⁿ), template-based
invariant synthesis with k-induction, and relational two-loop simulation, driven from LLVM's own
Scalar Evolution and closed against the optimizer's actual output with minimized concrete
counterexamples. A pass-aware orchestrator and an LLM-driven triage agent scale the system to
whole vendor pass trees under a strict trust model: the LLM routes, proposes, and stages; formal
verifiers decide every verdict. O2T is fully reproducible behind a 445-fixture suite in which
every paper claim is gated by an executable test.

## 1. Introduction

Compilers are trusted on two fronts that current verification tooling covers asymmetrically.

The first front is the peephole optimizer. Here verification is mature but *per-pair*: Alive2 and
its relatives take a concrete before/after IR pair and decide whether the rewrite refines the
input. This certifies outputs, not the pass. The distinction matters most exactly where formal
assurance is most needed — a vendor's in-house pass, written against the LLVM API and never
reviewed upstream, is only as verified as the inputs someone happened to feed it. A fold whose
guard is subtly too weak fires correctly on every test input and miscompiles on the one shape
nobody tried.

The second front is loop optimization, the least-covered and highest-stakes corner. A loop
transform's effect spans an unbounded number of iterations; bounded techniques — fuzzing, or
Alive2 on unrolled bodies — cannot see trip-count-dependent bugs at all.

O2T's thesis is that both gaps close from the same direction: **verify the pass itself, from its
source**. Doing so requires solving four problems that per-pair tools never face:

1. **Recovery.** What a pass *intends* must be read out of its C++ — matcher trees, builder
   calls, guard conjunctions, control flow, helper functions, loops, calling conventions — and
   turned into formal obligations, with everything unreadable *declined explicitly* rather than
   silently approximated.
2. **Recovery soundness.** The reader of that C++ is itself a program, and its bugs are exactly
   as dangerous as compiler bugs: a misread source yields a wrong obligation, and a wrong
   obligation can prove. The reading must be *certified*, not assumed.
3. **Unbounded loops.** Obligations for loop transforms must quantify over all trip counts, which
   requires invariants and induction, made automatic and cheap enough for a validation workflow.
4. **Scale.** A vendor tree has dozens of passes and a team has few verification engineers; the
   system must triage and route mostly by itself, without ever letting a heuristic component
   decide soundness.

**Contributions.**

- **Pass IR: structural source-intent recovery for peephole passes** (§3). A recovery ladder
  that turns fold functions into obligations: compositional matcher/rewrite trees; path
  conditions reconstructed from control flow; interprocedural helper inlining; per-fold slicing
  of multi-arm cascades; operand-list loops with non-independent iterations at a corroborated
  bounded arity; multi-conjunct composition; and caller contracts recovered from function
  signatures (`simplifyXInst`, two-icmp combiners). Every rung declines what it cannot model.
- **Certifying the recovery** (§4). A layered cross-check stack — engine reconciliation, a second
  solver, width- and arity-parametric corroboration, compiler-grounded recovery, a Clang-AST
  front-end that removes O2T's own parser from the trusted base (now at full shape parity with the
  regex path, recovering the two-icmp contract verbatim from real headers), certificates,
  abduction — treated as a verification object in its own right. A seeded-misrecovery ablation
  (§10, E7) shows zero escapes across seven corruption classes and identifies two layers as
  uniquely load-bearing; six recovery bugs found during development, each caught by a different
  mechanism, ground the design empirically.
- **All-trip-count loop validation** (§5). An integer-ring discharge sound for every bitwidth;
  Faulhaber-aware invariant synthesis with k-induction; relational two-loop simulation with
  inferred simulation relations.
- **Closed-loop translation validation** (§6) of real `opt` output across scalar, vector,
  memory, and CFG transforms, with minimized concrete counterexamples on failure.
- **Scaling under a strict trust model** (§7): a deterministic pass-aware orchestrator and an
  LLM-driven triage agent whose every output is quarantined, provenance-tagged, and gate-inert.
- **A reproducible artifact** (§10): 445 executable fixtures; a claim→fixture map connecting
  every statement in this paper to the test that gates it.

## 2. Background

**LLVM IR and undefined behavior.** LLVM's value semantics include `poison` (a deferred-UB value
produced e.g. by flag-violating arithmetic) and `undef` (an arbitrary-but-chosen value), forming
a two-level lattice; instruction flags (`nsw`, `nuw`, `exact`, `disjoint`) make results poison
when their side conditions fail. Soundness of a rewrite is therefore *refinement*, not equality:
the rewrite may be more defined than the original, never less. O2T uses value equality as the
obligation for poison-free folds — where the two coincide — and switches to an explicit
refinement encoding whenever flags, `freeze`, or declared-poison values appear.

**The idiom vocabulary.** LLVM passes are written against a small, highly stylized API:
`PatternMatch` matchers (`m_Add(m_Value(X), m_Zero())`) express the shapes a fold recognizes;
`IRBuilder` calls and instruction constructors express what it builds; analysis queries
(`isKnownNonNegative`, `haveNoCommonBitsSet`, `computeKnownBits`) express legality guards. This
stylization is what makes source-level recovery feasible: the vocabulary is finite and its
semantics are documented.

**Scalar Evolution.** LLVM's SCEV analysis canonicalizes loop values into add-recurrences
`{start, +, step}`. O2T uses SCEV both as an ingestion frontend (recovering recurrences from real
IR) and as a bridge for reading loop-pass source, since loop passes largely speak SCEV.

## 3. Pass IR: structural intent recovery

The unit of recovery is the **fold obligation**: for a rewrite reachable under path condition
`C`, with `before` the matched shape and `after` the built replacement,

```
C  ⟹  before ≡ after            (value equality; refinement where poison is in play)
```

The recovery is a ladder of rungs, each widening the fragment; the ladder's defining discipline
is that every rung *declines* what it cannot model — an unrecognized guard, an unmodeled matcher,
an unresolvable alias — because dropping a value-relevant precondition could turn an unsound fold
into a proof.

**Trees, guards, and path conditions.** The base rung parses the matcher into `before` and the
rewrite expression into `after` compositionally, so arbitrarily nested matcher algebra and
multi-step builder dataflow become one obligation. Guard conjuncts become the premise: analysis
queries lower to assumption facts; use-count and profitability guards are dropped as
value-irrelevant; unrecognized guards decline. Above single expressions, the fold *function*'s
control flow is walked to reconstruct the path condition — early-return bailouts contribute
negated (De Morgan) atoms, enclosing positive `if`s contribute their conjuncts, at arbitrary
nesting — and single-return helpers are inlined interprocedurally first.

**Cascades.** A real fold function is not one fold but a *cascade* of sequential
`if (match(...)) return ...;` arms. O2T slices every arm into an independent obligation with its
own path condition. The refutation discipline survives slicing by construction: the first arm's
refutation is a pass-level claim, but a later arm's premise omits the negations of earlier arms'
guards, so its refutation is reported *standalone-only* — the witness may be unreachable — while
its proof remains sound (proved over a superset of the reachable inputs). An in-place-mutation
screen declines any cascade that mutates the instruction between match and rewrite, where the
replaced value would no longer be the matched shape.

**Loops.** A loop over independent instructions (`for (Instruction &I : BB)`) is a universal
quantifier and is walked transparently. Loops whose iterations are *not* independent are
recovered at a bounded arity: the all-incoming-equal phi collapse becomes a nondeterministic
selector-merge that must collapse under the recovered pairwise-equality guard, and a
reduction-rebuild loop becomes the associativity obligation `right-fold ≡ left-fold`. Section 4's
arity corroboration is what licenses the bounded proof.

**Anchors and contracts.** Upstream code exposed a succession of conventions the ladder had to
learn, each measured before it was built (§10, E6). The dominant idiom returns the replacement
value instead of calling `replaceInstUsesWith`; the *return-form anchor* recovers it, gated on a
fold-contract name and on the match inspecting the function's instruction-typed parameter — the
gate that separates a fold from a query helper. Fold arms often constrain the instruction and its
operands in separate `match` conjuncts; *composition* splices operand conjuncts into the primary
tree structurally. Finally, two families of caller contract are recovered from signatures alone:
`simplify⟨Op⟩Inst(Value *Op0, Value *Op1, …)` — whose *name* is documented API semantics
declaring both the phantom instruction and the operand orientation — and the two-icmp combiners
`foldX(ICmpInst *Cmp0, ICmpInst *Cmp1, bool IsAnd, …)`, where the `IsAnd` selector chooses the
combining operation per case and rewrite-side operand projections (`Cmp0->getOperand(0)`) lower
to the matched subtree. Orientation is the load-bearing subtlety: `foldX` helper argument order
is commuted by callers and binding it by convention could falsely prove, so that class is
declined pending call-site verification; the `simplifyXInst` name contract is honored and pinned
by a non-commutative test (`0 − X → X` refutes).

**Guard vocabulary.** Predicate-*set* guards (`ICmpInst::isEquality(Pred)`) constrain a bound
predicate to a member set; the obligation is proved once per member, instantiated consistently
through matcher and rewrite alike, and *all* cases must prove — a rewrite that hardcodes one
member is refuted on the others. Domain-affirming guards (scalar-affirming type tests,
canonicalization-order tests in negative polarity) drop; their positive polarities decline where
they carry value or poison weight.

## 4. Certifying the recovery

The recovery of §3 is a program that reads C++, and its failure mode is silent: a misread source
yields a wrong obligation, and a wrong obligation can prove. O2T therefore treats *the reading
itself* as a verification obligation, defended by independence in layers:

1. **Engine reconciliation.** Every verdict is cross-checked between the symbolic prover (bv32)
   and an exhaustive concrete enumeration (bv8, premise-aware). Sound value identities hold at
   every width; disagreement marks the obligation untrusted, never proved.
2. **A second solver.** The identical SMT-LIB query is discharged by an independent solver
   (Bitwuzla) — the one check no amount of re-running the first solver provides.
3. **Parametric corroboration.** The obligation is re-proved at widths {8, 16, 32, 64} and, for
   bounded-arity loop recoveries, at arities {2, 3, 4}. A verdict that does not generalize is
   labeled `width-specific` or `arity-specific` rather than silently trusted at its
   representative bound. This is what licenses "for all N" claims from bounded proofs — and it
   catches bugs invisible at the bound: an under-recovered quantified guard and a non-associative
   reducer both *prove* at the representative arity and are exposed only by corroboration.
4. **Compiler grounding.** The verbatim source rewrite is compiled against an independent
   symbolic shim, and the SMT the compiler computes is checked equal to the recovered `after` —
   the translation-validation move applied one level up, to the *recovery*. This is strictly
   stronger than reconciling a harness rebuilt from O2T's own nodes, which cannot catch a
   recovery that is internally consistent but unfaithful to the source.
5. **Structured-tree front-end.** A Clang-AST miner supplies pre-parsed matcher/rewrite trees built
   from the C++ compiler's own AST, removing O2T's tokenizer and hand-parser from the trusted base
   entirely — a misparse is impossible on a tree. This is realized in two modes: a stub mode (fold
   source against a minimal API stub) and a **source-file mode that parses unmodified fold bodies
   against the real LLVM 18 headers**, recovering an obligation byte-identical to the regex path with
   the parser fully out of the loop. Its shape coverage is now at **parity with the regex front-end**:
   guarded/return-form folds, cascades, the two-icmp caller contract (recovered *verbatim* — the one
   datum the typed AST elides, the `m_Intrinsic<Intrinsic::ctpop>` id, is read at the source span the
   compiler itself pins), the `simplifyXInst` name contract, predicate-set case splits, and the
   operand/reduction collapse loops. Every shape carries teeth through the same AST path (a mutated
   rewrite, a swapped operand orientation, a predicate overreach, and a non-associative reducer all
   refute with witnesses; a non-collapse loop declines). A **whole-`.cpp` mode** closes the last gap:
   it parses the *unmodified upstream* `InstCombineAndOrXor.cpp` (~4830 lines) in its real lib context
   and recovers folds from it — five obligations byte-identical to the regex path. The only header
   outside the installed LLVM tree, `InstCombineInternal.h`, transitively needs only installed public
   headers, so the whole file compiles with no LLVM build; the recovery reads genuine pass source, not
   a trimmed rendering.
6. **Certificates and abduction.** Verdicts carry re-checkable certificates; when a fold refutes,
   abduction synthesizes the *missing precondition*, converting a rejection into a diagnosis.
7. **Observational grounding.** All of the above certify the *recovery*; this certifies that the
   recovery matches the *pass's actual behavior*. The recovered `before` is emitted as LLVM IR, the
   real `opt -passes=instcombine` is run, and the optimizer's output is checked against the recovered
   `after` — the peephole analogue of the loop track's translation validation, closing the loop from
   *source-recovered intent* to *what the compiled pass does*. A fold is **confirmed** when the pass
   performs it (equivalent forms accepted), **not-fired** when the pass declines it on inputs that do
   not establish the recovered precondition (checked under that precondition, so a guard is honored
   rather than mis-flagged), and **divergent** when the pass produces something the recovered `after`
   does not — a discrepancy the symbolic proof alone cannot see, since it never runs the pass.
8. **Whole-function translation validation.** Where observational grounding checks one recovered fold,
   this validates the *entire* transformation on *real code*: for a corpus of real IR functions the
   actual `opt -passes=instcombine` is run and the whole-function output is proved a sound refinement
   of the input (Alive2-style), verifying the *composition* of whatever folds fired rather than an
   isolated obligation. Over LLVM's own InstCombine tests (`and/or/xor/add.ll`, 715 functions) **351
   (49%) are proved sound end-to-end with zero false refutations**; the rest decline (memory /
   multi-block / vector shapes the scalar translator does not model, plus a few solver timeouts) —
   never a false proof. This is a whole-*function* result, not yet whole-*pass* (the worklist/fixpoint
   composition across functions is still unmodeled), and it is the broad-reach complement to
   source-recovery's narrow-but-explanatory obligations. The two **meet at attribution**: for each
   proved whole-function transform, the recovered fold whose `(before, after)` matches it — under a
   variable mapping, checked by SMT so an equivalent form still matches — is credited as the
   *explanation* (sound *and* accounted for by source-recovered intent). The exact match makes
   mis-attribution impossible: an unsound fold is never credited. Over the vendored real-test corpus a
   recovered-fold set explains 8 of 14 whole-function transforms by a *named* fold; the remainder is
   honest **residue** — a composed transform or a fold not yet recovered — which is precisely the
   work-list an enrichment loop would target.
9. **Self-enrichment, gated by an independent oracle.** When whole-function TV declines a function as
   `unsupported` because it uses an instruction outside the translator's fragment, an enrichment loop
   *proposes* that instruction's SMT semantics — but a proposed model can be *wrong*, so it is never
   trusted on its own. It is validated against `lli` **execution**: the real instruction is run on a
   battery of concrete inputs (LLVM's own semantics) and the proposed model must agree on every one;
   only then is it installed as an extra translator rule. This is the discipline that lets an
   autonomous (LLM) harness *grow* O2T's verification vocabulary without weakening it: the proposer may
   be a language model, but an oracle it did not author decides soundness. Demonstrated on `llvm.bswap`
   — the correct byte-reversal model is lli-validated and lifts a `bswap(bswap(x))→x` transform from
   unsupported to proved, while a wrong (identity) model is rejected by lli before it can enable a
   false proof. Point-wise lli agreement is strong *evidence*, not a proof, and is reported as such.

Beneath both tracks sits a meta-verification layer: premises must be jointly satisfiable before
an `unsat` counts as proof (anti-vacuity), every proved contract must kill all its single-point
mutants (teeth), and refutation witnesses are re-validated concretely.

Two kinds of evidence ground this design (details in §10). The seeded ablation shows that the
typical corruption is caught by three or four independent layers, but that two classes — a
width-specific constant and a skipped predicate case — each evade everything except one specific
layer: the stack is not redundant everywhere, and removing "belt-and-suspenders" layers would
open real escape classes. And during the development of §3's ladder itself, six recovery bugs
were caught by six different mechanisms — a corpus-run anomaly, a fixture gate, a smoke test,
design reading, and adversarial review among them — including two misattribution classes that
would have produced false refutations of upstream LLVM and one inverted-premise bug that was a
direct false-proof vector.

## 5. All-trip-count loop validation

**The integer-ring discharge.** Machine arithmetic is modular, and nonlinear bitvector
multiplication forces bit-blasting — a single 32-bit `i·i` identity does not terminate in
practice. O2T proves the polynomial identity over ℤ instead. Because the reduction ℤ → ℤ/2ⁿ is a
ring homomorphism for `+`, `−`, `×`, an integer identity holds in ℤ/2ⁿ for every n: one proof
certifies all bitwidths. Measured (E3): the nonlinear Faulhaber STEP proves over ℤ in 0.105 s
while its bit-blasted bv32 twin exhausts a 10 s cap. The homomorphism does
not cover width-changing operations, which remain a stated boundary.

**Invariant synthesis and induction.** A loop accumulator is a recurrence; O2T proves a claimed
invariant by 1-induction (BASE and STEP discharged over ℤ) and, when the closed form is unknown,
synthesizes it from a degree-aware polynomial template with factorial multipliers clearing
Faulhaber denominators — `acc += i·i` synthesizes `6·acc = 2i³ − 3i² + i`. Batched candidate
checking (one solver process, push/pop) and relevant-constant pruning make synthesis interactive.
Conditional strides ride along as `ite`-valued strides; geometric recurrences, having no
polynomial closed form, are correctly declined in single-loop form and proved relationally;
memory deltas model loads as uninterpreted functions, proving LICM-of-load and redundant-load by
congruence without knowing values.

**Relational simulation.** Two-loop transforms are proved over the product system: auxiliary
invariants for the transformed loop's induction variables are synthesized coupled (prior
invariants asserted in the STEP), and the output bijection is discovered by proving candidate
pairings inductive. The discovered relation is often simpler than either closed form — strength
reduction is certified by the *linear* relation `{k = c·i, acc = acc}` over a *quadratic*
accumulator — and a wrong stride admits no inductive pairing and is refuted.

## 6. Closed-loop translation validation and witnesses

The loop track closes against reality: `opt -passes=X` is run on real IR, the literal emitted
instructions are parsed back, and equivalence (or refinement) is proved between input and output.
Coverage spans InstCombine scalar folds (flag-introduction refuted), SLP vectorization per output
lane, mem2reg across multiple blocks with φ placement, DSE over a theory of arrays (final memory
plus surviving loads), SimplifyCFG if-conversion, and indvars' loop-to-closed-form rewrites
(surfaced honestly as `loop-eliminated` rather than claimed as loop equivalence).

On failure O2T emits a *minimized concrete counterexample*. The inductive-step model cannot be
trusted for witnesses — its pre-states may be unreachable — so witnesses are found by bounded
forward execution and minimized over trip count and parameters.

## 7. Scaling: the orchestrator and the verification agent

A deterministic orchestrator classifies a pass source into transform families by its API
fingerprint, plans the feasible checks per family against the available toolchain, dispatches
them, and rolls verdicts into per-pass headlines (`proved`, `refuted`, `error`, `advisory`,
`planned`, `skipped`, `unclassified`). Coverage gaps are explicit: an unsupported pass is
reported, never hidden.

Above it sits an LLM-driven agent for batch triage of the *residue* — the passes deterministic
classification left open. Per pass, the LLM observes accumulated evidence and selects one action
from a whitelisted, schema-validated registry: run a real verifier, recover a fold, propose
intent candidates that are then proof-gated by the solver, stage a new-tool candidate in
quarantine, or conclude. The trust model is a system invariant rather than a policy: the LLM
never emits shell; deterministic headlines are byte-preserved; agent-dispatched *formal* verdicts
are provenance-tagged into a separate headline; LLM conclusions are advisory and can trip no CI
gate; staged tools are hash-pinned, executed once in isolation, and promoted only by a human.
Prompt injection from hostile pass source is thereby bounded to steering *which whitelisted
verifier runs*, never *what counts as sound*.

The same discipline lets the agent *extend the verifier itself*. An enrichment agent diagnoses the
instructions behind whole-function TV's `unsupported` declines, asks the LLM to propose each one's SMT
semantics, and — critically — **validates every proposal against `lli` execution** before installing
it (§4.9). The LLM's model is data ratified by an oracle it did not author: a correct `llvm.bswap`
model is validated and lifts the reach (a `bswap(bswap(x))→x` transform goes from unsupported to
proved), while a wrong model is rejected by `lli` before it can enter the trust base. The loop runs
end-to-end on a deterministic stub (no model access); going live is a single `--llm-command` flag. So
an LLM can *grow* O2T's verification vocabulary without ever being trusted to decide soundness — the
autonomous-harness analogue of the whole design's thesis.

## 8. Implementation

O2T is a Python core (~40 modules) behind ~143 thin CLI tools, with a C++ side for bounded IR
generation and instrumentation. Frontends use real parsers — SCEV via `opt`, the Clang AST — over
a parser-agnostic prover; the LLM transport is provider-agnostic (any JSON-stdin/stdout command),
with a deterministic stub in CI; the gate runs 445 fixtures with no network access. External
requirements: Z3 (required); LLVM 18 tools and Bitwuzla, KLEE, CBMC/ESBMC optionally, each
degrading to an explicit skip.

## 9. Related work

**Alive/Alive2** verify peephole rewrites as IR pairs under a precise poison/undef refinement
semantics. O2T differs on three axes: it reads the *pass source* rather than sampled pairs; it
certifies the *reading itself* (no analogue exists in pair-based tools, which start from ground
truth); and it proves *unbounded* loop transforms via invariants where pair tools unroll.
**Translation validation** (Pnueli et al.; Necula; Tristan–Leroy) validates individual
compilations; O2T's closed-loop mode is TV, and its compiler-grounding of recovery transplants
the TV idea one level up. **CompCert** ends the trust question for its own passes; O2T targets
the passes verified compilers do not cover — third-party code against an unverified production
compiler. **Invariant inference** (Houdini, ICE, PDR) offers alternatives to O2T's template
synthesis; the coupled-relational use with output-bijection discovery is, to our knowledge, new
in a validation setting. **Compiler fuzzing** (Csmith, EMI, YARPGen) finds bugs without proofs;
O2T's witness pipeline connects refutation to the same actionable artifact. **LLM-assisted
verification** systems increasingly let model output into the proof path; O2T's agent is
deliberately the opposite design point — the LLM is untrusted by construction, and the paper's
claims are unaffected by its quality.

## 10. Evaluation

Two experiments are measured; the remainder are mechanism-gated and stated as pending.

**E6 — corpus coverage (measured, six runs).** The recovery ladder was driven over an unmodified
corpus: the eight scalar-side `InstCombine*.cpp` files plus `InstructionSimplify.cpp` from LLVM
`release/18.x` — 441 candidate fold functions, 38,267 lines. The initial run (anchors only)
recovered nothing and produced the decline taxonomy that *chose* every subsequent rung: 48% of
candidates declined at the rewrite anchor, which became the return-form anchor; its residue
decomposed into the composition, contract, and predicate-set phases, each built against a
measured population. The series ends at **twelve proved fold arms across eight functions** — an
algebraic rewrite proved and confirmed by exhaustive enumeration over 16.7M inputs; a three-arm
cascade; five `simplifyXInst` contract arms; and both arms of `foldIsPowerOf2OrZero`, the
bit-counting theorems `ctpop(X) ≠ 1 ∧ X ≠ 0 ↔ ctpop(X) > 1` and its or-dual. Across all six
runs: **zero false proofs and zero false refutations**. The zero-recovery first run and the
still-declining 429 functions are results, not failures: the taxonomy quantifies exactly which
vocabulary stratum blocks each population, and the per-phase deltas measure each mechanism's real
yield — including two phases whose honest yield was zero (their populations proved blocked on
other walls), recorded as such.

**E7 — recovery-soundness ablation (measured).** Seven misrecovery classes — dropped operator,
mislowered builder, weakened guard, swapped operands, width-specific constant, skipped predicate
case, contradictory premise — were seeded into four known-good obligations (each verified proved
before corruption) and run through the §4 stack. **Zero escapes.** The typical corruption is
caught by three or four independent layers; the width-specific constant is caught *only* by
width corroboration, and the skipped predicate case *only* by the all-cases discipline — the
empirical identification of the stack's uniquely load-bearing members. One seeded corruption
(mislowering `or → add` under a disjointness premise) is semantically invisible — the premise
makes it value-equal — and is pinned as a conscious fact: a wrong reading that denotes the same
function is not a soundness event. The matrix is complemented by six *field* specimens: recovery
bugs caught during development, each by a different mechanism, two of which would have produced
false refutations of upstream LLVM and one of which was a direct false-proof vector.

**E1 — closed-loop coverage (measured).** Five real loop passes (LICM, loop-rotate,
simple-loop-unswitch, loop-instsimplify, indvars) were run over a seven-loop recurrence benchmark
and their actual output validated against the input: 35 cells, **26 positive verdicts** (20
loop→loop `proved`, 6 indvars `proved-closed-form`), 9 honestly-reported `loop-eliminated`, and
**zero false refutations** — no correct LLVM pass falsely accused. The dual holds: a mutated
recurrence in `opt`'s output is refuted with a concrete witness, so the zero-refutation result is
a property of sound passes, not of a validator that never refutes.

**E4 — frontend robustness (measured).** The SCEV frontend and the legacy line-regex frontend
were run over a rotated/multi-block/LCSSA benchmark — the shape `clang -O1` emits. The regex
frontend recovers **0 of 4**; SCEV recovers **4 of 4** — strict domination on the real-world form.
A simple single-block control (regex 4/4) confirms the rotated failures are a property of loop
shape, not a dead parser. Since E1 validates rotated real `opt` output, the SCEV frontend is what
makes E1 measurable at all.

**E2 — mutation catch-rate (measured).** Across three independent teeth tiers — 34 single-point
corruptions of the deep family contracts (each killed with a witness, premises satisfiable), the 7
recovery misrecovery classes of E7, and 11 perturbed registry intents — **52 of 52 seeded
corruptions are caught, zero survivors**. Witness *minimality* (minimal trip count, |params|) is a
property of the loop-track CEGAR witnesses, measured with the loop fixtures rather than these
point mutations, and is not claimed here.

**E3 — performance (measured).** The nonlinear Faulhaber STEP proves over ℤ in 0.105 s while its
bit-blasted bv32 twin exhausts a 10 s cap; batched synthesis discharge runs 19.5× faster than
per-candidate while agreeing candidate-by-candidate; recovered fold obligations prove in 12–83 ms.

**E5 — case studies (worked examples).** Two end-to-end walk-throughs, each traceable to a gated
fixture: `foldIsPowerOf2OrZero` recovered from unmodified upstream source through nearly the whole
ladder and proved as two ctpop theorems — and recovered a *second, independent* way, from the real
LLVM 18 Clang AST with O2T's hand-parser fully out of the loop, byte-identically to the regex path
(two front-ends agreeing on the same obligation); and strength reduction proved relationally for all trip
counts by the inferred relation `{k = c·i, acc = acc}`, with a wrong stride refuted, on both
hand-written loops and the rotated real-`opt` output shape. On this benchmark O2T finds no
miscompile — the correct result, matching E1 — so discrepancy *detection* is demonstrated on
injected miscompiles (E1/E2/E7 teeth); a real class of discrepancy was nonetheless found and fixed
in development, in O2T's own reading of the source (E7's six field specimens). Closed-loop
validation of real `opt -passes=lsr` and reproduction of a wild LLVM bug remain open.

**Pending.** E8 (live-model agent triage of a vendor tree) is the one remaining experiment: its
trust invariants are gated with a deterministic stub, but a run with a live model is bounded by an
external dependency rather than missing mechanism.

## 11. Limitations

The recovery fragment is scoped and its boundary is public: worklist fixpoints, in-place mutation
semantics, dynamic-opcode folds, `foldX` operand-parameter binding (pending call-site
verification), floating point, and the KnownBits/APInt guard stratum all decline today, and the
E6 taxonomy counts each. O2T's scalar model has no `undef` distinct from poison beyond the
definite-value guard, and bounded-arity loop proofs are licensed by corroboration, not by
unbounded induction. On the loop track, width-changing operations bound the integer-ring
discharge; read-write memory beyond the current array fragment, loop-nest transforms, and
vectorization remain future work. Standalone-arm refutations in cascades are advisory by
construction. The agent's quarantine is protection against accidents and prompt-injected
sloppiness, not a security sandbox, and is documented as such.

## 12. Conclusion

Verifying the pass — not the pair — is tractable. Structural recovery with a certified reading
turns real peephole source into theorems; recurrence lifting with an integer-ring discharge turns
loop transforms into all-trip-count proofs; closed-loop validation ties both to the optimizer
that actually runs; and an agent scales the pipeline while staying outside the trusted base. The
discipline that makes it credible is uniform across all of it: decline what you cannot model,
cross-check what you can, measure the boundary, and let every claim be a test.
