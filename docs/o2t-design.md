# O2T: Design and Verification Methods

O2T (Optimizer Testing Toolkit) formally verifies LLVM optimizations by lifting their *intent*
into an SMT DSL and discharging soundness obligations with an SMT solver (Z3). This document is
the technical reference for the methods — written to be the methods section of a paper.

> Naming: the brand is **O2T**; the repository directory is `O2T`, the Python
> package namespace is `o2t`, and the preferred environment/build prefix is
> `O2T_*`. Compatibility aliases are listed in the README.

---

## 1. Overview and contributions

O2T verifies three kinds of objects, all reduced to the same SMT core:

1. **Peephole rewrites** — algebraic identities over machine integers (`x*2 == x<<1`), with
   poison/undef refinement, flag side-conditions, and fast-math reassociation bounds.
2. **Loop transforms** — recurrences and their rewrites, proved for *all* trip counts by
   k-induction and invariant synthesis, including relational (two-loop) simulation.
3. **Pass intent** — the transform a real LLVM pass *implements*, recovered from its source via
   Scalar Evolution idioms, and the transform a real LLVM pass *performs*, recovered from its
   actual output (closed-loop translation validation).

The contributions that distinguish O2T from prior translation validators (e.g. Alive2):

- **An integer-ring discharge** that proves nonlinear bitvector identities Z3 cannot bit-blast,
  sound for every width via the ring homomorphism ℤ → ℤ/2ⁿ (§2).
- **Template-based loop-invariant synthesis** over ℤ (Faulhaber-aware), discharged by
  k-induction, scaling to coupled/relational invariants for two-loop simulation (§3–4).
- **Intent recovery from pass source** through the SCEV ⇄ recurrence correspondence (§5).
- **Closed-loop translation validation**: run the real pass, prove its output equivalent,
  and on failure emit a **minimized concrete counterexample** (§6–7).
- A **frontend/prover separation** where real-parser frontends (SCEV via `opt`, Clang AST via
  `clang`) feed a parser-agnostic proof core, with a fully self-contained Python package (§8).

---

## 2. The integer-ring discharge (soundness for all widths)

Machine arithmetic is modular: an `iN` add/sub/mul is the corresponding operation in ℤ/2ⁿ.
Z3 models this with bitvectors, but **nonlinear** bitvector multiplication forces bit-blasting,
and even a single 32-bit `i*i` identity does not terminate in practice.

**Key idea.** Prove the *polynomial identity over the integers* (`(set-logic ALL)`, sort `Int`)
instead. The reduction map `ℤ → ℤ/2ⁿ` is a **ring homomorphism**: `+`, `−`, `×` commute with
`mod 2ⁿ`. Hence a polynomial identity `p(x⃗) = q(x⃗)` that holds over ℤ holds in ℤ/2ⁿ for **every**
n — one integer proof certifies all bitwidths at once. Empirically the integer query returns in
~0.02 s where the bv32 query does not return at all.

This is the engine under every loop obligation: deltas, invariants, and relational equalities are
lowered to `Int` and checked by asserting the negation and querying for `unsat`.

```
(set-logic ALL)
(declare-const c Int) (declare-const i Int) (declare-const ACC Int)
(assert (not (= (* M ACC) <poly>)))     ; M·acc == poly?  (negation)
(check-sat)                              ; unsat  ⇒  identity is a theorem
```

The homomorphism argument is what makes this sound; it does **not** apply to width-changing ops
(`trunc`, `zext`, `udiv`), which is why those remain a boundary (see §9).

---

## 3. Loop recurrences and invariant synthesis

A loop accumulator is a recurrence `acc₀ = init`, `acc_{i+1} = acc_i + δ(i, params)`. O2T proves
a claimed invariant `R(acc, i)` by **1-induction (k = 1)**:

- **BASE**: `init = R[i := 0]`
- **STEP**: `R(acc, i) ⟹ R(acc + δ, i+1)`

