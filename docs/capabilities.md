# What O2T can verify today

O2T is a **decline-by-default** verifier: it proves what it can model and *explicitly declines*
everything else — it never silently approximates, so a verdict of `proved` is trustworthy and a
verdict of `unsupported` is honest. This page is the map of that boundary, so results on your own
pass make sense. Read it as "what to expect," not a promise.

## Two tracks (plus a loop track)

- **Track A — recover from source.** Reads a peephole pass's C++ (`PatternMatch` matchers, `IRBuilder`
  rewrites, guards, control flow) into an SMT obligation and proves it. *Narrow but explanatory* — it
  names the fold and its precondition.
- **Track B — validate the real transform.** Runs the actual `opt` and proves its whole-function
  output a sound refinement of the input (Alive2-style). *Broad but behavioral.*
- **Loop track — all trip counts.** Lifts loop transforms into recurrences and proves them for *every*
  trip count (integer-ring discharge, invariant synthesis with k-induction, relational simulation).

The two peephole tracks meet at **attribution**: a proved whole-function transform is credited to the
recovered fold that provably matches it.

## Capability map

| Area | Status | Notes |
| --- | --- | --- |
| Scalar integer folds (`add/sub/mul/and/or/xor/shifts/icmp/select`, casts) | ✅ verifies | poison/UB-aware refinement — a fold that adds an unjustified `nsw/nuw/exact/disjoint` is refuted |
| Straight-line + acyclic branch/φ control flow | ✅ verifies (Track B) | symbolic CFG execution |
| Memory: `mem2reg`/`sroa`, DSE, load-forwarding, gep/type-punning | ✅ verifies (Track B) | byte-addressable theory of arrays, exact aliasing; a target that introduces a new dereference declines (null-deref UB unmodeled) |
| Fixed & scalable vectors (element-wise, shuffle/extract/insert) | ✅ verifies (Track B) | per-lane model |
| Interprocedural: inlining, IPSCCP-style, argument promotion, `deadargelim`, `globaldce` | ✅ verifies (composition) | pipeline / module / signature / promotion via refinement transitivity |
| Counted loops → closed form (`indvars`), strength reduction, LICM, SLP | ✅ verifies (loop track) | all trip counts |
| Guards needing `KnownBits` / `APInt` / `ConstantRange` reasoning | ⚠️ mostly declines | a scoped vocabulary stratum; the guard is dropped→decline unless modeled (never mis-applied) |
| Floating point (beyond `nnan`/`ninf`) | ⚠️ declines | `nsz`/`reassoc`/`contract` involve value-nondeterminism — out of scope |
| Width-changing loop recurrences; loop-nest transforms; loop vectorization | ⚠️ declines | stated loop-track boundary |
| In-place-mutation folds; dynamic-opcode folds; worklist fixpoints | ⚠️ declines | recovery-fragment boundary |
| Full undefined-behavior accounting (pointer validity, `undef` distinct from poison) | ⚠️ partial | single poison bit; UB modeled for div/rem and flags, not pointer validity |

✅ = a real proof or refutation with a witness. ⚠️ = an explicit `unsupported` decline (a sound
non-answer), **never** a false `proved`.

## Measured reach (on LLVM's own tests — treat as indicative, not a guarantee)

- **Track B whole-function TV:** **351 / 715 (49%)** of LLVM 18's `and/or/xor/add.ll` InstCombine test
  functions proved sound end-to-end, **0 false refutations**; the rest decline on shapes above.
- **Track A verbatim recovery:** ~a dozen upstream fold arms proved directly from unmodified
  InstCombine source (e.g. both arms of `foldIsPowerOf2OrZero`), **0 false proofs, 0 false refutations**
  across repeated runs. Verbatim reach is small *by design* — it is vocabulary-bounded, and the
  boundary declines rather than guesses.

## What this means for *your* pass

- If your pass does scalar/vector/memory peephole work or a counted-loop rewrite, expect real verdicts.
- If it leans on `KnownBits`/`APInt` range facts, floating point, or exotic control flow, expect honest
  declines on those folds — not wrong answers.
- A `refuted` verdict comes with a concrete counterexample you can replay against real `opt`.
- You can **independently cross-check** every `proved` whole-function transform against oracles that do
  not share O2T's SMT encoding — `o2t run tv-corpus <your.ll> --cross-check` runs `lli` (real execution)
  and reference Alive2 (`alive-tv`, poison/UB) over the proved set and flags any disagreement (a
  possible false proof), so you needn't take O2T's encoder on trust.
- Coverage is a moving frontier; the self-enrichment loop (`o2t agent`) can grow the modeled vocabulary
  behind an execution oracle, so a decline today can become a proof tomorrow — but only once an oracle
  the proposer didn't author has ratified the new semantics.

See [docs/paper-draft.md](paper-draft.md) for the full design and the soundness argument, and
[docs/soundness-review-2026-07.md](soundness-review-2026-07.md) for the adversarial audit of the
prover surfaces.
