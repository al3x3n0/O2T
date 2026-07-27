# Soundness review — adversarial audit of the prover surfaces (2026-07)

A manual, adversarial complement to [E7's seeded ablation](e7-ablation.md). Where E7 seeds
*misrecovery* corruptions into known-good obligations and checks the cross-check stack catches them,
this review targets the **provers and their encodings directly**: for each soundness-critical surface,
construct inputs that *should* refute or decline, then watch what silently proves (a false proof) or
over-refutes. The discipline's cardinal rule is *never a false proof*; the audit's job is to try to
produce one.

**Headline:** eight rounds over ten surfaces surfaced **two live false proofs** (both now fixed), three
unenforced-decline fragilities (hardened), and one false-*refutation* imprecision. Two rounds were
clean confirmations. Every finding is pinned with an adversarial regression test.

## The rounds

| # | Surface | Finding | Class | Commit |
| --- | --- | --- | --- | --- |
| 1 | Track B signature readers (5 modules) | first-`@name(` matched a forward-reference *call site*, not the `define` | silent decline / order-fragility | `7c1a031` |
| 2 | Track A whole-`.cpp` selector | returned the *first* of multiple same-name bodied decls (overloads) | silent guess | `dc92907` |
| 3 | Refinement encoding (`validate_transform`) | **`udiv`/`sdiv exact` introduction proved** — `exact` modeled only for shifts | **false proof** | `8dd11b4` |
| 4 | Byte-addressable memory model | `ret x → load p; ret x` proved — null-deref gap unenforced | silent (proved vs decline) | `08e7da4` |
| 5 | Pass-pipeline composition | a refuted step set the whole pipeline `refuted`, even if a later pass repairs it | false refutation | `a2fc4c9` |
| 6 | All-trip-count ℤ discharge (`prove_equal`) | **mixed signed/unsigned `min`/`max` proved** — both families alias to one `ite(>=)` | **false proof** | `4c079be` |
| 7 | Loop induction + relational simulation | none (structurally sound; shared flag model; two-sided teeth) | clean | — |
| 8 | `mini_alive` prover + anti-vacuity guard | none (shared flag model; guard correctly gated + tested) | clean | — |

## The two false proofs

Both were reachable because a fixture or comment *claimed* coverage it never adversarially exercised.

**Round 3 — `udiv exact` (`o2t/formal_ir.py`).** Adding `exact` to a division is poison when the
division is inexact, but `flag_poison_smt` implemented `exact` only for `lshr`/`ashr`, and `VALID_FLAGS`
had no `bvudiv`/`bvsdiv` entry — so `_own_poison` filtered the flag out and `exact` was a silent no-op on
division. `udiv %x,%y → udiv exact %x,%y` proved. The `instcombine_ir` fixture's docstring claimed
"exact" coverage but only ever tested `lshr exact`. Fix: `VALID_FLAGS += bvudiv/bvsdiv:{exact}` and
`[us]div exact` poison `= (b≠0) ∧ (rem(a,b)≠0)`. Scoped to Track B (`mini_alive.BIN_OPS` has no division,
so Track A declines it).

**Round 6 — mixed signed/unsigned `min`/`max` (`o2t/validate/closed_form.py`).** The ℤ→ℤ/2ⁿ carry is
`src ≡ opt (mod 2³²)` over unconstrained `Int`. It is sound under *one* representative choice — signed
reps `[-2³¹,2³¹)` make `bv-smax = ℤ-max`, unsigned reps `[0,2³²)` make `bv-umax = ℤ-max` — but `_MINMAX`
aliases both families to the same `ite(>=)` ("over Int, signed/unsigned alias"), and no single
representative carries both. `prove_equal(smax(a,b), umax(a,b))` proved, yet they differ at every width
(`a=0xFFFFFFFF`: smax picks 0, umax picks 0xFFFFFFFF). Fix: a signedness guard declines when both
families appear over shared variables; a single family stays sound.

## The other findings

- **Rounds 1–2 (silent declines):** a signature/definition reader that matches the first name occurrence
  can grab a call site or the wrong overload. Both currently only *declined* (misread names don't
  resolve), but that is order-dependent fragility and a latent wrong-verdict risk, so both were anchored
  on the definition / made to decline on ambiguity. **Lesson: a reader that resolves "a function's
  signature/body" must anchor on the definition, and picking the first of an ambiguous set is a guess,
  not a decline.**
- **Round 4 (unenforced gap):** the memory model does not track pointer validity, and its "sound where
  the load already occurred" was an *unchecked caller assumption* — it proved a load-introduction rather
  than declining. Fix: track each side's dereferenced-address set and decline when the target
  dereferences an address the source does not (refinement without deref-UB is valid iff target-derefs ⊆
  source-derefs).
- **Round 5 (false refutation):** a single refuted pipeline step does not establish the net pipeline is
  unsound — a later pass can mask an earlier miscompile (add an unsound `nsw`, then remove it). The
  composed verdict now follows the direct `f0→fn` check; per-step localization is retained separately.

## Method notes

- The winning technique every round was a **seeded-adversarial battery**: enumerate transforms that
  *should* refute (flag/UB introduction, alias-unsound loads, wrong strides) and that *should* decline
  (out-of-fragment shapes), and assert on the verdict. The happy-path fixtures found none of the six
  issues — they exercised the sound cases.
- **Shared code paid off.** The round-3 flag fix in `formal_ir` automatically covered the loop-induction
  path (which imports `_own_poison` from `scalar_ir`); the danger to hunt is *duplicate* models, of which
  none were found. Rounds 7–8 confirmed the core prover (`mini_alive`) and the anti-vacuity guard reuse
  the fixed shared model and are correctly gated.
- **Every fix declines or refutes rather than approximating.** No finding was closed by widening a
  model to prove more; each was closed by making the unsound case an explicit `unsupported`/`refuted`
  with a regression test that bites.

## Structural hardening (follow-up)

The review also exposed two *systemic* weak spots beyond the individual bugs, now addressed:

- **Track B had no independent cross-check.** Track A has concrete `reconcile` (bv8 enumeration, not
  sharing the SMT encoding); Track B's whole-function TV rested on one hand-written encoding checked by
  one z3 call, and the second solver reuses the same SMT — so an encoding bug was invisible (how
  `udiv exact` survived). **Fix:** `o2t/validate/concrete_tv.py` runs both sides with `lli` (real
  semantics) and cross-checks values; `cross_checked_tv` downgrades a z3 `proved` to
  `refuted-by-execution` when lli disagrees. It is a *value* oracle (poison stays z3's). Gated by
  `concrete_tv_fixture`, which catches an injected value-encoding false proof.
- **Flag coverage was not exhaustive.** Both false proofs came from a fixture claiming coverage it
  never exercised. **Fix:** `flag_matrix_fixture` enumerates every `(op, flag)` in `VALID_FLAGS`,
  asserts introduce-refutes / remove-proves, and asserts the exercised set *equals* `VALID_FLAGS` — so
  no flag can be a silent no-op again.

- **No ground-truth oracle for *poison* refinement.** `concrete_tv` is value-only; z3's internal
  checks share its encoding. **Fix:** `o2t/validate/alive_diff.py` — a differential against reference
  **Alive2** (`alive-tv`), which independently models poison/undef/UB. `differential` flags an O2T
  `proved` that Alive2 calls incorrect as `o2t-false-proof`. Gated by `alive_diff_fixture`, whose
  headline injects a poison-encoding bug (drops `nsw` from the model) that z3 and lli both miss and
  Alive2 catches — and shows lli *agreeing* on the same `add → add nsw` (its blind spot).

**Operationalized.** These oracles are not just demo fixtures — `corpus_tv.cross_check_file` runs both
(lli + Alive2) over O2T's *actual* whole-function TV verdicts on the vendored InstCombine corpus and
confirms every function O2T proves. Today: **14 proved, 0 disagreements** — Track B's proved set on real
code is independently verified by two oracles that don't share its encoding, and `disagreements == []`
is a standing guard (`corpus_cross_check_fixture`) that fails if a future change false-proves any
corpus function.

The three independent oracles now cover the axes: `reconcile` (Track A, concrete bv8), `concrete_tv`
(Track B, value/lli), `alive_diff` (poison/Alive2).

**Automated at scale.** The manual review found the two false proofs by hand; `tools/cv-fuzz-differential.py`
automates that hunt. It generates random scalar functions — *with random poison flags*
(`nsw`/`nuw`/`exact`/`disjoint`), the surface where both false proofs lived — runs real
`opt -passes=instcombine`, and cross-checks O2T's Track B TV against reference Alive2; an O2T `proved`
that Alive2 calls incorrect is a false proof. Across **two seeds and 1,200 random scalar functions, zero disagreements** — O2T proved 1,159, and
Alive2 independently decided and agreed on all ~1,052 non-trivial ones (the rest no-ops it skips). The
fuzzer then broadened to the modeled intrinsics (400 functions, 0 disagreements — the 10 new encodings
validated) and to the memory and vector shapes — **and there it caught a real bug**: a *false
refutation* (the safe direction, not a false proof) where `opt` folds `ashr x,x → 0` soundly (for a
shift ≥ width the source is poison, refining to any value) but the value-only memory model, lacking
poison refinement, saw a value mismatch and refuted. The fix gates the refutation on poison-freedom
(decline, don't refute, when the source carries poison risk); the re-run is then clean. This is the
value of the automated hunt — a bug in the least-reviewed encoding that 476 hand-written fixtures
missed. `fuzz_differential_fixture` runs a small batch of every shape on each build as a standing net.

## Closing the two remaining asymmetries with Track A

A follow-up pass asked a narrower question: which guards does **Track A** have that **Track B** — the
higher-reach engine, and the one both false proofs lived in — does not? Two, and both were closed.

**1. Anti-vacuity.** Refinement is vacuously true wherever the *source* is UB or poison, so a source
that is UB on every input refines to anything: `udiv %x, 0; add` "proves" against `ret i32 12345`.
That verdict is valid and information-free — and it is precisely what an **over-approximated UB or
poison model degrades into**. Claim UB where LLVM has none and a would-be refutation silently becomes
a proof, of exactly the shape none of the three existing oracles can see: `lli` and Alive2 are
consulted only on the proved set, and they agree that a UB source refines to anything. Track A has
had this guard since `mini_alive` (premises must be jointly SAT before an `unsat` is trusted); Track B
had nothing. **Fix:** after every `unsat`, `scalar_ir.validate_transform` probes whether the source is
defined on *any* input and reports `vacuous: True|False|None`. `vacuity_tv_fixture`'s headline injects
an over-approximated UB model (`add` always UB), watches a genuine miscompile (`add x,y → add x,x`)
*falsely prove*, and the probe catch it. The flag rides only on proofs; the value-equality validators
(memory, vectors) carry none, since they have no UB term to over-approximate — their corresponding
guard is the `poison_risk` gate on refutation.

**2. Solver independence.** The three oracles all check the *encoding*; none checks z3. Since Track B
already emits SMT-LIB2 to the z3 binary, **the identical script replays through an independently
implemented solver** (`bitwuzla`, `cvc5`, `cvc4` — auto-detected, reported `skipped` rather than
passed when absent). This now covers the fallback validators too, which matters most for `mem_state`:
its QF_ABV theory-of-arrays encoding is the least-exercised corner of the solver stack.

The probe also corrects a reading of the fuzzing numbers. A random generator readily emits functions
that are UB on every input, so some of the campaign's `proved` verdicts are vacuous — and being valid,
they can never surface as an Alive2 disagreement. `cv-fuzz-differential` now reports the count
alongside the totals (a small single-digit percentage on the scalar shape, higher on `cfg`), so
"O2T proved N of M" is not misread as reach.

**Measured over LLVM 18's `and/or/xor/add.ll` (715 functions):** 417 proved (58% — the scalar
refinement path 349, the memory/vector dispatch 68, the split shifting slightly with timeouts),
**zero vacuous** and **417/417 confirmed by bitwuzla, zero disagreements**. So the reach figure is not
inflated by information-free proofs, and no verdict rests on z3 alone. The vacuity count is a new
standing audit of the UB model: it is zero today, and a nonzero value is a signal to inspect.

**`freeze`, and what it exposed.** Modeling `freeze` — the instruction InstCombine introduces to
launder poison, and previously an outright decline — turned up a false proof *in the act of writing
it*. The obvious rule "freeze of a syntactically poison-free value is the identity" makes
`freeze %x -> %x` prove; reference Alive2 refutes it, because this model treats parameters as definite
while LLVM allows an argument to be `undef` unless `noundef`, and `freeze` is exactly the instruction
that observes the difference. The sound rule is asymmetric: the nondeterministic choice is
EXISTENTIAL on the target (so introducing `freeze` is verified, and freezing *newly* introduced poison
over a definite source is refuted with a witness) and UNIVERSAL on the source, which therefore
declines — freeze-REMOVAL is outside the fragment until `undef` is modeled. Every verdict in
`freeze_tv_fixture` is confirmed against Alive2, and a `freeze` fuzzer shape (target synthesized,
since InstCombine emits `freeze` on essentially no random IR — 0 of 400 measured) found **0
disagreements over 1,000 pairs across two seeds**, with all 12 refutations matching Alive2 exactly. Measured lift: +3 functions on LLVM's `select.ll`; on `freeze.ll`
nothing, since those tests put `freeze` in the *source*, where the decline is by design and is now the
file's top decline reason — an honest signpost at the undef gap.

Still open: a richer UB/`undef` model (single poison bit today; `undef` unmodeled, which is what bounds
`freeze`), loops in Track B (a cyclic CFG is an outright decline), and the inherent low reach
(decline-by-default means ~half of a real pass declines).

## Regression teeth

| finding | test |
| --- | --- |
| `udiv/sdiv exact` | `instcombine_ir_fixture` — `exact` introduce refutes, remove proves |
| mixed min/max | `closed_form_fixture` case 10 — mixed declines, single family proves |
| signature misread | `argprom_tv_fixture` — alias-unsound + forward-reference cases |
| overload ambiguity | `clang_tree_source_fixture` case 13 — overloads decline, `foobar` ≠ `foo` |
| new dereference | `mem_state_tv_fixture` — introduced load declines, re-read proves |
| masked miscompile | `compose_tv_fixture` — masked `nsw` proves net, localizes the pass |
| vacuous refinement | `vacuity_tv_fixture` — an injected over-approximated UB model makes a real miscompile prove; the probe catches it |
| unchecked solver | `vacuity_tv_fixture` — bitwuzla reproduces proof and refutation; a lying stub is caught; absent ⇒ `skipped` |
| freeze identity shortcut | `freeze_tv_fixture` — freeze-removal declines (Alive2 refutes it); introduction proves; new-poison freeze refutes |