both discharged as `unsat` of the negation over ℤ (§2).

**Closed-form synthesis.** When the closed form is unknown, O2T *synthesizes* it from a
degree-aware polynomial template:

```
M · acc == c₀ + c₁·i + c₂·i² + … + c_{d+1}·i^{d+1}
```

where `d = deg_i(δ)` and the multiplier `M` comes from `FACTORIAL_M = {0:[1], 1:[1,2], 2:[1,2,6]}`
(1!, 2!, 3!) to clear Faulhaber denominators — e.g. `acc += i·i` synthesizes `6·acc == 2i³−3i²+i`.
Two engineering points make this fast:

- **`batch_check`** runs all template candidates in *one* Z3 process via `(push)…(check-sat)(pop)`,
  turning per-candidate subprocess spawns (~26 s) into a single query (~0.9 s).
- **Relevant-const pruning** restricts the coefficient basis to constants that actually appear in
  `init`/`δ` (excluding e.g. the loop bound `n`), shrinking the candidate space.

**Beyond polynomials.** Two recurrence classes have no polynomial closed form and are handled
specially:

- *Conditional* (`acc += cond ? a : b`, loop-invariant `cond`): the stride is invariant, so the
  closed form is `acc == i · ite(cond, a, b)` — an ite-valued stride that rides along in the proof.
- *Geometric* (`acc *= c`): exponential `c^n`, with no polynomial/integer closed form (Z3 has no
  exponentiation), so single-loop closed forms are correctly *declined*. The transform is instead
  proved **relationally** (§4): two multiplicative loops are equal by a lockstep invariant.

**Memory deltas.** `acc += p[i]` has no closed form (it depends on the array). O2T models a load
as an **uninterpreted function** `ld(addr)` (deterministic: same address, no intervening write ⇒
same value), and proves LICM-of-load and GVN-redundant-load (`ld(i)+ld(i) == 2·ld(i)`) by
congruence over `Int + UF`, without knowing the values.

---

## 4. Relational (two-loop) simulation

Many transforms relate two loops (strength reduction, fusion, the before/after of a real pass).
O2T forms the **product system** of both loops' state, prefixed `A_`/`B_`, with shared params and
a shared iteration index `i`. It then:

1. **synthesizes auxiliary invariants** for `B`'s non-output induction variables (e.g. the running
   `k` of a strength-reduced loop) via coupled synthesis — a template `M·X == c₀ + c₁·i + c₂·i²`
   in which prior invariants are asserted in the STEP (this is the coupling); and
2. **discovers the output bijection** `A`-outputs ⇄ `B`-outputs by proving each candidate pairing's
   equality inductive under the accumulated relation.

The discovered relation is often simpler than either closed form — strength reduction
`acc += i·c` vs `k += c; acc += k` is proved by `{ k == c·i, acc == acc }`, a *linear* relation
certifying a *quadratic* accumulator. A wrong stride (`k += d`, `d ≠ c`) yields no inductive
pairing and is **refuted** — the prover has two-sided teeth.

SSA φ-loops are inherently parallel (each `%x.next` reads the φ, the iteration-start value), so the
synthesizer's parallel step semantics match the IR with no update-ordering subtlety.

---

## 5. Recovering intent from optimization code (SCEV bridge)

Other tools infer intent from *artifacts* (the IR before/after a pass). O2T also infers it from the
*implementation*. The bridge is **Scalar Evolution**: loop passes phrase themselves in SCEV, and a
SCEV add-recurrence `{start,+,step}` **is** O2T's `(init, δ)` recurrence.

Reading `LoopStrengthReduce` source, O2T recognizes the idiom `getMulExpr(C, IV)` (a loop-variant
product `c·i`) rewritten to `getAddRecExpr(0, C, L)` (a running add `{0,+,c}`), lifts it to the
before/after pair `acc += i·C` / `k += C; acc += k`, and proves it sound via §4. A rewrite whose
recurrence step disagrees with the eliminated product's coefficient is refuted; a product with no
IV operand is *declared* unhandled rather than silently passed.

