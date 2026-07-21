# E4 — frontend robustness (measured)

Experiment **E4** from the [paper outline](arxiv-outline.md) (§10): the SCEV frontend recovers
loops the line-regex frontend cannot. O2T has two loop-recurrence frontends — a legacy REGEX one
(`llvm_loop.recurrences`) that chases phi chains line by line, and a SCEV one
(`scev_loop.scev_recurrences`) that asks LLVM's own scalar-evolution analysis. Runner:
`tools/cv-frontend-robustness.py` (`o2t/frontend/robustness.py`); gated by
`frontend_robustness_fixture`.

**Run:** 2026-07-21, LLVM 18.1.8 `opt`. **Headline: on the rotated/LCSSA shape SCEV strictly
dominates — regex 0/4, SCEV 4/4.**

## The differential

The rotated/multi-block/LCSSA benchmark is the shape `clang -O1` actually emits: the recurrence is
split across a guard block, a body, and a latch, and the live-out is an LCSSA phi in the exit block
rather than the loop phi. The line-regex frontend's phi-chasing assumptions do not hold there.

| benchmark | regex recovers | SCEV recovers |
| --- | ---: | ---: |
| rotated / multi-block / LCSSA (4 loops) | **0 / 4** | **4 / 4** |
| simple single-block control (4 loops) | 4 / 4 | 3 / 4 |

On the rotated shape SCEV recovers all four loops the regex frontend fails on
(`rotSR_before/after`, `wrongStride_before/after`) — strict domination.

## The control (why this is a shape property, not a broken parser)

On the simple single-block benchmark the regex frontend recovers **all four** loops. Its rotated
failures are therefore a property of loop *shape* — block layout, LCSSA — not of a parser that
never works. (SCEV recovers three of the four simple loops; the fourth, `sum_squares`, has a
nonlinear delta SCEV expresses in a form the extractor here declines — an honest coverage
asymmetry, reported: the two frontends are complementary on simple loops, but SCEV strictly wins
on the rotated shape that dominates real output.)

## Why it matters

Every closed-loop translation-validation result (E1) rests on recovering the recurrences of real
`opt` output — which is rotated. A regex frontend would silently fail to extract those loops and
report nothing to validate; the SCEV frontend is what makes E1 measurable at all.

## Reproducing

```sh
tools/cv-frontend-robustness.py --opt-bin "$(command -v opt)" --report e4.json
python3 tests/fixtures/frontend_robustness_fixture.py     # needs opt 18
```
