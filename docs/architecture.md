# Architecture

O2T uses symbolic execution to synthesize compiler-pass tests through
a bounded IR generator.

## MVP Pipeline

```text
GeneratorConfig
  -> normalize into legal bounded knobs
  -> emit LLVM IR text
  -> record optimization-trigger coverage
  -> replay concrete configs as regression tests
```

The symbolic surface is the generator config, not LLVM's full object graph. That
keeps KLEE focused on decisions that shape the IR:

- arithmetic opcode
- right-hand operand mode
- optional canonicalization pattern
- branch predicate
- CFG shape: straight-line, diamond, nested diamond, unreachable tail, or
  switch-like branch chain
- memory shape: alloca/store/load, load-after-store, dead store, overwritten
  store, or unused alloca
- pointer, store, and load-use modes for bounded memory variants
- loop shape: counted loop, early exit loop, loop-invariant op, or dead body
  instruction
- loop trip, induction, and result-use modes for bounded loop variants
- feature bits such as `nsw` and select-vs-phi
- small constants

## Harness Contract

Each pass harness should follow the same shape:

```text
klee_make_symbolic(GeneratorConfig)
normalizeConfig
build abstract or concrete IR/module
scan pass probes or run pass under test
verify structural and semantic oracle
save failing config and generated IR
```

The default `instcombine_harness.cpp` path builds a tiny fixed-size abstract IR
model and scans optimization-like predicate probes. When built with
`O2T_WITH_LLVM=ON`, the LLVM backend parses the generated IR text into
an LLVM `Module`, verifies it, runs the shape-selected pass pipeline, and
verifies the optimized module.

The backend boundary is:

```text
GeneratorConfig
  -> runProbeBackend(config)
     -> runAbstractProbeBackend(config)
     -> runLLVMProbeBackend(config)
  -> PassProbeCoverage
  -> CV_PASS_PROBE_IF markers
```

`O2T_USE_LLVM_BACKEND` selects the LLVM backend path. Without
`O2T_WITH_LLVM`, that backend remains an unavailable stub so normal
builds stay dependency-free.

Native instrumentation events use the same `CV_PASS_PROBE*` macros as KLEE.
Instrumented predicates append fired marker strings to a thread-local recorder,
which lets `cv-llvm-probe` distinguish abstract expected markers from concrete
observed markers.

The probe oracle compares those sets. Empty observed markers are treated as
`not-instrumented`; once events exist, missing or unexpected markers become an
explicit mismatch unless the caller allows extra observed markers.

The default `cv-probe-demo` tool exercises this path without LLVM by firing
simulated instrumented predicates from `PassProbeCoverage`. It is the local
end-to-end check that expected markers, observed events, and oracle status agree.

## Coverage Markers

Pattern coverage is deliberately semantic rather than line-oriented:

- `probe.instcombine.add-zero`
- `probe.instcombine.mul-one`
- `probe.instcombine.xor-self`
- `probe.dce.dead-instruction`
- `probe.globalopt.dead-initializer`
- `probe.simplifycfg.diamond`
- `probe.simplifycfg.nested-branch`
- `probe.simplifycfg.unreachable-block`
- `probe.simplifycfg.branch-chain`
- `probe.mem2reg.promotable-alloca`
- `probe.mem2reg.store-load-forward`
- `probe.dse.dead-store`
- `probe.dse.overwritten-store`
- `probe.instcombine.redundant-load`
- `probe.cleanup.unused-alloca`
- `probe.loop.canonical-header`
- `probe.loop.induction-phi`
- `probe.loop.simple-trip-count`
- `probe.licm.invariant-op`
- `probe.dce.dead-loop-instruction`
- `probe.simplifycfg.loop-exit`

`probe.globalopt.dead-initializer` covers the conservative custom global
optimization: replacing a private/internal global initializer with a default
null initializer when source evidence proves the initializer is unobservable.
Whole-global deletion and global constructor path removal remain out of scope.
The miner records required, observed, and missing safety facts for this marker:
`initializer-dead`, `local-linkage`, and `no-uses`. Intent inference only emits
the formal `remove-global-initializer-if-dead-v1` contract when all three facts
are observed; otherwise the candidate stays unsupported with
`missing-global-initializer-safety-facts` and audit reports the missing facts.
Complete global-initializer records lower through the source-intent graph path
so graph structure, rewrite API, replacement kind, and safety facts are all
visible in the same evidence stream before validation. The intent registry uses
the `global-initializer-observable-v1` formal domain for this marker, which
checks the dead-initializer contract metadata before emitting the observable
equivalence proof.
`tools/cv-run-globalopt-coverage.py` runs a focused local coverage pass over an
explicit or discoverable `GlobalOpt.cpp`, writes a compact JSON/text summary,
and exits cleanly with `source-not-found` when no local source is available.
Optional budgets such as `--min-graph-derived`, `--max-unsupported`,
`--max-incomplete-safety`, and `--max-missing-fact local-linkage=0` turn the
same summary into a CI gate without making missing local LLVM sources fail.
The runner also writes `globalopt-baseline.json` and
`globalopt-baseline-diff.{json,txt}`. Supplying `--baseline` compares stable
GlobalOpt marker/location records across runs and enables regression budgets
such as `--max-new-unsupported 0` and `--max-new-incomplete-safety 0`.
With `--emit-witnesses`, proved complete records also produce deterministic
typed witness families under `witnesses/` for integer, pointer, and aggregate
initializers, where the only module change is defaulting the internal global
initializer. `--host-llvm-as` can assemble-check those witnesses, and
`--min-witnesses` / `--max-witness-failures` gate the materialized contract
evidence.
Each witness case also carries structural evidence: the global name, linkage,
initializer type, before/after initializer text, changed line count, and the
specific line numbers changed by the rewrite. Evidence building blocks
promotion when any required case has failed structural checks, even if the
top-level witness record is marked passed. Audit and promotion reports aggregate
both top-level witness status and per-case structural status so drift in the
typed witness contract is visible before an intent is promoted.
The normalized `globalopt_witness_contract` record is produced by the shared
GlobalOpt witness validator and preserved through evidence and promotion; flat
status fields remain compatibility views over that canonical contract.
`cv-verify-globalopt-witness-contract.py` independently checks the canonical
record against the registry-required witness family and reports missing cases,
model mismatches, structural failures, and compatibility-field drift. With
`--z3` and `--emit-smt`, it also writes per-case SMT obligations for the
canonical witness facts and records whether Z3 proves each obligation.
With `--alive-tv` and `--emit-alive2`, it also replays each materialized
before/after witness pair through Alive2 and records semantic replay status.
GlobalOpt source mining carries AST-derived safety provenance for
`initializer-dead`, `local-linkage`, and `no-uses`, including predicate family,
source expression, and source range; strict evidence requires this provenance
to validate before a dead-initializer intent can be promoted. Predicate
provenance verification is driven by the intent registry: a
`formal.predicate_provenance` contract names the required facts, expected
predicate families, and ordered provenance sources such as
`global.initializer.safety_provenance` or top-level `facts`. New optimizations
can opt into the same verifier by adding this contract and emitting fact records
at one of those declared sources.
The witness manifest carries source rewrite provenance, including
`setInitializer`, `Constant::getNullValue(GV->getValueType())`, the value-type
expression, and the global subject. Evidence, audit, promotion, campaign, and
workflow reports preserve those fields so a promoted intent can be traced back
to both the safety predicate and the concrete rewrite API.
`cv-run-verification-workflow.py --globalopt-coverage` includes this focused
gate in workflow planning and execution, writing artifacts under
`<workflow-out>/globalopt-coverage/`. When workflow intent evidence is enabled,
the GlobalOpt stage is planned before the source campaign and its
`globalopt-coverage.json` is passed into the campaign so witness status can gate
evidence and promotion.
`scripts/globalopt-strict-check.sh` bundles the strict fixture path for local
and CTest regression use; it cleans its dedicated output directory and Python
caches after successful runs.

