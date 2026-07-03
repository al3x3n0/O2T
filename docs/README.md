# O2T Documentation

O2T (Optimizer Testing Toolkit) — formal validation of LLVM optimizations by lifting their intent
into a recurrence DSL and discharging soundness with Z3.

- **[o2t-design.md](o2t-design.md)** — technical reference for the verification methods: the
  integer-ring discharge (all-width nonlinear identities), recurrence invariant synthesis +
  k-induction, relational two-loop simulation, intent recovery from pass source (SCEV bridge),
  closed-loop translation validation, CEGAR witnesses, the frontend/prover architecture, and the
  stated soundness boundaries. *(This is the paper's methods material.)*
- **[symexec_real_pass.md](symexec_real_pass.md)** — the `o2t/symexec/` subsystem:
  symbolically executing the **real compiled C++** of a custom pass fold over a symbolic input,
  enumerating its true control-flow paths, and discharging **poison/UB-aware refinement** per path
  (so an under-guarded pass is refuted with a witness). Covers the shim/driver/KLEE architecture, the
  optional CBMC/ESBMC model-checking cross-check, the precondition + poison + theory matrix, the
  source-derived `--modelcheck-intents` deep-audit bridge, the recipe for adding a fold, and the honest scope. *(Start here to catch up on the
  implementation-verification track.)*
- **[arxiv-outline.md](arxiv-outline.md)** — skeleton for the planned arXiv paper: abstract draft,
  contributions, section plan, evaluation experiments, related work, limitations, and the artifact
  appendix mapping each claim to the fixture that gates it.
- **[claim-fixture-map.md](claim-fixture-map.md)** — the artifact-appendix table itself: each paper
  claim and ledger row → the executable fixture(s) that gate it, plus the honest status of the
  E1–E5 evaluation experiments.
- **[verification-flow.md](verification-flow.md)** — the pipeline diagram (paper Figure 2): input →
  frontend → parser-agnostic prover → {proved | witness | unsupported} → meta-verification.

## Reproducing

Everything is gated by an executable suite:

```sh
cmake -S . -B build && ctest --test-dir build      # 417 fixtures
scripts/check-registries.sh                         # the gate layer, with JSON reports
```

Each verification tool also runs standalone, e.g.:

```sh
# Prove a REAL opt pass's output equivalent to its input, with a witness on failure:
python3 tools/cv-translation-validate.py --selftest --opt-bin "$(command -v opt)"
```

External tooling: Z3 4.16 (required); LLVM 18 `opt`/`clang`/`llvm-as` (for the SCEV/Clang
frontends and translation validation); optionally Bitwuzla, KLEE 3.2, CBMC or ESBMC, `alive-tv`.
