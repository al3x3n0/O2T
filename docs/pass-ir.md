# Pass IR — formalized DFG/CFG source mining

This is the design track for moving source-intent recovery from a flat pattern cascade to a typed
**Pass IR** (a control- + data-flow graph *of the optimization pass itself*) that the prover
consumes. Phase 2's core is implemented and gated (`o2t/intent/pass_graph.py`,
`pass_graph_fixture`); the rest is the roadmap.

## Why

The legacy path (`intent/extract_pass_model.py` + `scalar_formal_for`) keys formal IR off a flat
`(operation, identity, rewrite)` triple and orders findings by source line. That can only express
single-op identities and declines: nested matcher algebra, multi-step rewrites, guards spanning
`&&`/nesting/helpers, and any novel idiom. The symbolic-execution track (`o2t/symexec/real_pass.py`)
already does the real thing over compiled bitcode, but is disconnected from the source track.

A single normalized graph both tracks lower into closes that gap.

## The graph model

**Nodes**
- `match` — a `PatternMatch` bind carrying the matcher *tree* (`m_Add(m_Value(X), m_Zero())`)
- `query` — an analysis fact (`isKnownNonZero(X)`, `hasOneUse`, `isGuaranteedToExecute`) — a choice
  point / assumption grounded by `facts/value_tracking`
- `build` — an `IRBuilder.Create*` node with DFG edges to its operands
- `effect` — the rewrite: `replaceInstUsesWith(I,V)`, `eraseFromParent`, `setOperand`, `setInitializer`
  (in-place IR mutation, not only "return a value")
- `helper-call`, `const`, `phi/merge`

**Edges**
- **CFG** (control): entry → … → `effect`, labelled by branch polarity → the path condition is the
  conjunction of guards that *dominate* the effect (replaces "order by line")
- **DFG** (data): operand def-use from each effect operand back through locals/builders to the
  `match` bindings → recovers before→after compositionally

## Obligation lowering

For each `effect` reachable under path-condition `C`, with `before` = the matcher tree and `after`
= the DFG expression rooted at the replacement value, prove (poison/UB-aware):

```
C  =>  before ≡ after
```

This generalizes `scalar_formal_for` from a triple to a recursive lowering of arbitrary matcher/DFG
trees. Recovered obligations are discharged by the existing prover (`mini_alive.prove` /
`equivalence_smt`), so they inherit the **premise-SAT anti-vacuity gate, the teeth, and the
second-solver cross-check** for free.

## Two front-ends, and the soundness reconciliation

- **Bitcode (authoritative):** build the CFG/DFG from the pass's own compiled LLVM IR (elevate
  `symexec`). Ground truth for what runs; handles C++ desugaring for free.
- **AST (provenance + coverage):** build the same IR from the Clang AST; readable, covers
  non-compiling snippets, carries source locations.

When both exist, **cross-check the graphs** (as `meta/cross_check.py` does for solvers): a divergence
means the source recovery is wrong → **decline** (`unsupported`), never a false `proved`. This keeps
the "no silent mis-model" invariant.

## Status / roadmap

