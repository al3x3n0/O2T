# E7 — the recovery-soundness ablation (measured)

Experiment **E7** from the [paper outline](arxiv-outline.md) (§10): the mutation study applied to
the **recovery**, not the target program. Misrecovery classes are seeded into known-good recovered
obligations — corrupting them exactly the way a recovery bug would — and each corruption is run
through the C7 cross-check stack. Runner: `tools/cv-passir-ablation.py`
(`o2t/intent/ablation.py`); gated by `passir_ablation_fixture`.

**Run:** 2026-07-20, z3 4.16 + bitwuzla. **Headline: zero escapes** — every applicable
(class × fold) corruption is caught by at least one layer.

## The matrix

Seed folds: a nested identity, a precondition-guarded `sdiv→udiv`, a builder-DFG rewrite, and the
disjointness-guarded `add→or`. Catchers per corruption class:

| misrecovery class | typical catchers | notes |
| --- | --- | --- |
| dropped operator (`after` loses its root op) | prove-teeth **+** reconcile **+** second-solver **+** width | 3–4-layer redundancy |
| mislowered builder (root op swapped) | prove-teeth + reconcile + second-solver + width | see the disjoint-or subtlety below |
| weakened guard (assumption dropped) | prove-teeth + second-solver (+ reconcile where covered) | the missing premise refutes |
| swapped operands (non-commutative before) | prove-teeth + reconcile + second-solver + width | commutative roots are honestly `not-applicable` |
| **width-specific constant** | **width-corroboration ONLY** | proves at bv32 under every other check — this layer is uniquely load-bearing |
| **skipped predicate case** (one member consumed, no split) | **all-cases discipline ONLY** | the eq member proves; the skipped ne member refutes |
| contradictory premise | premise-SAT gate (never `proved`) | the vacuous-proof trap stays closed |

Two structural findings:

1. **Redundancy is real but not uniform.** The typical value-level corruption is caught by three
   or four *independent* layers (prover, exhaustive concrete enumeration, a second SMT solver,
   width re-proof). But two classes each evade everything except one specific layer — the width
   corroboration and the all-cases discipline are not belt-and-suspenders, they are the only belt
   for their failure mode.
2. **Some misrecoveries are semantically invisible — and that is honest, not a gap.** Mislowering
   `or → add` under the disjointness premise is value-equal (disjoint operands add without
   carry), so the value layers rightly do not refute it; the obligation still gets flagged
   width-conservatively. A "wrong" recovery that denotes the same function is not a soundness
   event; the fixture pins this as a conscious fact.

## The six field specimens

The seeded matrix is complemented by the recovery bugs the discipline caught **in practice**
while the phases were built — each found by the machinery, none by luck:

| # | bug (phase found) | what would have happened | caught by |
| --- | --- | --- | --- |
| 1 | operand-subject misattribution — `simplifyOrLogic(X, Y)` matching an operand read as the fold's `before` (36) | a **false refutation** of upstream LLVM | the first E6 corpus run: a refuted verdict on upstream triggered investigation |
| 2 | the `I.`-prefix gate hole — `match(I.getOperand(0), …)` impersonating an instruction-subject match (38) | the same misattribution class, silently through the gate | composition wiring: the fixture-driven routing test exposed the subject regex |
| 3 | return-type token as first operand param — `Value *simplifySubInst` parsed as a param (37) | every contract fold mis-bound | smoke test: zero recoveries where arms were expected |
| 4 | `getType()` normalization clobbering the cast folds' type-equality guard (37) | cast folds silently declined | **the gated fixture suite** — `pass_graph_fixture` failed within minutes |
| 5 | nullptr-sentinel substitution — `m_Value(X)` with `X := nullptr` creating a false shared variable (37) | cross-arm false unification | reading real `simplifySubInst` during design (the corpus as design input) |
| 6 | the inverted-guard premise — negated facts (`!isKnownNonNegative(X)`) binding their POSITIVE premise via substring match (39, predates the ladder) | a direct **false-proof** vector | adversarial widening of `_bail_atoms` + a direct fact-vocabulary probe |

The meta-observation the paper should make: specimens 1–6 were caught by *different* mechanisms —
a corpus anomaly, a fixture gate, a smoke test, design reading, adversarial review — which is the
practical argument for running all of them, not the cheapest one.

## Reproducing

```sh
tools/cv-passir-ablation.py --report e7.json     # exits non-zero on any escape
python3 tests/fixtures/passir_ablation_fixture.py
```
