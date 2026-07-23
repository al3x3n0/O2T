# Changelog

All notable changes to O2T are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
