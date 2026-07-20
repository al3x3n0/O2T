# E3 — performance (measured)

Experiment **E3** from the [paper outline](arxiv-outline.md) (§10): the paper's speed claims,
measured. Runner: `tools/cv-prove-timing.py` (`o2t/prove/timing.py`); mechanics gated by
`prove_timing_fixture` (which asserts orderings and generous bounds — never machine numbers).

**Run:** 2026-07-20, Apple Silicon macOS, z3 4.16, 10 s bv32 cap.

## 1. The integer-ring discharge vs bit-blasting (the headline)

The nonlinear Faulhaber STEP implication for `acc += i·i`
(`6·acc = 2i³−3i²+i ⟹ 6·(acc+i²) = 2(i+1)³−3(i+1)²+(i+1)`):

| encoding | verdict | time |
| --- | --- | ---: |
| `Int` (`set-logic ALL`) | `unsat` (theorem) | **0.105 s** |
| `QF_BV` bv32 (bit-blasted) | **timeout** | ≥ 10 s (cap) |

The bv32 non-return is a result, reported at the cap — consistent with the design claim that
nonlinear bitvector multiplication does not terminate in practice, now measured rather than
asserted. (The design doc's historical "~0.02 s" figure was another machine; the paper draft
cites this measured run.)

## 2. Batched synthesis discharge

24 synthesis-shaped candidates (coefficient guesses for `(i+1)² = i² + k·i + 1`, exactly one
valid), identical verdicts candidate-by-candidate:

| discharge path | time | |
| --- | ---: | --- |
| one z3 process per candidate | 0.450 s | |
| one process, push/pop (`batch_check`) | **0.023 s** | **19.5×** |

## 3. Per-obligation prove times (the peephole side)

| recovered fold family | verdict | time |
| --- | --- | ---: |
| nested identity `(X+0)·1 → X` | proved | 0.012 s |
| guarded `sdiv → udiv` (two facts) | proved | 0.083 s |
| builder DFG `sub` rebuild | proved | 0.015 s |
| relational disjoint `add → or` | proved | 0.030 s |

Tens of milliseconds per obligation — the "cheap enough for a validation workflow" claim, measured.

## Reproducing

```sh
tools/cv-prove-timing.py --report e3.json          # full 10s bv32 cap
python3 tests/fixtures/prove_timing_fixture.py     # CI-fast (4s cap), robust assertions only
```
