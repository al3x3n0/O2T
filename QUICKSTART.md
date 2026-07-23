# Quickstart

O2T verifies LLVM optimization passes **from their source** — it recovers what a pass intends and
proves it sound with an SMT solver, and it translation-validates the real `opt`'s output. This gets
you from a clone to a first proof in a few minutes.

## 1. Install

```bash
git clone <this-repo> O2T && cd O2T
pip install -e .          # puts the `o2t` command on your PATH
```

The Python core has no third-party runtime dependencies; the provers are external tools (below).

## 2. Check your toolchain

```bash
o2t doctor
```

`doctor` tells you exactly what's present and what each gap disables:

- **Z3** — *required*. Every proof needs it. `brew install z3`, `apt install z3`, or `pip install z3-solver`.
- **LLVM 18** `opt` / `lli` / `clang` — needed for translation validation, the execution oracle, and
  the Clang-AST front-end. `brew install llvm@18`, or [apt.llvm.org](https://apt.llvm.org/).
- **Bitwuzla / KLEE** — optional (a second solver; symbolic IR generation).

O2T resolves each tool from `$O2T_OPT` / `$O2T_LLI` / `$O2T_CLANG` / `$O2T_Z3` first, then your PATH,
then the macOS `llvm@18` keg. On Linux, point those env vars at your LLVM 18 binaries and `doctor`
will confirm. With just Z3 you can already run source-recovery proofs; add LLVM 18 for the full stack.

## 3. Your first proof

```bash
o2t verify --selftest
```

This runs a self-contained batch: it recovers a handful of InstCombine folds, proves the sound ones,
and flags a deliberately-planted unsound one — every `verified` rests on both an SMT proof *and*
cross-validation against the real `opt`.

## 4. Verify your own pass

```bash
# a single fold, from a source snippet:
o2t verify --mine path/to/fold.cpp

# a whole vendor pass tree (classify -> plan -> verify -> per-pass report):
o2t orchestrate --source path/to/passes/ --report report.json

# add LLM-driven triage of whatever the deterministic pass leaves open:
o2t agent --source path/to/passes/ --llm-command 'your-model --json'
```

Formal verifiers decide every verdict; the LLM (in `agent`) only routes and proposes — it can never
make something count as proved.

## 5. Where to go next

- **[docs/capabilities.md](docs/capabilities.md)** — what O2T verifies today vs. what it declines
  (read this before pointing it at your own pass, so the results make sense).
- **[o2t list](.)** — `o2t list` enumerates the ~160 underlying `cv-*` tools; `o2t run <name> -h`
  for any one of them.
- **[CHEATSHEET.md](CHEATSHEET.md)** — the full command surface.
- **[docs/paper-draft.md](docs/paper-draft.md)** — the design and the soundness argument.

## Running the test gate

```bash
cmake -S . -B build && ctest --test-dir build
```

Fixtures self-skip when a tool they need is absent, so a partial toolchain still gives a green,
meaningful subset. The `cli_fixture` (this command surface) needs no toolchain at all.
