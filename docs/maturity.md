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
  for all trip counts, with live miscompile-catching teeth. A working capability, not a mock.
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
| **"Passes" vs obligations** | O2T verifies isolated recovered **fold obligations**, not passes. Worklists, iteration, in-place IR mutation, and analysis dependencies are not modeled. The "verify the pass" framing is aspirational relative to the mechanism; "recover-and-verify fold obligations from pass source" is the precise claim. |
| **Recovery brittleness** | Recovery is regex + a hand-parser over C++. **Seven latent soundness holes** surfaced during phases 36–40 alone (each caught by the discipline, but their density shows the layer is young). |
| **Structured-tree front-end** | **Wired, STUB-MODE** (`o2t/mine/clang_tree.py`): a Clang-AST producer walks `clang -ast-dump=json` against a minimal API stub and feeds `recover_pair` real matcher/rewrite trees, regex parser OUT of the loop -- byte-identical to the regex path (incl. assumptions) across unguarded, guarded, and return-form folds whose source is stub-compatible (`clang_tree_fixture`). **Honest reach: 0/8 on the VERBATIM upstream E6 functions** -- real folds reference API the minimal stub does not declare, so clang emits RecoveryExprs and extraction declines. This proves the parser-free PRINCIPLE, not production reach. Verbatim reach needs the real LLVM headers + full InstCombiner compile context (spike: the matcher tree parses cleanly on verbatim source with `-I <llvm-include> -ast-dump-filter`, but Builder/guard resolution needs the class context -- a compile-DB-scale effort, roadmap #1a). |
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

1. **Clang-AST front-end: from stub-mode to verbatim reach** (highest leverage). The parser-free
   PRINCIPLE is proven (stub-mode: byte-identical to regex on stub-compatible folds; the matcher
   tree also parses cleanly on VERBATIM upstream with real headers + `-ast-dump-filter`). The gap
   is production reach: parsing whole upstream `.cpp`s in their real compile context (compile DB /
   LLVM source tree) so Builder/guard/API references resolve, then filtering to each fold's method.
   Until then clang_tree recovers 0/8 verbatim E6 functions -- a stated limitation.
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
