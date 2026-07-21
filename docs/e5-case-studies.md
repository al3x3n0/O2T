# E5 — case studies (worked examples)

Experiment **E5** from the [paper outline](arxiv-outline.md) (§10): end-to-end walk-throughs of
the system on real inputs. Each is traceable to a gated fixture, so the narrative is executable,
not illustrative.

## Case 1 — a peephole fold recovered from verbatim upstream source

The most complete peephole example is `foldIsPowerOf2OrZero`, taken **unmodified** from LLVM 18
`lib/Transforms/InstCombine/InstCombineAndOrXor.cpp`. It is not an algebraic identity but a
bit-counting theorem, and recovering it exercises nearly the whole ladder at once.

**The source** (verbatim):
```cpp
static Value *foldIsPowerOf2OrZero(ICmpInst *Cmp0, ICmpInst *Cmp1, bool IsAnd,
                                   InstCombiner::BuilderTy &Builder) {
  CmpInst::Predicate Pred0, Pred1;
  Value *X;
  if (!match(Cmp0, m_ICmp(Pred0, m_Intrinsic<Intrinsic::ctpop>(m_Value(X)), m_SpecificInt(1))) ||
      !match(Cmp1, m_ICmp(Pred1, m_Specific(X), m_ZeroInt())))
    return nullptr;
  Value *CtPop = Cmp0->getOperand(0);
  if (IsAnd && Pred0 == ICmpInst::ICMP_NE && Pred1 == ICmpInst::ICMP_NE)
    return Builder.CreateICmpUGT(CtPop, ConstantInt::get(CtPop->getType(), 1));
  if (!IsAnd && Pred0 == ICmpInst::ICMP_EQ && Pred1 == ICmpInst::ICMP_EQ)
    return Builder.CreateICmpULT(CtPop, ConstantInt::get(CtPop->getType(), 2));
  return nullptr;
}
```

**What the recovery does, rung by rung:**
1. *Return-form anchor* (phase 36): the function returns the replacement value; the two
   `return Builder.CreateICmp…` arms are the rewrites.
2. *Two-icmp caller contract* (phase 40): the signature `(ICmpInst *Cmp0, ICmpInst *Cmp1, bool
   IsAnd)` says the replaced value is `and(Cmp0, Cmp1)` when `IsAnd` else `or(Cmp0, Cmp1)`.
3. *Cascade slicing* (phase 36b) + *IsAnd case reachability*: the two arms are independent
   obligations, each reachable in exactly one `IsAnd` world.
4. *Two-primary composition* (phase 38): the `match(Cmp0, …)` and `match(Cmp1, …)` conjuncts are
   combined into one before-tree under the case's combiner.
5. *Predicate guards* (phase 39): `Pred0 == ICMP_NE`, etc., fix the icmp predicates.
6. *Operand projection* (phase 40): `Cmp0->getOperand(0)` in the rewrite lowers to the matched
   `ctpop(X)` subtree.
7. The `ctpop` intrinsic (phase 26) supplies the model.

**The two obligations, proved:**
```
IsAnd arm:   (ctpop(X) ≠ 1)  ∧  (X ≠ 0)   ≡   ctpop(X) > 1
!IsAnd arm:  (ctpop(X) = 1)  ∨  (X = 0)   ≡   ctpop(X) < 2
```
Both prove, and both survive the concrete reconciliation cross-check. The teeth are live: a
`UGE`-for-`UGT` rewrite (admitting `ctpop = 1`) refutes with a witness, and claiming the AND arm
under `!IsAnd` (a combiner swap) refutes in the or world.

**Gated by** `pass_graph_twoicmp_fixture`. This is the paper's central claim in one example: a real
LLVM fold, verified from its unmodified source, with the reading itself cross-checked.

## Case 2 — strength reduction, proved relationally for all trip counts

The loop-track flagship is strength reduction: a loop that accumulates a loop-variant product
`acc += i·c` is transformed into one that maintains a running `k += c; acc += k`. The two loops
are not equal state-by-state — the transformed loop carries an extra induction variable — so O2T
proves them equivalent over the **product system** by discovering the simulation relation:
```
{ k == c·i ,  acc == acc }
```
a *linear* relation certifying a *quadratic* accumulator, inferred (not supplied) by coupled
template synthesis, and discharged by k-induction over ℤ. The teeth are two-sided: a wrong stride
`k += d` with `d ≠ c` admits no inductive pairing and is **refuted**.

This runs both on hand-written before/after loops (`loop_simulation_fixture`) and, crucially, on
the **rotated real-`opt` output** shape — the `wrongStride_before/after` functions in the E1
benchmark are exactly a correct and a corrupted strength reduction in the multi-block/LCSSA form
`clang -O1` emits, recovered via the SCEV frontend (E4) and validated closed-loop (E1).

**Gated by** `loop_simulation_fixture` and, in the rotated real-output form, `tv_matrix_fixture`.

## Case 3 — discrepancy detection (honest scope)

E5's outline also asks for "a found discrepancy / known-bug reproduction, if any." On this
benchmark O2T finds **no** miscompile — which is the correct result, and is exactly E1's
zero-false-refutation headline: the LLVM 18 loop passes tested are sound, and O2T agrees.

O2T's discrepancy-*detection* is therefore demonstrated on **injected** miscompiles rather than a
wild-caught one: E1's mutated-recurrence teeth (a corrupted phi initial value → `output-not-
preserved` with a witness), E2's 52 seeded corruptions (all caught), and E7's seeded misrecoveries
(all caught). A separate, real class of discrepancy *was* found and fixed during development — six
recovery-soundness bugs in O2T's own reading of the source (E7 field specimens), two of which
would have produced false refutations of upstream LLVM. Reproducing a wild LLVM miscompile, and
closed-loop validation of real `opt -passes=lsr` output (the source-model of LSR exists; the
closed-loop validator does not yet), remain stated open items.

## Reproducing

```sh
python3 tests/fixtures/pass_graph_twoicmp_fixture.py     # Case 1
python3 tests/fixtures/loop_simulation_fixture.py        # Case 2 (hand-written)
tools/cv-tv-matrix.py --opt-bin "$(command -v opt)"      # Case 2 (rotated real output) + Case 3 teeth
```
