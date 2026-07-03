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
| 3b | Reconciliation, compiled half: lower a recovered fold to a `symbolic_llvm.h` shim harness, compile + symbolically execute it (`symexec/real_pass`), require the compiled-path verdict to match -- catches source-parse-level mis-recovery the shared-parse concrete check cannot | Next |
| 3 | Bitcode graph export + AST↔bitcode reconciliation (decline on mismatch) | Planned |
| 4 | Interprocedural: inline/summarize guard + value helpers (retire "blocked helper slice") | Planned |
| 5 | Loops over IR (`for (I : BB)`, `users()`): bounded unroll vs summarize | Planned |

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

1. **Loops over IR** — the pass-CFG has loops; bound against a fixed input-IR shape (like
   `loop_cfg_ir`) or summarize per-matching-instruction. Main decline frontier.
2. **In-place mutation semantics** — the `after` is a mutation of the IR graph (RAUW/erase/setOperand),
   needing a small IR-state semantics (the memory-model work gestures at this).
3. **Recovery soundness** — a mis-recovered DFG edge silently changes the obligation; the bitcode
   cross-check (phase 3) plus the existing anti-vacuity/teeth layer are the mitigations.
