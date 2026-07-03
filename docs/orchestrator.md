# The pass-aware orchestrator (O2T's front door)

Hand O2T LLVM pass sources (and/or pass names); it figures out *what each pass does* and
*schedules the right checks* for it — instead of you knowing which verifier to run.

```
tools/cv-orchestrate.py --source pass1.cpp pass2.cpp
tools/cv-orchestrate.py --source /path/to/Transforms --include Vendor --no-execute
tools/cv-orchestrate.py --pass indvars --pass instcombine
tools/cv-orchestrate.py --source mypass.cpp --pass dse --report out.json --fail-on-refuted
tools/cv-orchestrate.py --selftest
```

## How it works

`o2t/orchestrate/` is a three-stage pipeline:

1. **Classify** (`classify.py`) — read the pass `.cpp` and score it against the known
   transform **families** by the idioms it uses (no build needed), plus an optional pass-name
   hint. Families and their identifying idioms:

   | Family | Identifying idioms | Verification strategies |
   | --- | --- | --- |
   | `loop-scev-recurrence` | `getAddRecExpr`, `getMulExpr`, `getSCEV` | scev-intent, translation-validation |
   | `loop-structural` | `isLoopInvariant`, `hoist`, `rotateLoop`, `unswitch` | licm-source, licm-model, loop-cfg-ir, loop-induction, loop-simulation, loop-rotate-ir, loop-multiexit, loop-nested, translation-validation |
   | `peephole` | `m_*(`, `match(`, `replaceInstUsesWith` | symexec-fold-cascade, instcombine-ir, reassociate-ir, early-cse-ir, symexec-real-pass, modelcheck-real-pass, klee-symexec |
   | `memory-dse` | `isOverwrite`, `isRemovable`, `MemorySSA` | memory-source, memory-model, dse-ir, dse-facts |
   | `global` | `GlobalVariable`, `setInitializer`, `isGlobalInitializerDead` | globalopt-source, globalopt-model, globalopt-witness |
   | `cleanup-dce` | `isInstructionTriviallyDead`, `wouldInstructionBeTriviallyDead`, `isDeadLoopInstruction`, `AllocaInst`, `eraseFromParent` | dce-source, dce-model |
   | `vectorize-slp` | `TreeEntry`, `vectorizeTree`, `m_SplatOrPoison` | slp-source, slp-model, slp-ir, slp-transaction |
   | `promotion` | `PromoteMemToReg`, `isAllocaPromotable`, `IDFCalculator` | mem2reg-ir |
   | `cfg` | `MergeBlockIntoPredecessor`, `getSinglePredecessor`, `FoldTwoEntryPHINode`, `getIncomingValueForBlock` | cfg-source, cfg-shape |

2. **Plan** (`plan.py`) — map each family's strategies to a concrete, runnable check (which
   O2T tool, what prerequisites: z3 / opt / clang / the AST miner / optional model checker, and whether it runs on the
   pass source or on a test `.ll` through a real `opt` pass). Each check is marked **feasible**
   or **skipped-with-reason**, so coverage gaps are explicit, never silent.

3. **Dispatch** (`run.py`) — run the feasible checks against the real verifiers and aggregate
   their verdicts (`proved` / `sound` / `validated` / `refuted` / `partial` / `inconclusive` /
   `planned`). Wired end-to-end today: `scev-intent` → `cv-mine-pass-scev`,
   `symexec-fold-cascade` → `cv-extract-pass-model`, `translation-validation` →
   `cv-translation-validate`, `memory-source` → `cv-mine-memory-pass` (recover the pass's OWN
   memory transforms + guards and prove each over a theory of arrays — refuting an
   insufficient-guard fold from its source), `memory-model` → `cv-validate-memory` (the canonical
   theory-of-arrays DSE/forwarding contracts), `dse-facts` / `globalopt-witness` /
   `slp-transaction` → the generic
   intent pipeline (`cv-infer-optimization-intent | cv-validate-intent-candidates`), and
   `cfg-shape` → `cv-validate-cfg` (the SimplifyCFG diamond→select if-conversion contract, §6c),
   `dce-source` / `dce-model` → `cv-mine-dce-pass` / `cv-validate-dce`
   (dead-instruction, dead-loop-instruction, and unused-alloca erasure), and
   `modelcheck-real-pass` → `cv-modelcheck-real-pass` when CBMC or ESBMC is installed.
   **All strategies are wired** to real verifiers; the memory-dse family dispatches BOTH the deep
   memory-model proof and the source-intent dse-facts check.

## Third-party source-tree intake

