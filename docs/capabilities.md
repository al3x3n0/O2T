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
| Acyclic control flow: branches, `phi`, **`switch`**, **void returns** | ✅ verifies (Track B) | symbolic CFG execution. A switch's cases ACCUMULATE onto a shared block; a void function's obligation is its observable calls plus UB |
| Memory: `mem2reg`/`sroa`, DSE, load-forwarding, gep/type-punning | ✅ verifies (Track B) | byte-addressable theory of arrays, exact aliasing; a target that introduces a new dereference declines (null-deref UB unmodeled) |
| **Pointers as values**: `select`/`load`/`store`/`icmp`/`ret` of `ptr`, `ptrtoint`, geps | ✅ verifies (Track B) | a pointer is its address, with its own poison channel. Comparing addresses ignores provenance — an over-approximation that costs refutations, never proofs |
| **Address width from the datalayout** | ✅ enforced | `p:32` makes `ptrtoint ptr to i32` EXACT; assuming 64 turned a sound LLVM fold into a refutation. Widths past 64 decline |
| **Globals and allocas as objects** | ✅ verifies (Track B) | non-null, non-wrapping, mutually disjoint. An alloca may be asserted distinct from a pointer PARAMETER (it is fresh); a GLOBAL may not — `f(&g)` is ordinary code. A `constant` global DECLINES (its initialiser is not read) |
| Fixed & scalable vectors (element-wise, shuffle/extract/insert) | ✅ verifies (Track B) | per-lane model with per-lane poison |
| **Multi-block vectors**, **pointer lanes**, element-wise intrinsics, `llvm.smin/smax/umin/umax` | ✅ verifies (Track B) | the CFG walk is SHARED with the scalar model, not mirrored. Control flow in these is scalar; only values are lanes |
| `llvm.assume`, `bswap`, `bitreverse`, `ctpop`, `abs`, `ctlz`/`cttz`, funnel shifts, saturating adds | ✅ verifies | `assume` is a UB term (`(not c) or poison(c)`), not an opaque effect — treating it as opaque refuted folds simplified USING the assumption |
| **Constant expressions** | ✅ verifies | computable ones are FOLDED by LLVM; address-dependent ones (`ptrtoint (ptr @g to i32)`) become unconstrained symbols keyed by text, since a fold must hold for EVERY address the global could have |
| Interprocedural: inlining, IPSCCP-style, argument promotion, `deadargelim`, `globaldce` | ✅ verifies (composition) | pipeline / module / signature / promotion via refinement transitivity |
| Counted loops → closed form (`indvars`), strength reduction, LICM, SLP | ✅ verifies (loop track) | all trip counts |
| `freeze` (poison laundering) | ✅ verifies introduction AND removal (Track B) | a target choice is existential; a SOURCE choice is universal and `forall`-bound. Parameters without `noundef` carry a poison flag shared by both sides, which is what makes `freeze %x -> %x` refute |
| **Floating point: bit-level** (`fneg`, `llvm.copysign`, `select`, `freeze`, lane-preserving `bitcast`, FP constants) | ✅ verifies — EXACTLY | LLVM defines these BIT-WISE: no rounding, no trap, no NaN/zero special case. This is not an approximation of FP |
| **Floating point: conversions and `fcmp`** | ✅ verifies structurally | modelled as UNINTERPRETED functions/predicates — the weakest honest model. Folds that route a conversion's RESULT through structure decide; a REFUTATION from such a query DECLINES (`guard: uninterpreted-fp`), because the witness may use a function real IEEE never realises |
| Floating-point ARITHMETIC (`fadd`, `fmul`, `fdiv`, `frem`) | ⚠️ declines | no honest bits-only answer: `+0.0`/`-0.0` differ in bits and compare equal, NaN compares unequal to itself. The containment is ASSERTED by fixture, not described |
| Fast-math flags | ⚠️ ignored on the SOURCE, DECLINED on the TARGET | FMF only ENLARGE a value's behaviour set. On the source that is conservative; on the target, modelling it smaller than it is would prove pairs reality refutes |
| Guards needing `KnownBits` / `APInt` / `ConstantRange` reasoning | ⚠️ mostly declines | a scoped vocabulary stratum; the guard is dropped→decline unless modeled (never mis-applied) |
| Loops in Track B (`cyclic CFG`) | ⚠️ declines | the loop TRACK handles counted loops; Track B's CFG walk is acyclic. Hoisting a `freeze` out of a loop needs induction plus the freeze-quantifier problem |
| Exception handling (`invoke`, `landingpad`, `catchswitch`) | ⚠️ declines | unwind edges unmodeled |
| Non-byte-width memory (`i1`, `i4`, `i67`, `i177`) | ⚠️ declines | Alive2 tracks how many BITS of a byte were written; after `store i1`, a `load i8` of that byte is POISON. A byte array cannot represent a partially-written byte, and modelling it zero-padded is a FALSE PROOF |
| `undef` used more than once | ⚠️ declines | an undef value is not one value — each USE may observe a different one, so it cannot be one term |
| Full UB accounting (pointer validity) | ⚠️ partial | single poison bit; UB modeled for div/rem, flags, poison-pointer dereference and violated `!noundef`, not pointer validity |

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

