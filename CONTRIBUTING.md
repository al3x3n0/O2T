# Contributing to O2T

Thanks for helping verify compilers. This project has one non-negotiable norm; everything else is
ordinary open-source practice.

## The one rule: formal oracles decide soundness

O2T's whole value is that a `proved` verdict is trustworthy. So:

- **Never a false proof.** A change that could make an unsound transform report `proved` is a bug of
  the highest severity — worse than a crash.
- **Decline by default.** If the tooling cannot *model* something, it must return `unsupported`
  (an explicit, sound non-answer) — never silently approximate. Widening a model to "prove more" is
  only acceptable when the new case is provably sound; otherwise, decline it.
- **The LLM only routes and proposes.** In `o2t agent` / self-enrichment, a language model may suggest
  semantics or which verifier to run, but an independent oracle it did not author (Z3, real `opt`,
  `lli` execution) must ratify every result. Proposed semantics are *data*, not trust.

If you're unsure whether a change respects this, open an issue and ask before writing code.

## Setup

See [QUICKSTART.md](QUICKSTART.md): `pip install -e .`, then `o2t doctor` to check the toolchain
(Z3 required; LLVM 18 for the full stack). [AGENTS.md](AGENTS.md) documents the repo layout, build,
and coding style; [SOURCES.md](SOURCES.md) maps every module.

## Every claim is gated by a fixture — with teeth

Fixture registration is not optional. If your change adds or alters what O2T can verify, it must come
with a `tests/fixtures/*.py` fixture, registered in `CMakeLists.txt`, that has **two-sided teeth**:

- it **proves** a case that should hold, **and**
- it **refutes** (with a witness) or **declines** a case that should not — ideally a *seeded-adversarial*
  case: an input crafted to be unsound, asserting the tool catches it.

Happy-path-only tests are how false proofs hide. The two false proofs fixed in the 2026-07 soundness
review both slipped through fixtures that claimed coverage they never adversarially exercised — see
[docs/soundness-review-2026-07.md](docs/soundness-review-2026-07.md) for the pattern to avoid.

Guard fixtures that need external tools (Z3/`opt`/`clang`) so they self-skip when absent (grep an
existing `*_tv_fixture.py` for the `command -v` pattern). Toolchain-free fixtures (like `cli_fixture`)
register unguarded.

## Adding a capability soundly

To grow the modeled vocabulary (a new instruction, guard, or matcher), the new semantics must be
validated against an oracle you did not write:

- an SMT instruction model ← validated by `lli` execution on a battery of concrete inputs
  (`o2t/validate/enrich.py`);
- a recovered fold shape ← cross-checked between the symbolic prover and exhaustive concrete
  enumeration (`reconcile`), and byte-compared against the Clang-AST front-end.

A model that any oracle disagrees with is rejected, not installed.

## Workflow

- Branch from `main`; keep each commit one logical change (`area: concise change`).
- Run the gate before submitting: `cmake -S . -B build && ctest --test-dir build`. Fixtures self-skip
  on a partial toolchain, so a meaningful subset still runs.
- In your PR, state which fixture gates the change and confirm the gate is green.

## Reporting a wrong verdict

The most valuable bug report is a **wrong verdict** — a `proved` that is actually unsound, or a
`refuted` of a transform that is actually correct. Use the *Wrong verdict* issue template and include
the exact IR/pass source, the command, the verdict you got, and why it's wrong (a witness, or the LLVM
LangRef clause). These are triaged ahead of everything else.

## License

By contributing you agree your contributions are licensed under [Apache-2.0](LICENSE).