The next layer should map these markers to concrete pass predicates, for example
InstCombine matcher success, legality checks, and profitability decisions.

## Near-Term Work

1. Map LLVM pass events or instrumented predicate callbacks back to concrete
   `probe.*` markers.
2. Compile KLEE harnesses to bitcode in the pinned Docker image and extract raw
   KLEE tests into normalized configs plus replayable `.ll` files.
3. Add an `opt` replay script that runs emitted IR through selected pass
   pipelines and verifies with `llvm-as`.
4. Add a pass-harness template with pluggable oracles.
5. Store every interesting KLEE test case as both normalized config and `.ll`.

## KLEE Artifact Flow

```text
named symbolic config fields
  -> KLEE .ktest files
  -> ktest-tool text dumps
  -> normalized .cfg files
  -> optional reduced .cfg files
  -> cv-replay generated .ll files
  -> Dockerized llvm-as / opt replay
  -> manifest.jsonl with coverage markers
```

KLEE uses named objects for each generator field instead of a single symbolic
struct. That avoids ABI padding issues and makes extraction independent of the
host compiler layout. The KLEE harness path also uses header-only normalization
and coverage predicates to avoid linking the config parser, IR text generator,
or C++ stream/string-heavy implementation into the symbolic bitcode.

## Abstract Pass Probe Flow

The current KLEE harness does not link LLVM pass code yet. Instead it builds a
small POD abstract function and scans it with pass-like predicates:

```text
symbolic GeneratorConfig
  -> fixed-size AbstractFunction
  -> scanOptimizationProbes
  -> KLEE covers probe.instcombine.* / probe.simplifycfg.* / memory / loop markers
  -> extracted config replays to concrete LLVM IR text
```

This gives KLEE branch conditions that resemble optimization predicates while
keeping the bitcode small. The next LLVM-backed layer should preserve the same
marker names and replace the probes with real pass predicates or pass-manager
execution.

The `CV_PASS_PROBE` and `CV_PASS_PROBE_IF` macros in
`PassInstrumentation.h` are the compatibility layer for that transition. They
can wrap real LLVM pass predicates while preserving the predicate value.

## Source Mining Flow

The source miner maps recognizable LLVM pass source predicates to probe markers:

```text
LLVM pass source
  -> cv-mine-pass-source.py
  -> optional cv-llm-candidate-pack.py / cv-llm-runner.py / cv-llm-import-candidates.py
  -> constraints/pass_constraints.json
  -> findings with marker, source line, constraints, instrumentation suggestion
  -> cv-constraints-to-configs.py emits reduced seed configs
  -> cv-run-campaign.py optionally runs opt-check-cases and summary generation
  -> optional cv-instrument-pass-source AST rewrite
  -> optional LLVM source patch using CV_PASS_PROBE_IF
```

This is intentionally heuristic. It recognizes first-pass patterns such as
`m_Zero`, `m_One`, `isInstructionTriviallyDead`, `UnreachableInst`,
`getSinglePredecessor`, and `SwitchInst`. It is a way to prioritize and scaffold
instrumentation sites, not a full C++ semantic analysis.