`cv-orchestrate.py` accepts either individual pass sources or directories. Directory inputs are
expanded recursively over C/C++ source-like suffixes (`.cpp`, `.h`, `.inc`, etc.) and can be narrowed
with repeated `--include` / `--exclude` path-substring filters. With `--no-execute`, the command is a
fast triage pass: classify every selected file, list feasible/skipped checks, and write the same
machine-readable report shape used by executed audits.

Reports include a top-level `summary` with pass counts, classified/unclassified totals, raw check
verdict counts, source-level headline counts, and strategy/family rollups. Each pass entry also gets
a `headline` object computed from that source's primary-family checks: `refuted` dominates, then
`error`, then `proved`; plan-only audits report `planned` / `skipped`, and unsupported coverage is
`advisory`. Existing stdout keys (`passes`, `positive_verdicts`) are preserved, with additional
`negative_verdicts`, `error_verdicts`, `unclassified`, `headlines`, and `attention` counts for
automation. The full JSON summary carries `attention` lists for source-level `refuted`, `error`,
`advisory`, `skipped`, and `unclassified` cases.

Use `--summary-text out.txt` to also write a stable human-readable summary with headline/family/raw
verdict rollups, each source's primary headline, and the attention list. This is intended for CI logs
or PR comments; use the JSON report for automation.

When a compile database is available, `cv-orchestrate.py` can also launch the deeper external source
audit in the same run:

```
tools/cv-orchestrate.py --source vendor/lib/Transforms --no-execute \
  --compile-commands vendor/build/compile_commands.json \
  --audit-out o2t-deep-audit --mine-pass-impl-ir --modelcheck-intents \
  --report o2t.json --summary-text o2t.txt
```

This delegates to `cv-run-external-pass-audit.py`, stores its normal artifacts under
`--audit-out`, and embeds a `deep_audit` object plus `summary.deep_audit` rollup in the orchestrator
JSON. Use `--fail-on-deep-audit-error` to make a non-zero deep-audit exit fail the unified command.
The JSON summary also includes `readiness_matrix`, with per-family headline/check counts and, when
deep audit is enabled, proof status, implementation-IR status, transaction-graph status, readiness
diagnostics, and recommendations derived from the existing external-audit artifacts. The companion
`next_actions` list orders source-level refutations/errors, deep-audit failures, budget violations,
and readiness/modeling gaps into a compact follow-up queue. Deep-audit summaries also preserve
modelcheck width mode, selected widths, per-component summaries, the number of projected
modelcheck findings, the top five finding summaries, and `modelcheck_omitted_findings` when the
displayed list is truncated. If more than ten modelcheck findings are present, `next_actions`
adds a compact `modelcheck-findings-omitted` entry instead of silently dropping the rest.

`--modelcheck-intents` is optional and advisory. When enabled, deep audit lowers supported validated
`scalar-bv32` intent records into CBMC/ESBMC harnesses, mines SimplifyCFG diamond-to-select
`CreateSelect` folds into source-specific `cfg-bv32` obligations, mines source-recovered
DSE/store-forward folds into memory obligations, mines LICM hoists into value-invariance /
trap-safety obligations, mines GlobalOpt initializer-defaulting folds into observability
obligations, mines DCE dead-instruction, dead-loop-instruction, and unused-alloca erasures into observability
obligations, and mines SLP packs/reductions into lane-mapping / reassociation obligations. It
records `proved`, `refuted`, `unsupported`, `skipped`, and `error` counts in `run-summary.json`,
`real-pass-readiness.json`, and the external/orchestrator summary.
Refuted/error records are also projected into `modelcheck.findings` with marker, source location,
source function, harness, reason, and witness excerpt so CI summaries and orchestrator
`next_actions` can point at the exact failed obligation. Human-readable summaries print the first
few findings and include an explicit `... N more` line when more are available. Missing CBMC/ESBMC
is a skip, not a default audit failure.
By default the modelcheck bridge uses each record's native uniform bitvector width (`i1`, `i8`,
`i16`, `i32`, or `i64`, defaulting to `i32` when unspecified). Use
`--modelcheck-widths 8,16,32,64` to opt into width-parametric expansion through the same constant
porting logic used by the Z3 multiwidth prover; non-portable widths are reported as `unsupported`.
Use `--max-modelcheck-refuted 0` and/or `--max-modelcheck-errors 0` to turn this advisory signal
into an explicit CI budget. Once an audit baseline has been written, use
`--max-new-modelcheck-refuted 0` and/or `--max-new-modelcheck-errors 0` to fail only on modelcheck
findings that are new relative to that baseline.

For CI-style intake, opt-in failure budgets make the command non-zero only when requested:

