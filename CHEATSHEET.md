# CHEATSHEET — O2T usage

Quick, copy-pasteable commands. For *what things are* see [`SOURCES.md`](SOURCES.md);
for *how the methods work* see [`docs/README.md`](docs/README.md).

All Python tools live in `tools/cv-*.py`; most take `--report out.json` and a
`--z3-bin` / `--opt-bin` / `--clang-bin` override. Run any with `--help`.

---

## 0. Build & smoke-test

```sh
cmake -S . -B build && cmake --build build     # C++ generator/probe side (no LLVM/KLEE needed)
ctest --test-dir build --output-on-failure     # ~417 fixtures — the whole gate
scripts/check-registries.sh                      # registry gate layer (+ JSON reports)
pip install -e .                                 # optional: import o2t / compilerverif
```

Prereqs: **Z3 4.16** (required). For SCEV/Clang frontends + translation validation:
**LLVM 18** (`opt`/`clang`/`llvm-as`). Optional: KLEE 3.2, CBMC/ESBMC, cvc5, Bitwuzla.

---

## 1. Front door — start here

Hand it pass source(s) and/or pass name(s); it classifies → plans → dispatches.

```sh
tools/cv-orchestrate.py --selftest                       # verify the front door works
tools/cv-orchestrate.py --source mypass.cpp              # verify one pass source
tools/cv-orchestrate.py --source a.cpp b.cpp             # several sources
tools/cv-orchestrate.py --pass instcombine --pass dse    # by built-in pass name (oracle tier)
tools/cv-orchestrate.py --source mypass.cpp --pass dse   # source + name hint
```

Triage a whole vendor tree (classify + coverage plan, no execution):

```sh
tools/cv-orchestrate.py --source /path/to/Transforms --include Vendor --no-execute \
  --report o2t.json --summary-text o2t.txt
```

CI intake with failure budgets (exit non-zero only when asked):

```sh
tools/cv-orchestrate.py --source vendor/lib/Transforms --report o2t.json \
  --fail-on-refuted --fail-on-error --fail-on-unclassified
```

| Flag | Effect |
| --- | --- |
| `--no-execute` | classify + plan only (fast triage) |
| `--fail-on-refuted` | non-zero if a source's *primary-family* headline is refuted |
| `--fail-on-any-refuted` | stricter: any raw check refuted |
| `--fail-on-error` / `--fail-on-unclassified` / `--fail-on-advisory` / `--fail-on-no-positive` | other CI gates |
| `--llm-command CMD` | provider-agnostic LLM tie-breaker for ambiguous classification (advisory) |
| `--report f.json` / `--summary-text f.txt` | machine + human output |

Deep external audit in the same run (needs a compile DB):

```sh
tools/cv-orchestrate.py --source vendor/lib/Transforms --no-execute \
  --compile-commands vendor/build/compile_commands.json \
  --audit-out o2t-deep-audit --mine-pass-impl-ir --modelcheck-intents \
  --report o2t.json --summary-text o2t.txt --fail-on-deep-audit-error
```

Sweep the built-in multi-family manifest into a coverage matrix:

```sh
tools/cv-orchestrate-sweep.py                  # print the matrix
tools/cv-orchestrate-sweep.py --report sweep.json
```

---

## 2. Verify by transform family (call a verifier directly)

Most take `--selftest` (runs a built-in sound + unsound case) and `--report`.

```sh
# Peephole / folds — recover a model from real pass source, optionally symexec it
tools/cv-extract-pass-model.py --selftest
tools/cv-extract-pass-model.py --mine snippet.cpp --symexec
tools/cv-extract-pass-model.py --findings findings.jsonl --symexec

# Memory (DSE / store-forwarding) — theory of arrays (QF_ABV)
tools/cv-validate-memory.py --selftest             # canonical contracts
tools/cv-validate-memory.py --teeth                # drop side-conditions → expect refutation
tools/cv-mine-memory-pass.py --source dse_pass.cpp # recover + prove from the pass's OWN guards

# CFG — SimplifyCFG diamond→select if-conversion vs real opt output
tools/cv-validate-cfg.py --selftest
tools/cv-validate-cfg.py --source diamond.ll
tools/cv-validate-cfg.py --source diamond.ll --mutate   # swap select operands → expect refutation

# SCEV / loops — see cv-mine-pass-scev.py, and the loop validators in o2t/validate/*
tools/cv-mine-pass-scev.py --help
```

(Same pattern for `cv-mine-dce-pass.py` / `cv-validate-dce.py`, `cv-validate-intent-candidates.py`, etc.)

---

## 3. Translation validation — prove real `opt` output ≡ input

The miscompile finder: runs the actual pass, then proves output equals input for all inputs.

```sh
tools/cv-translation-validate.py --selftest --opt-bin "$(command -v opt)"
tools/cv-translation-validate.py --source fn.ll --passes licm
tools/cv-translation-validate.py --source fn.ll --passes licm --mutate   # inject miscompile → must REFUSE
```

`--mutate` = teeth: perturbs a phi initial value; the validator must reject it.

---

## 4. Symbolic execution of the real compiled pass

The core third-party track — run the actual C++ over symbolic IR, per-path refinement.

```sh
# KLEE-driven (finds feasible paths automatically); needs KLEE 3.2 + matching clang, else skipped
tools/cv-klee-symexec-pass.py --harness harnesses/fold_symbolic_harness.cpp --report ke.json

# Bounded model-check the fold C++ with CBMC/ESBMC; missing checker = honest skip
tools/cv-modelcheck-real-pass.py --source fold.cpp --fold my_fold --engine auto \
  --unwind 8 --timeout 120 --report mc.json
```

---

## 5. Meta-verification — trust the verdicts

```sh
tools/cv-audit-proofs.py                              # anti-vacuity + mutation-kill on every "proved"
tools/cv-cross-check.py                               # witness re-validation (z3)
tools/cv-cross-check.py --solver cvc5=/path/to/cvc5   # + second-solver agreement
```

- `cv-audit-proofs`: exit 0 iff every premise is SAT **and** every single-point mutant is killed
  (a surviving mutant = teeth gap).
- `cv-cross-check`: substitutes each refutation witness back independently, and replays proofs
  through any available second SMT solver (cvc5/cvc4 auto-detected; skipped honestly if absent).

---

## 6. Generate / replay LLVM IR test inputs (C++ MVP)

```sh
build/cv-replay --seed 42 --out examples/seed42.ll        # deterministic IR from a seed
build/cv-replay --config examples/add_zero.cfg --out /tmp/add_zero.ll
```

Regenerate the `include/o2t/Generated*.h` headers from `constraints/*.json`:

```sh
tools/cv-generate-idiom-header.py       # (and the other cv-generate-*.py generators)
```

---

## 7. Verdict vocabulary

`proved` / `sound` / `validated` — checks passed for all inputs ·
`refuted` — an unsound transform caught, with a concrete **witness** ·
`partial` / `inconclusive` — incomplete coverage ·
`unsupported` / `advisory` — known gap, reported not hidden ·
`planned` / `skipped` — plan-only or prerequisite missing (never a silent pass).

## Handy shell helpers (`scripts/`)

```sh
scripts/llvm-shell.sh        scripts/klee-shell.sh        scripts/clang-tooling-shell.sh
scripts/replay-with-opt.sh   scripts/opt-check-cases.sh   scripts/validate-ir.sh
```
