# Claim → Fixture Map (Artifact Appendix)

This table maps each claim in the paper (`docs/arxiv-outline.md`, contributions and design
§-numbers) and each row of the [verification ledger](llvm_transform_verification_ledger.md) to the
executable fixture(s) that gate it. Every entry is a test that runs under `ctest` or the formal gate,
so a reviewer can re-run any single claim.

## Reproducing

```sh
cmake -S . -B build && ctest --test-dir build      # 438 fixtures (the full suite)
scripts/check-registries.sh                         # the formal proof gate (Z3), with JSON reports
```

Individual fixtures run standalone, e.g. `python3 tests/fixtures/<name>.py` (needs `z3`, and for the
closed-loop validators `opt`/`llvm-as` from LLVM 18). Names ending `_fixture` that are not `.py`
files under `tests/fixtures/` are inline `add_test` targets in `CMakeLists.txt`; run them with
`ctest --test-dir build -R <name>`.

External tooling: Z3 4.16 (required); a second SMT solver (bitwuzla / cvc5, auto-detected) for the
independent cross-check; LLVM 18 `opt`/`clang`/`llvm-as` for the SCEV/Clang frontends and closed-loop
translation validation; optionally KLEE 3.2 (+ matching clang) and CBMC/ESBMC and `alive-tv`.

---

## Contributions (paper §-numbers → design §-numbers → fixtures)

### C1 — Integer-ring discharge for all-width nonlinear identities (design §2)

Nonlinear modular identities proved over ℤ and carried to every bitwidth via ℤ → ℤ/2ⁿ.

| Claim | Gating fixture(s) |
| --- | --- |
| Loop → closed-form (Faulhaber) discharged by per-widening modular lemmas, degree ≤ 3, mod 2³² | `closed_form_fixture`, `translation_validation_fixture` |
| All scalar identities re-proved at i8/i16/i32/i64 (width-parametric re-encoding) | `check-registries.sh` (`cv-prove-multiwidth.py`); inline `*_multiwidth_*` tests |
| Extended identity families (Reassociate / InstSimplify / shift-by-zero) proved at 4 widths | `check-registries.sh` (`cv-prove-identities.py`) |
| The prover is not vacuous: negative + mutation soundness (a wrong identity is refuted) | `check-registries.sh` (`cv-check-negative-intents.py --mutate`) |

### C2 — Recurrence invariants: synthesis + k-induction; relational/coupled extension (design §3–4)

Proving loop transforms for **all** trip counts by induction over loop-carried state, with the
simulation relation inferred automatically (Houdini over equality + affine atoms).

| Claim | Gating fixture(s) |
| --- | --- |
| Unbounded loop equivalence by induction (init/guard/step/result); wrong step/exit refuted | `loop_induction_fixture` |
| Simulation-relation equivalence for reshaped state; strength reduction (`j == 3·i` inferred) | `loop_simulation_fixture` |
| Unbounded nested-loop equivalence, compositional (inner as an uninterpreted function) | `loop_nested_fixture` |
| Unbounded multi-exit loop equivalence (ordered exits + step) | `loop_multiexit_fixture` |
| Unbounded loop-rotate as guard-motion (reconstruct canonical model + self-verify) | `loop_rotate_fixture` |
| Bounded loop-CFG transforms (loop-rotate / simple-loop-unswitch at constant trip counts) | `loop_cfg_ir_fixture` |

### C3 — Intent recovery from pass *source* via SCEV ⇄ recurrence (design §5)

Recovering a fold's before/after intent and its legality guards from the pass's own C++, then
discharging the obligation — sound vs refuted vs declared-unsupported.