When Clang LibTooling is enabled, `cv-mine-pass-source-ast` can mine the same
marker families from AST matches and attach exact predicate and rewrite source
slices. The Python miner remains the default; AST findings use the same schema
and flow through the existing intent validation and replay evidence gates.
AST findings also carry a `source_intent_graph` provenance object. The graph is
an additive explanation layer over the existing `source_intent`: predicate
nodes record matched calls/operators, rewrite nodes record the extracted rewrite
action, bindings connect source symbols and vector parameters to formal
lowering, and guard nodes retain catalog-normalized proof effects. Intent
inference prefers complete and internally consistent graphs, records formal
symbol bindings under `evidence.formal_parameters`, then falls back to the
older source-intent, semantic-facts, and registry paths when graph lowering is
incomplete or inconsistent. The consistency check compares graph structure,
rewrite actions and replacements, scalar symbol bindings, and vector parameter
bindings against the source intent and lowered formal parameters.
Coverage auditing reports graph status, lowering, missing rewrites, bindings,
unsupported graph reasons, consistency failures, and any case where graph
lowering happened despite an inconsistency.
The evidence builder keeps the same graph status, lowering, consistency,
consistency errors, binding counts, and formal symbol bindings in its replay
join output. A failed graph consistency check prevents otherwise clean proof and
replay evidence from becoming `verified`; the record is marked `blocked` and
promotion treats it like any other failed evidence.

```text
LLVM pass AST
  -> source_intent_graph
  -> graph consistency check
  -> source_intent / semantic_facts
  -> formal IR candidate
  -> Z3 validation
  -> replay / oracle / Alive2 evidence join
  -> graph-safe promotion gate
  -> intent coverage audit
```

