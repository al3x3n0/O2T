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
- **[paper-draft.md](paper-draft.md)** — the working PROSE draft (v1): every quantitative claim
  gated by the fixture named in the claim map; E6/E7 measured, E1–E5/E8 honestly pending.
- **[arxiv-outline.md](arxiv-outline.md)** — skeleton for the planned arXiv paper, reframed around
  the unifying thesis *verify the pass, not the pair*: the Pass-IR structural-recovery track and its
  recovery-certifying cross-check stack lead, with the all-trip-count loop track, closed-loop TV,
  and the trust-modeled verification agent as co-contributions; abstract draft, section plan,
  evaluation experiments (E1–E8), related work, and the artifact appendix.
- **[claim-fixture-map.md](claim-fixture-map.md)** — the artifact-appendix table itself: each paper
  claim and ledger row → the executable fixture(s) that gate it (C1–C8, now including the Pass-IR
  recovery track, its cross-check stack, and the verification agent), plus the honest status of the
  E1–E8 evaluation experiments.
- **[verification-flow.md](verification-flow.md)** — the pipeline diagram (paper Figure 2): input →
  frontend → parser-agnostic prover → {proved | witness | unsupported} → meta-verification.
- **[pass-ir.md](pass-ir.md)** — design track for formalized DFG/CFG source mining: a typed Pass IR
  the prover consumes, with compositional before/after recovery (phase-2 core implemented) and the
  roadmap to CFG path-conditions, bitcode reconciliation, interprocedural helpers, and IR loops.
- **[agent.md](agent.md)** — the verification agent (`cv-agent.py`): LLM-driven batch triage of the
  orchestrator's residue via a whitelisted action registry, with the trust quarantine (formal
  verifiers decide every verdict; agent output is provenance-tagged or advisory) and the
  tool-synthesis staging procedure.
- **[e6-passir-corpus.md](e6-passir-corpus.md)** — the measured E6 series: the Pass-IR recovery
  over 441 upstream InstCombine/InstSimplify fold functions across phases 36–40 — 0 → 12 proved
  arms, 0 false proofs / 0 false refutations, the decline taxonomy as the coverage frontier.
- **[e7-ablation.md](e7-ablation.md)** — the measured E7 ablation: seeded misrecovery classes ×
  catching layers, zero escapes, two uniquely-load-bearing layers, and the six field specimens.
- **[e3-timing.md](e3-timing.md)** — the measured E3 performance table: the integer-ring discharge
  vs bit-blasting (0.105 s vs a 10 s timeout), 19.5× batched synthesis, per-obligation times.
- **[e2-mutation.md](e2-mutation.md)** — the measured E2 mutation catch-rate: 52/52 seeded
  corruptions caught, zero survivors across the deep-contract, recovery, and registry teeth tiers.
- **[e1-coverage.md](e1-coverage.md)** — the measured E1 coverage matrix: 5 real opt passes × 7
  loops, 26 positive verdicts, zero false refutations on sound LLVM, mutated-recurrence teeth.
- **[e4-robustness.md](e4-robustness.md)** — the measured E4 frontend differential: on rotated/LCSSA
  loops regex recovers 0, SCEV recovers 4 (strict domination), with a simple-loop control.
- **[e5-case-studies.md](e5-case-studies.md)** — the E5 worked case studies: foldIsPowerOf2OrZero
  recovered from verbatim upstream source, strength reduction proved relationally, discrepancy scope.

## Reproducing

Everything is gated by an executable suite:

```sh
cmake -S . -B build && ctest --test-dir build      # ~438 fixtures
scripts/check-registries.sh                         # the gate layer, with JSON reports
```

Each verification tool also runs standalone, e.g.:

```sh
# Prove a REAL opt pass's output equivalent to its input, with a witness on failure:
python3 tools/cv-translation-validate.py --selftest --opt-bin "$(command -v opt)"
```

External tooling: Z3 4.16 (required); LLVM 18 `opt`/`clang`/`llvm-as` (for the SCEV/Clang
frontends and translation validation); optionally Bitwuzla, KLEE 3.2, CBMC or ESBMC, `alive-tv`.