---

## 6. Closed-loop translation validation

The strongest mode validates **LLVM itself**: it runs the real pass, `opt -passes=<X>`, on a source
function and proves opt's *actual output* equivalent to the input for all trip counts. It composes
the whole stack — the SCEV frontend extracts both sides' recurrences, the relational prover (§4)
discharges the simulation — turning O2T from a model checker into a **miscompile finder**.

Two transform shapes arise:

- **loop → loop** (LSR, LICM, loop-rotate, loop-unswitch, loop-instsimplify): both sides still have
  a recurrence; `prove_mined` certifies equivalence (and synthesizes the closed-form relation,
  e.g. `2·B_acc == −c·i + c·i²`).
- **loop → closed form** (`indvars`/SCEV deletes the accumulator and returns a closed form in the
  exit block, e.g. `c·(n-1)(n-2)/2`): the optimized side has no loop recurrence. For a recognized
  loop class — a canonical counted do-while (`i = {0,+,1}`, an accumulator phi with a **polynomial**
  per-iteration delta `Σ_e c_e·i^e` up to degree 3, exit guard `icmp slt %i.next, %n`, returning the
  accumulator) — O2T validates the closed form **formally** (`validate/closed_form.py`). It derives
  the trip count `T = smax(n,1)` *structurally* from the loop (not from SCEV's own answer) and forms
  the source exit value as the **Faulhaber sum** `acc₀ + Σ_e c_e·(Σ_{j<T-1} j^e)`, cleared to a single
  exact integer quotient (`polyquot`, e.g. `Σj² = (T-1)(T-2)(2T-3)/6`). The optimized side computes
  the products with a width-changing idiom — `trunc_i32( (zext_i33 X₁ · … · zext_i33 Xₙ) /u K )`,
  an **N-ary** product (`n=2,K=2` triangular; `n=3,K=2` for cubic; `n=4,K=8` in i35 for quartic)
  widened to avoid `i32` overflow.
  Since that depends on the *modular* widening, the ℤ-homomorphism (§2) does not apply directly, so
  the proof is **two parts**: (A) a fast **modular lemma** per widening — the widened (by enough guard
  bits for `/K`), unsigned-divided, truncated product equals the exact `(∏Xᵢ)/K` mod 2³²; then (B)
  abstracting each to a `divprod`
  symbol (constrained `K·v = ∏Xᵢ`, exact since a product of consecutive integers is divisible by `K`)
  and discharging `source(n) = optimized(n)` **mod 2³²** — the i32 obligation, which absorbs the
  cubic's **modular-inverse magic constant** (`indvars` writes `Σj²` as `½·… + (2·3⁻¹ mod 2³²)·…`,
  an identity that holds only mod 2³²). `smax/smin` are commutativity-canonical `ite`. A corrupted
  closed form is **refuted** with a concrete witness `n` (the (B) identity has the teeth; each (A)
  lemma guards a widening; a vacuous proof from a non-divisible quotient is caught by a
  satisfiability check). A delta beyond degree 3, an `shl`-built delta, a non-divisible/insufficient-width
  widening, or an unmodeled SCEV op is declined honestly (`loop-eliminated`, naming the blocking ops)
  and falls back to the **semi-formal differential** verdict (§6a) — never silently passed.

### 6a. Semi-formal differential validation

When a transform is outside the symbolic prover's reach (`loop-eliminated`, or any case the
discharge cannot close), O2T does not give up: it **compiles both the source and the optimized
function with `clang` and runs them** over a sweep of small inputs, using actual LLVM/CPU
semantics — no interpreter to get wrong. Agreement on every sampled input is a `differential-pass`
(a *semi-formal* verdict — testing, not proof, explicitly bounded by the sweep); a disagreement is
a concrete `differential-mismatch` carrying the offending input. This is what validates `indvars`.