| Claim | Gating fixture(s) |
| --- | --- |
| Symbolic execution of the **real compiled pass C++** over its true control-flow paths | `symexec_real_pass_fixture` |
| KLEE-driven variant: analysis queries + opcode symbolic, forks on feasible paths (incl. `&&`) | `klee_symexec_fixture` |
| Source-recovered SimplifyCFG if-conversion (operand binding, swap without negate refuted) | `extract_cfg_model_fixture` |
| Source-recovered DCE erasure guards (`isInstructionTriviallyDead`, `use_empty`, …) | `extract_dce_model_fixture` |
| Source-recovered GlobalOpt dead-initializer legality (linkage / no-use) | `extract_globalopt_model_fixture` |
| Source-recovered LICM hoist guards (invariance / speculatable / guaranteed-to-execute) | `extract_loop_structural_model_fixture` |
| Source-recovered memory-transform obligations (DSE / forwarding array facts) | `extract_memory_model_fixture` |
| Source-recovered SLP reduction / pack lane-mapping recovered from source | `extract_slp_model_fixture` |
| Mined intents lowered to formal IR; ambiguous/multi-pass predicate-site disambiguation | `intent_inference_registry_formal_fixture`, `globalopt_intent_inference_registry_formal_fixture`, `source_miner_ambiguity_fixture` |
| Optional CBMC/ESBMC model-check cross-check of source-mined folds | `modelcheck_intents_fixture`, `modelcheck_{cfg,dce,globalopt,licm,memory,slp}_source_fixture` |

### C4 — Closed-loop translation validation of real `opt` + CEGAR witnesses (design §6–7)

Running the **actual** `opt -passes=X`, parsing the literal emitted instructions, and proving them
equivalent to the input — with a minimized concrete counterexample on failure.

| Claim | Gating fixture(s) |
| --- | --- |
| InstCombine scalar TV (Alive2-style refinement; introduced nsw/nuw/exact/div-UB refuted) | `instcombine_ir_fixture`, `scalar_tv_fixture` |
| SLP-vectorizer TV per output memory cell (vector load/op/shuffle/store; poison-refinement) | `slp_ir_fixture` |
| Mem2Reg TV — first multi-block + phi validator (phi placed to match memory) | `mem2reg_ir_fixture` |
| DSE TV over a theory of arrays (final memory + surviving-load values; mixed-width/volatile declined) | `dse_ir_fixture` |
| SimplifyCFG diamond→select if-conversion TV | `cfg_shape_fixture` |
| indvars loop→closed-form TV (surfaced as loop-eliminated) with CEGAR witness | `closed_form_fixture`, `translation_validation_fixture` |

### C5 — Real-parser frontends over a parser-agnostic prover; reproducible artifact (design §8, §10)

| Claim | Gating fixture(s) |
| --- | --- |
| Pass-aware orchestrator: classify → plan → dispatch across every validator | `orchestrate_fixture`, `orchestrate_sweep_fixture` |
| Provider-agnostic LLM brain hook (deterministic stub) | `orchestrate_llm_stub`, `orchestrate_fixture` |
| Reproducible packaging: offline wheel, renamed/compat packages | `python_package_wheel_fixture`, `cmake_legacy_with_llvm_fixture` |
| Full reproducibility: the whole suite is executable and deterministic | the 438-fixture `ctest` run |

### C6 — Pass IR: structural peephole-intent recovery from pass source (paper §3)

Recovering a fold's `before ≡ after` obligation STRUCTURALLY from its C++ — matcher trees, rewrite
DFGs, path conditions, helpers, operand-list loops — with everything unmodeled declined, never
mis-modeled.

