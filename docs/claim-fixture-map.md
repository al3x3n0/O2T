# Claim → Fixture Map (Artifact Appendix)

This table maps each claim in the paper (`docs/arxiv-outline.md`, contributions and design
§-numbers) and each row of the [verification ledger](llvm_transform_verification_ledger.md) to the
executable fixture(s) that gate it. Every entry is a test that runs under `ctest` or the formal gate,
so a reviewer can re-run any single claim.

## Reproducing

```sh
cmake -S . -B build && ctest --test-dir build      # 417 fixtures (the full suite)
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
| Full reproducibility: the whole suite is executable and deterministic | the 417-fixture `ctest` run |

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
| E1 soundness coverage (passes × loops → proved / loop-eliminated / refuted) | the closed-loop TV + loop fixtures (C4, C2) | ☐ TODO — no aggregate matrix yet |
| E2 teeth / mutation catch-rate + witness minimality | `proof_audit_fixture`, `cv-check-negative-intents --mutate` | ☐ TODO — per-run kills logged, no aggregate table |
| E3 performance (integer vs bv32; batch vs per-candidate; per-obligation Z3 time) | `closed_form_fixture`, multi-width proofs | ☐ TODO — no timing table |
| E4 frontend robustness (SCEV succeeds where regex fails) | SCEV frontend fixtures (C3/C4) | ☐ TODO — no comparative study |
| E5 case studies (LSR from source; a found discrepancy) | `symexec_real_pass_fixture`, `extract_*` fixtures | ◐ Partial — LSR source model exists; no closed-loop LSR validator |

> Note (LSR scope): the abstract lists LSR among "proved" passes, but the current evidence is
> source-model recovery (design §5), not closed-loop TV of real `opt -passes=lsr` output. This is an
> open item — either implement the validator or soften the abstract's wording.
