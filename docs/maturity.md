# O2T — maturity and limitations (honest self-assessment)

This document states, in one place, how mature O2T actually is and where its boundaries are. It
follows the repository's standing norm (`o2t-design.md` §9, the E6 decline taxonomy): the boundary
is public, and coverage is never overclaimed. Written 2026-07-21.

## What O2T is

O2T is a **research prototype** (~3 weeks of development, single author, ~28k LOC Python core)
demonstrating a specific method: *verify LLVM optimization passes from their source, and certify
the source-reading itself as a verification obligation.* It is a technique demonstrator with an
unusually rigorous soundness discipline — not a production verifier of real passes.

## What is genuinely solid

- **The formal proving core** (`mini_alive`, `formal_ir`) and the **integer-ring discharge**: the
  ℤ → ℤ/2ⁿ homomorphism gives all-width nonlinear proofs that bit-blasting cannot (E3: 0.1 s vs a
  10 s timeout). Correct and mature.
- **Closed-loop translation validation** (E1): proving *real* `opt` output equivalent to its input
  for all trip counts, with live miscompile-catching teeth. A working capability, not a mock. The
  **peephole analogue** now exists too (`observe_fold_fixture`): a fold recovered from source is
  emitted as IR, the real `opt -passes=instcombine` is run, and its output is checked against the
  recovered `after` — closing the source-intent ↔ actual-behavior loop (confirmed / not-fired /
  divergent), so the recovery is grounded against what the pass *actually does*, not just its intent.
  And **whole-function TV** (`corpus_tv_fixture`) proves the *entire* transformation sound over real
  code: for every function in LLVM's own InstCombine tests, real `opt` runs and the whole-function
  refinement is proved. Measured over `and/or/xor/add.ll` — **351/715 functions (49%) proved sound
  end-to-end, 0 false refutations**; the rest decline (353 unsupported memory/multi-block/vector,
  11 z3-timeout). This verifies the *composition* of whatever folds fired — a whole-function (not
  whole-pass) end-to-end result whose reach (49%) far exceeds source-recovery (Track A, ~4%) because
  it TVs the real IR directly, with the miscompile teeth biting. **Attribution** (`attribute_fixture`)
  welds the two tracks: for each proved whole-function transform it credits the recovered fold whose
  before/after matches it (SMT-exact, so no mis-attribution — an unsound fold is never credited),
  leaving the unexplained remainder as honest residue. 8/14 of the vendored corpus is explained by a
  *named* recovered fold; the residue is the enrichment work-list. And an **enrichment loop**
  (`enrich_fixture`) closes the harness thesis: it *grows* whole-function TV's instruction vocabulary
  (e.g. `llvm.bswap`) by proposing an SMT model and validating it against **`lli` execution** (the
  independent oracle) — the correct model is validated and lifts a transform from unsupported to
  proved, a wrong model is rejected before it can enable a false proof. This is how an autonomous
  harness can extend O2T's verifier without weakening it: the proposer suggests, an oracle it did not
  author decides. An **enrichment agent** (`enrich_agent_fixture`) closes the last mile: an LLM
  (deterministic stub in the gate, `claude -p` one flag away) *drives* the loop — diagnose the
  decline, propose the semantics, lli validates, install, re-run — lifting reach 0→2 on a validated
  bswap while a wrong proposal is rejected and never installed. The autonomous harness's hard parts
  (the soundness discipline) are built and gated; what remains is breadth (more instruction/shape
  enrichments) and a live-model run.
- **The soundness discipline**: decline-by-default, the recovery cross-check stack, anti-vacuity
  gates, mutation teeth, "no silent mis-model." E7 (zero-escape ablation) and E2 (52/52 mutants
  caught) measure it. More rigorous than most shipped verifiers.
- **Reproducibility**: ~449 gated fixtures; every paper claim mapped to a test
  (`claim-fixture-map.md`); seven of eight evaluation experiments measured.
- **The novel idea**: certifying the source-*reading* (§4 of the draft) — to our knowledge new.

## Where it is immature — with the numbers

