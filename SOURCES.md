# SOURCES — code map of the O2T repository

This file explains **what lives where** in the source tree, so you can go from a
task ("verify a peephole pass", "add a loop-simulation proof", "regenerate an
idiom header") to the file that owns it. It complements the *conceptual* docs in
[`docs/`](docs/README.md); this document is about the **layout of the sources**.

> O2T verifies LLVM optimization passes from their **source** (see
> [`docs/README.md`](docs/README.md) and the mission: verify third-party / in-house
> passes that use the LLVM API, not just IR pairs). The word "sources" is
> load-bearing here in two senses — the *pass* sources O2T consumes, and the O2T
> *code* sources described below.

---

## Top-level layout

| Path | What it is |
| --- | --- |
| [`o2t/`](o2t/) | The Python verification core (importable package). Everything below the front door lives here. |
| [`tools/`](tools/) | ~157 `cv-*.py` CLI entry points — thin shims that bootstrap `sys.path` and call into `o2t/`. This is what CMake/CTest fixtures invoke. |
| [`src/`](src/) + [`include/o2t/`](include/o2t/) | The C++ side: the bounded LLVM-IR **test generator**, config model, and probe/instrumentation layer (the original MVP). |
| [`harnesses/`](harnesses/) | C++ harnesses compiled to bitcode for **KLEE** symbolic exploration (fold / InstCombine style). |
| [`o2t/symexec/`](o2t/symexec/) | The "symexec the **real compiled pass**" track + the symbolic-LLVM shim and model-check bridges. |
| [`constraints/`](constraints/) | JSON knowledge base: optimization intents, guard semantics, LLVM idioms, lift rules, negative intents, formal templates. Data, not code. |
| [`compilerverif/`](compilerverif/) | Compatibility shim: `import compilerverif` re-exports `o2t` for legacy automation. |
| [`docs/`](docs/) | Conceptual/paper docs (design, symexec, orchestrator, pass-IR, arXiv outline, claim→fixture map). |
| [`tests/`](tests/) | Fixture drivers (`tests/fixtures/*.py`, C++ tests) and `tests/golden/` expected outputs. Gated by CTest. |
| [`scripts/`](scripts/) | Shell helpers for the external toolchains (opt / llvm / klee / clang-tooling / registry gate). |
| [`examples/`](examples/) | Sample generated `.ll` and configs. |
| `build*/` | CMake build trees (generated; not source). |
| [`pyproject.toml`](pyproject.toml) | Packages `o2t*` + `compilerverif*`; `pip install -e .` optional. |

---

## The verification core: `o2t/`

### Front door — `o2t/orchestrate/`
The pass-aware entry point (`tools/cv-orchestrate.py`). See
[`docs/orchestrator.md`](docs/orchestrator.md).
- `classify.py` — score a pass `.cpp` into a transform **family** from the LLVM
  idioms it uses (no build needed).
- `plan.py` — map each family's strategies to concrete, runnable checks
  (feasible / skipped-with-reason).
- `run.py` — dispatch the feasible checks to real verifiers and aggregate verdicts.
- `sweep.py` — route a whole curated pass-set into a coverage matrix.
- `brain.py` — optional, advisory LLM tie-breaker for ambiguous classification
  (provider-agnostic; formal verifiers still decide soundness).

### Verification agent — `o2t/agent/`
LLM-driven **batch triage** of the orchestrator's residue (`tools/cv-agent.py`). See
[`docs/agent.md`](docs/agent.md). The LLM picks whitelisted actions whose handlers run REAL
verifiers; everything agent-derived is quarantined under `pass["agent"]` with trust labels —
formal verifiers still decide every verdict.
- `actions.py` — the whitelisted action registry (schema-validated args; the LLM never emits shell).
- `loop.py` — per-pass agent loop (evidence, strikes, budgets, residue selection).
- `staging.py` — quarantine for synthesized tool candidates (`--enable-synthesis`; human-promoted).
- `report.py` — trust-quarantined merge into the orchestrator report; provenance-tagged headline.
- `llm.py` / `o2t/llm_io.py` — budgeted client over the shared provider-agnostic JSON transport
  (also used by `brain.py`).

### Ingestion — `o2t/frontend/`
Turn inputs into internal models.
- `scev_loop.py` — recover loop recurrences via `opt` SCEV.
- `llvm_loop.py` — parse LLVM loop IR.

### Source mining — `o2t/mine/`
Recover *what a pass does* from its source.
- `pass_scev.py`, `scev_relational.py` — SCEV/recurrence intent from pass source.
- `clang_pass.py` — Clang-AST-driven mining.
- `relational.py`, `llvm_relational.py`, `loop_invariant.py`, `shapes.py` —
  relational drivers and shape recognition.

### Intent extraction — `o2t/intent/`
Per-family model extractors that lift a pass into a verifiable model.
- `extract_pass_model.py` (peephole), `extract_memory_model.py` (DSE/forwarding),
  `extract_slp_model.py`, `extract_globalopt_model.py`, `extract_dce_model.py`,
  `extract_cfg_model.py`, `extract_loop_structural_model.py` (LICM).
- `pass_graph.py` — the typed **Pass IR** DFG/CFG model (see
  [`docs/pass-ir.md`](docs/pass-ir.md)).
- `corpus.py` — the E6 corpus runner (`tools/cv-passir-corpus.py`): the Pass-IR recovery over a
  real pass-source tree, with the decline taxonomy and the reconcile-gated zero-false-proof
  discipline (measured results: [`docs/e6-passir-corpus.md`](docs/e6-passir-corpus.md)).
- `infer.py`, `validate_registry.py` — intent inference + registry validation.

### Facts / analysis grounding — `o2t/facts/`
Ground each LLVM analysis query by its precondition.
- `value_tracking.py` — the analysis-fact SMT bridge (`isKnownToBeAPowerOfTwo`, …).
- `analysis_facts.py`, `semantic_facts.py`, `guard_semantics.py`,
  `globalopt_witness.py`, `source_graph_contract.py`, `source_marker_rules.py`,
  `ast_mining_metadata.py`.

### Synthesis — `o2t/synth/`
Synthesize the invariants/relations proofs need.
- `invariant.py`, `relational.py`, `coupled.py`, `pairing.py`, `poly.py`.

### Provers — `o2t/prove/`
The Z3 discharge engines.
- `loop.py`, `loop_induction.py` — recurrence invariant synthesis + k-induction.
- `memory.py` — theory-of-arrays memory reasoning.
- `multiwidth.py` — all-width / parametric integer-ring discharge.
- `cond_geom.py` — conditional/geometric closed forms.

### Validators — `o2t/validate/`
Closed-loop **translation validation** and family contracts (run real `opt`, prove
literal output equals input, with two-sided teeth). These are the oracle/reference
tier for LLVM's *built-in* passes.
- Peephole/scalar: `scalar_ir.py`, `translation.py`, `differential.py`, `witness.py`.
- Memory: `dse_ir.py`, `memory_model.py`, `mem2reg_ir.py`.
- Vectorize: `slp_ir.py`, `slp_model.py`.
- CFG: `cfg_shape.py` (diamond→select if-conversion), `globalopt_model.py`, `dce_model.py`.
- Loops: `loop_cfg_ir.py`, `loop_induction.py`, `loop_simulation.py`,
  `loop_rotate.py`, `loop_multiexit.py`, `loop_nested.py`,
  `loop_structural_model.py`, `closed_form.py`.

### Symbolic execution of the real pass — `o2t/symexec/`
The core third-party track (see [`docs/symexec_real_pass.md`](docs/symexec_real_pass.md)).
- `symbolic_llvm.h` — the **API-compatible symbolic-LLVM shim** an unmodified pass
  compiles against (PatternMatch, dyn_cast/isa, IRBuilder, …).
- `real_pass.py` — enumerate the real pass's control-flow paths + per-path
  poison/UB-aware refinement.
- `klee_driver.py`, `klee_fold*.c` — KLEE driver and fold harnesses.
- `modelcheck.py` + `modelcheck_{cfg,dce,globalopt,licm,memory,slp,intents}.py`,
  `modelcheck_llvm.h` — optional CBMC/ESBMC cross-check bridge.
- `vellvm_interp.py` — Vellvm-style interpreter track.

### Meta-verification — `o2t/meta/`
Guard the proofs themselves.
- `proof_audit.py` — every "proved" is non-vacuous (premises SAT) and load-bearing
  (single-point mutations killed).
- `cross_check.py` — witness re-validation + second-solver cross-check (cvc5/Bitwuzla).
- `parametric.py` — re-prove contracts across widths {8,16,32,64} × n {2,4,8,16}.

### Registry — `o2t/registry/`
- `optimization_registry.py`, `lift_matcher.py`, `lift_rules.py`,
  `targeted_ir_configs.py` — the known-optimizations catalog and lift rules.

### Standalone modules (`o2t/*.py`)
- `formal_ir.py`, `transaction_formal.py`, `assumption_algebra.py`, `mini_alive.py`.

---

## The C++ generator/probe side: `src/` + `include/o2t/`

The original MVP — build with `cmake` and does **not** require LLVM/KLEE.
- `src/GeneratorConfig.cpp` + `include/o2t/GeneratorConfig.h` — the bounded
  `GeneratorConfig` (symbolic under KLEE, or replayed from a file).
- `src/IRTextGenerator.cpp` — deterministic small-function LLVM-IR text generator.
- `src/SourceProgramGraph.cpp` — abstract program graph.
- `src/ProbeBackend.cpp` + `include/o2t/ProbeOracle.h`, `AbstractIR.h` — KLEE-safe
  abstract probe layer + backend boundary (abstract now, guarded LLVM slot).
- `include/o2t/PassProbes.h`, `PassInstrumentation.h` — `CV_PASS_PROBE*` macros.
- `include/o2t/Generated*.h` — **generated** headers (idioms, marker maps, matcher
  specs, klee feedback, vector intents); regenerated by `tools/cv-generate-*.py`
  from the `constraints/*.json` knowledge base. Do not hand-edit.

---

## CLI entry points: `tools/cv-*.py`

Thin shims — each bootstraps `sys.path` and calls a package `main()`; the CMake
gate fixtures invoke these so their paths stay stable. Common ones:

| Tool | Purpose |
| --- | --- |
| `cv-orchestrate.py`, `cv-orchestrate-sweep.py` | The front door + coverage sweep. |
| `cv-translation-validate.py` | Prove a real `opt` pass output ≡ input (has `--selftest`). |
| `cv-extract-pass-model.py` | Peephole/fold model extraction. |
| `cv-mine-pass-scev.py`, `cv-mine-memory-pass.py`, `cv-mine-dce-pass.py` | Source miners per family. |
| `cv-validate-memory.py`, `cv-validate-cfg.py`, `cv-validate-dce.py`, `cv-validate-intent-candidates.py` | Family validators. |
| `cv-klee-symexec-pass.py`, `cv-modelcheck-real-pass.py` | Real-pass symexec / model-check. |
| `cv-audit-proofs.py`, `cv-cross-check.py`, `cv-cross-solver.py` | Meta-verification. |
| `cv-run-external-pass-audit.py` | Deep external source-tree audit (compile-commands driven). |
| `cv-generate-*.py` | Regenerate the `Generated*.h` headers from `constraints/*.json`. |

(The IR replay tool `cv-replay` is a **C++ binary** built into `build/cv-replay`, not a Python shim — see the README.)

---

## Data & fixtures

- [`constraints/*.json`](constraints/) — the knowledge base the miners/validators
  and header generators read (`optimization_intents.json`, `guard_semantics.json`,
  `llvm_idioms.json`, `lift_rules.json`, `negative_intents.json`,
  `formal_templates.json`, `pass_constraints.json`, …).
- [`tests/fixtures/*.py`](tests/fixtures/) — executable fixtures (the CTest suite,
  ~417 cases); the sample pass sources here (`cfg_ifconv_sound.cpp`,
  `cfg_ifconv_folds.cpp`, …) double as sound/unsound teeth cases.
- `tests/golden/` — expected outputs.
- The **claim→fixture** mapping lives in
  [`docs/claim-fixture-map.md`](docs/claim-fixture-map.md).

---

## Build & run

```sh
cmake -S . -B build && ctest --test-dir build   # ~417 fixtures
scripts/check-registries.sh                       # the gate layer (JSON reports)
pip install -e .                                  # optional editable install of o2t
```

Required: **Z3 4.16**. For SCEV/Clang frontends + translation validation: **LLVM 18**
(`opt`/`clang`/`llvm-as`). Optional: KLEE 3.2, CBMC/ESBMC, Bitwuzla, cvc5, `alive-tv`.

See [`AGENTS.md`](AGENTS.md) for contribution conventions and
[`docs/README.md`](docs/README.md) for the conceptual entry points.
