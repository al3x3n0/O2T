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
| 36 | RETURN-form rewrite anchor: upstream's dominant fold contract ("return the replacement value") -- E6 measured 48% of upstream candidates declining at the RIUW anchor. A non-bail `return <expr>;` in a fold-NAMED helper whose match inspects its INSTRUCTION-typed parameter is the rewrite; single-assignment `Value *T = ...;` locals inline as pure lets (mutation declines); +m_OneUse/m_Neg/m_Not/CreateNeg/CreateNot vocabulary. First VERBATIM upstream fold (`combineAddSubWithShlAddSub`, LLVM 18) recovered + proved + exhaustively reconciled. The first corpus run with the anchor caught an operand-subject MISATTRIBUTION (`simplifyOrLogic` -- a false refutation); the subject gate now pins it (`_find_fold_path(return_form=True)`, `_inline_lets`) | **Done** (same module) |
| 36b | Per-fold CASCADE slicing: a real fold function is a sequence of `if (match...) return ...;` arms -- each arm is now an independent obligation with its own path condition (`_iter_fold_paths`, `recover_folds_from_function`). Arm 0's refutation is a pass-level claim; a LATER arm's refutation is `standalone` only (earlier-arm exclusions unmodeled -- the witness may be unreachable), preserving zero false refutations. Plus an in-place-MUTATION screen (`setOperand`/`swapOperands`/flag setters decline the cascade), closing a silent misattribution gap that predates slicing. Fold-granular E6 accounting; the multiplier for phases 37/38 | **Done** (same module) |
| 38 | Multi-match conjunct COMPOSITION: `match(&I, ...) && match(I.getOperand(K), ...)` compose into ONE before-tree -- the operand conjunct spliced into slot K of the primary tree (on the structured trees, never string surgery). Sound bounds: the slot must be a bound `m_Value(NAME)` the splice retires (NAME referenced elsewhere declines); foreign subjects decline conjunct-wise. Comma-declarator lets (`Value *Op0 = I.getOperand(0), *Op1 = ...;`) normalize upstream's operand-local idiom into composable form. Wiring this CLOSED A GATE HOLE: the phase-36 subject regex captured `I` from `match(I.getOperand(0), ...)`, letting an operand match impersonate the instruction subject (`_compose_fold`, comma-anchored `_MATCH_SUBJECT_RE`). Residual multi-match frontier (measured): caller-contract parameter binding -- `foldX(I, Op0, Op1)` where `Op0 == I.getOperand(0)` only by the visitX calling convention -- unifies the remaining 61 with phase 37's 27 | **Done** (same module) |
| 37 | The simplifyXInst CALLER CONTRACT: `simplify<Op>Inst(Value *Op0, Value *Op1, ...)` is DOCUMENTED as "simplify `<op> Op0, Op1`" -- the name declares the phantom instruction and the operand ORIENTATION (unlike foldX helper arg order, which callers commute -- out of scope, stated). The phantom primary `match(&__P, m_<Op>(m_Value(Op0), m_Value(Op1)))` is synthesized and every arm handed to the phase-38 composer; orientation is honored on non-commutative ops (`0 - X -> X` refutes). Let-inliner refinements real cascades forced: nullptr-sentinel inits never substitute, reassigned locals skip per-name; `getType()` chains normalize in REWRITES only (the cast type-equality guard stays load-bearing). E6: upstream proved arms 1 -> 10 across 7 functions, incl. a 3-arm cascade (`_contract_arm`, `_SIMPLIFY_CONTRACT_RE`) | **Done** (same module) |
| 39 | Predicate-SET guards + domain-affirming drops: `isEquality/isUnsigned/isSigned(Pred)` constrain a bound predicate to a MEMBER SET -- the obligation is proved once PER MEMBER, instantiated consistently through matcher and the new generic `CreateICmp(Pred, ...)` rewrite handler; ALL cases must prove (`recover_pair_cases`), so a rewrite hardcoding one member REFUTES on the others (overreach caught by the split). Domain/ordering guards (`!isa<VectorType>`, `isIntOrIntVectorTy()`, `!isa<Constant>`, `!shouldChangeType`) drop, incl. through POSITIVE bails (whose path atom is now the textual negation). Building this exposed and closed the SIXTH latent hole: the fact vocabulary matches by substring, so a NEGATED fact (`!isKnownNonNegative(X)`) bound its POSITIVE premise -- an inverted guard and false-proof vector predating the phase; negated non-domain conjuncts now decline | **Done** (same module) |

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
