# E6 — Pass-IR corpus coverage over upstream LLVM (measured)

First measured run of experiment **E6** from the [paper outline](arxiv-outline.md) (§10): the
structural Pass-IR recovery (`o2t/intent/pass_graph.py`) executed over unmodified upstream LLVM
sources by the corpus runner (`tools/cv-passir-corpus.py`, mechanics gated by
`passir_corpus_fixture`).

**Corpus:** LLVM `release/18.x`, 9 files, 38,267 lines — the eight scalar-side
`InstCombine*.cpp` (`AddSub`, `AndOrXor`, `MulDivRem`, `Shifts`, `Select`, `Compares`, `Casts`,
`InstructionCombining`) and `Analysis/InstructionSimplify.cpp`.

**Run:** 2026-07-20, `cv-passir-corpus.py /tmp/e6-corpus` (z3 4.16). Wall clock ≈ 1.2 s.

## Result

| metric | value |
| --- | --- |
| candidate fold functions extracted (returning `Value*`/`Instruction*`) | **441** |
| recovered (any rung) | **0** |
| **false proofs** | **0** |
| declined | 441 (100%) |

Decline taxonomy (the coverage frontier, by heuristic bucket):

| bucket | n | % | reading |
| --- | ---: | ---: | --- |
| `no-riuw-rewrite` — has matchers, but the rewrite is not `replaceInstUsesWith` | 212 | 48% | **the fragment's rewrite ANCHOR is the binding constraint** (see below) |
| `no-match-call` — no `PatternMatch` inspection at all | 165 | 37% | helpers/utilities; out of scope by design |
| `in-fragment-shape` — match + `replaceInstUsesWith`, still declined | 55 | 12% | large multi-fold visit methods (median 141 lines, none ≤ 30): need per-fold slicing, not just guard modeling |
| `loop-over-ir` | 9 | 2% | beyond the bounded operand-loop rungs |

## Interpretation (honest)

- **Zero recovery on unmodified upstream code is the expected, stated result** of the
  declines-by-default fragment design — and the run's point was the invariant that held: **zero
  false proofs** across 441 real-world candidates. Nothing outside the modeled fragment was
  silently mis-modeled.
- The synthetic-corpus fixture (`passir_corpus_fixture`) demonstrates the same runner produces
  `recovered-proved` (reconcile-cross-checked), `recovered-refuted` (with witness), and every
  decline bucket when the source *is* in fragment — so the 0 is a fragment-scope measurement, not
  a tooling failure.

## The measured frontier (what a next phase buys)

Within the 212 `no-riuw-rewrite` declines:

| sub-population | n | share of all 441 |
| --- | ---: | ---: |
| has `match(` **and** returns a `Builder.Create*` / `BinaryOperator::Create*` / `SelectInst::Create` / `CastInst::Create` / `ConstantInt::get` rewrite | **103** | 23% |
| has `match(` and returns a bound value (`return X;`) | 47 | 11% |

By file, the 103 return-form targets: AndOrXor 32, InstructionSimplify 25, Select 13, AddSub 10,
InstructionCombining 7, MulDivRem 5, Casts 4, Shifts 4, Compares 3.

**Phase-36 candidate:** a *return-form rewrite anchor* — treating `return <builder-expr>;` /
`return X;` in a `Value*`-returning fold helper as the rewrite (upstream's dominant idiom; the
InstCombine contract is "return the replacement value") — would convert ~150/441 (34%) of
candidates from anchor-declines into recovery *attempts*, each still subject to the full
guard/lowering fragment and the C7 cross-check stack. The 55 `in-fragment-shape` functions need
the orthogonal capability: slicing multi-fold visit methods into per-fold obligations.

## Phase-36 re-run (same day): the anchor lands, and the run catches a bug

With the return-form anchor implemented (name-gated, subject-gated, let-inlining,
+`m_OneUse`/`m_Neg`/`m_Not` vocabulary — `pass_graph_return_form_fixture`):

| metric | before (RIUW-only) | after (phase 36) |
| --- | ---: | ---: |
| recovered + **proved** | 0 | **1** — `combineAddSubWithShlAddSub` (InstCombineAddSub.cpp), a VERBATIM upstream fold: `(-B << Cnt) + A → A - (B << Cnt)`, proved and confirmed by exhaustive bv8 enumeration (16.7M inputs) |
| false proofs | 0 | **0** |
| false refutations | 0 | **0** — but only after a catch (below) |

**The run caught a recovery bug immediately** — the E7 story in live action: the first phase-36
run reported `simplifyOrLogic` (InstructionSimplify.cpp) as `recovered-refuted`. Investigation
showed a *misattribution*, not an LLVM bug: `simplifyOrLogic(Value *X, Value *Y)` simplifies the
**implicit outer** `X | Y`; its `match(Y, m_Not(m_Specific(X)))` inspects an *operand*, so taking
the matched pattern as `before` produced the false obligation `~X ≡ -1`. The fix is a **subject
gate**: the fold-path match must inspect the function's *instruction-typed parameter* (what the
returned value replaces); operand-subject helpers decline. The gate is pinned by the fixture.

## Cascade slicing (same day): fold-granular accounting, ready for the multiplier

