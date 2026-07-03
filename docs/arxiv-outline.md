# O2T — arXiv Paper Outline

Working title options:
- *O2T: Lightweight Formal Validation of LLVM Loop Optimizations via Recurrence Lifting*
- *From Pass Source to Proof: Closed-Loop Translation Validation of LLVM Loops*
- *Proving Loop Optimizations for All Trip Counts with an Integer-Ring Discharge*

Target: arXiv `cs.PL` (cross-list `cs.LO`). Companion artifact: this repository.

---

## Abstract (draft)

> Optimizing compilers are trusted but rarely proved correct on the transforms that matter most —
> loop optimizations, whose effect spans an unbounded number of iterations. We present O2T, a
> lightweight system that formally validates LLVM loop optimizations by lifting their *intent* into
> a small recurrence DSL and discharging soundness obligations with an SMT solver. Three ideas make
> this practical. First, an **integer-ring discharge** proves nonlinear modular identities that bit-
> blasting cannot, sound for every bitwidth via the homomorphism ℤ → ℤ/2ⁿ. Second, **template-based
> invariant synthesis** over the integers, discharged by k-induction and lifted to relational
> (two-loop) simulation, proves transforms such as strength reduction for *all* trip counts. Third,
> a **frontend/prover separation** lets us drive the same proof core from LLVM's own Scalar Evolution
> analysis (`opt`) and Clang AST (`clang`), so we validate the optimizer's *actual output* —
> closed-loop translation validation — and, on failure, emit a **minimized concrete counterexample**.
> O2T proves real LLVM passes (LSR, LICM, loop-rotate, loop-unswitch) equivalent to their input on a
> benchmark of loops, refuses injected miscompiles with witnesses, and is fully reproducible behind a
> 417-fixture test suite.

---

## 1. Introduction
- Motivation: loop opts are high-stakes and least-trusted (LoopVectorize/LSR/indvars miscompiles).
  Bounded testing (fuzzing, Alive2 on unrolled bodies) misses trip-count-dependent bugs.
- Gap: proving "for all n" needs invariants; doing it *automatically* and *cheaply* on real LLVM IR
  is the open problem.
- Approach in one paragraph: lift to recurrences → integer-ring discharge + invariant synthesis →
  drive from real LLVM analyses → closed-loop translation validation + minimized witnesses.
- Contributions (bulleted, map to §-numbers in `o2t-design.md`):
  1. Integer-ring discharge for all-width nonlinear identities (design §2).
  2. Faulhaber-aware invariant synthesis + k-induction; coupled/relational extension (design §3–4).
  3. Intent recovery from pass *source* via the SCEV⇄recurrence correspondence (design §5).
  4. Closed-loop translation validation of real `opt` passes with CEGAR witnesses (design §6–7).
  5. Real-parser frontends over a parser-agnostic prover; reproducible artifact (design §8, §10).

## 2. Background
- LLVM IR, SSA φ-loops, induction variables.
- Scalar Evolution: add-recurrences `{start,+,step}`, chained recurrences, exit values.
- SMT over `Int` vs bitvectors; why nonlinear bv multiply is hard.
- Translation validation vs verified compilation; Alive2's refinement model.

## 3. The integer-ring discharge  (design §2)
- Modular semantics; the homomorphism argument (one figure: commuting square ℤ→ℤ/2ⁿ).
- Worked example: `acc += i·i` ⇒ `6·acc == 2i³−3i²+i` proved in ~0.02 s; bv32 does not terminate.
- Scope: holds for `+,−,×`; boundary at width-changing ops.

## 4. Recurrence invariants and synthesis  (design §3)
- k-induction obligations (BASE/STEP).
- Degree-aware template, `FACTORIAL_M`, relevant-const pruning, `batch_check` (perf table).
- Non-polynomial classes: conditional (ite-stride), geometric (relational), memory (UF loads).

## 5. Relational simulation for two-loop transforms  (design §4)
- Product system; aux-invariant synthesis; output-bijection discovery.
- Strength reduction case study: `{k==c·i, acc==acc}` certifies a quadratic accumulator linearly.
- Two-sided teeth: wrong stride refuted.

## 6. Intent from optimization code  (design §5)
- SCEV ⇄ recurrence correspondence.
- Lifting LSR's `getMulExpr`→`getAddRecExpr` idiom from source; sound vs refuted vs declared-skip.