Two engineering safeguards keep it sound and bounded: inputs are kept small (loops are
parameter-bounded, so a `<`-exit loop runs few iterations) and each execution is hard-timeout'd,
so no adversarial trip count can hang the suite. To link a source module and its optimized form
without symbol clashes, every user-defined function is suffixed (`_src`/`_opt`); intrinsics
(declared, not defined) are untouched.

The result is a two-tier story: **formal** (all-trip-count proof) where the integer discharge
reaches, **semi-formal** (bounded differential) where it does not — with two-sided teeth on both
tiers (a corrupted transform is refuted formally *and* differentially, each with a witness).

---

## 7. CEGAR counterexamples (minimized witnesses)

When validation refutes a transform, Z3's verdict is abstract — and its inductive-step model may
assign an *unreachable* pre-state. O2T instead emits a **concrete, reachable, minimized** witness:
it forward-executes *both* recurrence systems from their true initial values over an edge-value
parameter sweep (small values first) and increasing trip counts, returning the first divergence.
That witness is therefore reachable and minimal in trip count, e.g.:

```
witness: params={c: 0, n: 1}  trip=1   ⇒   source = 0   vs   optimized = 1
```

This closes the loop: a refutation becomes a runnable miscompiling input, suitable for a bug report
or for seeding a reducer.

---

## 8. Architecture: real-parser frontends, parser-agnostic prover

A recurring fragility in lifting tools is *regex-as-parser*. O2T separates **frontends** (text →
recurrence AST) from the **proof core** (recurrence AST → SMT), and uses real parsers in the
frontends:

- **`.ll` → SCEV**: `opt -passes='print<scalar-evolution>'` runs LLVM's own analysis; O2T parses
  the small, fixed printer grammar (`{a₀,+,a₁,+,a₂}`), converting chained add-recurrences via
  Newton's forward difference (`init = a₀`, `δ = a₁ + a₂·i`). This is rotation/block-layout/
  GEP/temporary invariant by construction — LLVM normalizes; O2T does not re-implement it.
- **`.cpp` → Clang AST**: `clang -Xclang -ast-dump=json` over a minimal SCEV-API stub yields typed
  `CXXMemberCallExpr` nodes; O2T walks the AST, not the source text.

The prover (`prove_mined`, the ℤ discharge) is **unchanged** across frontends — a frontend swap is
isolated from the proof layer. The implementation is a self-contained Python package,
`o2t/`, with sub-packages `frontend/ mine/ synth/ prove/ intent/ registry/ facts/
validate/`; CLI entry points are thin shims. The package performs **no `sys.path` manipulation**
and contains **no dynamic-import (`spec_from_file_location`) hacks**.

---

## 9. Soundness boundaries and limitations (stated, not hidden)

O2T's design rule is *no silent caps*: every unhandled case is surfaced.

- **Width-changing ops.** The ℤ-homomorphism (§2) covers `+,−,×` and `smax/smin` (as `ite`) directly.
  The `indvars` product widenings (`i33 zext`/`/u K`/`trunc`, any arity) are handled by the two-part
  proof of §6 — a modular lemma per widening plus an abstracted identity discharged mod 2³² — so the
  counted-loop closed forms for polynomial deltas up to degree 3 (`acc += c`, `i·c`, `a·i+b`, the
  cubic `i·i`, **and the quartic `i·i·i`** with its 4-factor `/u 8` i35 widening and modular-inverse
  constants) are proved **formally** for all `n`. The remaining boundary: deltas of degree ≥ 4, `shl`- or
  otherwise-built deltas the resolver does not model, and widening idioms whose divisor does not divide
  the product or whose guard width is insufficient — all declined honestly and validated semi-formally
  (§6a). General `trunc`/`zext`/`udiv` *outside* the recognized product-widening shape still falls to
  the differential.