### Verifying a pass O2T did not build (`--pass-plugin`)

O2T's strongest machinery is Track B: run the real pass and prove its whole-function output a
refinement of its input, cross-checked by `lli`, reference Alive2 and Bitwuzla. It never cared whose
pass produced the output — but it was **unreachable for third-party code**, because every
pass-runner strategy carried a `canonical_pass` and the planner said outright that "a custom/unbuilt
pass would need a build (out of scope)". Pointed at a vendor pass, those strategies validated LLVM's
own InstCombine instead.

    o2t run orchestrate --source MyPass.cpp --pass mypass \
        --pass-plugin ./libMyPass.so --pass-corpus my-tests.ll

`plugin-tv` runs *your* pass through `-load-pass-plugin` and has **no canonical fallback**: it
verifies the pass under test or it does not run, so its verdict is about your pass and nothing else.
O2T builds nothing — anyone using their pass has already built it.

It routes through Track B's full dispatcher — scalar, then the theory-of-arrays memory model, then
the vector lane model — so a memory- or vector-heavy pass is verified rather than declined. That was
not true when this first shipped: it called the scalar translator directly, and on `<4 x i32>` code
it reported `unsupported` for every function and missed a planted miscompile entirely.

Every proof it produces is confirmed against oracles that do **not** share O2T's SMT encoding —
`lli` actually executing the before/after, reference Alive2, and a second SMT solver replaying each
query — and the report says how many proofs those oracles **actually examined**
(`independently_confirmed`), because `disagreements: 0` alone cannot tell confirmation from an
oracle that never ran. An oracle contradicting a proof **outranks** it: the verdict becomes
`refuted`, named as a possible false proof, since that is the failure direction this project is
organised against.

**Two limits, both load-bearing.** The plugin must be built against the same LLVM as `--opt-bin`, or
it will not load. And verification is only as good as the IR you supply: **a pass that transforms
nothing has not been verified.** Every "proof" is then a function it left untouched refining itself,
which is true and says nothing. That is not hypothetical — the first end-to-end run of this feature
reported `proved` for a plugin carrying a planted `add x,x -> x`, because the default corpus contains
no `add x,x` for it to break. A zero-change run is now `inconclusive` and says why
(`plugin_pass_tv_fixture` pins both the catch and the guard).

  The change count compares **instructions, not text**. `opt` strips `; CHECK-...` lines and every
  real LLVM test file carries them, so a textual comparison read comment removal as transformation:
  `sroa`, `gvn` and `early-cse` each reported 207 of 207 functions "changed" on `and.ll` without
  touching an instruction, and the guard could never fire on real input. Comparing instructions
  gives `sroa` 17, `early-cse` 34, `instcombine` 171. Measured on the same run: three real LLVM
  passes over 207 real functions each, **200–201 proved, 0 refuted, 0 oracle disagreements**.

### What a `proved` headline means (and what it refused to mean)