## 7. Closed-loop translation validation  (design §6)
- Pipeline: `opt -passes=X` → SCEV-extract both sides → relational proof.
- loop→loop (proved) vs loop→closed-form (`indvars`, surfaced as `loop-eliminated`).
- Result table: passes × functions → {proved, loop-eliminated}.

## 8. CEGAR counterexamples  (design §7)
- Why not trust the inductive-step model (unreachable pre-states).
- Forward-execution witness search; minimality; example `{c:0,n:1} trip=1 ⇒ 0 vs 1`.

## 9. Implementation  (design §8)
- Real-parser frontends (SCEV via `opt`, Clang AST via `clang`); parser-agnostic prover.
- Package architecture; no dynamic-import hacks; reproducibility via `--selftest`/CTest.

## 10. Evaluation
Proposed experiments (fill with measured numbers):
- **E1 (soundness coverage):** N real `opt` passes × M benchmark loops → proved / loop-eliminated
  / refuted counts. Headline: zero false refutations on sound passes.
- **E2 (teeth / mutation):** inject K recurrence mutations (base, step, stride); report catch rate
  and witness-minimality (trip count, |params|).
- **E3 (performance):** per-obligation Z3 time; `batch_check` vs per-candidate; integer vs bv32
  (the latter as "timeout").
- **E4 (frontend robustness):** rotated/multi-block/LCSSA loops where regex frontends fail but the
  SCEV frontend succeeds.
- **E5 (case studies):** LSR from source (E5a); a found discrepancy / known-bug reproduction if any
  (E5b).

Suggested benchmark sources: hand-written recurrence kernels (sum, dot, polynomial, reductions),
loops extracted from LLVM's own test suite, and `clang -O1` output of small C kernels.

## 11. Related work
- **Alive / Alive2** (Lopes et al.): peephole refinement, bounded-loop unrolling. O2T differs by
  proving *unbounded* loops via invariants and by lifting intent from pass *source*.
- **Translation validation** (Pnueli et al.; Necula; Tristan & Leroy for LLVM): O2T is a lightweight,
  SMT-only, loop-recurrence-specialized point in this space, driven by SCEV.
- **CompCert / verified compilation**: full proof vs O2T's per-run validation tradeoff.
- **SMT invariant inference** (Houdini, ICE/data-driven, PDR/IC3): O2T uses template synthesis +
  k-induction; data-driven inference is discussed as future work.
- **Scalar Evolution** (Bachmann/Wise/Zima; LLVM SCEV): O2T's novel use is as a *verification
  frontend* and an *intent-lifting bridge*, not just an analysis.
- **Compiler fuzzing** (Csmith, EMI, YARPGen): finds bugs without proofs; O2T's closed-loop mode +
  witnesses connects fuzzing to proof.

## 12. Limitations and future work  (design §9)
Width-changing ops / Mode B (validate `indvars`); read–write memory (theory of arrays, DSE);
loop-nest transforms (interchange/fusion, dependence side-conditions); loop vectorization
(VF lanes + reduction + tail); full Alive2-style refinement; witness completeness (z3-seeded).

## 13. Conclusion
Lifting optimizations to recurrences + an integer-ring discharge + real-LLVM frontends yields
cheap, automatic, all-trip-count validation that finds and *minimizes* miscompiles.

---

## Artifact appendix (for arXiv + artifact evaluation)
- Repo layout; how to run `scripts/check-registries.sh` and `ctest`.
- Exact tool versions (Z3 4.16, LLVM 18, optional Bitwuzla / KLEE 3.2 / alive-tv).
- Mapping from each paper claim → the fixture that gates it: **[claim-fixture-map.md](claim-fixture-map.md)**.

## Figures / tables to produce
1. Commuting square ℤ → ℤ/2ⁿ (the homomorphism).
2. Pipeline diagram: source/IR → frontend → recurrence AST → prover → {proved | witness}.
3. Synthesis perf table (integer vs bv32; batch vs per-candidate).
4. Translation-validation results matrix (passes × loops).
5. Witness-minimality table (mutation kind → minimal trip count, params).

## Threats to validity (note for the eval)
- Frontend reliance on SCEV: a SCEV-analysis bug could mask a miscompile (mitigated: SCEV is the
  same analysis the pass trusts; differential cross-checks via concrete witnesses).
- Coverage is over recurrence-shaped loops; non-recurrence effects (control-flow-only, memory
  ordering) are out of current scope and stated as such.