| Phase | Content | Status |
| --- | --- | --- |
| 2-core | Compositional `before`/`after`: parse the matcher tree + rewrite DFG, lower to formal IR, prove | **Done** (`pass_graph.py`, `pass_graph_fixture`) |
| 1 | Guard/precondition recovery: analysis-query conjuncts (`isKnownNonNegative`, ...) → the premise the equivalence is proved UNDER; unrecognised guards decline | **Done** (same module) |
| 1+ | Function-level path condition: reconstruct the guard from a fold FUNCTION's control flow -- early-return bailouts (`if (!G) return nullptr;` -> path gains `G`, De Morgan on `!A||!B`) + positive guards (`recover_from_function`) | **Done** (same module) |
| 1++ | Nested-brace `if (G) { ... }` blocks: fold inside enclosing positive guards at arbitrary nesting, unified with bailouts via a recursive path-finder (`_find_fold_path`) | **Done** (same module) |
| 3a | Reconciliation, always-available half: cross-check the recovered obligation across two independent engines -- symbolic z3 (bv32) vs exhaustive CONCRETE enumeration (bv8, precondition-aware). Agreement required; a divergence (e.g. width-non-uniform) is flagged untrustworthy (`reconcile`) | **Done** (same module) |
| 3b | Reconciliation, compiled half: `to_shim_harness` realizes a recovered fold as a `symbolic_llvm.h` harness, compiles it, and symbolically executes it through its real branches (`symexec/real_pass`); the compiled-path verdict must match z3 -- an independent compiled oracle (`reconcile_compiled`, graceful skip without clang++) | **Done** (same module) |
| 3c | Full source-parse independence: generate the shim harness directly from the C++ source (not the recovered pair), so a front-end parse bug diverges | Next |
| 4 | Interprocedural: single-return guard + value helpers (incl. chained) inlined before recovery, retiring the "blocked helper slice"; multi-statement helpers decline (`_parse_helpers`/`_inline_calls`) | **Done** (same module) |
| 5 | Loops over IR, INDEPENDENT iterations: a `for (Instruction &I : BB)` header is a universal quantifier over instructions (no value precondition) -- skipped transparently, the per-instruction body fold recovered under its guards (`_find_fold_path`) | **Done** (same module) |
| 34 | Loops over an OPERAND list, NON-independent iterations (the GUARD case): `for (In : PN->incoming_values()/operands())` whose guard is quantified over every operand (`SimplifyPHINode`'s `phi [x,x,..,x] -> x`). Recovered at a BOUNDED arity -- the phi as a nondeterministic selector-merge that must collapse under the recovered pairwise-equality guard -- and **arity-corroborated** (`recover_operand_loop`, `corroborate_arity`) | **Done** (same module) |
| 35 | Loops over an operand list, NON-independent iterations (the REDUCTION case): a loop that ACCUMULATES a fold, rebuilding an n-ary op from its operands (reassociate style). Obligation `right-fold(OP_before) == left-fold(OP_after)` -- sound iff the operator is associative and the reducer matches I's op. Associativity is invisible at arity 2 and only bites at 3+, so `corroborate_arity` catches a non-associative reducer where a single arity-2 proof would bless it (`recover_reduction_loop`, `_reduction_obligation`) | **Done** (same module) |

### What phase 2-core already buys

`pass_graph.recover_pair(predicate_source, rewrite_source)` recovers, proves, and (on a wrong fold)
refutes folds the flat triple cannot express — e.g. the nested `(X+0)*1 -> X`, and **`or-self`
(`X|X -> X`) with zero registry coupling**: a fold that otherwise needs a hand-wired
`pass_constraints` + `semantic_facts` + registry entry now falls out of the recovered structure. New
identity idioms (`X&0`, `X*0`, `X-X`, …) come for free as the matcher/rewrite vocabulary grows,
instead of one registry triple at a time.

Phase 1 adds the **precondition**: the guard's analysis queries become the premise the equivalence is
proved *under*. `sdiv X,Y -> udiv X,Y` — unsound in general — is **refuted unguarded**, **proved**
under `isKnownNonNegative(X) && isKnownNonNegative(Y)`, **refuted** on an insufficient guard (only one
operand), and **caught vacuous** on a contradictory one (via the premise-SAT gate). A value-irrelevant
guard (`hasOneUse`) is dropped; an *unrecognised* guard **declines** — dropping a value-relevant
precondition could turn an unsound fold into a false `proved`.

## Hard parts (named honestly)

1. **Loops over IR** — the pass-CFG has loops. Two cases now handled: a loop with **independent**
   iterations (`for (I : BB)`) is a universal quantifier over instructions, skipped transparently
   (phase 5); a loop over an instruction's **own operand list** with **non-independent** iterations
   (a guard quantified over every operand, `SimplifyPHINode`) is recovered at a **bounded arity** and
   **arity-corroborated** — a genuine universal identity holds at every operand count, so an
   under-recovered guard that is sound at arity 2 diverges (`arity-specific`) at arity 3+, exactly as
   `corroborate_widths` flags a width-32 coincidence (phase 34). Its dual, a loop that **accumulates a
   reduction** to rebuild an n-ary op (reassociate style), is the same bounded+corroborated obligation
   `right-fold == left-fold`, where the corroboration catches a **non-associative** reducer that is
   value-equal at arity 2 (phase 35). Still declining: worklist **fixpoints**, cross-instruction
   accumulation across DISTINCT instructions, and unbounded/data-dependent trip counts (sound declines,
   not silent). Remaining decline frontier.
2. **In-place mutation semantics** — the `after` is a mutation of the IR graph (RAUW/erase/setOperand),
   needing a small IR-state semantics (the memory-model work gestures at this).
3. **Recovery soundness** — a mis-recovered DFG edge silently changes the obligation; the bitcode
   cross-check (phase 3) plus the existing anti-vacuity/teeth layer are the mitigations.