| dimension | reality |
| --- | --- |
| **Peephole coverage** | Over 441 real upstream InstCombine/InstSimplify functions, O2T recovers and proves **12 fold arms across 8 functions** (~2%). The other 429 decline. |
| **"Passes" vs obligations** | O2T verifies isolated recovered **fold obligations**, not passes. Worklists, iteration, in-place IR mutation, and analysis dependencies are not modeled. The "verify the pass" framing is aspirational relative to the mechanism; "recover-and-verify fold obligations from pass source" is the precise claim. **Partially mitigated** on three fronts: observational validation (`observe_fold_fixture`) checks a recovered fold against the *real* `opt` output; whole-function TV (`corpus_tv_fixture`) proves a whole pass's per-function effect (the internal worklist fixpoint, black-box); **pipeline composition** (`compose_tv_fixture`) verifies a multi-pass *sequence* by composing per-pass TVs via refinement transitivity (localizing a miscompile to the culprit pass); and **module-level composition** (`module_tv_fixture`) verifies a whole-module transform incl. **function deletion** — a deleted function must be provably dead (internal, unreferenced), so `globaldce` of dead code proves while deleting live/observable code is refuted. What remains unmodeled is interprocedural **value flow** (inlining, IPSCCP) and **signature changes** (arg promotion). |
| **Recovery brittleness** | Recovery is regex + a hand-parser over C++. **Seven latent soundness holes** surfaced during phases 36–40 alone (each caught by the discipline, but their density shows the layer is young). |
| **Structured-tree front-end** | **Wired, two modes** (`o2t/mine/clang_tree.py`): (a) STUB-MODE parses in-memory fold source against a minimal stub (`clang_tree_fixture`); (b) **SOURCE-FILE MODE parses fold source against the REAL LLVM 18 headers** (`-ast-dump-filter` keeps the AST in KBs) and recovers a **VERBATIM upstream fold** (combineAddSubWithShlAddSub) byte-identical to the regex path, proved and reconcile-agreed, regex parser fully out of the loop -- **no stub approximation** (`clang_tree_source_fixture`). Both modes decline guards/mutations they cannot map, never dropping a premise. **Measured verbatim reach: 3/3 InstCombine E6 folds** recovered parser-free from their real `.cpp`s (up from 0/8 stub-mode): combineAddSubWithShlAddSub (return-form), foldXorToXor (a 3-arm cascade, each arm a real Boolean identity `(A&B)^(A|B) -> A^B`), and **foldIsPowerOf2OrZero (the two-icmp caller contract, both arms)** -- two-primary composition under the IsAnd-selected connective, `PredK == ICMP_*` guards, and `Cmp0->getOperand(0)` projection, each arm a real ctpop theorem. The one datum clang's typed AST elides -- the `m_Intrinsic<Intrinsic::ctpop>` id (it prints only `IntrinsicID_match`) -- is read at the DeclRefExpr span the compiler itself pins, not by a structural parse; a UGE-for-UGT mutation refutes with a witness through the real-AST path. The AST extractor covers **every non-verbatim fold shape the string path does** (faithful free-function renderings, each byte-identical to the regex path): the **simplifyXInst name contract** (phase-37: synthesize the phantom `m_<Op>(m_Value(Op0), m_Value(Op1))` the name declares, splice each arm's operand match; orientation honored -- a swapped 0-X->X refutes), **predicate-SET splits** (phase-39: an `isEquality(Pred)` guard expands to one case per member, all must prove; predicate overreach refuted), and the **operand/reduction collapse LOOPS** (phases 34-35: a phi-all-equal collapse and an associativity rebuild, obligations synthesized from the recognized loop structure; a non-associative reducer refutes, a non-collapse loop declines). **Shape parity with the string path is complete** -- the regex parser is retireable on the whole E6 shape vocabulary. These renderings grow shape coverage, NOT the verbatim count (which stays the 3/3 InstCombine E6 folds). (c) **WHOLE-.cpp mode** (`clang_tree_wholecpp_fixture`): recovers folds from the **UNMODIFIED upstream `InstCombineAndOrXor.cpp`** (~4830 lines) in its real lib context -- 5 arms (foldIsPowerOf2OrZero + the foldXorToXor cascade), all proved, byte-identical to the regex path. The only header not in the installed tree is `InstCombineInternal.h` (lib/, not include/), and it needs only installed public headers -- so the whole `.cpp` compiles with NO LLVM build; `InstCombinerImpl::` methods are accepted too. This lifts the claim from *vendored fold bodies* to *genuine pass source*. Skips hermetically unless an LLVM 18 source dir is located (`O2T_INSTCOMBINE_DIR`/`O2T_LLVM_SRC`). |
| **Loop benchmark** | E1's zero-false-refutation result is over **7 hand-crafted recurrence kernels**, not LLVM's test suite. |
| **Discrepancy finding** | No wild miscompile has been found; all discrepancy detection is on **injected** faults (E1/E2/E7 teeth). |
| **Agent (E8)** | Never run with a live model; trust invariants gated only with a deterministic stub. |
| **Ops** | LLVM-18-specific; hardcoded homebrew `opt` fallback; the full gate takes 30–170 min (contention-sensitive, not a fixed defect). |