Vector findings can also carry formal parameters such as
`vector.shuffle.mask`, `vector.shuffle.splat_lane`, and
`vector.extract_insert.lane`. Intent inference copies the parameters it used
into `evidence.formal_parameters`, making lane-sensitive SIMD proofs auditable
instead of relying only on marker-level templates.
Large vectorizers can expose intent as an `optimization_transaction` instead of
a single predicate/rewrite pair. The transaction miner recognizes SLP-style
binary-op and min/max vectorization either inside one function or by stitching
helper summaries: candidate tree discovery, legality, profitability, vector
emission, and scalar replacement. Same-opcode scalar lanes, or scalar
compare/select min/max lanes, are packed; a vector operation is emitted; and
scalar users are replaced from vector lanes.
Intent inference lowers this transaction to vector formal IR that proves
`vec(op(a0,b0), ..., op(aN,bN)) == vop(vec(a), vec(b))` for supported fixed
widths 2, 4, 8, and 16. It records transaction
actions, participating functions, role provenance, legality guards,
profitability evidence, opcode, lane count, and vector width under
`evidence.formal_parameters`.
Scalable SLP binary and min/max transactions use the same transaction shape with
`base_lanes` and bounded `vscale` instances. Supported integer binops
`add`/`sub`/`mul`/`xor`/`or`/`and` and min/max opcodes
`smin`/`smax`/`umin`/`umax` lower to scalable vector formal IR over
`base_lanes * vscale`, including pack and result permutations through
`svshuffle`.
Min/max transactions use the `slp-vectorize-minmax` kind with opcodes `smin`,
`smax`, `umin`, and `umax`; lowering proves scalar lane `icmp + select` forms
against vector `vsmin`, `vsmax`, `vumin`, and `vumax`.
Reduction transactions use the `slp-vectorize-reduction` kind for fixed-width
integer reductions: arithmetic `add`/`mul`, bitwise `and`/`or`/`xor`, and
integer min/max `smin`/`smax`/`umin`/`umax`. The miner records the packed
operand, reduction emission source, replacement action, reduction lane count,
and result provenance. Intent lowering proves the scalar reduction tree against
formal `vreduce_*` operations, including permutation provenance through
`vshuffle` when the pack order differs from scalar lane order. Widened integer
reductions are proved when mining records explicit input, accumulator, and result
bit widths from source expressions such as `Type::getInt32Ty` or
`IntegerType::get`; the formal IR models the extension and optional truncation
around the reduction. Ambiguous or conflicting width provenance is preserved in
fallback evidence. Fixed integer reductions are proved for 2, 4, 8, 16, 32, and 64
lanes. Ordered FP32 `fadd`/`fmul` reductions lower to SMT `Float32`
with RNE folding. Scalable reductions lower to bounded proof instances
over `base_lanes * vscale`, currently for `vscale` values 1, 2, and 4, and reuse
blockwise lane mapping provenance for scalable pack permutations. Scalable
widened integer reductions reuse the same extension/truncation semantics when
the miner recovers complete width provenance. Ordered scalable FP32 `fadd` and
`fmul` reductions with identity lane order lower to bounded `Float32` proof
instances with RNE folding. Scalable ordered FP reductions use the same bounded
RNE proof strategy. FP reduction permutation/reassociation and explicitly
unordered or fast-math reductions are not treated as IEEE equality; when miners
recover explicit policy evidence, intent inference emits a `relaxed-fp-policy`
contract instead of SMT. Scalable widening reductions are modeled when input,
accumulator, and result width provenance is complete; ambiguous scalable
widening and FP reductions without enough policy evidence are intentionally
reported as unsupported or fallback evidence gaps.
The source miners still recognize unsupported reduction construction sites and
attach explicit transaction consistency errors such as
`unsupported-scalable-widening-reduction` for incomplete scalable widening
provenance. Intent inference preserves those reasons in fallback evidence, and
coverage audit groups them separately so unsupported reduction families remain
visible without being promoted as proved semantics.
Coverage audit also turns reduction consistency failures into prioritized gap
recommendations: FP permutation without reassociation evidence and unordered FP
records point at missing policy evidence, ambiguous or incomplete widening records
point at width-provenance mining, conflicting width records point at source
inspection, and unsupported wider-lane records point at adding wider vector
formal domains.
Transactions also carry lane mapping provenance. Identity mappings lower directly;
permutation mappings lower through formal `vshuffle` masks before and after the
vector operation so reordered packs are proved against the original scalar lane
order. Operand-level pack provenance records the direct `packOperand` source or
resolved helper-pack builder for LHS and RHS lanes; formal lowering requires
both operands to expose the same valid identity or permutation map. Result-lane
provenance records how vector result lanes replace scalar users and emits
per-lane `{result,lhs,rhs}` pairing evidence.
Transactions can also carry an optional `transaction_graph` for multi-node SLP
intent. The supported graph shape is an integer DAG over binops `add`, `sub`,
`mul`, `xor`, `or`, `and`, `shl`, `lshr`, and `ashr`, integer min/max nodes
`smin`, `smax`, `umin`, and `umax`, and integer cast nodes `zext`, `sext`, and
`trunc` with recovered target bit widths. It also supports integer `icmp` mask
nodes feeding `select` nodes for lane-wise blends, plus explicit `shuffle`
nodes with recovered fixed `mask` or scalable `base_mask` evidence, and
fixed-width `extract`/`insert` nodes with recoverable constant lane indexes.
Packed vector operations feed
later vector operations through explicit producer-consumer edges. Graph operands may
also be lane-uniform integer constants, which lower to scalar `bvconst` terms
before vectorization and to `vsplat` or `svsplat` terms after vectorization.
Graphs can also mark operands as `memory-pack` when source mining recovers
scalar `CreateLoad(Base[K])` pack lanes. Contiguous packs use
`transaction.graph.memory_contract = "contiguous-load-pack-v1"`; unique static
non-contiguous offsets use `static-gather-pack-v1` with `address_order` and
optional `address_stride` provenance. These memory packs lower like normal
vector operands but carry packed-lane memory provenance instead of claiming a
full SMT array memory model. Scalable load-only memory packs are accepted as
scalable symbolic vector inputs and marked with
`transaction.graph.scalable_memory_pack`; masked scalable load packs with
complete named-mask or repeatable mined mask-condition provenance lower to
`svselect` and are also marked with
`transaction.graph.scalable_masked_memory_pack`. Scalable store
sinks lower to bounded per-`vscale` observable stored-value proofs and are
marked with `transaction.graph.scalable_store_sink`; masked scalable store
sinks use either named masks or repeatable mined mask conditions in
`svselect(mask, stored_value, old_memory_value)` to preserve masked-off lanes
and are also marked with
`transaction.graph.scalable_masked_store_sink`.
Memory-pack operands also carry helper-local side conditions such as
`memory_safety_status`, `no_intervening_store`, `alias_scope`,
`memory_effect_window`, and `load_order`; graph formalization requires complete
side-condition evidence. Intent
inference validates the acyclic graph, lowers nodes topologically, and builds
composed formal IR, for example proving
`vec((a0+b0)*c0, ...) == vmul(vadd(vec(a), vec(b)), vec(c))`. Graph formal
provenance points each semantic term back to graph nodes, graph edges, operands,
lane maps, and scalar-lane witnesses, so the existing transaction formalization
verifier can diff and coverage-check graph-derived proofs without a separate
trust boundary. Graph v1 supports fixed-width vectors and bounded scalable
vectors over `base_lanes * vscale` for `vscale` values 1, 2, and 4, and falls
back unless all operands, producer references, and the single root output are
resolved. Identity and
single compatible permutation lane maps are supported: graph leaves are packed
with `vshuffle` or `svshuffle` when the source uses a reordered lane map, and
the graph root is shuffled back through the inverse result map before comparison
with scalar results. Operand-pack indexes may use the same statically recovered
integer constants and simple static expressions as lane-frame indexes. Explicit
graph shuffle masks are interpreted in the packed
vector lane frame: scalar-side lowering evaluates the corresponding packed
result lane and maps pack leaves back through the lane map before comparing with
the vector rewrite. Min/max graph nodes require canonical integer compare/select
or direct vector min/max builder evidence; non-canonical select order is modeled
as a general select graph when the condition and value operands resolve.
General select graph nodes require a graph-produced integer `CreateICmp`
condition and lower to scalar `ite` before vectorization and `vselect` or
`svselect` after vectorization.
Shuffle graph nodes require a statically recoverable mask from an integer array
or inline braced literal. Fixed vectors support one- and two-input
`CreateShuffleVector` nodes and lower to scalar lane selection before
vectorization and `vshuffle` after vectorization. Scalable vectors support
single-input permutation shuffles and static two-input blockwise shuffles, both
lowering to `svshuffle`; unresolved masks leave the graph absent with an
explicit reason.
Extract/insert graph nodes use lane indexes in the packed vector lane frame and
lower to `vextract`/`vinsert` for fixed-width vectors or `svextract`/`svinsert`
for scalable vectors; variable lane indexes leave the graph absent with an
explicit unresolved reason.
Memory-pack operands require statically recoverable, non-volatile, non-atomic
load evidence. Duplicate gather lanes, variable gather indexes, intervening
stores, unknown memory-effect calls, ambiguous bases, pointer mutation,
volatile/atomic memory, unresolved lane addresses, and unresolved scalable
masked memory leave the graph absent with explicit reasons.
Graphs can also record `memory-store` sinks when source mining recovers stores
of the graph root to statically addressed lanes. Contiguous stores use
`transaction.graph.store_contract = "contiguous-store-pack-v1"`; unique
non-contiguous stores use `static-scatter-store-pack-v1` with `address_order`
and optional `address_stride` provenance. When fixed-width load packs or store
sinks have complete side conditions, formalization can switch to
`transaction.graph.memory_model = "bounded-lane-memory-v1"`: memory is modeled
only over the finite lane address set, using `mem_load`/`mem_store` terms and
comparing the observable values at mined store addresses. Addresses use
`base-offset-addresses-v1`: the miner records base+offset identities, same
base+offset pairs share one symbolic address, and distinct bases require mined
`noalias` evidence that lowers to address disequality assumptions. Accepted
source guards include direct `noAlias`/`isNoAlias`-style calls,
`isKnownNoAlias`, negated `mayAlias`, and `AA.isNoAlias`/`AA->isNoAlias`
method calls in either argument order. This proves the load/compute/store memory
transition without claiming general alias analysis.
Masked fixed-width memory is represented by `masked-contiguous-load-pack-v1`,
`masked-static-gather-pack-v1`, `masked-contiguous-store-pack-v1`, and
`masked-static-scatter-store-pack-v1`. Masked loads lower false lanes to the
mined pass-through value; explicit `UndefValue::get` or `PoisonValue::get`
passthrough, and recognized omitted-passthrough overloads, lower to fresh
symbolic lane values. Masked stores lower false lanes to the old memory value at
the same address, preserving memory while still checking the bounded observable
transition. The miner accepts both direct
`CreateMaskedLoad`/
`CreateMaskedStore` calls and simple guarded idioms where a lane is initialized
from pass-through and overwritten by `CreateLoad` under `if (Mask[i])`, or where
`CreateStore` is guarded by `if (Mask[i])`. Guarded idioms may also use named
mask temporaries such as `if (M0)` when `M0` is recovered from mask provenance.
When mask lanes come from simple per-lane `CreateICmp` temporaries, the graph
records `mask_conditions` and formalization lowers the mask to the recovered
comparison instead of an opaque `MaskN != 0` predicate. Simple
`CreateAnd`/`CreateOr` combinations of recovered mask temporaries are preserved
as boolean mask-condition trees and lower to formal boolean
conjunction/disjunction. Recovered masks may also flow through simple
`CreateNot` and `CreateSelect` temporaries, which lower to formal boolean
negation and `ite`. Simple boolean mask constants and `CreateXor` with a
recovered true/false mask are normalized to constant or negated mask-condition
trees. Complete local `if/else` assignments to one mask temporary, including
nested complete branches, lower to select-shaped mask-condition trees; missing
paths remain unresolved mask provenance. Mask temporaries may use `Value *`,
`Value *const`, or `auto *`
declarations, split declaration/assignment, local aliases, and either
`Builder.Create*` or `Builder->Create*` calls. Conflicting non-alias writes to a
mask temp are rejected as unresolved mask provenance.
Masked-load pass-through values may be direct lane-indexed arrays, omitted or
explicit undef/poison/null operands, local aliases of those forms, or simple
same-file helper returns that materialize to those same shapes. Unknown
pass-through helpers remain blocked with a source-located `missing-passthru`
diagnostic.
Lane-aligned dynamic mask array indexes such as `Mask[Lane]` are
preserved as symbolic indexed-mask guards, so dynamic mask selection can be
proved without claiming a concrete mask lane. Simple same-file helpers are
expanded with bounded parameter
substitution, so helper-returned masks, nested helper memory packs,
helper-returned compute nodes, and helper store sinks can feed the same graph
contracts. Lane-local opaque conditional helper returns are normalized into
select-shaped mask conditions with opaque guard variables. Recursive,
ambiguous, unresolved, or non-normalizable helper slices keep the graph absent
with the existing unsupported or unresolved reasons plus structured
`transaction_graph_absent_diagnostics` containing helper name, role, source
snippet, expansion stack, and depth. Coverage audit and pass-source audit
surface the top helper-slice diagnostics so source-mining blockers point at a
concrete helper path rather than only an aggregate reason.
Lane-frame indexes for mask arrays, memory offsets, pass-through arrays,
and extract/insert nodes may be literal lane numbers, statically recovered
integer constants, or simple static integer expressions over those constants.
Masked loads, masked stores, and guarded load/store forms can also retain safe
symbolic mask indexes such as `Mask[Lane + 1]` or `Mask[(Lane & 3)]` as
indexed-mask guard evidence; formalization canonicalizes them into stable
symbolic guard variables such as `Mask_Lane_plus_1` and `Mask_Lane_and_3`.
Safe symbolic mask indexes may reference statically recovered integer constants
such as `Mask[Lane + MaskDelta]`; the miner normalizes those constants in the
recorded index while keeping calls and unknown identifiers blocked.
Gather loads can retain the same safe lane-index expressions as address terms
under `symbolic-gather-pack-v1` or `masked-symbolic-gather-pack-v1`. These
symbolic gather records preserve load-address provenance and lower through
stable symbolic address variables when bounded memory modeling is enabled.
Store sinks can also retain safe symbolic address terms under
`symbolic-store-pack-v1` or `masked-symbolic-store-pack-v1`; repeated normalized
`(base, index)` terms reuse one address variable so sequential overwrite
semantics are preserved.
For scalable masked memory, mixed static/symbolic indexed-mask groups lower
through `svindexed_mask`, which expands the base-lane mask entries blockwise
for each bounded `vscale` proof instance. Heterogeneous scalable mask tuples
whose base lanes do not share one repeatable expression lower through
`svmask_tuple`; each base-lane entry retains its recovered predicate,
boolean combinators, and lane-indexed operands before blockwise expansion.
Tuple-backed formalizations set `transaction.graph.scalable_mask_tuple`, and
coverage audit reports them separately from uniform scalable masks and
`svindexed_mask` symbolic-index masks.
Mask indexes with calls, dereferences, assignments, comma expressions, or other
unsafe syntax, unresolved masks, and missing pass-through operands leave
explicit fallback reasons.
Scalable masked-memory fallbacks also preserve source-located diagnostics, so
the broad `unsupported-scalable-masked-memory` bucket can separate unsupported
mask syntax from unresolved mask provenance, unsafe indexes, and missing
pass-through operands.
Coverage audit normalizes masked-memory fallback evidence into
`transaction_mask_blocker_kind` values: `unresolved-mask`,
`unsafe-mask-index`, `missing-passthru`, `scalable-mask-syntax`, `alias`,
`volatile-atomic`, and `helper-slice`. The raw absent reasons remain in the
report for compatibility, while `masked_memory_coverage_gaps.blocker_kinds`
drives the next modeling target.
Audit records also preserve `transaction_mask_blocker_detail` and aggregate
`masked_memory_coverage_gaps.blocker_details` so unresolved-mask work can
separate `incomplete-branch-assignment`, `conflicting-assignment`,
`unresolved-helper-call`, `unknown-mask-expression`, and helper-slice reasons.
When recovery fails at a specific masked load/store use, absent diagnostics also
prefer the failing mask source, role, and temporary name over whole-function
classification, so reports can point at the exact unsupported mask operand.
Variable store indexes, duplicate scatter lanes, ambiguous store bases, pointer
mutation, volatile/atomic stores, unknown memory-effect calls, and scalable
store graphs leave explicit fallback reasons.
Variable store-index fallbacks also carry `unsafe-store-index` diagnostics with
the failing store expression, store base, and `memory-store` role so reports
identify the unsupported sink rather than only the broad reason.
Variable gather-index fallbacks similarly carry `unsafe-gather-index`
diagnostics with the failing load expression, gather base, and `memory-pack`
role.
Coverage audit also aggregates memory-address blockers separately from masked
memory blockers, including unsafe gather/store indexes, duplicate gather/scatter
lanes, ambiguous bases, and unresolved address recovery.
Cast graph nodes require explicit recoverable `Type::getInt*Ty` or
`IntegerType::get(..., bits)` width evidence; unresolved widths leave the
transaction graph absent. Shift nodes require vector-packed shift amounts with
the same lane width as the shifted value.
Constant graph operands require recoverable integer width and non-negative
literal value evidence from `ConstantInt::get`, `Constant::getNullValue`, or
`Constant::getAllOnesValue`; unresolved constants leave the graph absent.
The miner can build this graph across simple helper boundaries when
reachable helper functions directly return complete pack builders or vector
`Create*` nodes. Helper fallback extraction uses balanced braces, so
block-bodied guarded idioms remain visible even when the helper is not retained
as a summarized SLP role. Unresolved helper operands leave the graph absent and
preserve the existing transaction fallback path.
Transaction lowering is gated on consistency: legality and emission opcode
sources must agree, and the lane source must match a supported fixed-vector
width or scalable base-lane count. Missing, mismatched, duplicate, malformed,
or unsupported result pairings are kept as evidence but fall back instead of
getting a formal proof.
The replay evidence join preserves compact transaction provenance: kind, opcode,
lane count, lowering, consistency, lane-map presence, result-lane-map presence,
scalar lane-pair count, and source-slice contract status/checks when present. A proved
transaction without replay stays
`uncovered`; only proved transaction evidence covered by replay/oracle/Alive2 can
become `verified`. A failed source-slice contract check blocks verification even
if the replay is otherwise clean, and promotion copies transaction provenance
through the same verified-evidence gate. Helper-slice absent diagnostics are also
preserved in evidence and promotion reports for blocked or unsupported records.
Contract checks carry named witnesses or
counterexamples such as role reachability, predicate expansion, control-flow
reachability, and lane-map binding.
`cv-verify-source-slice-contract.py` is the independent trust-boundary checker:
it recomputes those structural checks from the emitted source slice and reports
mismatches when a mined contract claims a status that the slice evidence does not
support. `cv-run-pass-source-audit.py --verify-source-slice-contracts` runs this
checker after mining and writes the JSON/text verification artifacts. Evidence
building can consume the verifier report; mismatches become blocked evidence and
therefore cannot be promoted.
`cv-verify-transaction-formalization.py` is the next trust-boundary checker: it
recomputes formal IR from each mined `optimization_transaction` and compares it
with `intent_candidate.formal`. `cv-run-pass-source-audit.py
--verify-transaction-formalization` runs it after intent inference; failed
formalization checks are propagated through evidence, coverage auditing, and
promotion as blocking mismatches.
The same verifier also reports formal provenance coverage: every semantic
formal path is checked against the mined transaction facts that produced it.
Coverage is diagnostic by default, but `cv-run-pass-source-audit.py
--max-incomplete-formal-provenance` can make incomplete provenance a CI budget.
Intent coverage auditing treats proved source-derived transactions as covered
even when no static marker registry entry exists yet. It reports transaction
lowering, kind, opcode, lane count, consistency, lane/result map presence, scalar
lane-pair evidence, and top consistency errors in a dedicated optimization
transaction section.
For real LLVM source trees, `cv-run-pass-source-audit.py` wraps the AST miner
with a mandatory `compile_commands.json`, expands selected source files, skips
files absent from the compilation database, then runs intent inference, Z3
validation, and intent coverage auditing without instrumentation or replay.
It also emits `run-summary.json` and `run-summary.txt` with source selection,
finding, proof, promotion, recommendation, transaction, filter, and budget-gate
status. Marker filters can narrow mined findings before intent inference, and
optional budgets make the audit suitable for CI while still preserving all
normal artifacts on failure.
The same run also writes `real-pass-readiness.json` and
`real-pass-readiness.txt`. These reports are intended for first-contact real
pass slices: they summarize selected source files, mined transactions,
transaction graph present/absent counts, graph absence reasons, memory/store
contracts, and SLP transaction IR graph-lowering status when
`--emit-slp-transaction-ir` is enabled. A typical selected-file probe is:

