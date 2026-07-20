# O2T — arXiv Paper Outline

Working title options:
- *Verify the Pass, Not the Pair: Source-Intent Recovery and All-Trip-Count Validation for LLVM*
- *From Pass Source to Proof: Recovering, Cross-Checking, and Discharging Optimization Intent*
- *O2T: Formal Validation of LLVM Optimization Passes from Their Source*

Target: arXiv `cs.PL` (cross-list `cs.LO`, `cs.SE`). **Prose draft: [paper-draft.md](paper-draft.md).**
Companion artifact: this repository
(438-fixture gate; every claim maps to a fixture — [claim-fixture-map.md](claim-fixture-map.md)).

> Reframe note (2026-07): the original outline presented only the loop/recurrence track. The system
> has since grown a second, co-equal track — **Pass IR**: 35 phases of structural source-intent
> recovery for peephole passes with a recovery-soundness cross-check stack — plus an LLM
> **verification agent** as the scaling layer. The paper now leads with the unifying thesis both
> tracks serve: *verify the pass itself, not sample input/output pairs*.

---

## Abstract (draft)

> Tools like Alive2 verify compiler optimizations one IR *pair* at a time: they prove that a given
> rewrite of a given input is sound, not that the *pass* that emits it is. We present O2T, a system
> that verifies LLVM optimization passes **from their source**. For peephole passes, O2T recovers a
> pass's intent *structurally* — its `PatternMatch` matcher trees, `IRBuilder` rewrite dataflow,
> function-level path conditions, interprocedural helper guards, and loops over operand lists — into
> formal obligations discharged by an SMT solver; anything outside the modeled fragment is declined,
> never silently mis-modeled. Because a mis-*recovery* is as dangerous as a mis-*compile*, O2T
> defends the recovery itself with a cross-check stack that shrinks the trusted base: symbolic vs
> exhaustive-concrete reconciliation, an independent second solver, width- and arity-parametric
> corroboration (a bv32 or arity-2 verdict is labeled with the bound it generalizes from),
> compilation of the verbatim source rewrite through an independent shim ("does the compiler read
> this code the way we do?"), re-checkable proof certificates, and precondition abduction that
> diagnoses *why* an unsound fold fails. For loop passes, whose effect spans unboundedly many
> iterations, O2T lifts intent into a recurrence DSL and proves transforms for *all* trip counts via
> an integer-ring discharge (sound for every bitwidth by ℤ → ℤ/2ⁿ), template-based invariant
> synthesis with k-induction, and relational two-loop simulation — driven from LLVM's own Scalar
> Evolution, closing the loop against the optimizer's *actual output* with minimized concrete
> counterexamples on failure. A pass-aware orchestrator and an LLM-driven triage agent scale the
> system to whole vendor pass trees under a strict trust model: the LLM routes, proposes, and
> stages; formal verifiers decide every verdict. O2T is fully reproducible behind a 438-fixture
> test suite in which every paper claim is gated by an executable test.

---

## 1. Introduction
- Motivation: compilers are trusted on two fronts that current tooling covers asymmetrically.
  Peephole verification (Alive2) is mature but *per-pair* — it certifies outputs, not the pass; a
  vendor's in-house pass is only as verified as the inputs someone happened to test. Loop
  optimizations are the *least* covered: bounded unrolling misses trip-count-dependent bugs.
- Gap: verifying the **pass itself** needs (a) recovering what the source intends — soundly, with
  explicit declines; (b) trusting that recovery — the reading of C++ becomes part of the TCB;
  (c) all-trip-count proofs for loops; (d) a way to scale beyond one-pass-at-a-time expert use.
- Approach in one paragraph: structural Pass-IR recovery → formal obligations → SMT discharge,
  wrapped in a recovery cross-check stack; recurrence lifting + integer-ring discharge + invariant
  synthesis for loops; closed-loop TV with CEGAR witnesses; orchestrator + LLM agent under
  "formal verifiers decide" trust.
- Contributions (bulleted, map to §-numbers):
  1. **Pass IR**: structural recovery of peephole intent from pass source — nested matcher algebra,
     rewrite DFGs, path conditions from control flow (bailouts, De Morgan, nesting),
     interprocedural helper inlining, and operand-list loops with non-independent iterations
     (quantified all-equal guards; reduction rebuilds) at a bounded, corroborated arity (§3).
  2. **Recovery soundness**: a cross-check stack that certifies the *reading* of the source —
     engine/solver reconciliation, width/arity-parametric corroboration, compiler-grounded
     recovery, certificates, abduction (§4). To our knowledge the first system to treat
     source-intent recovery itself as a verification obligation.
  3. **All-trip-count loop validation**: integer-ring discharge, Faulhaber-aware invariant
     synthesis + k-induction, relational simulation with inferred simulation relations (§5).
  4. **Closed-loop translation validation** of real `opt` output with minimized CEGAR witnesses,
     across scalar, vector (SLP), memory (DSE, mem2reg), and CFG transforms (§6).
  5. **Scaling under a strict trust model**: a pass-aware orchestrator (classify → plan →
     dispatch) and an LLM verification agent whose every output is quarantined, provenance-tagged,
     and gate-inert — the LLM routes and proposes; Z3/opt decide (§7).
  6. Reproducible artifact: the 438-fixture gate + the claim→fixture map (§10, appendix).

## 2. Background
- LLVM IR, poison/undef (two-level lattice), no-wrap flags; why refinement ≠ equality.
- `PatternMatch` / `IRBuilder` idioms — the vocabulary passes are written in.
- Scalar Evolution add-recurrences; SMT over Int vs bitvectors.
- Translation validation vs verified compilation; Alive2's refinement model.

## 3. Pass IR: structural intent recovery  (the peephole track)
- The obligation: for each rewrite reachable under path condition `C`, prove `C ⇒ before ≡ after`
  (poison/UB-aware; refinement where flags/freeze are involved).
- Recovery ladder (each rung widens the fragment; everything outside **declines**):
  matcher trees + rewrite DFGs → guard conjunctions as preconditions (unrecognized guards decline —
  dropping a value-relevant premise could false-prove) → function-level path conditions (early
  returns, De Morgan'd bailouts, arbitrary nesting) → interprocedural single-return helper
  inlining → loops over IR: independent iterations as universal quantifiers; operand-list loops
  with *non-independent* iterations — the phi all-equal collapse (a quantified guard) and the
  reduction rebuild (associativity obligation) — at a bounded arity (§4 corroborates the bound).
- Worked examples: `sdiv→udiv` refuted unguarded / proved under both-nonneg / caught vacuous on a
  contradictory guard; `phi [x,x,…,x] → x`; a non-associative reducer caught only at arity 3.
- Scope honesty: worklist fixpoints, cross-instruction accumulation, data-dependent trip counts —
  sound declines, stated.

## 4. Certifying the recovery  (the trust story — lead novelty)
- Threat model: the recovery is a C++-reading program; a silent misparse or mislowering changes
  the obligation and can false-prove. The mitigation is *independence*, layered:
  1. **Engine reconciliation** — symbolic Z3 (bv32) vs exhaustive concrete enumeration (bv8,
     precondition-aware): verdicts must agree.
  2. **Second solver** — identical SMT-LIB discharged by bitwuzla; guards against a solver or
     encoding bug z3 handles "consistently".
  3. **Parametric corroboration** — re-prove at widths {8,16,32,64} and arities {2,3,4}: a verdict
     that does not generalize is labeled `width-specific` / `arity-specific`, never a silent
     coincidence of the representative bound. (Case study: an under-recovered quantified guard and
     a non-associative reduction are both *proved* at the representative bound and caught only by
     corroboration.)
  4. **Compiler grounding** — compile the *verbatim* source rewrite against an independent symbolic
     shim and check the compiler's reading equals the recovered `after` (the CompCert-TV move,
     applied to *recovery*). Strictly stronger than reconciling a harness rebuilt from our own
     nodes.
  5. **Structured-tree front-end** — a Clang-AST miner emitting pre-parsed trees removes the
     tokenizer/parser from the TCB entirely (no misparse is possible on a tree).
  6. **Certificates + abduction** — re-checkable verdict certificates; when a fold refutes,
     abduction synthesizes the *missing precondition* (diagnosis, not just rejection).
- The meta-verification layer beneath both tracks: anti-vacuity (premises must be SAT), mutation
  teeth (every proved contract kills all single-point mutants), witness re-validation.

## 5. All-trip-count loop validation  (the recurrence track)
- Integer-ring discharge: modular semantics, the ℤ → ℤ/2ⁿ homomorphism (figure: commuting square);
  worked example `acc += i·i` proved in ~0.02s where bv32 diverges; boundary at width-changing ops.
- Recurrence invariants: k-induction (BASE/STEP), degree-aware templates, Faulhaber forms,
  relevant-const pruning, `batch_check`.
- Relational simulation: product system, aux-invariant synthesis, output bijection discovery;
  strength-reduction case study (`k == c·i` inferred); two-sided teeth.
- SCEV ⇄ recurrence correspondence: LLVM's own analysis as the verification frontend.

## 6. Closed-loop translation validation + CEGAR witnesses
- Pipeline: `opt -passes=X` → parse the literal output → prove equivalence/refinement.
- Coverage: InstCombine scalar TV, SLP per-lane, mem2reg (multi-block + phi), DSE over arrays,
  SimplifyCFG if-conversion, indvars loop→closed-form (surfaced as `loop-eliminated`).
- Witnesses: why the inductive-step model can't be trusted for counterexamples; forward-execution
  search; minimality.

## 7. Scaling: the orchestrator and the verification agent
- Orchestrator: classify a pass source into transform families (deterministic, feature-scored) →
  plan feasible checks → dispatch to the verifiers; headlines {proved, refuted, error, advisory,
  planned, skipped, unclassified}; coverage gaps explicit, never silent.
- The agent (`cv-agent`): batch triage of the *residue*. Per pass, an LLM observes evidence and
  selects from a whitelisted, schema-validated action registry — run a real verifier, recover a
  fold, propose Z3-proof-gated intent candidates, stage a new-tool candidate in quarantine — under
  global/per-pass budgets with strike-based degradation.
- The trust model as a *system invariant*, not a policy: deterministic headlines byte-preserved;
  agent-dispatched formal verdicts provenance-tagged (`origin: agent`) in a separate headline; LLM
  conclusions advisory; staged tools hash-pinned, isolated (`python -I`, temp cwd), human-promoted;
  fail gates split (deterministic vs agent-formal); prompt injection bounded to *which whitelisted
  verifier runs*, never *what counts as sound*.
- Positioning: this is how "low on human reviewers" teams consume formal verification — one merged
  report instead of 143 tools.

## 8. Implementation
- Real-parser frontends (SCEV via `opt`, Clang AST) over a parser-agnostic prover; provider-
  agnostic LLM transport (any JSON-stdin/stdout command; deterministic stub in CI).
- Package architecture; ~143 CLI tools as thin shims over one Python core; no network in the gate.

## 9. Related work
- **Alive / Alive2** (Lopes et al.): per-pair peephole refinement. O2T differs on axis 1 (verify
  the pass source, not an IR pair), axis 2 (certify the recovery itself), axis 3 (unbounded loops
  via invariants).
- **Translation validation** (Pnueli; Necula; Tristan & Leroy): O2T's closed-loop mode is TV;
  the compiler-grounding of *recovery* transplants the TV idea one level up.
- **CompCert / verified compilation**: full proof vs per-run validation; O2T targets the passes
  CompCert doesn't cover — third-party/vendor passes against an unverified LLVM.
- **Invariant inference** (Houdini, ICE, PDR/IC3): template synthesis + k-induction here;
  data-driven inference as future work.
- **SCEV as an analysis** (Bachmann/Wise/Zima; LLVM): novel use as verification frontend.
- **Compiler fuzzing** (Csmith, EMI, YARPGen): finds bugs without proofs; O2T's witnesses connect
  the two.
- **LLM agents for verification/testing** (spec inference, proof repair, agentic SE): O2T's agent
  is deliberately *not* a proof generator — the LLM never contributes to soundness; contrast with
  systems where model output enters the trusted path.

## 10. Evaluation
Proposed experiments (fill with measured numbers; status tracked in
[claim-fixture-map.md](claim-fixture-map.md)):
- **E1 (loop soundness coverage):** N real `opt` passes × M benchmark loops → proved /
  loop-eliminated / refuted. Headline: zero false refutations.
- **E2 (teeth / mutation):** K recurrence + contract mutations → catch rate, witness minimality.
- **E3 (performance):** per-obligation Z3 time; integer vs bv32 (timeout); batch vs per-candidate.
- **E4 (frontend robustness):** rotated/multi-block/LCSSA loops where regex fails, SCEV succeeds.
- **E5 (case studies):** LSR from source; a found discrepancy if any.
- **E6 (Pass-IR corpus coverage) — NEW:** run the structural recovery over upstream
  InstCombine/InstSimplify fold functions → recovered / proved / declined / refuted counts, by
  recovery-ladder rung. Headline: X% recovered with **zero false proofs**; every decline reasoned.
- **E7 (recovery-soundness ablation) — NEW:** seed misrecoveries (dropped operators, mislowered
  builders, weakened guards, wrong reducers) and report which cross-check layer catches each
  (grounding / reconciliation / corroboration / teeth) — the mutation study applied to the
  *recovery*, not the target program.
- **E8 (agent triage) — NEW:** vendor-tree residue before/after an agent run; LLM budget vs
  provenance-tagged headline upgrades; confirmation that zero advisory outputs affected any gate.

Benchmark sources: recurrence kernels, LLVM's own test suite loops, `clang -O1` output of small C
kernels, and upstream `InstCombine`/`InstSimplify` sources for E6/E7.

## 11. Limitations and future work
Loop track: width-changing ops / Mode B; read–write memory beyond the current array fragment;
loop-nest transforms; vectorization. Pass IR: worklist fixpoints, cross-instruction accumulation,
in-place mutation semantics (RAUW/erase as IR-state transitions), full bitcode-side recovery of
the pass CFG/DFG. Agent: staged-tool promotion automation (deliberately absent), richer diagnosis
actions. Witness completeness (z3-seeded).

## 12. Conclusion
Verifying the pass — not the pair — is tractable: structural recovery with certified reading for
peepholes, recurrence lifting with an integer-ring discharge for loops, closed-loop validation
against the real optimizer, and an agent that scales the whole pipeline while keeping the LLM
outside the trusted base.

---

## Artifact appendix (for arXiv + artifact evaluation)
- Repo layout; `ctest` (438 fixtures) + `scripts/check-registries.sh`.
- Exact tool versions (Z3 4.16, LLVM 18, optional bitwuzla / KLEE 3.2 / CBMC / alive-tv).
- Claim → fixture map: **[claim-fixture-map.md](claim-fixture-map.md)** (now covering C1–C7).

## Figures / tables to produce
1. Commuting square ℤ → ℤ/2ⁿ.
2. Two-track pipeline diagram: pass source → {Pass-IR recovery | SCEV lifting} → obligations →
   prover → {proved | witness | declined}, with the cross-check stack as a vertical guard rail.
3. The recovery ladder (§3) with per-rung decline examples.
4. Recovery cross-check stack (§4) as a layered TCB diagram: what each layer removes.
5. Synthesis perf table; TV results matrix; witness-minimality table.
6. E6 corpus table: recovery-ladder rung × {recovered, proved, declined, refuted}.
7. E7 ablation matrix: misrecovery class × catching layer.

## Threats to validity
- SCEV reliance (a SCEV bug could mask a miscompile) — mitigated by concrete witnesses.
- The recovery fragment is regex/AST-scoped C++ — mitigated by declines-by-default + the §4 stack;
  residual risk: an *unmodeled* idiom silently absent from coverage (addressed by E6's corpus
  accounting).
- The agent's LLM is untrusted by construction; the residual agent risk is *wasted budget*, not
  soundness — and staged-tool quarantine is quarantine, not a security sandbox (stated).
- Coverage claims are fixture-relative; the map keeps them honest.