```
tools/cv-orchestrate.py --source vendor/lib/Transforms --report o2t.json \
  --fail-on-refuted --fail-on-error --fail-on-unclassified
```

`--fail-on-refuted` follows source-level headlines, so noisy secondary-family checks do not fail a
source whose primary family proved. Use `--fail-on-any-refuted` when a stricter raw-check gate is
wanted. `--fail-on-advisory` gates unsupported primary-family coverage, and `--fail-on-no-positive`
requires at least one source-level `proved` headline.

When a single `--pass` name is supplied with multiple expanded sources, it is broadcast to every
source; otherwise pass names remain positionally paired with sources.

## The cfg-shape contract (diamond → select)

Most CFG simplifications are control-flow only (no value changes -- sound by construction).
The one that changes the value computation is **if-conversion**: a diamond
`br i1 %c …; merge: %r = phi [%a, then], [%b, else]` becomes `%r = select i1 %c, %a, %b`.
`cv-validate-cfg` runs the real `opt -passes=simplifycfg` on a diamond `.ll`, parses the
source merge-phi semantics and the optimized `select`, and proves them equal for **all inputs**
(Z3) -- with two-sided teeth: a swapped-operand or flipped-condition select is **refuted** with
a witness. This is closed-loop translation validation for control-flow value equivalence, the
analogue of the loop validator (§6) for CFG.

## Sweeping a multi-family pass-set (`sweep.py`, `cv-orchestrate-sweep.py`)

`cv-orchestrate` routes one pass; `cv-orchestrate-sweep` routes a whole curated set through the
front door at once and rolls the verdicts into a **coverage matrix**. The built-in manifest
spans every modeled family with a mix of sound sources, planted/under-guarded unsound ones, and
a known-gap advisory:

```
cv-orchestrate-sweep.py            # run the manifest, print the matrix
cv-orchestrate-sweep.py --report sweep.json
```

A source can score into several families; the sweep treats only the **primary family's**
strategies as authoritative for that source's headline. A secondary-family dispatch on a source
that isn't really that family (e.g. a peephole/memory cross-check on a CFG pass) is recorded
separately as cross-family advisory and **never flips the headline**. The roll-up reports:

- **families exercised** end to end (all nine: loop-scev, loop-structural, peephole, memory-dse,
  global, cleanup-dce, vectorize-slp, promotion, cfg);
- **deep verifiers dispatched** (scev-intent, symexec, memory-source/model, slp-source/model,
  cfg-shape, ...);
- **where the teeth fire** — planted-unsound *and* genuinely under-guarded sources the front door
  refutes from source (e.g. an FP reduction without fast-math, a pack whose extract lanes don't
  match its insert lanes, a DSE fold that deletes a store on a `noalias`-with-one-instruction
  guard, an initializer defaulted with no linkage/use guard, or an instruction erased without a
  trivially-dead guard);
- **honest gaps** — a family whose only verdict is `inconclusive`/`planned`, reported as advisory
  rather than a pass (the GlobalOpt dead-initializer gap is now closed by a deep semantic
  contract, so the current built-in sweep has none).

This makes the front door's reach legible and regression-gated (`orchestrate_sweep_fixture`):
every modeled family now reaches a deep verifier, the same sweep that proves the sound sources
also confirms the teeth fire on every unsound one, and a noisy secondary cross-check cannot mask
a sound primary verdict.

## Optional LLM brain (`brain.py`)

The feature classifier is deterministic and authoritative. When it is **ambiguous** about a
pass — no family clears the threshold, or the top two retained families are within a small
margin — an optional LLM can break the tie. It is **provider-agnostic** (O2T's existing
convention): `--llm-command` is any command that reads a JSON request on stdin and writes a
JSON verdict on stdout; no provider is baked in.

The LLM is **advisory**: its suggestion is recorded under `pass["llm"]` and a disagreement
with the deterministic primary is surfaced as a note — never silently applied. Without
`--llm-command`, or on any failure, the deterministic classification stands. The trust model
is unchanged: **formal verifiers decide soundness; the LLM only helps route.**

## Status

Gated by `orchestrate_fixture` (classify → plan → dispatch + the LLM-brain hook, with a stub
provider) and `orchestrate_sweep_fixture` (the multi-family coverage sweep). The classifier
covers 9 families; the wired strategies dispatch real verifiers (scev-intent, symexec,
memory-source/model, dse-facts, slp-source/model, slp-transaction, cfg-shape, globalopt-witness,
licm-source/model, dce-source/model, cfg-source, translation-validation,
modelcheck-real-pass when available), and the sweep confirms end to end that all nine families
route correctly, each reaches a deep verifier, and the teeth fire on every unsound source — with no
advisory gaps remaining in the built-in manifest.