```sh
python3 tools/cv-run-pass-source-audit.py \
  --compile-commands /path/to/llvm/build/compile_commands.json \
  --out /tmp/o2t-slp-audit \
  --ast-miner build-clang-tools/cv-mine-pass-source-ast \
  --emit-slp-transaction-ir \
  --validate-slp-ir \
  --llvm-as /path/to/llvm-as \
  /path/to/llvm-project/llvm/lib/Transforms/Vectorize/SLPVectorizer.cpp
```

For third-party LLVM pass projects, `cv-run-external-pass-audit.py` is a thin
wrapper around the same audit pipeline. It keeps the normal artifacts under
`<out>/audit/` and adds a compact external-facing summary:

```sh
python3 tools/cv-run-external-pass-audit.py \
  --compile-commands /path/to/build/compile_commands.json \
  --out /tmp/o2t-external-audit \
  --mine-pass-impl-ir \
  /path/to/project/lib/Transforms
```

When a local LLVM checkout is not already available, the exact upstream source
file can be staged for a first-readiness experiment from GitHub:

```sh
mkdir -p /tmp/o2t-llvm-slice/llvm/lib/Transforms/Vectorize
curl -L \
  https://raw.githubusercontent.com/llvm/llvm-project/main/llvm/lib/Transforms/Vectorize/SLPVectorizer.cpp \
  -o /tmp/o2t-llvm-slice/llvm/lib/Transforms/Vectorize/SLPVectorizer.cpp
```

