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
| `freeze` (poison laundering) | ✅ verifies introduction (Track B) | a pass INTRODUCING `freeze` is verified (its choice is existential); REMOVING one declines — without an `undef` model the identity shortcut is a false proof reference Alive2 refutes |
| Transforms whose soundness depends on an argument being `undef` | ⚠️ declines unless `noundef` | parameters are modeled as one definite value, i.e. `noundef` is assumed; where that assumption is load-bearing (the target's result depends on a non-`noundef` parameter the source's does not) the verdict declines. Declaring `noundef` makes it provable |
| Full undefined-behavior accounting (pointer validity, `undef` distinct from poison) | ⚠️ partial | single poison bit; UB modeled for div/rem and flags, not pointer validity; `undef` unmodeled, so `freeze`-removal and undef-sensitive folds decline |

✅ = a real proof or refutation with a witness. ⚠️ = an explicit `unsupported` decline (a sound
non-answer), **never** a false `proved`.

## How the IR is read

O2T parses LLVM IR with **LLVM 18's own parser** (`cv-ir-dump` → `o2t/validate/ir_model.py`), not with
regexes — the same parser `opt` used to produce the IR being validated, so the two cannot disagree
about what the text means. Poison flags come from LLVM's accessors, types arrive structured (struct
layouts, vector lane counts, scalable-ness), and an unmodeled opcode declines *on its opcode* rather
than a pattern quietly failing to match. This is a hard requirement: there is no text fallback,
because a silent second parser is the drift the migration removed. `o2t doctor` reports whether it is
built.

Two consequences worth knowing when reading verdicts:

- IR that LLVM rejects now yields `error` (carrying LLVM's diagnostic) rather than being read past. A
  transform that produces invalid IR — deleting a still-referenced function, removing a live argument
  — is reported as such.
- Some functions that previously declined now verify: a trailing `; comment`, an `immarg` attribute, a
  `zeroinitializer`, LLVM 18's `splat (iW C)` spelling, and **named or packed struct types** in a
  `getelementptr` were all valid IR the old readers could not match.

## Measured reach (on LLVM's own tests — treat as indicative, not a guarantee)

- **Track B whole-function TV:** **428 / 715 (60%)** of LLVM 18's `and/or/xor/add.ll` InstCombine test
  functions proved sound end-to-end, **0 false refutations**; the rest decline on shapes above, plus a
  handful of timing-dependent per-function solver timeouts (a sound decline). Re-measured at the
  current head: the scalar refinement path proves 359 and the memory/vector dispatch the other 69.
  The previously documented 351/715 (49%) counted the scalar path alone.
- **Assumption hygiene.** Where a proof would rest on an undeclared assumption, O2T declines instead:
  a source that is UB/poison everywhere is flagged vacuous (below), and a transform whose soundness
  needs an argument to be non-`undef` declines unless the argument is declared `noundef`.
- **Non-vacuity: zero vacuous proofs.** A refinement proof is vacuously true wherever the source is UB
  or poison, so `udiv %x, 0` legitimately "refines" to anything — and an *over-approximated* UB model
  would quietly convert refutations into proofs of exactly that shape, invisibly to the execution and
  Alive2 oracles (which are consulted only on the proved set). Every proof on the refinement path
  (~359 of the 428; the split with the value-equality validators shifts a little with timeouts) is
  probed for a defined source: **none is vacuous**, so the reach number is not inflated and the
  UB/poison model is not over-approximating anywhere on LLVM's own tests. The memory and vector
  validators carry no vacuity flag — they compare *values* and have no UB term to over-approximate.
- **Solver independence: 428 / 428 confirmed by a second solver.** Every decided query is replayed
  verbatim through an independently implemented SMT solver (`bitwuzla`, or `cvc5`/`cvc4` if present),
  including the QF_ABV memory encoding — **zero disagreements**. The other oracles check O2T's
  *encoding*; this is the only one that checks the *solver*.
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
  and reference Alive2 (`alive-tv`, poison/UB) over the proved set, replays every query through a
  second SMT solver, and flags any disagreement (a possible false proof) — so you needn't take O2T's
  encoder *or* its solver on trust. The same run reports how many proofs were **vacuous** (true only
  because the source is UB/poison everywhere); a nonzero count on your own pass means the reach figure
  is inflated and the UB model deserves a look.
- Coverage is a moving frontier; the self-enrichment loop (`o2t agent`) can grow the modeled vocabulary
  behind an execution oracle, so a decline today can become a proof tomorrow — but only once an oracle
  the proposer didn't author has ratified the new semantics.

See [docs/paper-draft.md](paper-draft.md) for the full design and the soundness argument, and
[docs/soundness-review-2026-07.md](soundness-review-2026-07.md) for the adversarial audit of the
prover surfaces.
