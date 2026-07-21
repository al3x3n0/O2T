# Roadmap: vocabulary strata (lifting verbatim reach past 3/3)

**Status:** design, not yet implemented. Scopes the one remaining capability frontier for the Pass
IR / Clang-AST recovery track after **shape parity is complete** (see [maturity.md](maturity.md)
roadmap #1). The AST front-end now recovers every *shape* the regex path does; what bounds verbatim
reach is no longer the parser but the **model's fact and value vocabulary**. This document names the
real gaps (grounded in the code, not invented), orders them by soundness risk, and recommends a
bounded first slice with its fixture plan.

## The wall is the model, not the parser

The regex path already proves 12 upstream fold arms (E6); the AST path reaches 3 verbatim. Neither
number is parser-limited. The 22 remaining two-icmp functions (and most of visitAnd/Or/Xor) decline
because their legality rests on facts O2T's model cannot *pin to a concrete value*, or their rewrites
compute constants O2T cannot *evaluate*. Concretely:

- **`computeKnownBits(X)` with a computed mask.** The fold branches on a KnownBits result whose
  known-zero/known-one masks are computed at pass runtime, not written as a literal in the guard.
- **APInt constant arithmetic in the rewrite.** `ConstantInt::get(Ty, C1.lshr(C2))`,
  `getSignMask()`, `C.countTrailingZeros()` — a constant the rewrite *derives*, not a literal.
- **Structural analysis helpers** (`decomposeBitTestICmp`, `getInverseMinMaxPred`) that return a
  small struct the fold then consumes.

## What the model ALREADY supports (do not rebuild)

`o2t/facts/value_tracking.py` already encodes, as SMT over the bv32 value, every fact below; the
prover (`mini_alive`) and symexec both consume them, and the abduction/anti-vacuity/teeth machinery
already covers them:

| Assumption `op` | Predicate | SMT |
|---|---|---|
| `power-of-two` (+`or_zero`) | `isKnownToBeAPowerOfTwo(X)` | `X≠0 ∧ (X & (X-1))=0` |
| `known-bits` (`zero_mask`,`one_mask`) | (mask facts) | `(X & Z)=0 ∧ (X & O)=O` |
| `cmp` (sge/sgt/slt/eq/ne), `not-eq` | `isKnownNonNegative/Positive/Negative/NonZero` | `X ⋈ 0` |
| `mask-pair` | `haveNoCommonBitsSet(X,Y)` / `MaskedValueIsZero(X,Y)` SSA | `(X & Y)=0` |

Crucially, `scalar_assumption_smt`'s `known-bits` case **already** handles both a `zero_mask` and a
`one_mask`. The gap is that `fact_to_assumptions` only ever *produces* a `known-bits` with a
`zero_mask`, and only from a **literal-mask** `MaskedValueIsZero(X, C)`. The SMT layer is ahead of
the reconstructor.

## The strata, ordered by soundness risk

Each stratum is gated by the invariant that has held all along: **an assumption's SMT must be a
sound over-approximation of the predicate** (never assume more than the predicate guarantees), and
**anything O2T cannot pin to a concrete value DECLINES** — a sound bound, never a mis-model.

### Stratum A — literal-mask KnownBits (LOW risk; recommended first slice)
Widen the *reconstructor*, not the model. Recognize the two remaining literal-mask idioms and emit
the `known-bits` assumption the SMT layer already discharges:
- `MaskedValueIsZero(X, C)` with literal `C` → `{known-bits, name: X, zero_mask: C}` *(already
  done; extend the parse to the `(X & C) == 0` guard written inline, not only the helper call).*
- `(X & C) == C` inline, or `MaskedValueIsZero(~X, C)` → `{known-bits, name: X, one_mask: C}`
  *(new: the one-mask direction the SMT already supports but the reconstructor never emits).*

Soundness obligation: the mask must be a **literal or a compile-time-constant expression O2T can
fold** (Stratum C); a computed/SSA mask stays a `mask-pair` (relational) or declines. No new SMT.

### Stratum B — APInt literal constant-folding in rewrites (LOW–MEDIUM risk)
A small, closed evaluator for APInt/ConstantInt methods **when every operand is a literal**:
`lshr`, `shl`, `ashr`, `and`, `or`, `xor`, `add`, `sub`, `getSignMask`, `getAllOnes`,
`countTrailingZeros`, `countLeadingZeros`. Emits a `{kind:int, value:…}` rewrite node, matching bv32
wrapping/signedness exactly. This unlocks rewrites that *derive* a constant. Any non-literal operand
declines (no symbolic APInt). The teeth already in place (a mutated constant refutes) validate it.

### Stratum C — computed KnownBits (HIGH risk; stays a principled DECLINE)
`computeKnownBits(X)` with a runtime-computed mask is **out of scope by design**: O2T cannot pin the
mask, so assuming any specific known bits would be unsound and assuming none proves nothing. This
must remain an explicit decline (documented), not a silent gap. Revisit only behind a *proof* that
the recovered mask is a sound under-approximation of the true known bits — a research problem, not an
increment.

### Stratum D — structural helpers (`decomposeBitTestICmp`, …) (separate track)
These return a small struct the fold destructures; recovering them is a *shape* problem (a new
contract), not a vocabulary one, and belongs with the recovery ladder, not here.

## Recommended first slice + fixture plan

**Do Stratum A (the one-mask direction) first**: it is a pure reconstructor widening over an SMT
encoding that already exists, it unlocks a real masked-bit-test fold class, and its soundness
obligation is local. Mirror the existing guard fixtures (`pass_graph_fixture`'s load-bearing-guard
pattern):

1. A fold that **proves** under `{known-bits, one_mask: C}` (e.g. a rewrite valid only when the low
   bits of `X` are known set).
2. The same fold **refutes with a witness** when the guard is dropped (the fact is load-bearing).
3. It is **caught vacuous** on a contradictory instantiation (anti-vacuity still bites).
4. A byte-identical **cross-front-end** check: the AST path reconstructs the same `known-bits`
   assumption as the regex path (`clang_tree_source_fixture`-style), so the new vocabulary is proven
   on *both* front-ends at once.

Register the fixture in CMake (z3-gated), as every fact fixture is. Measure the delta in verbatim
reach honestly — Stratum A likely lifts 3/3 by a small, countable number, not a landslide; the
majority wall is Stratum C, which stays a decline.

## Non-goals (stated, so silence is not mistaken for coverage)

- No symbolic APInt (only literal folding).
- No computed KnownBits (Stratum C stays a decline).
- No new SMT theory — everything lands in the existing bv32 model or declines.
- Verbatim reach is expected to rise **incrementally**; this is not a path to "most of InstCombine."