That downloaded file still needs a matching `compile_commands.json` or a
minimal compile database with the include flags needed by the AST miner.

For the first exact-source DSE readiness pass, use the dedicated wrapper. It is
non-networked and treats the upstream file as optional when `--allow-missing` is
set, which keeps CI independent of a local LLVM checkout:

```sh
python3 tools/cv-run-upstream-dse-readiness.py \
  --upstream-dse-source /path/to/llvm-project/llvm/lib/Transforms/Scalar/DSE.cpp \
  --compile-commands /path/to/llvm/build/compile_commands.json \
  --out /tmp/o2t-upstream-dse \
  --mine-pass-impl-ir
```

The wrapper writes `upstream-dse-readiness.{json,txt}` with DSE-only finding
counts, matched/blocked/source-incomplete buckets, blocker reasons, missing
source facts, and sample unsupported exact-source idioms. If no compile database
is supplied, it creates a minimal one for source-mining experiments; real LLVM
checkouts should prefer the build tree compile database.
Optional budgets such as `--min-dse-matched`, `--max-dse-blocked`,
`--max-dse-source-incomplete`, and `--max-new-dse-unsupported` make the wrapper
fail on readiness regressions. `--write-baseline` and `--baseline` create a
wrapper-local DSE baseline and `upstream-dse-baseline-diff.{json,txt}` so exact
upstream audits can track newly unsupported DSE findings without depending on
global audit baselines.

