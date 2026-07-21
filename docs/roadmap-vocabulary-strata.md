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

### Stratum A — literal-mask KnownBits (LOW risk) — **LANDED (string path)**
Widen the *reconstructor*, not the model. Done (`pass_graph_inline_mask_fixture`): `fact_to_assumptions`
now recognizes the inline mask-test forms and emits the `known-bits` assumption the SMT layer already
discharges:
- `(X & C) == 0` (literal `C`) → `{known-bits, name: X, zero_mask: C}`.
- `(X & C) == C` (literal `C`) → `{known-bits, name: X, one_mask: C}` — the one-mask direction the SMT
  supported but the reconstructor never emitted.
- `(X & Y) == 0` (both SSA) → the relational `mask-pair` (inline `haveNoCommonBitsSet`).
- `(X & C) == D`, `D ∉ {0, C}` → declines (not a clean known-bits fact).

The concrete cross-check engine (`_assumption_holds`) gained the matching `known-bits` filter, so the
two engines do not drift — `reconcile` agrees on these folds instead of falsely refuting. Proven
load-bearing (`or(X,8)→xor(X,8)` under `(X&8)==0` proves, refutes unguarded), and a contradictory
known-both-ways guard is rejected at formal-IR construction (`known-bits facts conflict`), never proved
vacuously.

**Still open in Stratum A:** (a) wire the inline `(A & B) == C` form through the *Clang-AST* guard
reconstructor (currently call-only, so the AST front-end still declines inline-mask guards — the
string path is unaffected and gains the capability now); (b) the common *real-source* idiom is
`MaskedValueIsZero(X, <mask-expr>)` / `computeKnownBits` where the mask is an **APInt expression**, not
a bare literal — reconstructing that needs Stratum B's literal APInt evaluator to fold the mask first.
Soundness obligation (held): the mask must be a **literal or a compile-time-constant O2T can fold**; a
computed/SSA mask stays a `mask-pair` or declines. No new SMT.

### Stratum B — APInt literal constant-folding in rewrites — **RE-SCOPED by a finding**
Original plan: a closed evaluator that folds `ConstantInt::get(Ty, APInt::getSignMask(BW))` and friends
to a `{kind:int}` node. **An exploratory implementation revealed this is largely blocked by O2T's own
width-uniformity cross-check, and correctly so.** `reconcile` re-proves the obligation at a small
concrete width (8 bits) and demands the verdict be width-uniform. A *width-parametric* constant like
`getSignMask(32) = 0x80000000` baked as a fixed 32-bit literal truncates to `0x00` at width 8 — it is
NOT the width-8 sign mask — so the two engines disagree and `reconcile` flags the fold untrusted. That
is the cross-check doing its job: a fixed literal cannot represent a value whose meaning depends on the
bitwidth.

Consequences for the slice:
- **Truncation-robust** constants (`getAllOnes`/`getMaxValue` → all-ones, `getMinValue`/`getZero` → 0,
  and small literal arithmetic that fits every reconcile width) fold safely — but these are already
  expressible (`getAllOnesValue`, `getNullValue`) or rare, so the net new reach is negligible.
- **Width-parametric** constants (`getSignMask`, `getSignedMinValue`, `getSignedMaxValue`) — the
  actually-useful case, since sign-bit manipulation is common in InstCombine — need a **width-parametric
  formal-IR constant node** (e.g. a `signmask` op evaluating to `1 << (w-1)` at each engine's width),
  NOT a fixed int. That is a cross-cutting change: ~10 op-dispatch sites across 6 soundness-critical
  files (`formal_ir`, `mini_alive` ×2, `symexec/modelcheck_intents`, `intent/infer`, `pass_graph` ×5)
  must all learn the node, or they drift. Medium risk, deliberate — not a reflexive increment.

So Stratum B is **re-scoped**: the fixed-literal folding is dropped (it fails the cross-check for the
interesting cases and adds nothing for the safe ones); the real slice is the width-parametric `signmask`
node, tracked here as a deliberate future change. Any non-literal (symbolic/matched) operand always
declines regardless.

### Stratum C — computed KnownBits (HIGH risk; stays a principled DECLINE)
`computeKnownBits(X)` with a runtime-computed mask is **out of scope by design**: O2T cannot pin the
mask, so assuming any specific known bits would be unsound and assuming none proves nothing. This
must remain an explicit decline (documented), not a silent gap. Revisit only behind a *proof* that
the recovered mask is a sound under-approximation of the true known bits — a research problem, not an
increment.

### Stratum D — structural helpers (`decomposeBitTestICmp`, …) (separate track)
These return a small struct the fold destructures; recovering them is a *shape* problem (a new
contract), not a vocabulary one, and belongs with the recovery ladder, not here.

## First slice — done, and what it taught us

Stratum A (inline literal-mask reconstruction) is **landed** for the string path, gated by
`pass_graph_inline_mask_fixture` (z3-guarded, CMake-registered) with the full teeth set:
reconstructor cases, load-bearing zero- and one-mask folds, and a contradictory guard rejected at
construction. One design note earned in the doing: the fact was already discharged by the SMT layer,
but the **concrete cross-check engine had to gain the same `known-bits` filter** or the two engines
drift and `reconcile` falsely refutes — a reminder that every new fact touches *both* engines, not
just the SMT.

The honest reach delta: this widens the **string** front-end's guard vocabulary now; it does not yet
lift the 3/3 verbatim count, because (i) the AST front-end still needs the inline-guard wiring, and
(ii) real upstream folds write the mask as an APInt expression or a `computeKnownBits` result, which
needs Stratum B or stays a Stratum C decline.

A Stratum B exploration then produced a second, sharper finding (see below): folding width-parametric
constants (`getSignMask`, …) to fixed literals is **rejected by the width-uniformity cross-check**, and
correctly so. The genuinely-useful next slice is therefore a **width-parametric `signmask` formal-IR
node** — a deliberate cross-cutting change, not a reflexive one — rather than the fixed-literal
evaluator originally sketched.

## Non-goals (stated, so silence is not mistaken for coverage)

- No symbolic APInt (only literal folding).
- No computed KnownBits (Stratum C stays a decline).
- No new SMT theory — everything lands in the existing bv32 model or declines.
- Verbatim reach is expected to rise **incrementally**; this is not a path to "most of InstCombine."
