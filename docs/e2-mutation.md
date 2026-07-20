# E2 — mutation catch-rate (measured)

Experiment **E2** from the [paper outline](arxiv-outline.md) (§10): a proof is only as strong as
what its *refutation* catches, so O2T seeds single-point corruptions and measures the catch rate.
The teeth exist in three independent tiers, each already gated point-wise; this is the aggregate.
Runner: `tools/cv-mutation-catchrate.py` (`o2t/meta/mutation_catchrate.py`); gated by
`mutation_catchrate_fixture`.

**Run:** 2026-07-20, z3 4.16. **Headline: 52 / 52 seeded corruptions caught, zero survivors.**

## The three tiers

### Deep contracts — 34 / 34 mutants killed (each with a witness), 0 survivors, premises SAT

Single-point corruptions of the family SMT models — swap a vector lane, drop a legality guard,
flip a condition, replace an op with a non-associative one, expose a dead initializer — each of
which must be refuted with a concrete witness:

| family | killed / mutants | contracts |
| --- | ---: | ---: |
| vectorize-slp | 9 / 9 | 5 |
| memory-dse | 6 / 6 | 4 |
| cleanup-dce | 8 / 8 | 3 |
| global | 4 / 4 | 2 |
| loop-structural | 3 / 3 | 3 |
| cfg | 2 / 2 | 1 |
| memory-dse-byte | 2 / 2 | 2 |

Every audited contract also passed the anti-vacuity check (its premises are jointly satisfiable —
no contradictory guard makes the proof vacuously true).

### Recovery side — 7 / 7 misrecovery classes caught, 0 escapes

The E7 ablation, folded in: dropped operator, mislowered builder, weakened guard, swapped
operands, width-specific constant, skipped predicate case, contradictory premise (see
[e7-ablation.md](e7-ablation.md) for the class × layer matrix).

### Registry intents — 11 / 11 perturbed intents rejected

Each sound scalar optimization intent is perturbed (`after + 1`) and must then be rejected; a
perturbed intent that still "proves" would expose a vacuous prover. The 7 hand-authored negative
intents are also all rejected.

## Scope (stated, not overclaimed)

E2's outline also asks for **witness minimality** (minimal trip count, `|params|`). That is a
property of the loop-track *CEGAR* witnesses — the forward-execution search that minimizes a
counterexample over trip count and parameters — not of these point-mutation refutations, whose
witnesses are single input assignments. Minimality is therefore measured with the loop fixtures
(E1/E5 territory), and is deliberately not claimed here.

## Reproducing

```sh
tools/cv-mutation-catchrate.py --report e2.json    # exits non-zero on any survivor
python3 tests/fixtures/mutation_catchrate_fixture.py
```