Audit runs also emit `audit-baseline.json` and `baseline-diff.{json,txt}`.
Supplying a prior baseline compares stable file/line/marker/transaction keys and
reports new, resolved, and changed records, including newly unsupported findings
and new fallback transactions for CI gates.
Source miners emit `semantic_facts` for recognized scalar, CFG, memory, loop,
fixed-vector, and scalable-vector predicates. These facts describe the
operation, shape, identity, and rewrite in a small source-derived model; intent
inference lowers them to formal IR before falling back to marker templates.
Guard helpers are normalized through `constraints/guard_semantics.json`.
Catalog-backed guards can become SMT assumptions, structural preconditions, or
profitability-only evidence. The catalog carries simple text recognizers for
fallback mining, while AST mining keeps explicit C++ matchers for callee and
subject extraction. AST mining reads the same catalog with `--guard-semantics`
and uses it as the source of truth for guard roles and proof effects. Both text
and AST mining recognize `isGuaranteedNotToBePoison(X)` as a formal
`not-poison` assumption, `isKnownNonZero(X)` as a non-zero value assumption,
and `isKnownPositive(X)` / `isKnownNonNegative(X)` as signed comparison
assumptions. `MaskedValueIsZero(X, Mask)` becomes a known-zero bitmask
assumption, and `isKnownPowerOf2(X)` / `isKnownToBeAPowerOfTwo(X)` become
non-zero single-bit assumptions. `hasOneUse()` is retained as a structural
precondition with no SMT effect.
Intent inference applies guard semantics from catalog `formal_effect` and
`formal_effect_args` metadata, not from hard-coded guard names, so changing the
catalog changes lowering policy consistently across text and AST findings.
Unknown guard helpers remain side conditions and block promotion until they are
modeled.
Catalog-derived value assumptions pass through a small assumption algebra before
SMT lowering. The algebra deduplicates compatible facts, merges known-bit masks,
records implications such as power-of-two implying non-zero, and rejects
contradictory preconditions before they can produce vacuous proofs.
The semantic contract is checked across all three registries:
`constraints/pass_constraints.json`, `constraints/semantic_facts.json`, and
`constraints/optimization_intents.json`. Run `scripts/check-registries.sh` to
validate JSON syntax, semantic fact coverage, and, when Z3 is available, formal
intent proofs. Set `CV_SKIP_Z3=1` to exercise only the syntax and semantic
coverage checks.

For scalar rewrites, inferred candidates include a compact `scalar-bv32` formal
intent IR. The registry also carries `cfg-bv32` formal intent for the
`simplifycfg` marker family, `memory-bv32` formal intent for value-level memory
preservation, `loop-bv32` formal intent for closed-form loop result
preservation, `vector-bv32x4` formal intent for fixed-width SIMD rewrites, and
`scalable-vector-bv32` formal intent for bounded scalable SIMD rewrites.
Validation lowers these expression trees to SMT-LIB and asks Z3 for a
counterexample to result equivalence. Fixed vector values are represented as
sort-checked tuples, with reduction proofs currently allowing 2, 4, 8, or 16
lanes; scalable records expand to bounded
`base_lanes * vscale` proof instances, for example vscale 1, 2, and 4. Vectors
are packed only for final SMT equality. Optional poison-aware records carry
`poison_variables` and can request
`refinement: "refinement"` to prove that defined inputs produce defined,
value-equivalent outputs. Records may also carry `assumptions`, currently used
for catalog-derived `not-poison`, non-zero, signed comparison, known-bits, and
power-of-two preconditions; these assumptions are conjoined with the SMT
counterexample query after assumption-algebra normalization. The lowerer
propagates poison through scalar and vector operations including
add/sub/mul/xor/and/or, treats `freeze` as a stable
defined value, and rejects raw
`undef` use. Legacy marker-specific SMT generation remains as a compatibility
fallback for older scalar candidates. The lowering is centralized in
`tools/cv_formal_ir.py` so candidate validation and registry validation use the
same semantics. The scalar algebra and vector lane/shuffle registry records opt
into poison-aware refinement today, while records without explicit
`poison_variables` keep the older defined-input equality model.
`cv-validate-intent-registry.py` proves every current registry record.