A positive verdict may only certify a pass some check actually **read**. Two kinds cannot:
`canonical` strategies discharge fixed contracts (true whatever pass you point them at), and a
`pass-runner` whose pass is not the one under verification falls back to its `canonical_pass` —
`instcombine-ir` then validates real InstCombine on canonical IR, which is a true proof about LLVM
and says nothing about a vendor pass. Both are recorded, named in `unattributed_proofs`, and kept
out of the status, with the headline stating why.

This was demonstrated, not theorised. A vendor pass emitting an FP horizontal reduction with no
reassoc guard, written in peephole idiom (`replaceInstUsesWith`, `Builder.CreateFAddReduce`),
classifies `peephole` — so `slp-source`, the only strategy that could mine the reduction, is never
planned. Its one source-targeted check answered `inconclusive` ("no fold functions mined") while
three canonical-fallback pass-runners and two canonical checks answered `proved`, and the pass was
reported **proved**. The agent could not help: `proved` is not residue, so it never ran.
`headline_attribution_fixture` pins it; the rule lives once, in `o2t/orchestrate/attribution.py`,
shared with the agent headline so the two cannot drift apart again.

Attribution gates POSITIVE verdicts only. A refutation still counts from any strategy and an
unknown strategy still counts as attributable — both alternatives lose negative evidence, and
losing a refutation hides a miscompile.

**How exposed each family was, measured.** A family can only speak about a vendor pass through its
attributable strategies; the rest prove things about other code. Counting them per family:

| family | attributable for a vendor pass | unattributable |
|---|---|---|
| `promotion` | **none** | 1 |
| `peephole` | `symexec-fold-cascade` | 6 |
| `loop-structural` | `licm-source`, `translation-validation` | 7 |
| `memory-dse` | `memory-source`, `dse-facts` | 2 |
| `vectorize-slp` | `slp-source`, `slp-transaction` | 2 |
| `global` | `globalopt-source`, `globalopt-witness` | 1 |
| `cfg`, `cleanup-dce` | one source strategy each | 1 |
| `loop-scev-recurrence` | `scev-intent`, `translation-validation` | 0 |

`promotion` is the structural worst case: its only strategy is `mem2reg-ir`, a pass-runner with
`canonical_pass=mem2reg`, so **every** vendor pass reaching that family was certified `proved` by a
proof about LLVM's own Mem2Reg — no coincidence required. `peephole` is where the bug was actually
found, and needed only its single source-targeted check to answer `inconclusive`.
`headline_attribution_fixture` asserts the blind-family set is exactly `{promotion}`, so neither a
new blind family nor a fix to this one can land quietly.

### The remaining declines, attributed (2026-09-04) — and why none is worth opening

Of the 111 non-proved functions in the pinned corpus: **32 timeouts** (budget exhaustion, a
different population from vocabulary), **8 named guards** (`uninterpreted-fp` 3, `new-deref` 2,
`target-poison` 2, `opaque-const-expr` 1 — deliberate soundness declines, not gaps; opening them
means weakening a guard, which is the false-proof direction), and **71 vocabulary declines**.

Those 71 fall into **45 distinct wall combinations, the largest covering 7 functions.** The census
must be built from each function's `declines` dict — every validator's own wall — and NOT from
`reason`, which is the scalar validator's, always the first to look. Read the wrong field and
"vectors" appears to be one 23-function bucket; read the right one and it is several unrelated
pieces of work. The top combinations:

| n | what would have to be built |
|---|---|
| 7 | vector values in the theory-of-arrays model (or `ptr` parameters in the lane model) |
| 5 | stores through an escaped pointer, width/target out of scope in `mem_state` |
| 4 | `alloca` of a non-integer type; the lane models stop on an unmodeled instruction |
| 3 | loads from an escaped/uninitialised pointer |
| 3 | cyclic CFG (loops) in the whole-function models |

**The conclusion is that there is no cheap win left in Track B's reach.** The boundary is ragged
rather than blocked on one missing feature, which is what a 94.3% figure over real code should look
like. Each remaining piece is per-shape work with a single-digit yield, and the project's history
(a bitcast bucket estimated at 18 that delivered 4) says those estimates run optimistic. Recorded
here so the measurement is not repeated.