- **Synthesis template ceiling.** Polynomial synthesis covers affine→cubic deltas; quartic+ is
  declined. The invariant template assumes an accumulator base of `0` or a parameter, so a literal
  non-zero base (e.g. `init = 1`) is not representable and yields `no-aux-invariant` rather than a
  false proof.
- **Memory model.** Two layers. In the *loop* track, loads are uninterpreted (read-only,
  deterministic) — enough for LICM-of-load and GVN-redundant-load by congruence. For *memory
  transforms* (DSE / store-forwarding / redundant-load), O2T now uses a real **theory of arrays**
  (`validate/memory_model.py`, QF_ABV): memory is `Mem : Addr → Word`, a before/after op
  sequence is symbolically executed from the same initial memory, and the observable (a returned
  load, or the final memory by extensionality) is proved equal for **all memories, addresses, and
  values**. Aliasing is first-class: a transform is proved sound under its no-alias side-condition
  (`q ≠ p`) and **refuted with a concrete colliding-address witness** when it is dropped — so the
  side-condition is shown load-bearing, the same two-sided teeth elsewhere in O2T, now over real
  read–write memory. These op sequences are also **recovered from pass source**
  (`intent/extract_memory_model.py`): a DSE/forwarding fold's rewrite
  (`deleteDeadInstruction`/`eraseFromParent`, `replaceAllUsesWith(load, storedValue(store))`) and
  its OWN legality guards (`isOverwrite`/`fullyOverwrites` → alias `eq`, `isNoAlias`/`!mayAlias`
  → `ne`) are lifted to the before/after op sequence and the SMT assumptions, then discharged —
  so a fold whose guards are **insufficient** (removes a store without establishing an overwrite)
  is refuted from its source with a colliding-address witness. **Byte granularity** is modeled
  too (a byte array `Addr → i8`): a dead store removed under a FULL overwrite (the killer's byte
  range covers it) proves, while a PARTIAL overwrite is refuted with a surviving-byte witness —
  the `isPartialOverwrite` blocker, now an exact array obligation rather than a source fact.
  **CFG-shaped** memory flow is path-sensitive (a `branch` forks the memory and merges with
  `ite`): DSE across a diamond proves only when *every* path overwrites the dead store, and a
  one-path overwrite is refuted with a witness where it survives on the other path; store
  sinking (two conditional stores → one select-valued store) proves. **Atomics/ordering** are
  modeled by observable *sync snapshots*: an atomic (≥monotonic) / volatile op is a point where
  the memory is visible to other threads, so a transform must preserve the whole snapshot
  sequence *and* the final memory. Hence eliminating an atomic store is refuted (its snapshot
  vanishes) and reordering a store across a barrier is refuted (the memory seen there changes),
  while non-atomic DSE and the reordering of non-aliasing non-atomic stores prove — conservatively
  treating any sync op as a full barrier (sound; the per-ordering, two-direction relaxation is a
  refinement). Remaining: that per-ordering relaxation, true multi-thread interleavings, and
  unbounded loops over memory.
- **Witness completeness.** The CEGAR search is a bounded concrete sweep; witnesses requiring large
  constants outside the sweep are not found (the absence of a witness is *not* a proof of
  equivalence — equivalence comes only from the SMT discharge).
- **Refinement.** Peephole flag/poison handling exists, but full two-sided poison/undef refinement
  (Alive2 semantics) for loops/multi-instruction sequences is future work.

---

## 10. Reproducibility

All claims are gated by an executable test suite (CTest, 417 fixtures). Each verification tool has
a `--selftest` that emits a JSON report; CMake fixtures assert both *soundness* (sound transforms
prove) and *teeth* (wrong transforms are refuted, with a witness). External tooling: Z3 4.16,
optionally Bitwuzla (second-solver cross-check), and Homebrew LLVM 18 (`opt`/`clang`/`llvm-as`);
KLEE 3.2 and `alive-tv` enable an optional Tier-3 differential layer.