## Structural declines (by design, stated)

Peephole: worklist fixpoints · in-place mutation semantics · interprocedural analysis
dependencies · read-write memory beyond the array fragment · floating point · dynamic-opcode
folds · `foldX` commuted operand-parameter binding. Loop: width-changing ops bound the ring
discharge; loop-nest transforms and vectorization are out.

## Prioritized roadmap toward maturity

1. **Clang-AST front-end: broaden verbatim reach** (highest leverage). Both modes wired
   (stub-mode + SOURCE-FILE mode against real LLVM 18 headers, recovering 3/3 InstCombine E6 folds
   parser-free -- return-form, a 3-arm cascade, the two-icmp caller contract, and the simplifyXInst
   name contract, predicate-set splits, the collapse loops -- SHAPE PARITY COMPLETE). Remaining:
   (a) lift verbatim reach beyond 3/3 -- the shape renderings prove the AST handles each shape, but
   most upstream folds need vocabulary O2T does not yet model (KnownBits/APInt); the walls and a
   bounded first slice are scoped in [roadmap-vocabulary-strata.md](roadmap-vocabulary-strata.md);
   and (b) ~~whole-`.cpp` recovery~~ **DONE** -- the front-end now parses an UNMODIFIED upstream
   InstCombine `.cpp` in its real lib context and recovers folds from it (`clang_tree_wholecpp_fixture`:
   5 arms from the genuine `InstCombineAndOrXor.cpp`, byte-identical to the regex path). The feared
   blocker was one header: `InstCombineInternal.h` (in lib/, not the release include tree) #includes
   only installed public headers, so the whole `.cpp` compiles against `<llvm-include>` + the
   InstCombine lib dir with NO LLVM build. Methods (`InstCombinerImpl::`) are accepted, not just free
   functions. This makes the verbatim-reach claim about *genuine pass source*, not vendored renderings.
   A **whole-file sweep** over two real files (`InstCombineAndOrXor.cpp` + `InstCombineAddSub.cpp`,
   **75 fold-shaped functions**) recovers-and-proves **3 (6 arms)** and declines 72, with **0 false
   proofs and 0 false refutations** — the honest reach-vs-decline picture over genuine source (see
   [e6-passir-corpus.md](e6-passir-corpus.md)). The 72 declines are the vocabulary wall
   (KnownBits/APInt/ConstantRange/FP), not parser failures.
2. **Broaden both benchmarks** to LLVM's own test suite (loops for E1/E4; a larger InstCombine
   slice for E6), so coverage and soundness numbers are over a representative corpus.
3. **Grow the guard vocabulary** (KnownBits/APInt/`decomposeBitTestICmp`) to lift E6 out of single
   digits — the measured next stratum after the binding ladder.
4. **Find one real discrepancy** (or reproduce a known LLVM loop-opt bug) to earn the
   miscompile-finder framing on real code.
5. **Run E8 live** and **de-flake the gate** (isolate the contention-sensitive campaign fixtures).

## Bottom line

As a **method demonstrator / paper artifact**: strong — novel idea, sound core, exemplary honesty
discipline, a defensible seven-experiment evaluation. As a **tool to point at a vendor tree
today**: not yet — 2% recovery, a brittle parser, a small benchmark, and no wild bug found. The
trajectory and the discipline are right; it is early.