| Claim | Gating fixture(s) |
| --- | --- |
| Compositional matcher-tree + rewrite-DFG recovery; nested folds the flat triple cannot express; recovered preconditions load-bearing (`sdiv→udiv` refuted unguarded / proved guarded / vacuous caught); teeth (wrong fold refuted with witness); unmodeled matchers and misparse-prone operators decline | `pass_graph_fixture` |
| Function-level path conditions: early-return bailouts (De Morgan), positive guards, arbitrary nesting; interprocedural single-return helper inlining (multi-statement declines) | `pass_graph_fixture` |
| Poison/undef two-level lattice, freeze guards, no-wrap/exact/disjoint flags as refinement obligations; icmp predicates, min/max & bit-manipulation intrinsics, cast round-trips | `pass_graph_fixture` |
| Refinement via the existential (2QBF) encoding — freeze idempotence proved where the single-quantifier check declines | `pass_graph_refinement_fixture` |
| Memory obligations over the theory of arrays: store-to-load forwarding / DSE proved, unsound-without-aliasing-guard refuted | `pass_graph_memory_fixture` |
| Operand-list loops, NON-independent iterations (guard case): `phi [x,x,…,x] → x` recovered with its quantified all-equal guard; under-recovered guard refutes; worklist/side-effect bodies decline | `pass_graph_operand_loop_fixture` |
| Operand-list loops (reduction case): left-fold rebuild recovered as the associativity obligation; mismatched reducer refuted; non-associative reducer caught by arity corroboration | `pass_graph_reduction_loop_fixture` |
| The AST-miner finding schema bridges into the same recovery (operand-level findings) | `pass_graph_miner_fixture` |
| RETURN-form rewrite anchor (upstream's "return the replacement" contract): a VERBATIM LLVM 18 fold recovered + proved + exhaustively reconciled; teeth (mutated reducer refutes); name gate, instruction-subject gate (pins the operand-subject false-refutation found on upstream), let-mutation guard, in-place/unbound returns decline; RIUW anchor byte-unchanged | `pass_graph_return_form_fixture` |
| Per-fold CASCADE slicing: every `if (match...) return ...;` arm an independent obligation; arm-0 refutations pass-level, later arms `refuted-standalone` only (zero false refutations preserved); in-place-mutation screen declines the cascade | `passir_corpus_fixture` (3-arm cascade + mutation screen) |
| Multi-match COMPOSITION: instruction + operand conjuncts spliced into one before-tree (structured trees; retired-name/foreign-subject/out-of-range/m_Specific slots decline); comma-declarator lets; the operand-subject gate hole (`match(I.getOperand(0),...)` impersonating the instruction subject) closed | `pass_graph_compose_fixture` |
| The simplifyXInst CALLER CONTRACT: phantom instruction synthesized from the documented name; ORIENTATION honored on non-commutative ops (`0-X->X` refutes); nullptr sentinels/reassigned locals inert; `getType()` normalized in rewrites only (cast type-equality guard survives); non-canonical names/missing params/foreign subjects decline | `pass_graph_contract_fixture` |
| Predicate-SET case splits: per-member obligations through matcher + generic CreateICmp rewrite, ALL must prove (hardcoded-member overreach refuted); subject form via unique binder; domain-affirming drops incl. positive bails; positive `isa<Constant>`/i1-width decline; the inverted-guard hole (negated facts binding their POSITIVE premise) closed on both routes | `pass_graph_predset_fixture` |
| The TWO-ICMP caller contract: verbatim foldIsPowerOf2OrZero proves both arms (real ctpop theorems) via two-primary composition, IsAnd case selection, and operand projection; UGE-mutation and combiner-swap teeth refute; single-cmp/selector-less shapes decline | `pass_graph_twoicmp_fixture` |

### C7 — Certifying the recovery itself: the cross-check stack (paper §4)

A mis-recovery is as dangerous as a miscompile; each layer independently checks the *reading* of
the source, shrinking the trusted base.

| Claim | Gating fixture(s) |
| --- | --- |
| Engine reconciliation: symbolic z3 vs exhaustive concrete enumeration must agree (divergence = untrustworthy) | `pass_graph_fixture` (`reconcile` cases) |
| Compiled-oracle reconciliation: the fold realized as a shim harness, compiled, and symbolically executed must agree with z3 | `pass_graph_fixture` (phase 3b cases) |
| Independent second SMT solver (bitwuzla) on the identical SMT-LIB | `pass_graph_solver_fixture` |
| Width-parametric corroboration: verdicts at {8,16,32,64} must agree, else `width-specific` (byte masks, 32-bit closed forms flagged); cast folds cross-width reconciled | `pass_graph_width_fixture` |
| Arity-parametric corroboration: verdicts at arities {2,3,4} must agree, else `arity-specific` — catches under-recovered guards and non-associative reducers invisible at the representative bound | `pass_graph_operand_loop_fixture`, `pass_graph_reduction_loop_fixture` |
| Compiler-grounded recovery: the VERBATIM source rewrite compiled through an independent shim must compute the recovered `after` (a self-consistent-but-unfaithful recovery diverges and is caught) | `pass_graph_grounding_fixture` |
| Structured-tree front-end: pre-parsed matcher/rewrite trees recover the IDENTICAL obligation, removing the tokenizer/parser from the TCB -- a Clang-AST producer (`clang -ast-dump=json`) realizes this byte-for-byte in STUB-mode (unguarded/guarded/return-form) and in SOURCE-FILE mode against the REAL LLVM 18 headers, where it recovers **3/3 InstCombine E6 folds verbatim, parser-free** (combineAddSubWithShlAddSub return-form, the foldXorToXor 3-arm cascade, and both arms of the two-icmp contract foldIsPowerOf2OrZero -- the `m_Intrinsic<ctpop>` id read at the compiler-pinned span), each byte-identical to the regex path, a UGE mutation refuted with a witness | `pass_graph_structured_fixture`, `clang_tree_fixture`, `clang_tree_source_fixture` |
| Re-checkable verdict certificates | `pass_graph_certificate_fixture` |
| Precondition abduction: an unsound fold's MISSING guard is synthesized (diagnosis, not just rejection) | `pass_graph_synthesis_fixture`, `pass_graph_memory_fixture` (aliasing guard) |
| Obligations lowered to real LLVM IR for a machine-checked interpreter oracle (Vellvm-ready) | `pass_graph_ir_fixture` |

### C8 — The verification agent: LLM-in-the-loop under a strict trust model (paper §7)

The LLM routes, proposes, and stages; formal verifiers decide every verdict.

| Claim | Gating fixture(s) |
| --- | --- |
| A scripted LLM drives an unclassified residue pass to a REAL Z3-proved verdict via whitelisted actions; the deterministic headline is byte-preserved; agent formal verdicts are provenance-tagged (`origin: agent`) in a separate headline | `agent_fixture` |
| An advisory LLM "refuted" conclusion changes no headline and trips no fail gate; gates split deterministic vs agent-formal | `agent_fixture` |
| Invalid/malformed LLM replies execute NOTHING (observation + strike; two strikes degrade); budget exhaustion winds down cleanly; resume skips settled passes (sha256-guarded) | `agent_fixture` |
| Tool synthesis is opt-in and quarantined: staged only under agent-staging/ (hash-pinned manifest, unsafe names refused), fixtures run isolated (`python -I`, temp cwd), results advisory-staged with zero verdict weight, tools/ untouched | `agent_synthesis_fixture` |
| The action registry whitelists and schema-validates; unknown actions and out-of-enum strategies rejected; no shell from the LLM | `agent_selftest`, `agent_fixture` |

---

## Deep transform models (the ledger's "Formal Proof" tier → fixtures)

Each deep contract is a canonical SMT model with two-sided teeth (a single-point corruption is
refuted with a witness).

| Transform family | Deep-model fixture |
| --- | --- |
| DSE / store-forwarding / redundant-load (theory of arrays, byte-granular, atomics) | `memory_model_fixture` |
| SLP pack lane-mapping + reduction associativity (incl. FP-without-fast-math refuted) | `slp_model_fixture` |
| GlobalOpt dead-initializer observability | `globalopt_model_fixture` |
| DCE dead-instruction / dead-loop-instruction / unused-alloca erasure | `dce_model_fixture` |
| LICM hoist legality (invariance + trap-safety) | `loop_structural_model_fixture` |
| SimplifyCFG if-conversion | `cfg_shape_fixture` |

---

## Meta-verification (proof-about-the-proofs → fixtures)

These certify what a "proved" verdict *means* — the trust base behind every claim above.

| Meta-property | Gating fixture(s) |
| --- | --- |
| Every proved deep contract is non-vacuous (premises SAT) and load-bearing (all single-point mutants refuted, no survivors) | `proof_audit_fixture` |
| Every verdict independently confirmed: refutation witnesses re-validated + second-solver (bitwuzla) cross-check | `cross_check_fixture` |
| Deep contracts re-proved off their fixed instance across widths {8,16,32,64} and arities {2,4,8,16} | `parametric_fixture` |
| Anti-vacuity of the main equivalence pipeline: contradictory premises rejected (not proved) | `formal_ir_vacuous_premises_fixture` |
| The two analysis-fact provers share ONE encoder (no drift) | `value_tracking_consolidation_fixture` |
| The full formal registry gate: 51 positive proofs + multi-width + negative/mutation + extended identities | `registry_contract` (inline), `scripts/check-registries.sh` |

---

## Evaluation experiments (paper §10) — status

The outline's E1–E5 propose measured experiments. Their **mechanisms** are gated by the fixtures
above (e.g. the teeth/mutation infrastructure for E2, the width re-proofs for E3, the SCEV frontend
for E4), but the **aggregate result tables/figures with measured numbers are not yet produced**.

| Experiment | Mechanism gated by | Result table produced? |
| --- | --- | --- |
| E1 soundness coverage (passes × loops → proved / loop-eliminated / refuted) | `tv_matrix_fixture` (zero-false-refutation invariant + mutate teeth) | ✓ **Measured** ([e1-coverage.md](e1-coverage.md)): 5 real opt passes × 7 loops = 35 cells, 26 positive verdicts, **0 false refutations** on sound LLVM, 9 honest loop-eliminated; a mutated recurrence refutes with a witness |
| E2 teeth / mutation catch-rate + witness minimality | `mutation_catchrate_fixture` (zero-survivor invariant across all tiers) | ◐ **Catch-rate measured** ([e2-mutation.md](e2-mutation.md)): 52/52 seeded corruptions caught, 0 survivors (34 deep-contract mutants + 7 recovery classes + 11 registry intents); witness MINIMALITY scoped to the loop track (E1/E5), stated not overclaimed |
| E3 performance (integer vs bv32; batch vs per-candidate; per-obligation Z3 time) | `prove_timing_fixture` (orderings + generous bounds; never machine numbers) | ✓ **Measured** ([e3-timing.md](e3-timing.md)): Int `unsat` 0.105 s vs bv32 timeout at the 10 s cap; batch 19.5× over per-candidate; fold obligations 12–83 ms |
| E4 frontend robustness (SCEV succeeds where regex fails) | `frontend_robustness_fixture` (differential + shape-not-parser control) | ✓ **Measured** ([e4-robustness.md](e4-robustness.md)): on 4 rotated/LCSSA loops regex recovers 0, SCEV recovers 4 (strict domination); regex recovers 4/4 on the simple control |
| E5 case studies (peephole-from-source; strength reduction; discrepancy detection) | `pass_graph_twoicmp_fixture`, `loop_simulation_fixture`, `tv_matrix_fixture` | ◐ **Documented** ([e5-case-studies.md](e5-case-studies.md)): foldIsPowerOf2OrZero recovered from verbatim upstream source end-to-end; strength reduction proved relationally + wrong-stride refuted; discrepancy detection scoped honestly to injected miscompiles. Closed-loop LSR-of-real-opt and wild-bug reproduction remain open |
| E6 Pass-IR corpus coverage (upstream InstCombine/InstSimplify → recovered/proved/declined/refuted by ladder rung) | `passir_corpus_fixture` (runner mechanics), `pass_graph_return_form_fixture`, `pass_graph_compose_fixture`, `pass_graph_contract_fixture` | ◐ **Measured, four runs** ([e6-passir-corpus.md](e6-passir-corpus.md)): 441 candidates → **10 upstream fold arms proved** across 7 functions (incl. a 3-arm cascade), every arm reconcile-cross-checked, **0 false proofs / 0 false refutations** across all runs; five recovery bugs caught by the discipline itself along the way (E7 specimens); residual frontier decomposed and stated |
| E7 recovery-soundness ablation (seeded misrecoveries × catching layer) | `passir_ablation_fixture` (zero-escape invariant + uniquely-load-bearing layers) | ✓ **Measured** ([e7-ablation.md](e7-ablation.md)): 7 misrecovery classes × 4 seed folds, ZERO escapes; width-corroboration and the all-cases discipline each proven uniquely load-bearing; plus the six field specimens, each caught by a different mechanism |
| E8 agent triage (residue reduction, budget vs upgrades, zero gate violations) | `agent_fixture` (mechanisms + trust invariants) | ☐ TODO — no vendor-tree run with a live LLM yet |

> Note (LSR scope): an earlier abstract draft listed LSR among "proved" passes; the current
> evidence remains source-model recovery (design §5), not closed-loop TV of real `opt -passes=lsr`
> output. The reframed abstract no longer makes the claim; implementing the closed-loop LSR
> validator stays an open E5 item.