The formal IR lowerer performs basic sort checking before emitting SMT: scalar
`before` and `after` values must be bit-vector expressions, vector values must
have four lanes, comparisons produce booleans, poison variables must be declared
formal variables, and `ite` requires a boolean condition with matching branch
sorts. Source-inferred CFG, memory, loop, and vector candidates attach deep
copies of registry formal blocks. Scalar candidates prefer rewrite-sensitive
formal inference, then fall back to registry formal IR only when predicate
mining gives strong marker-specific evidence. Empty rewrite bodies are still
unsupported.

Validated inferred intents can be promoted into
`constraints/optimization_intents.json`, but evidence-aware promotion requires a
verified replay join first. `cv-build-intent-evidence.py` combines proof status,
semantic replay, probe oracle results, optional Alive2 results, and source
intent graph consistency plus transaction source-slice contract status/checks; the
promoter then skips any ready candidate whose evidence is blocked, uncovered,
unsupported, or absent. Promoted evidence keeps compact graph and contract
provenance so reviewers can see whether a registry change came from complete,
consistent source evidence or from an older fallback path.
Campaign runs can pass focused GlobalOpt witness output into the evidence join
with `--globalopt-coverage`; `--require-globalopt-witnesses` and
`--max-globalopt-witness-failures` make missing or failed dead-initializer
witnesses block otherwise proved GlobalOpt evidence.
`cv-audit-intent-coverage.py` reports guard handling separately from formal
coverage: modeled guards, structural-only guards, profitability guards, and
unsupported guard kinds with their source records.
`cv-audit-intent-coverage.py` is the complementary measurement layer: it reads
validated intent candidates, compares them with the intent and semantic-facts
registries, and reports source-derived formal coverage, registry fallbacks,
unsupported reasons, transaction coverage, and missing registry markers.
Campaigns enable it with `--audit-intent-coverage`; it writes
`intent-coverage.{json,txt}` without acting as a promotion gate.

The optional LLM path is suggestion-only. Prompt bundles include source excerpts
and the known marker registry. `cv-llm-runner.py` is provider-agnostic: it sends
one prompt JSON object to an adapter command on stdin, stores raw stdout, and
collects valid JSON responses. Imported model responses must validate against
the same marker and constraint vocabulary before they can join the findings
stream. Rejected and unsupported candidates can be preserved in sidecar JSONL
files and summarized by `cv-llm-review-candidates.py`, which turns model output
into a generator-support backlog. LLM output never edits LLVM source directly in
this workflow.

Constraint-to-config generation currently supports only constraints representable
by `GeneratorConfig`, including scalar, CFG, memory, loop, and vector seed
shapes.
Unsupported findings are skipped by default or rejected with `--strict`.

The AST instrumentation tool is opt-in because it requires Clang LibTooling. It
rewrites selected one-line predicate conditions and prints rewritten source to
stdout; normal builds remain dependency-free.

Campaign runs are the automation layer over this flow. A campaign records mined
findings, generated configs, optional instrumentation artifacts, replay
manifests, the exact commands used, and a summary grouped by replay,
probe-oracle, semantic, and category status.

## Config Reduction Flow

The reducer treats probe markers as the behavior to preserve:

```text
case.cfg
  -> markerStringsForConfig records probe.* markers
  -> greedy field minimization tries simpler values
  -> candidate accepted only if all required markers remain present
  -> reduced.cfg becomes the preferred human-readable regression seed
```

Reduction does not preserve textual IR identity. It preserves the selected probe
coverage only, which is the intended curation boundary for KLEE-generated cases.

## LLVM Oracle Flow

The `opt-check-cases.sh` script treats each normalized config as the stable test
input:

```text
case.cfg
  -> cv-replay creates opt/<case>.before.ll
  -> opt-check-cases selects pass pipeline from scalar/CFG/memory/loop shape
  -> llvm-as validates input IR in an LLVM container
  -> opt -S -passes=<pipeline> creates opt/<case>.after.ll
  -> llvm-as validates optimized IR
  -> sampled semantic oracle compares before/after @test outputs
  -> optional Alive2 checker proves before/after IR refinement
  -> opt/manifest.jsonl records pass/fail, probe, and semantic status
```

The first semantic oracle is intentionally sampled: input IR assembles, `opt`
succeeds, optimized IR assembles, and a clang-compiled driver checks a fixed set
of small `i32 @test(i32, i32)` inputs. Sampled mismatches fail replay.

Alive2 is an optional formal layer. Pass `--alive2` to `opt-check-cases.sh` and
set `O2T_ALIVE_TV` or `--alive2-bin` when the binary is not on `PATH`.
The manifest records `alive2_status` as `proved`, `failed`, `unsupported`, or
`error`; failed and errored proofs fail replay, while unsupported IR is kept as a
recorded tool limitation.

The campaign, KLEE-campaign, backfill, and verification workflow runners expose
the same `--alive2` and `--alive2-bin` flags, so formal IR checks can be enabled
without calling `opt-check-cases.sh` directly.

Intent evidence bundles join the source-inferred intent, SMT proof result,
replay manifest, probe oracle, sampled semantic check, and optional Alive2
result by marker. `verified` records have a proved intent and at least one clean
covering replay case; `blocked` records have proof failures, replay failures, or
failed source-slice contracts;
`uncovered` records lack replay coverage; `unsupported` records preserve known
formal limitations for review.
