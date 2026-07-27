# Changelog

All notable changes to O2T are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Anti-vacuity guard for Track B.** Refinement holds vacuously wherever the source is UB or poison,
  so a `proved` can be valid and information-free — and that is exactly what an over-approximated UB
  model degrades into, invisibly to the `lli` and Alive2 oracles (which only see the proved set).
  `validate_transform` now probes whether the source is defined anywhere and reports
  `vacuous: True|False|None`; `validate_file` / `cv-tv-corpus` / `cv-fuzz-differential` report the
  count. Track A has had this guard since `mini_alive`. Gated by `vacuity_tv_fixture`, whose headline
  injects an over-approximated UB model, watches a genuine miscompile falsely prove, and catches it.
- **Second-solver cross-check for Track B.** Every decided query is replayed verbatim through an
  independently implemented SMT solver (`bitwuzla`/`cvc5`/`cvc4`, auto-detected; `skipped` — never a
  silent pass — when absent), including `mem_state`'s QF_ABV encoding. The other oracles check the
  *encoding*; this is the only one that checks the *solver*.

- **`freeze` in Track B.** The poison-laundering instruction InstCombine introduces was an outright
  decline, blinding whole-function TV on the poison-critical folds. Its nondeterministic choice is
  modeled asymmetrically: EXISTENTIAL on the target (introducing `freeze` verifies; freezing *newly*
  introduced poison over a definite source refutes with a witness) and UNIVERSAL on the source, which
  declines — `freeze`-removal stays outside the fragment until `undef` is modeled, because the
  tempting "freeze of a definite value is the identity" shortcut proves `freeze %x -> %x`, which
  reference Alive2 refutes (an argument may be `undef`). Gated by `freeze_tv_fixture` with every
  verdict confirmed against Alive2, plus a `--shape freeze` fuzzer mode (0 disagreements over 1,000
  pairs across two seeds; all 12 refutations matched Alive2). Measured lift: +3 functions on LLVM's
  `select.ll`.

- **The undeclared `noundef` assumption, and the false proofs it produced.** Track B modeled each
  parameter as one definite SMT constant — silently assuming `noundef` on every argument. LLVM lets an
  argument be `undef`, where each *use* may observe a different value, so `ret i32 0 -> xor %x, %x`
  (and `sub`, `icmp eq` of a parameter with itself) **proved** while reference Alive2 refutes them;
  adding `noundef %x` makes Alive2 prove the same transform, pinning the mechanism. Found by hand-built
  adversarial probes, not by the campaigns: the oracles only ever see targets produced by real
  InstCombine, which never *introduces* a duplicated argument use, so the class is reachable through
  the `validate_transform` API rather than through a corpus sweep. `noundef` is now parsed, and a guard
  declines exactly where the assumption is load-bearing (the target's result or its poison depends on a
  non-`noundef` parameter the source's does not), leaving UB refutations and sound transforms alone.
  Parameter attributes are now understood generally — including ones containing commas and parentheses,
  which previously truncated the signature and silently dropped every later parameter. Gated by
  `undef_param_fixture` with every verdict confirmed against Alive2.

### Changed
- Measured Track B reach re-stated at **428/715 (60%)** of LLVM 18's `and/or/xor/add.ll` — the scalar
  refinement path 359 plus the memory/vector dispatch 69 — with **zero vacuous proofs** and
  **428/428 confirmed by a second solver, zero disagreements**. The previously documented 351/715
  (49%) counted the scalar path alone; parameter-attribute parsing accounts for the latest rise.

### Planned
- Reproducible install + CI: a container pinning Z3 + LLVM 18, env-driven tool discovery replacing the
  macOS-hardcoded paths, and a GitHub Actions workflow running the fixture gate on clean Linux.

## [0.1.0] — 2026-07-23

First public release.

### Added
- **`o2t` command** — a single front-door CLI over the ~160 `tools/cv-*.py` shims:
  `o2t doctor` (toolchain check with actionable per-tool hints), `o2t verify` / `o2t orchestrate` /
  `o2t agent` (verify a fold, a pass tree, or LLM-triage the residue), `o2t list` / `o2t run`.
- **`QUICKSTART.md`** (clone → first proof) and **`docs/capabilities.md`** (the honest
  what-verifies-vs-declines map).
- Complete packaging metadata and a `console_scripts` entry point (`pip install -e .` puts `o2t` on PATH).

### Verification (already in the tree at first release)
- **Track A** — recover peephole-pass intent from C++ source and prove it with Z3, defended by a
  cross-check stack (engine reconciliation, a second solver, width/arity corroboration, a Clang-AST
  front-end, certificates, abduction).
- **Track B** — whole-function translation validation of the real `opt` output over a bounded-code
  fragment (branch/φ, byte-addressable memory, fixed & scalable vectors, interprocedural value flow,
  argument promotion); composition up to pipelines and modules; attribution to the recovered fold.
- **Loop track** — all-trip-count proofs via an integer-ring discharge, invariant synthesis with
  k-induction, and relational two-loop simulation.

### Fixed (soundness review, this release)
Eight-round adversarial audit of the prover surfaces
([`docs/soundness-review-2026-07.md`](docs/soundness-review-2026-07.md)) — two live false proofs
found and closed, three unenforced declines hardened, one false-refutation imprecision corrected; each
pinned with a regression test:
- `udiv`/`sdiv exact`-flag introduction is now caught as poison (was proved) — the refinement encoding.
- Mixed signed/unsigned `min`/`max` in the all-trip-count ℤ discharge now declines (was proved).
- The memory model declines a target that introduces a dereference the source lacks (null-deref gap).
- Pipeline composition no longer falsely refutes a net-sound pipeline a later pass repairs.
- Function-signature readers anchor on the definition (not a forward-reference call site); the
  whole-`.cpp` selector declines ambiguous overloads.

[Unreleased]: https://github.com/al3x3n0/O2T/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/al3x3n0/O2T/releases/tag/v0.1.0