The corpus now slices every function into its fold ARMS (`recover_folds_from_function`) and counts
at fold granularity. Discipline: arm 0's refutation is a pass-level claim; a **later** arm's
refutation is `refuted-standalone` — earlier-arm exclusions are unmodeled, so the witness may be
unreachable; it is an advisory frontier marker, never "the pass is unsound". A new **in-place
mutation screen** (`setOperand`/`swapOperands`/flag setters between match and rewrite decline the
whole cascade) closes a silent misattribution gap that predates slicing.

Upstream yield today: unchanged (1 arm — the one recovered fold is single-arm; the multi-arm
cascades are precisely the phase-37/38 populations). Slicing is the **multiplier**: when those
phases convert a cascade function, every arm becomes an obligation instead of only the first.
The machinery is gated synthetically (`passir_corpus_fixture`: a 3-arm cascade slices to
proved/proved/refuted-standalone; the mutation screen declines).

**Residual frontier, measured** (why the other ~102 return-form targets still decline):

| sub-population of the 103 | n | next capability |
| --- | ---: | --- |
| multi-match conjuncts on the instruction's operands (`match(&I, …) && match(I.getOperand(1), …)`) | 69 | compose conjunct subjects into ONE before-tree (phase-38 candidate) |
| operand-only signatures (`simplifyXInst(Op0, Op1)` — the implicit op is named by the FUNCTION) | 27 | function-name-implied `before` (phase-37 candidate) |
| FP matchers/builders (outside the bitvector fragment) | 13 | out of scope (stated) |

## Phase 38 (same day): composition lands; the 69 decompose further

The multi-match composition (`pass_graph_compose_fixture`) splices `match(I.getOperand(K), …)`
conjuncts into slot K of the primary tree; comma-declarator lets normalize the dominant
`Value *Op0 = I.getOperand(0), *Op1 = …;` idiom into composable form. Wiring it **closed a second
gate hole**: the subject regex captured `I` from `match(I.getOperand(0), …)`, letting a single
operand match impersonate an instruction-subject one (the simplifyOrLogic misattribution class
hidden behind the `I.` prefix) — now comma-anchored and fixture-pinned.

The pre-implementation decomposition of the 69 (read-only analysis) reshaped the plan honestly:

| shape within the 69 | n | status |
| --- | ---: | --- |
| directly composable (one instruction primary + `getOperand` secondaries in-function) | ~1–3 | **unlocked by phase 38** (the machinery; synthetic-gated) |
| operand params bound only by the CALLER's convention (`foldX(I, Op0, Op1)` with `Op0 ≡ I.getOperand(0)` invisible in-function) | ~61 | **caller-contract parameter binding** — one mechanism that also covers phase 37's 27 operand-only signatures; the next big phase, gated on the visitX/simplifyXInst naming contract |
| dynamic-opcode folds (`CreateBinOp(BO.getOpcode(), …)`) | 5 | needs op-parametric obligations (stated) |
| two-instruction folds (icmp pairs under and/or) | 2 | needs multi-instruction before (stated) |

## Phase 37 (the simplifyXInst half of the caller contract): 1 → 10 proved arms

`simplify<Op>Inst(Value *Op0, Value *Op1, …)` is *documented* as "simplify `<op> Op0, Op1`" — the
name declares both the phantom instruction and the operand **orientation** (the property that
makes `foldX` arg-order binding unsound without call-site verification: callers commute those).
The phantom primary is synthesized and each arm handed to the phase-38 composer; orientation is
fixture-pinned on non-commutative sub (`0 - X → X` refutes with a witness — the swapped-reading
false proof is structurally impossible).

| metric | phase 36 | phase 37 |
| --- | ---: | ---: |
| upstream functions recovered | 1 | **7** |
| upstream fold arms **proved** (all reconcile-cross-checked) | 1 | **10** |
| false proofs / false refutations | 0 / 0 | **0 / 0** |

The 10: `combineAddSubWithShlAddSub` (1), **`foldXorToXor` (3 arms — the cascade-slicing
multiplier live on upstream)**, `simplifyAdd/Sub/Mul/Xor Inst` (1 each), `simplifyAndInst` (2).
Two more recovery bugs were caught by the discipline while building this phase (a return-type
token parsed as the first operand param; a `getType()` normalization that clobbered the cast
folds' type-equality guard — caught by the gated fixture suite immediately) — E7 specimens four
and five.

## Reproducing

```sh
# fetch the corpus (or point at a local llvm-project checkout):
for f in InstCombineAddSub InstCombineAndOrXor InstCombineMulDivRem InstCombineShifts \
         InstCombineSelect InstCombineCompares InstCombineCasts InstructionCombining; do
  curl -sO https://raw.githubusercontent.com/llvm/llvm-project/release/18.x/llvm/lib/Transforms/InstCombine/$f.cpp
done
curl -sO https://raw.githubusercontent.com/llvm/llvm-project/release/18.x/llvm/lib/Analysis/InstructionSimplify.cpp
tools/cv-passir-corpus.py . --report e6.json --summary-text e6.txt
```

The corpus mechanics (extraction, taxonomy, reconcile-gated `proved`, oversize accounting) are
gated by `passir_corpus_fixture` in the ctest suite.
