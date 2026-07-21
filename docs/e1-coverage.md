# E1 — closed-loop translation-validation coverage (measured)

Experiment **E1** from the [paper outline](arxiv-outline.md) (§10): the headline soundness
experiment. For every (real `opt` pass × benchmark loop) cell, the actual `opt -passes=<X>` is run
and its output proved equivalent to the input for all trip counts (SCEV frontend + relational
prover). Runner: `tools/cv-tv-matrix.py` (`o2t/frontend/tv_matrix.py`); gated by
`tv_matrix_fixture`.

**Run:** 2026-07-21, LLVM 18.1.8 `opt`, z3 4.16. **Headline: zero false refutations on sound
passes.**

## The matrix — 5 passes × 7 loops = 35 cells

| pass | proved | proved-closed-form | loop-eliminated | refuted |
| --- | ---: | ---: | ---: | ---: |
| licm | 5 | — | 2 | 0 |
| loop-rotate | 5 | — | 2 | 0 |
| simple-loop-unswitch | 5 | — | 2 | 0 |
| loop-instsimplify | 5 | — | 2 | 0 |
| indvars | — | 6 | 1 | 0 |
| **total** | **20** | **6** | **9** | **0** |

**26 of 35 cells are positive verdicts** (`proved` for loop→loop transforms, `proved-closed-form`
for indvars' loop→closed-form rewrites, each proved over the SCEV recurrences of both sides). The
9 `loop-eliminated` cells are reported honestly: the accumulator was deleted into a shape the
closed-form validator does not yet cover — a real transform surfaced as a coverage gap, never
silently passed. **Zero cells are `output-not-preserved`** — no correct LLVM pass is falsely
accused.

## Teeth — a real miscompile is caught

The invariant above ("no false alarm") is meaningless without the dual ("real alarms fire"). With
`--mutate`, one phi initial value in `opt`'s output is corrupted — simulating a transform that
miscompiled the recurrence — and the validator **refutes it with a concrete witness** (e.g.
`sumProduct`, `sumConst`, `shiftLeft` all flip to `output-not-preserved`). The proof that E1's
zero-refutation result is a property of *sound passes*, not of a validator that never refutes.

## Scope

The benchmark is seven recurrence-shaped loop kernels (products, affine and shift-multiplier
deltas, closed-form-reducible sums). The passes are the loop transforms whose output the relational
prover covers (loop→loop) or the closed-form validator handles (indvars). Non-recurrence loop
effects, loop-nest transforms, and vectorization are out of scope (§11 of the draft). Expanding the
benchmark to LLVM's own loop test suite is the natural next enlargement.

## Reproducing

```sh
tools/cv-tv-matrix.py --opt-bin "$(command -v opt)" --report e1.json
python3 tests/fixtures/tv_matrix_fixture.py     # needs z3 + opt 18
```