## Measured reach (on LLVM's own tests — treat as indicative, not a guarantee)

- **Track B whole-function TV, 2026-09-03, ALL NINE InstCombine test files at the PINNED tag
  `llvmorg-18.1.8` (1,937 functions), every proof cross-checked in the same pass:**
  **1,826 proved (94.3%)**, 78 unsupported, 32 budget exhaustions (non-answers), **0 refutations**,
  1 vacuous — and **0 disagreements** from `lli` (real execution), reference Alive2 (poison/UB) and
  Bitwuzla (a second SMT solver replaying every query). 158/158 fixtures.

  **This is a DIFFERENT corpus from the 1,835-function figure quoted before, not an update to it.**
  That corpus was local and unpinned and no longer exists; the file list was recorded nowhere, so
  the old number could not be regenerated. Refetching is not equivalent — the obvious source,
  `release/18.x`, is a moving branch and the same nine files now hold 1,937 functions. The corpus is
  pinned by tag and sha256 in `tests/fixtures/trackb_corpus_manifest.json`; fetch and verify it with
  `tools/cv-fetch-trackb-corpus.sh`. Compare percentages, not counts.

  **The proof/cross-check gap is closed: there is no longer a set of proofs the oracles have not
  seen.** Two caveats stated rather than glossed. (1) **8 proofs are not confirmed at the SOLVER
  layer** — the `combine_mul_abs*`/`nabs*` family in `mul.ll`, where Bitwuzla cannot answer inside
  its 30s bound. They are confirmed by `lli` and Alive2; the tool now reports them as
  `solver_no_answer` and excludes them from `cross_checked` rather than counting them as confirmed.
  (2) **Vacuity is the one shape no oracle here can see** — `lli` and Alive2 are consulted only on the
  proved set and agree that a UB source refines to anything — so it is caught by O2T's own probe or
  not at all. That probe used to live only in the scalar validator, covering 71% of proofs; it now
  runs in the lane model and the memory model too. **Coverage 99.3%, and the vacuous count moved
  1 → 10**: of the 1,826 proofs, 10 vacuous, 1,803 verified non-vacuous, and 13 undecided, reported
  as such. Two unrelated fixes got it there: the probe had to run in the lane and memory models at
  all, and on `freeze`/`undef` sources it had to DECLARE the source's nondeterministic choices
  rather than inherit the refutation's `forall` binding — which had been producing solver errors
  reported as "undecided", 21 of the original 34.

  **The remaining 0.7% was measured and deliberately not bought.** Giving the probe a larger
  deterministic budget does decide them — 8x leaves 5 undecided, 40x leaves none — but the vacuous
  count is **10 at every setting**. The budget buys no detection at all; it only converts "undecided
  non-vacuous" into "verified non-vacuous", while 40x took `orchestrate_fixture` from 198s to 433s
  and made the gate unreliable. The guard's value was in existing on all three validators (71% →
  99%), not in the last fraction. The probe keeps a 20s wall cap of its own, because a
  decline-either-way check must never inherit the 300s hang-guard meant for verdict-bearing
  queries. The nine newly exposed proofs were real and had been
  counted as meaningful — `shift.ll:test62_splat_vector`, `shift.ll:test38_poison` (`srem` by a
  poison divisor, UB rather than poison because it decides whether the division traps),
  `and.ll:negate_lowbitmask_commute` (both lanes poison, from opposite directions) and the six
  `icmp.ll:or_poison_vec_*`. `svec_tv` is marked `not-applicable` rather than probed: it asserts no
  UB premise at all, so no vacuity escape exists to look for.

  **The corpus's first-ever refutation appeared in this run, and it was FALSE.**
  `test_mul_canonicalize_neg_is_not_undone` is plain `mul` commutativity; the source computed
  `0 - ptrtoint(@X)` from instructions while InstCombine emitted the folded constant expression, and
  an opaque `cexpr_` symbol let z3 choose the two inconsistently. Refutations that depend on one now
  decline `opaque-const-expr` (`opaque_const_expr_refutation_fixture`); no proof was affected.

  **Why it stayed behind, which was not neglect: the run could not finish.** The second-solver
  replay called `subprocess.run` with no timeout, so one hard query could stall a whole-corpus pass
  indefinitely — measured 2026-09-03, a single bitwuzla query held a run for 78 minutes at 99% CPU
  while the parent sat idle, and since `--cross-check` also forces `jobs=1`, nothing else advanced.
  Bounded at 30s (`SOLVER_TIMEOUT`), a file now cross-checks in minutes. `lli` (30s) and `alive-tv`
  (60s) were already bounded; the gap was only in the solver replay. Run it PER FILE so one stall
  cannot cost the other eight.

