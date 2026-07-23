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

Still open (named, not yet built): a differential against reference **Alive2** (the only oracle that
independently covers *poison* refinement, closing `concrete_tv`'s blind spot), and a richer UB/`undef`
model.

## Regression teeth

| finding | test |
| --- | --- |
| `udiv/sdiv exact` | `instcombine_ir_fixture` — `exact` introduce refutes, remove proves |
| mixed min/max | `closed_form_fixture` case 10 — mixed declines, single family proves |
| signature misread | `argprom_tv_fixture` — alias-unsound + forward-reference cases |
| overload ambiguity | `clang_tree_source_fixture` case 13 — overloads decline, `foobar` ≠ `foo` |
| new dereference | `mem_state_tv_fixture` — introduced load declines, re-read proves |
| masked miscompile | `compose_tv_fixture` — masked `nsw` proves net, localizes the pass |
