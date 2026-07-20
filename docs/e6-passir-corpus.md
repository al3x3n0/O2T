# E6 — Pass-IR corpus coverage over upstream LLVM (measured)

First measured run of experiment **E6** from the [paper outline](arxiv-outline.md) (§10): the
structural Pass-IR recovery (`o2t/intent/pass_graph.py`) executed over unmodified upstream LLVM
sources by the corpus runner (`tools/cv-passir-corpus.py`, mechanics gated by
`passir_corpus_fixture`).

**Corpus:** LLVM `release/18.x`, 9 files, 38,267 lines — the eight scalar-side
`InstCombine*.cpp` (`AddSub`, `AndOrXor`, `MulDivRem`, `Shifts`, `Select`, `Compares`, `Casts`,
`InstructionCombining`) and `Analysis/InstructionSimplify.cpp`.

**Run:** 2026-07-20, `cv-passir-corpus.py /tmp/e6-corpus` (z3 4.16). Wall clock ≈ 1.2 s.

## Result

| metric | value |
| --- | --- |
| candidate fold functions extracted (returning `Value*`/`Instruction*`) | **441** |
| recovered (any rung) | **0** |
| **false proofs** | **0** |
| declined | 441 (100%) |

Decline taxonomy (the coverage frontier, by heuristic bucket):

| bucket | n | % | reading |
| --- | ---: | ---: | --- |
| `no-riuw-rewrite` — has matchers, but the rewrite is not `replaceInstUsesWith` | 212 | 48% | **the fragment's rewrite ANCHOR is the binding constraint** (see below) |
| `no-match-call` — no `PatternMatch` inspection at all | 165 | 37% | helpers/utilities; out of scope by design |
| `in-fragment-shape` — match + `replaceInstUsesWith`, still declined | 55 | 12% | large multi-fold visit methods (median 141 lines, none ≤ 30): need per-fold slicing, not just guard modeling |
| `loop-over-ir` | 9 | 2% | beyond the bounded operand-loop rungs |

## Interpretation (honest)

- **Zero recovery on unmodified upstream code is the expected, stated result** of the
  declines-by-default fragment design — and the run's point was the invariant that held: **zero
  false proofs** across 441 real-world candidates. Nothing outside the modeled fragment was
  silently mis-modeled.
- The synthetic-corpus fixture (`passir_corpus_fixture`) demonstrates the same runner produces
  `recovered-proved` (reconcile-cross-checked), `recovered-refuted` (with witness), and every
  decline bucket when the source *is* in fragment — so the 0 is a fragment-scope measurement, not
  a tooling failure.

## The measured frontier (what a next phase buys)

Within the 212 `no-riuw-rewrite` declines:

| sub-population | n | share of all 441 |
| --- | ---: | ---: |
| has `match(` **and** returns a `Builder.Create*` / `BinaryOperator::Create*` / `SelectInst::Create` / `CastInst::Create` / `ConstantInt::get` rewrite | **103** | 23% |
| has `match(` and returns a bound value (`return X;`) | 47 | 11% |

By file, the 103 return-form targets: AndOrXor 32, InstructionSimplify 25, Select 13, AddSub 10,
InstructionCombining 7, MulDivRem 5, Casts 4, Shifts 4, Compares 3.

**Phase-36 candidate:** a *return-form rewrite anchor* — treating `return <builder-expr>;` /
`return X;` in a `Value*`-returning fold helper as the rewrite (upstream's dominant idiom; the
InstCombine contract is "return the replacement value") — would convert ~150/441 (34%) of
candidates from anchor-declines into recovery *attempts*, each still subject to the full
guard/lowering fragment and the C7 cross-check stack. The 55 `in-fragment-shape` functions need
the orthogonal capability: slicing multi-fold visit methods into per-fold obligations.

## Reproducing

```sh
# fetch the corpus (or point at a local llvm-project checkout):
for f in InstCombineAddSub InstCombineAndOrXor InstCombineMulDivRem InstCombineShifts \
         InstCombineSelect InstCombineCompares InstCombineCasts InstructionCombining; do
  curl -sO https://raw.githubusercontent.com/llvm/llvm-project/release/18.x/llvm/lib/Transforms/InstCombine/$f.cpp
done
curl -sO https://raw.githubusercontent.com/llvm/llvm-project/release/18.x/llvm/lib/Analysis/InstructionSimplify.cpp
tools/cv-passir-corpus.py . --report e6.json --summary-text e6.txt
```

The corpus mechanics (extraction, taxonomy, reconcile-gated `proved`, oversize accounting) are
gated by `passir_corpus_fixture` in the ctest suite.