- **The denominator was wrong until 2026-08-31, and the fix is worth knowing about.** Every earlier
  figure said "nine files" over EIGHT: ONE function in `shift.ll` (`ashr_out_of_range`, an OSS-Fuzz
  regression test) makes `opt -passes=instcombine` abort the WHOLE FILE with "did not reach a
  fixpoint". The runner recorded `opt_ok: False`, printed an empty count dict, and the file dropped
  silently out of the denominator — a file whose every function died read exactly like a file with
  no work in it. `run_instcombine` now falls back to `instcombine<no-verify-fixpoint>` (what
  `shift.ll`'s own RUN line uses), and a file `opt` cannot process is reported loudly. It scores
  163/171, dead on the corpus average: never an unusual file, only an invisible one.

- **The budget is DETERMINISTIC, and that was worth seven proofs.** The per-function budget used to
  be WALL CLOCK, so a verdict depended on machine load: the `icmp.ll test_sdiv_pos_*` family took
  2.5s in one run and over 15s in another on BYTE-IDENTICAL query text (same sha256), flipping
  between `proved` and `timeout` and moving the total by seven functions. z3's `rlimit` counts SOLVER
  WORK instead. Calibrate it against the hardest thing that still SUCCEEDS — `test_sdiv_pos_ugt`
  proves at 7,027,220 units, the default is 10M, and a first guess of 6M would have silently turned
  all seven into timeouts. The wall clock is now only a 300s hang-guard, or the flakiness returns.
  **A sweep is therefore reproducible and may share the machine**: `-j 0` runs it in 416s instead of
  2,135s (5.1x) with **0 verdict differences across all 1,835 functions**.

- **Two false REFUTATIONS were introduced and caught, both the same shape.** A model that gains reach
  by WIDENING what it permits will refute on the behaviours reality excludes. Uninterpreted FP
  conversions permit every function, and refuted `signbit_bitcast_fpext` — a sound fold, since
  `fpext` preserves the sign bit. Arbitrary global contents permit every value, and refuted
  `select.ll test61` — sound because `@glbl` is a `constant` whose initialiser LLVM folds with.
  **The fix's breadth must match the imprecision's**: FP wanted a BLANKET guard (no refutation from
  any query containing an uninterpreted function), globals wanted a NARROW one (refutations about
  MUTABLE globals are trustworthy; only `constant` ones are mis-modelled). Too broad loses real
  proofs; too narrow leaves the false refutation standing.

- **158 fixtures passed while those false refutations were live**, because every assertion tested
  that something must PROVE or must NOT prove — none tested that something must NOT REFUTE. That
  class of tooth now exists, and is required whenever a change trades precision for reach.

- **What the decline census says today**, attributed to the validator that got FURTHEST (the FIRST
  whose reason is not a door refusal — taking the last biases to a fallback that reached nothing):
  loops 11, `invoke` 7, escaped-pointer memory 13, `alloca` memory 5, vector `fcmp` needing
  vector-aware memory 3, `undef` per-use 4, non-byte widths, budget exhaustion 4. **These are
  capability decisions, not a backlog** — the cheap incremental work is done.

  **Split a bucket by opening the functions, not by its name.** Every estimate this way shrank on
  contact: `ptrtoint` said 11 and delivered 3; `alloca` said 10 and delivered 2; `!noundef` said 3
  and delivered 1; float said 48, then 18, then 14; and a "34 multi-block vector functions" bucket
  was really 11 loops + 10 vectors + 7 invoke + 3 switch + 2 div/rem + 1 callbr — six orthogonal
  capabilities. A decline reason can even be a fact about the MODEL rather than the IR: one function
  reported `cyclic CFG (loop)` on an acyclic function because a bookkeeping bug starved the
  block worklist.

### How the model got here (the findings that cost the most)

Each of these was a live false proof or a live false refutation, and **none was found by the corpus
sweep** — they came from probing the model directly, ablating a fixture, reading *why* Alive2
declined to answer, or reading a diff and asking who else calls a shared layer.

- **A duplicated `undef` use.** A literal `undef` is named fresh at every read, but a register
  CARRYING that freedom is one term, so two reads modelled the uses as agreeing. `xor %u, %u`
  modelled 0 and `ret zeroinitializer -> xor %u, %u` PROVED while Alive2 refutes it. Sharing shrinks
  the TARGET's behaviour set, and a smaller target set is easier to prove a refinement of.
- **An observable call is observable whatever the function returns.** The effect terms sat INSIDE the
  guard on the returned value's poison, so once the source's result was poison the solver never
  looked at the calls.
- **A store through a poison pointer recorded no UB** though the load path did — a target that is
  secretly UB is missing the very disjunct that should refute the pair.
- **Pointer poison did not travel into an inlined callee**, whose frame started the map empty; the
  same program written with the access inside a call and inlined by hand refuted against itself.
- **Fast-math flags were ignored on both sides.** They only ENLARGE a behaviour set, which is
  conservative on the source and a false proof on the target.
- **`llvm.assume` was invisible for a NAMING reason**: the intrinsic lookup only inspected a PREFIX
  of a dotted name, and `assume` carries no type suffix. Treating it as an opaque effect drops the
  fact it establishes, and a target simplified USING the assumption is refuted on exactly the inputs
  the assumption excluded.
- **A shared-layer symbol only one validator declared.** Constant expressions reach the memory model
  through any `sem.value`, and only the scalar validator emitted their declarations — such a function
  came back a solver ERROR, not a verdict. Every validator that calls into `semantics` owes its
  declarations.
- **Bit-level floats are exact, and the containment is what makes them so.** Nothing may read a
  float as a NUMBER; the fixture asserts that `fadd`/`fmul`/`fdiv`/`frem` still decline, and that
  removing the guard on an uninterpreted conversion's refutation fails.

- **Track A verbatim recovery:** ~a dozen upstream fold arms proved directly from unmodified
  InstCombine source (e.g. both arms of `foldIsPowerOf2OrZero`), **0 false proofs, 0 false refutations**
  across repeated runs. Verbatim reach is small *by design* — it is vocabulary-bounded, and the
  boundary declines rather than guesses.

- **Symbolic execution of real pass C++:** thirteen UNMODIFIED upstream LLVM 18 InstCombine folds
  (`combineAddSubWithShlAddSub`, `foldNotXor`, `foldXorToXor`, `foldOrToXor`, `foldAndToXor`,
  `foldLogOpOfMaskedICmps_NotAllZeros_BMask_Mixed`, `foldSelectICmpLshrAshr`, `foldSelectZeroOrOnes`, `foldSelectICmpAndAnd`, `foldAndOrOfICmpsOfAndWithPow2`, `foldICmpAddOpConst`, `foldSetClearBits`, `foldAndOrOfICmpEqConstantAndICmp`) are verified by
  compiling their byte-for-byte source against the symbolic shim and discharging every rewriting
  path. Corrupting any of the four rewrites refutes with a concrete witness. **Thirty-five rewriting arms**
  are proved -- EVERY arm of the three AndOrXor folds, not merely the first of each, plus the commuted
  operand orders upstream's own comments enumerate. Two ablations keep those claims honest: disabling
  arm 1 silences only its harness (so each arm is genuinely distinct, not a fall-through), and
  disabling commutative matching silences only the commuted variants (so they are coverage of the
  `m_c_*` path rather than repetition). Reach
  is **14 of 379** fold-shaped functions compiling, **13 verified** -- the one gap is `foldBoxMultiply`,
  a documented SOLVER bound rather than a modelling one. That denominator is now a REPRODUCIBLE
  measurement (`tools/cv-symexec-reach-sweep.py`, gated by `symexec_reach_sweep_fixture`) over all 15
  InstCombine files, replacing a hand-run figure quoted from a session nobody could re-derive.

  **The sweep refutes the way this reach was being planned, including in an earlier version of this
  paragraph.** It is not matcher vocabulary -- three shim batches (matchers, generic construction, and
  the `Intrinsic` surface, the largest single blocker at 68 hits) each unblocked ZERO further folds.
  But neither is the missing surface *shared*: measured across the whole pass, **no category unblocks
  a fold on its own**. `KnownBits` appears in 55 blocker slots and is the SOLE blocker of nothing; a
  composite batch of every `Create*` builder, the instruction flag readers, `dyn_cast`/`isa`, the
  bit-counting helpers and the constant statics unblocks **two** folds. What remains is a long thin
  tail -- 34 folds each wanting its own handful of names. So the actionable measure is not blocker
  frequency but how many folds a category blocks ALONE, which is what the sweep reports and why a
  frequency table is a trap here.

  **That seam is now closed.** The sweep named exactly six reachable folds; the batch took the four
  that carry a rewrite (two's-complement APInt arithmetic, the instance-form predicate inverse, a
  5-argument select, `new ICmpInst`), and the `shape-mismatch` and `apint` buckets are now EMPTY.
  What the sweep still lists is not more of the same: one intrinsic type (`WithOverflowInst`, which
  returns a struct), two folds needing their file's own pass-local helpers, and one FP matcher family
  belonging to `stripSignOnlyFPOps` -- which is a *helper*, not a rewrite, so verifying it as a fold
  would be a category error. Further reach on this track is architectural, not vocabulary.
- **The ordinary builders carry poison, and that was a live false proof.** Only the explicitly
  poison-aware builders propagated their operands' poison; `CreateOr`/`CreateAnd` -- what upstream
  actually writes -- silently dropped it. Measured, not argued: the same unsound `select C, true, Y
  -> or C, Y` rewrite REFUTES built with the poison-aware spelling and PROVED built with the ordinary
  one, differing in nothing a value model can see. Fixed by propagating operand poison through every
  unflagged builder, which is inert wherever operands are definite (so all existing arms are
  unchanged) and decisive wherever they are not. It also made the first `IsLogical` arm verifiable:
  `a || b` does not evaluate `b`, so upstream freezes it before using its value, and now that arm
  proves while deleting only the freeze call refutes with a poison witness.
- **What `freeze` YIELDS is modelled, not decided.** The shim modelled `freeze` as the operand's own
  term with the poison cleared, which says freeze(poison) equals whatever the operand happened to be
  -- stronger than the semantics, which leave the choice arbitrary. Measured by ablation: under that
  model `select C,X,Y -> freeze Y` given `X == Y` PROVES, and it is a miscompile (with C selecting X
  and Y poison the source returns a definite value; a select does not propagate its unselected arm's
  poison). Freeze is now a fresh unconstrained value selected exactly where the operand is poison, so
  that refutes with a witness; the legitimate freeze fold proves under both models, so the correction
  costs no reach. It survived being executed because every freeze in the harness was correct for ANY
  frozen value -- running the code was not enough, an obligation had to DEPEND on the answer.
- **Poison, not just values.** `foldSelectICmpLshrAshr` (`(X >s -1) ? lshr X,Y : ashr X,Y -> ashr X,Y`) is sound only because upstream propagates `exact` onto the result when BOTH source shifts had it. Forcing the flag on unconditionally leaves every value identical and is still REFUTED with a witness, because the target is poison where the source is defined -- a value-only checker sees nothing wrong.
- **Analysis facts are grounded, and a missing grounding is visible.** Each analysis query a fold asks (`isKnownToBeAPowerOfTwo`, `MaskedValueIsZero`) is a trust edge to LLVM's own analysis. A query the shim recorded but the discharger could not ground used to contribute nothing, silently widening the input space -- which cannot cause a false proof (proving under fewer assumptions is stronger) but can cause a SPURIOUS REFUTATION. Such a query now downgrades a refutation to a non-answer and leaves proofs alone, and the two sets are checked for drift.
- **Pure helpers are proved, not trusted.** The shim's icmp predicate algebra (`getSwappedPredicate`, `getInversePredicate`) is checked against its specification by z3 for every modelled predicate. It was wrong in **16 of 20** cases -- returning its argument unchanged, and collapsing every non-equality predicate to `ICMP_EQ` -- and nothing noticed because no fold could reach it until icmp was modelled.
- **An external oracle on the symexec proofs.** The shim builds BOTH the input and the output term, so z3 alone cannot catch a systematically wrong encoding -- it would prove a wrong output equal to a matching wrong input. All 20 proved arms are rendered back to LLVM IR and **confirmed by reference Alive2**, which never sees the shim. A corrupted rewrite is refuted, so the oracle can fail. It checks the *encoding*, not `undef` behaviour: both sides model parameters as definite values.
- **"Compiles" is an upper bound on what is modelled.** Two of these folds were silently INERT when
  first added -- one bound copies so a pointer-identity test never held, one SEGFAULTED and the crash
  was swallowed -- and both looked exactly like a fold that legitimately declines. Every vendored
  fold is therefore required to actually rewrite on some path, not merely to compile and run.
- **A known solver bound, not a gap in the model:** `foldBoxMultiply` compiles and executes, but its
  obligation — the schoolbook decomposition of a 32x32 multiply — was settled by neither z3 (>10 min)
  nor bitwuzla (killed at ~2.5 h), while the identity itself checks out concretely over 200k random
  pairs. It is recorded as a solver timeout, which counts as a NON-ANSWER and blocks SOUND. Three
  flavours of non-answer are treated identically and none can read as a proof: an errored discharge,
  a solver timeout, and a **crashed harness** (which used to be silently dropped, making a crash look
  exactly like a fold that declines).

## What this means for *your* pass

- If your pass does scalar/vector/memory peephole work or a counted-loop rewrite, expect real verdicts.
- If it leans on `KnownBits`/`APInt` range facts, floating-point ARITHMETIC, loops in Track B, or
  exception handling, expect honest declines on those folds — not wrong answers. Floating point is
  worth a second look before you rule O2T out: bit-level FP (`fneg`, `copysign`, `select`, `freeze`,
  lane-preserving `bitcast`) verifies EXACTLY, and folds that merely route a conversion or an `fcmp`
  through structure verify too.
- **Run the sweep with `-j 0`.** The solver budget is deterministic (`--rlimit`), so a verdict does
  not depend on machine load and a sweep can share the machine — roughly 5x faster with identical
  verdicts. If you lower `--rlimit`, calibrate it against the hardest function that still succeeds,
  or you will convert real proofs into non-answers and the total will barely move.
- A `refuted` verdict comes with a concrete counterexample you can replay against real `opt`.
- You can **independently cross-check** every `proved` whole-function transform against oracles that do
  not share O2T's SMT encoding — `o2t run tv-corpus <your.ll> --cross-check` runs `lli` (real execution)
  and reference Alive2 (`alive-tv`, poison/UB) over the proved set, replays every query through a
  second SMT solver, and flags any disagreement (a possible false proof) — so you needn't take O2T's
  encoder *or* its solver on trust. The same run reports how many proofs were **vacuous** (true only
  because the source is UB/poison everywhere); a nonzero count on your own pass means the reach figure
  is inflated and the UB model deserves a look.
- **If you extend the model, add a "must not refute" tooth.** Anything that gains reach by WIDENING
  what the model permits — an uninterpreted function, an unconstrained value — will refute on the
  behaviours reality excludes. That is how both of this project's false refutations arose, and 158
  passing fixtures said nothing, because every one of them tested PROVE or NOT-PROVE.
- Coverage is a moving frontier; the self-enrichment loop (`o2t agent`) can grow the modeled vocabulary
  behind an execution oracle, so a decline today can become a proof tomorrow — but only once an oracle
  the proposer didn't author has ratified the new semantics.

See [docs/paper-draft.md](paper-draft.md) for the full design and the soundness argument, and
[docs/soundness-review-2026-07.md](soundness-review-2026-07.md) for the adversarial audit of the
prover surfaces.
