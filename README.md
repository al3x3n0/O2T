# O2T — Optimizer Testing Toolkit

O2T (Optimizer Testing Toolkit) is a framework for testing and formally verifying
LLVM optimization passes: it mines optimization intent from real pass source and
proves soundness with Z3, locally translation-validates rewrites, and hunts
miscompiles by differential and mutation testing against real `opt`. It also
generates LLVM IR tests that exercise passes — the original MVP keeps LLVM data
structures concrete and makes the IR-generator knobs symbolic, the tractable part
of the search space.

> Note: the repository directory is named `O2T`; commands below use that
> path. The project/brand name is **O2T**. The internal C++ namespace, the
> `o2t/` include path, and the `O2T_*` env/build variables are the preferred
> names. `CV_*` instrumentation macros and selected `COMPILERVERIF_*` aliases
> remain for compatibility.

## Compatibility Aliases

New integrations should use `O2T_*` CMake options, environment variables, and
artifact model IDs. Existing automation can still use these legacy names:

- CMake aliases: `COMPILERVERIF_BUILD_TESTS`, `COMPILERVERIF_WITH_KLEE`,
  `COMPILERVERIF_WITH_LLVM`, `COMPILERVERIF_USE_LLVM_BACKEND`, and
  `COMPILERVERIF_BUILD_CLANG_TOOLS`.
- Runtime env aliases: `COMPILERVERIF_HOST_OPT`,
  `COMPILERVERIF_HOST_LLVM_AS`, `COMPILERVERIF_SEMANTIC_CLANG`,
  `COMPILERVERIF_ALIVE_TV`, `COMPILERVERIF_LLVM_IMAGE`,
  `COMPILERVERIF_KLEE_IMAGE`, `COMPILERVERIF_CLANG_TOOLING_IMAGE`,
  `COMPILERVERIF_DOCKER_PLATFORM`, `COMPILERVERIF_KLEE_CXX`,
  `COMPILERVERIF_RUN_ID`, `COMPILERVERIF_GLOBALOPT_STRICT_OUT`, and
  `COMPILERVERIF_PASS_PROBE_LOG`.
- Import/schema aliases: `import compilerverif` resolves to `o2t`, and readers
  accept legacy `compilerverif-*` baseline model IDs where baselines are
  persisted.

## Current MVP

- A bounded generator config (`GeneratorConfig`) that can be made symbolic under
  KLEE or replayed from a concrete file.
- A deterministic LLVM IR text generator for small scalar, stack-memory, and
  loop-shaped functions.
- Pattern coverage hooks for optimization triggers such as add-zero, mul-one,
  xor-self, dead arithmetic, branch diamonds, nested diamonds, unreachable
  tails, switch-like branch chains, promotable allocas, store/load forwarding,
  redundant loads, dead stores, overwritten stores, unused allocas, canonical
  loops, induction PHIs, simple trip counts, loop-invariant ops, loop exits,
  and dead loop-body instructions.
- A KLEE-safe abstract IR probe layer that scans generated shapes with
  optimization-like predicates before replaying configs into textual LLVM IR.
- A probe backend boundary with the current abstract backend and a guarded LLVM
  backend slot.
- Lightweight `CV_PASS_PROBE*` macros for instrumenting real LLVM pass
  predicates later.
- A source-mining prototype that scans LLVM-like pass code and maps known
  predicate patterns to `probe.*` markers.
- An opt-in Clang LibTooling source instrumentation tool skeleton that rewrites
  selected pass predicates with `CV_PASS_PROBE_IF`.
- A native replay tool that turns a config or seed into `.ll`.
- A KLEE-compatible harness stub for InstCombine-style exploration.

This first increment intentionally does not require LLVM or KLEE to build. On a
machine with KLEE installed, the harness can be compiled to LLVM bitcode and
executed by KLEE. On a machine with LLVM installed, the emitted IR can be piped
through `opt` and checked with `llvm-as` / `llvm-dis`.

## Build

```bash
cmake -S O2T -B O2T/build
cmake --build O2T/build
ctest --test-dir O2T/build --output-on-failure
```

## Replay A Generated Test

Generate IR from a deterministic seed:

```bash
O2T/build/cv-replay --seed 42 --out O2T/examples/seed42.ll
```

Generate IR from a saved key/value config:

```bash
O2T/build/cv-replay --config O2T/examples/add_zero.cfg --out /tmp/add_zero.ll
```

Example config:

```text
arith_opcode=0
rhs_mode=0
extra_opcode=3
predicate=2
shape=1
feature_bits=3
memory_shape=0
pointer_mode=0
store_mode=0
load_use_mode=0
loop_shape=0
loop_trip_mode=0
induction_mode=0
loop_use_mode=0
const_a=7
const_b=1
```

`shape` values currently supported:

```text
0 straight-line
1 diamond
2 nested-diamond
3 unreachable-tail
4 switch-like-chain
```

`memory_shape` values currently supported:

```text
0 none
1 alloca-store-load
2 load-after-store
3 dead-store
4 overwritten-store
5 unused-alloca
```

Memory examples live under `O2T/examples/*alloca*.cfg`,
`dead_store.cfg`, `overwritten_store.cfg`, and `store_load_forward.cfg`.

`loop_shape` values currently supported:

```text
0 none
1 counted-loop
2 early-exit-loop
3 invariant-op-loop
4 dead-body-loop
```

Loop examples live under `O2T/examples/*loop.cfg`.

`vector_shape` values currently supported:

```text
0 none
1 add-zero
2 mul-one
3 xor-self
4 shuffle-identity
5 shuffle-splat
6 extract-insert
7 reduction-add-zero
8 sub-zero
9 or-zero
10 and-allones
11 insert-extract-identity
12 reduction-add-single-lane
13 scalable-add-zero
14 scalable-mul-one
15 scalable-xor-self
16 scalable-sub-zero
17 scalable-or-zero
18 scalable-and-allones
19 scalable-reduction-add-zero
```

Vector examples live under `O2T/examples/vector_*.cfg`.

`compose_bits` controls shape composition. The legacy generator emits exactly
one shape per function through a priority cascade; with `compose_bits=0` (the
default) output is unchanged. A non-zero `compose_bits` is a bitmask that threads
multiple shape regions through a single `@test` function so CFG, memory, and loop
shapes can co-occur — the kind of mixed IR that exercises pass interactions the
single-shape catalog never produces:

```text
1  include the CFG shape    (uses shape)
2  include the memory shape (uses memory_shape, skipped when none)
4  include the loop shape   (uses loop_shape, skipped when none)
8  include the vector shape (uses vector_shape, skipped when none)
16 include the global shape (uses global_shape, skipped when none)
32 include a cast stage     (uses cast_mode)
```

Bits combine: `compose_bits=7` emits the CFG shape, feeds its result into the
memory shape, seeds the loop accumulator with the loaded value, then applies the
`extra_opcode` fold. The vector bit (`8`) lifts the running scalar into a vector,
applies the identity-style op, and extracts a lane back to a scalar so the value
keeps threading; reduction shapes pull in their `llvm.vector.reduce` declare. The
global bit (`16`) emits the dead-initializer global at module scope (semantics
unchanged) alongside the composed `@test`. So `compose_bits=31` can produce one
module with a dead global and a `@test` that runs a CFG diamond into a stack
slot, into a counted loop, into a vector reduction. See
`O2T/examples/composed_diamond_mem_loop.cfg` and
`O2T/examples/composed_loop_vector_reduce.cfg`.

Seed-driven generation (`cv-replay --seed N`, and the KLEE config search) also
explores composition: roughly seven of every eight seeds set a non-zero
`compose_bits`, so the search covers mixed CFG/memory/loop functions, not just
the single-shape catalog.

`int_width` selects the composed function's integer width (`0`=i8, `1`=i16,
`2`=i32 (default), `3`=i64). The width retypes the whole composed function
consistently -- scalar, memory, loop, and vector element types -- while vector
lane indices and shuffle masks stay i32. Legacy (`compose_bits=0`) output is
always i32. `feature_bits` now carries a third flag (`4`=nuw) alongside `1`=nsw
and `2`=select, so `add`/`sub`/`mul` can be tagged `nuw`, `nsw`, or both --
poison-generating shapes that exercise width- and overflow-sensitive folds.

The composed function's argument surface is configurable too (composed mode
only; legacy is always `(iN %a, iN %b)`):

```text
scalar_args      0=2 args (default), 1=3 (%a..%c), 2=4 (%a..%d); extras are
                 folded into the threaded value so reassociation/CSE/GVN see them
pointer_args     0 (default), 1, or 2 pointer parameters (%p, %q)
pointer_noalias  0/1 -- tag the pointer parameters `noalias`
```

When `pointer_args > 0`, the memory stage routes stores/loads through `%p`/`%q`
instead of a local alloca. With two pointers it interposes a store to `%q`
between a store and a load on `%p`: under `noalias` the load forwards and the
redundant load dies (DSE/GVN fire); under may-alias it cannot. That
`noalias`-vs-may-alias gap is the alias-reasoning trigger that local allocas can
never express. See `O2T/examples/composed_noalias_pointers.cfg`.

The cast stage (`compose_bits` bit `32`) round-trips the threaded value through a
different integer width with a `trunc`/`zext`-or-`sext` pair (plus a non-identity
`add` in the intermediate width), exercising InstCombine's cast and known-bits
folds that a single width can never reach. `cast_mode` bits 0-1 pick the target
width (i8/i16/i32/i64, bumped if equal to the function width) and bit 2 selects
signed (`sext`) vs unsigned (`zext`).

## Validate Generated IR Locally

When a local LLVM toolchain is available, validate that generated (and composed)
IR parses and survives a real optimization pipeline:

```bash
O2T/scripts/validate-ir.sh --dir O2T/examples
O2T/scripts/validate-ir.sh O2T/examples/composed_diamond_mem_loop.cfg
O2T/scripts/validate-ir.sh --seeds 64
```

Set `CV_LLVM_BIN` to the directory holding `llvm-as`/`opt` (it defaults to
`/opt/homebrew/opt/llvm@18/bin`, then falls back to `PATH`), and `CV_PASSES` to
override the pipeline. This is the local-toolchain counterpart to
`scripts/opt-check-cases.sh` for environments where the Docker LLVM image is not
reachable.

## Coverage-Guided Fuzzing

The seed and KLEE front-ends explore the config space blindly. `cv-fuzz-campaign`
makes the search *directed*: it mutates a corpus of configs, runs each generated
module through `opt`, and keeps a config only when it produces **new coverage** --
a bucketized fingerprint of the optimizer's behavior: the opcode histogram of the
optimized IR (works even on a Release LLVM where `-stats` is empty), the
**before→after opcode delta** -- how many of each opcode the pass added or removed,
signed and bucketized, which fingerprints what the optimizer *did* rather than what
the output looks like (two configs that reach a similar-shaped output via different
transformations no longer collide), plus `opt -stats` counters when available. The
corpus grows toward configs that exercise more and different optimizations:

```bash
O2T/tools/cv-fuzz-campaign.py \
  --out-dir /tmp/o2t-fuzz \
  --iterations 2000 --passes 'default<O2>' --minimize
```

A config is a **finding** when `opt` crashes, emits no output, or -- most
valuable -- produces IR that fails to verify (`llvm-as` rejects the optimized
module: a genuine miscompile/verifier bug); `--alive2` adds refinement failures.
`--minimize` shrinks each finding with `cv-reduce-failing-config.py`. It needs
only `opt`/`llvm-as` (no KLEE) -- set `CV_LLVM_BIN` or `--opt`/`--llvm-as`. The
run writes `corpus/`, `findings/`, optional `minimized/`, and `summary.json`.

## Execution-Based Differential Testing (Miscompile Finding)

This is the proof-of-value loop: it catches real miscompiles by *running the
program*, with no Alive2 required. `cv-grammar-gen --main` emits a deterministic,
UB-free module (no poison flags, guarded div/shift, no memory) whose `@main`
folds `@test`'s results over a range of inputs into the process exit code. For a
UB-free program every correct optimizer must preserve that exit code, so
`cv-differential.py` runs each module through several optimizer configurations
under one `lli` and flags any divergence -- a genuine miscompile:

```bash
O2T/tools/cv-differential.py --count 1000 --cfg \
  --out-dir /tmp/cv-diff --minimize
```

It generates seeds, compares exit codes across `raw`/`O0`/`O1`/`O2`/`O3` (same
`opt` + same `lli`), and writes any divergence to `findings/`, minimized into
`minimized/` via `cv-reduce-ir.py` with a `--check-one` divergence oracle.

Important: the optimizer and executor must be the **same LLVM version** -- a
cross-version `opt`/`lli` pairing produces spurious divergences (the executor may
run the other version's IR differently), not miscompiles. On a sound compiler
this reports **zero** findings (validated: 300 modules, llvm@18 O0-O3, 0
divergences); a planted miscompiling optimizer is detected and minimized.

## Grammar-Based Random IR

`cv-grammar-gen` is a Csmith-for-IR front-end: instead of the config catalog it
builds *random valid programs* by growing a typed pool of SSA values and drawing
operands from it (values are reused, so the result is a DAG with real CSE/GVN
material), mixing a broad opcode set across i1..i64 with poison flags
(`nsw`/`nuw`/`exact`/`disjoint`), casts, pointer-param memory ops, integer
intrinsics (`llvm.smax`/`smin`/`umax`/`umin`, `abs`, `ctlz`/`cttz`/`ctpop`,
`bitreverse`/`bswap` -- reaching InstCombine's intrinsic-fold family, and UB-free
so they run under the execution differential), floating-point arithmetic
(`fadd`/`fsub`/`fmul`/`fdiv`/`fneg`, `fcmp`, int<->fp casts, **scalar and vector
`<N x float>`/`<N x double>`**) with **fast-math flags** to exercise
reassociation/refinement folds -- emitted only in the non-executed `--validate`
path, since fast-math lets a correct optimizer change the numeric result and would
produce spurious divergences in the execution differential -- and special constants
(signmin/max, powers of two, all-ones). Everything is one
basic block, so the IR is valid by construction. Integer **vectors**
(`<N x iM>` element-wise arithmetic, `extractelement`/`insertelement`,
`shufflevector`, splats) reach the vector-InstCombine / VectorCombine / shuffle-fold
families; because integer vector arithmetic is deterministic and UB-free on the safe
op subset, vectors -- unlike FP -- also run in the execution differential (feeding
the scalar return via `extractelement`), so they find real vector miscompiles.
Scalable vectors (`<vscale x N x iM>`, with the canonical splat and element-wise
ops) reach the SVE/RVV scalable-vector fold surface; being unknown-length they are
validate-only (never in the execution differential). Each module is deterministic
in its `--seed`:

```bash
O2T/tools/cv-grammar-gen.py --seed 0 --instructions 30      # one module to stdout
O2T/tools/cv-grammar-gen.py --seed 0 --count 1000 --validate  # Csmith loop
O2T/tools/cv-grammar-gen.py --seed 0 --cfg --count 1000 --validate  # with control flow
O2T/tools/cv-grammar-gen.py --cfg --count 1000 --validate --minimize --out-dir /tmp/g  # self-minimizing
```

With `--minimize` the Csmith loop is self-shrinking: each opt finding is written
to `<out-dir>/findings/` and reduced with `cv-reduce-ir.py --opt-invalid` into
`<out-dir>/minimized/`, so a discovered bug comes out as a minimal witness in one
run.

`--cfg` adds control flow: the body becomes a chain of nested single-entry/
single-exit regions -- if/then, if/then/else diamonds, and counted loops -- with
random-DAG block bodies, `phi` merges, and loop-carried induction/accumulator
phis. Validity is preserved by construction: the pre-region value pool dominates
the region's continuation, and values created inside a branch only re-enter
through `phi` nodes whose incomings are defined in the matching predecessor
(`--cfg-regions`/`--cfg-depth` tune breadth/nesting).

`--validate` runs `llvm-as`/`opt`/`llvm-as` on each module and reports findings
(generator-invalid -- a generator gap; or `opt` crash / invalid optimized output
-- an opt bug). `--out`/`--out-dir` write modules; `--report` writes JSON. Needs
`opt`/`llvm-as` only for `--validate` (`CV_LLVM_BIN` or `--opt`/`--llvm-as`).

Grammar findings are raw `.ll`, so they shrink with `cv-reduce-ir.py` (LLVM's
`llvm-reduce` driven by an oracle) rather than the config reducers:

```bash
O2T/tools/cv-reduce-ir.py --input witness.ll --out min.ll --opt-invalid
O2T/tools/cv-reduce-ir.py --input witness.ll --out min.ll \
  --oracle 'alive-tv {ll} ... ' --invert
```

`--opt-invalid` keeps reducing while `opt` crashes or emits IR that fails to
verify; or pass any `--oracle` (`{ll}` = candidate, exit 0 = still failing,
`--invert` for a validity check). It refuses to start if the input is not
already interesting. Needs `llvm-reduce` (+ `opt`/`llvm-as` for `--opt-invalid`).

## KLEE Direction

The harness in `harnesses/instcombine_harness.cpp` makes each `GeneratorConfig`
field symbolic, normalizes it into legal bounded knobs, and records which
optimization-trigger predicates were reached. It does this by building a small
fixed-size abstract IR model and scanning it with pass-like probes such as
`probe.instcombine.add-zero` and `probe.simplifycfg.unreachable-block`. The KLEE
build uses header-only normalization/probe code so the harness bitcode does not
pull in the text IR generator or config parser. Concrete `.ll` files are emitted
after KLEE by replaying extracted configs through `cv-replay`.

Run `cv-run-klee-campaign.py --feedback` to enable **oracle-novelty feedback**:
before each build the campaign regenerates `GeneratedKleeFeedback.h` from the
markers covered by prior runs (accumulated in `feedback-covered.json`), and the
harness -- built with `-DO2T_KLEE_FEEDBACK` -- `klee_assume`s
`isNovelCoverage(coverage)`, pruning paths whose config only re-derives covered
markers so KLEE spends its budget on the frontier. Regenerate the header by hand
with `cv-generate-klee-feedback.py` (`--state`/`--covered-json`/`--markers` in,
`--out` header / `--update-state` accumulator out).

See [docs/instrumenting_llvm_passes.md](docs/instrumenting_llvm_passes.md) for
the macro shape intended for LLVM pass source patches. See
[docs/llvm_transform_verification_ledger.md](docs/llvm_transform_verification_ledger.md)
for the current built-in LLVM transform verification coverage ledger.

The LLVM-integrated layer uses the generated IR text as the handoff into real
LLVM APIs:

```text
symbolic GeneratorConfig
  -> generated LLVM IR text
  -> parsed LLVM Module
  -> shape-selected pass pipeline
  -> verifier / future Alive2 / structural oracle
  -> accepted or failing config + minimized .ll
```

Until LLVM dev packages are available locally, this project focuses on the
generator and artifact pipeline.

The harness currently uses `runProbeBackend(config)`, which selects the abstract
backend by default. `O2T_USE_LLVM_BACKEND` switches to the LLVM backend
when `O2T_WITH_LLVM=ON`; otherwise the LLVM backend reports
unavailable.

## Build With LLVM Backend

The normal build does not need LLVM development packages. To build the optional
backend, provide an LLVM package config directory:

```bash
cmake -S O2T -B O2T/build-llvm \
  -DO2T_WITH_LLVM=ON \
  -DLLVM_DIR=/path/to/lib/cmake/llvm
cmake --build O2T/build-llvm
ctest --test-dir O2T/build-llvm --output-on-failure
```

The LLVM build adds `cv-llvm-probe`, which parses generated IR into an LLVM
module, verifies it, runs the same shape-aware pass pipeline used by the Docker
checker, verifies the optimized module, and prints both abstract expected
markers and native observed markers recorded by instrumented pass code:

```bash
O2T/build-llvm/cv-llvm-probe --config O2T/examples/counted_loop.cfg
```

Without instrumented LLVM pass code, `cv-llvm-probe` reports
`oracle_status=not-instrumented` while still validating IR parsing and pass
execution. Once instrumented predicates are linked in, require observed marker
coverage with:

```bash
O2T/build-llvm/cv-llvm-probe \
  --config O2T/examples/counted_loop.cfg \
  --require-observed
```

## Probe Demo

`cv-probe-demo` is built by default and proves the marker recorder/oracle path
without LLVM development packages. It computes abstract expected markers, fires
simulated instrumented predicates through `CV_PASS_PROBE_IF`, and compares the
observed events:

```bash
O2T/build/cv-probe-demo \
  --config O2T/examples/promotable_alloca.cfg \
  --require-observed
```

A matched run prints `oracle_status=matched`. For negative testing:

```bash
O2T/build/cv-probe-demo \
  --config O2T/examples/add_zero.cfg \
  --drop-marker probe.instcombine.add-zero
```

## Run With KLEE Docker

The KLEE workflow uses the pinned image `klee/klee:3.0` by default. Docker must
be running locally.

```bash
O2T/scripts/klee-shell.sh klee --version
O2T/scripts/klee-run-instcombine.sh
```

`klee-run-instcombine.sh` is a compatibility wrapper around the structured
campaign runner:

```bash
O2T/tools/cv-run-klee-campaign.py \
  --check \
  --require-observed-probes \
  --passes instcombine,simplifycfg
```

The InstCombine harness run writes artifacts under:

```text
O2T/klee-out/instcombine/<timestamp>/
  build/          harness bitcode
  klee/           raw KLEE output
  ktest-dumps/    ktest-tool text dumps
  cases/          normalized .cfg, generated .ll, manifest.jsonl
  commands.log    KLEE, extraction, and optional check commands
  summary.json    run id, artifact paths, case count, and check status
  coverage-summary.txt   generated/replayed marker coverage
  coverage-summary.json  machine-readable coverage gaps
```

Useful runner options:

```bash
O2T/tools/cv-run-klee-campaign.py \
  --run-id smoke \
  --reduce \
  --check \
  --alive2 \
  --require-observed-probes \
  --host-opt /path/to/llvm-build/bin/opt \
  --host-llvm-as /path/to/llvm-build/bin/llvm-as \
  --klee-arg=--max-time=60s
```

Use `--dry-run` to emit the same command log and summary without invoking
Docker/KLEE or replaying cases.

Add `--minimize-failures` to auto-minimize any opt-check failures into minimal
witnesses. After the check, every `cases/opt/manifest.jsonl` record with
`status: failed` is fed to `cv-reduce-failing-config.py`, using an oracle
(`scripts/single-config-opt-oracle.sh`) that re-runs the same opt check
(honouring `--passes`, `--alive2`, and `--host-opt`/`--host-llvm-as`) on each
candidate config. The minimal configs and a `summary.json` land in
`<run>/minimized/`. This runs even when the check fails -- that is exactly when
there are failing cases worth shrinking -- turning a large composed witness into
a filable minimal one:

```bash
O2T/tools/cv-run-klee-campaign.py \
  --check --alive2 --minimize-failures \
  --host-opt /path/to/llvm-build/bin/opt \
  --host-llvm-as /path/to/llvm-build/bin/llvm-as
```

The coverage summary compares generated markers from `cases/manifest.jsonl`
with observed markers from `cases/opt/manifest.jsonl` when `--check` is used.
It highlights probe markers never generated by KLEE, generated markers that
were not observed in instrumented host replay, and Alive2 proof status when the
optional checker is enabled.

Coverage gaps can be backfilled into targeted deterministic cases:

```bash
O2T/tools/cv-backfill-coverage-gaps.py \
  --coverage-json O2T/klee-out/instcombine/<timestamp>/coverage-summary.json \
  --out-dir /tmp/o2t-backfill \
  --replay O2T/build/cv-replay \
  --reducer O2T/build/cv-reduce-config \
  --reduce
```

Or enable the same step inside the KLEE campaign runner:

```bash
O2T/tools/cv-run-klee-campaign.py --backfill-gaps --backfill-check
```

Package KLEE and optional backfill cases into the standard campaign layout used
by the instrumented LLVM runner:

```bash
O2T/tools/cv-package-verification-campaign.py \
  --klee-campaign O2T/klee-out/instcombine/<timestamp> \
  --instrumentation O2T/build/campaign/instrumentation \
  --out /tmp/o2t-verification-campaign
```

The KLEE runner can also package at the end of a run:

```bash
O2T/tools/cv-run-klee-campaign.py \
  --backfill-gaps \
  --package-campaign \
  --package-out /tmp/o2t-verification-campaign
```

For a single command plan that ties source instrumentation, KLEE generation,
backfill, packaging, and optional instrumented replay together, use the workflow
driver. It writes `workflow-commands.log` and `workflow-summary.json`; add
`--execute` to run the planned stages, and add `--execute-instrumented` only
when the final LLVM apply/build/replay step should execute.
Add `--audit-instrumentation` to have the source instrumentation stage summarize
which mined predicates were actually covered by the generated patch. Add
`--recommend-instrumentation` when skipped candidates should also produce
machine-readable rewrite recommendations. Add
`--repair-instrumentation-candidates` to repair line or predicate-text drift,
and `--retry-repaired-instrumentation` to run one explicit retry.

```bash
O2T/tools/cv-run-verification-workflow.py \
  --out /tmp/o2t-workflow \
  --sources /path/to/llvm/lib/Transforms/InstCombine \
  --llvm-source /path/to/llvm-project \
  --llvm-build /path/to/llvm-build \
  --backfill-gaps \
  --package-campaign
```

For the GlobalOpt dead-initializer path, the workflow can generate focused
witness coverage before the source campaign and feed that coverage into
intent-evidence promotion:

```bash
O2T/tools/cv-run-verification-workflow.py \
  --out /tmp/o2t-workflow \
  --sources /path/to/llvm/lib/Transforms/IPO/GlobalOpt.cpp \
  --globalopt-coverage \
  --globalopt-source /path/to/llvm/lib/Transforms/IPO/GlobalOpt.cpp \
  --globalopt-emit-witnesses \
  --globalopt-min-witnesses 1 \
  --globalopt-max-witness-failures 0 \
  --require-intent-evidence \
  --promote-intents \
  --replace-existing-intents \
  --require-promotable-intent \
  --require-globalopt-witnesses \
  --max-globalopt-witness-failures 0 \
  --execute
```

For local regression work, run the strict fixture harness. It covers focused
coverage, rewrite provenance, typed witnesses, evidence, audit, promotion, and
workflow feed-through, then removes its generated output on success:

```bash
O2T/scripts/globalopt-strict-check.sh --z3 /path/to/z3
```

Pass `--keep-output` to inspect generated reports under
`build-clang-tools/globalopt-strict-check`.

On Apple Silicon the scripts default to `--platform linux/amd64`, because the
published KLEE image is commonly consumed as an amd64 image. Override with:

```bash
O2T_DOCKER_PLATFORM=linux/arm64 O2T/scripts/klee-shell.sh
```

The image can be changed with:

```bash
O2T_KLEE_IMAGE=klee/klee:3.0 O2T/scripts/klee-run-instcombine.sh
```

## Check Cases With LLVM Docker

Generated configs can be replayed through `llvm-as` and `opt` in a Docker
container, so host LLVM tools are not required:

```bash
O2T/scripts/llvm-shell.sh llvm-as --version
O2T/scripts/opt-check-cases.sh O2T/examples
```

When no pass pipeline is provided, the checker selects one per case from the
config shape: scalar cases use `instcombine`, CFG cases use
`simplifycfg,instcombine`, memory cases use `mem2reg,dse,instcombine`, and loop
cases use `loop-simplify,licm,indvars,simplifycfg,instcombine`. Inspect the
mapping without Docker:

```bash
O2T/scripts/opt-check-cases.sh --list-pipelines O2T/examples
```

For KLEE output, point the checker at the generated `cases` directory:

```bash
O2T/scripts/opt-check-cases.sh \
  O2T/klee-out/instcombine/<timestamp>/cases \
  instcombine,simplifycfg
```

The checker writes:

```text
<cases>/opt/
  <case>.before.ll
  <case>.after.ll
  manifest.jsonl
```

The default LLVM image is `silkeh/clang:18`, chosen as a lightweight prebuilt
Clang/LLVM image. Override it when you have a preferred LLVM image:

```bash
O2T_LLVM_IMAGE=my-llvm-image:tag O2T/scripts/opt-check-cases.sh O2T/examples
```

To check generated cases with a local or instrumented LLVM build instead of
Docker, point the checker at host tools:

```bash
O2T_HOST_OPT=/path/to/llvm-build/bin/opt \
O2T_HOST_LLVM_AS=/path/to/llvm-build/bin/llvm-as \
  O2T/scripts/opt-check-cases.sh O2T/examples
```

Host replay sets `O2T_PASS_PROBE_LOG` around each `opt` invocation.
Instrumented pass predicates that use `CV_PASS_PROBE*` append observed markers
there, and `opt/manifest.jsonl` records the expected markers, observed markers,
and probe-oracle status. Marker mismatches are recorded by default. Add
`--require-observed-probes` to fail host replay when the observed markers do not
match the generated config:

```bash
O2T_HOST_OPT=/path/to/llvm-build/bin/opt \
O2T_HOST_LLVM_AS=/path/to/llvm-build/bin/llvm-as \
  O2T/scripts/opt-check-cases.sh --require-observed-probes O2T/examples
```

Replay also runs a sampled semantic oracle over the generated
`i32 @test(i32, i32)` function. It compiles before/after IR with clang and a
small driver, compares deterministic input samples, and records
`semantic_status` in the manifest. Semantic mismatches fail replay. Override
the compiler with:

```bash
O2T_SEMANTIC_CLANG=/path/to/clang O2T/scripts/opt-check-cases.sh O2T/examples
```

For an optional IR-level proof check, install Alive2 and enable `alive-tv`:

```bash
O2T_ALIVE_TV=/path/to/alive-tv \
  O2T/scripts/opt-check-cases.sh --alive2 O2T/examples
```

You can also pass `--alive2-bin /path/to/alive-tv`. The checker records
`alive2_status`, `alive2_exit_code`, `alive2_message`, and `alive2_output` in
`opt/manifest.jsonl`. A failed or errored Alive2 check fails replay; unsupported
IR is recorded without failing the case.

## Reduce Configs

Configs can be minimized while preserving probe markers:

```bash
O2T/build/cv-reduce-config \
  --config O2T/examples/switch_like_chain.cfg \
  --preserve probe.instcombine.mul-one,probe.simplifycfg.branch-chain
```

If `--preserve` is omitted, the reducer preserves every probe marker reached by
the original config. KLEE extraction can also write reduced artifacts:

```bash
O2T/tools/cv-ktest-extract.py \
  --dump-dir O2T/tests/fixtures \
  --cases /tmp/o2t-cases \
  --replay O2T/build/cv-replay \
  --reduce \
  --reducer O2T/build/cv-reduce-config
```

`cv-reduce-config` minimizes against *abstract* probe markers. When a config
triggers a *real* failure -- an Alive2 refinement mismatch, an `llvm-as`
verifier rejection, or an `opt` crash -- use `cv-reduce-failing-config.py`, which
shrinks the config while an external oracle confirms the failure still
reproduces. The oracle is any shell command (`{ll}`/`{cfg}` are substituted with
the candidate's generated IR and config); exit `0` means "still failing", or use
`--invert` when the oracle is a validity check you want to keep failing:

```bash
# Shrink an invalid-IR witness: keep reducing while llvm-as rejects it.
O2T/tools/cv-reduce-failing-config.py \
  --config /tmp/failing.cfg \
  --replay O2T/build/cv-replay \
  --invert --oracle 'llvm-as {ll} -o /dev/null' \
  --out /tmp/min.cfg --report /tmp/min.json

# Shrink an Alive2 refinement failure (alive-tv exits non-zero on mismatch).
O2T/tools/cv-reduce-failing-config.py \
  --config /tmp/failing.cfg --invert \
  --oracle 'O2T/scripts/replay-with-opt.sh {cfg} instcombine | alive-tv {ll} -' \
  --out /tmp/min.cfg
```

It visits the same fields as `cv-reduce-config` (plus `compose_bits`), trying the
simplest values first, and refuses to start when the original config does not
reproduce the failure. The `--report` JSON records which fields shrank and how
many oracle calls it took.

## Mine LLVM Pass Sources

The miner scans LLVM-like C++ pass sources for known matcher/helper predicates
and emits probe metadata:

```bash
O2T/tools/cv-mine-pass-source.py \
  O2T/tests/fixtures/llvm_pass_snippet.cpp
```

For JSONL output:

```bash
O2T/tools/cv-mine-pass-source.py \
  /path/to/llvm/lib/Transforms/InstCombine \
  --format jsonl
```

The pattern registry lives at
[constraints/pass_constraints.json](constraints/pass_constraints.json). Findings
include the marker, source file and line, matched pattern, rough generator
constraints, and an instrumentation suggestion.

Mined findings can be converted into replayable seed configs:

```bash
O2T/tools/cv-mine-pass-source.py \
  O2T/tests/fixtures/llvm_pass_snippet.cpp \
  > /tmp/o2t-findings.json

O2T/tools/cv-constraints-to-configs.py \
  --input /tmp/o2t-findings.json \
  --out-dir /tmp/o2t-mined-configs \
  --replay O2T/build/cv-replay \
  --reducer O2T/build/cv-reduce-config
```

That writes one reduced `.cfg` and generated `.ll` per supported marker.

## LLM-Assisted Candidate Discovery

The deterministic miner can be augmented with provider-agnostic LLM candidate
suggestions. O2T writes prompt bundles, can run any adapter command
that accepts prompt JSON on stdin and returns candidate JSON on stdout, and then
imports validated responses:

```bash
O2T/tools/cv-llm-candidate-pack.py \
  --out /tmp/o2t-llm-prompts.jsonl \
  /path/to/llvm/lib/Transforms/InstCombine
```

Each prompt bundle includes a source excerpt, known probe markers, and the
expected response schema. To run a local or provider-specific model wrapper over
those bundles:

```bash
O2T/tools/cv-llm-runner.py \
  --prompts /tmp/o2t-llm-prompts.jsonl \
  --out-dir /tmp/o2t-llm-run \
  --command '/path/to/model-adapter --json'
```

The runner writes raw per-bundle output under `raw/`, valid JSON responses to
`responses.jsonl`, and command or parse failures to `runner-errors.jsonl`.
After a model produces candidate JSON, validate it:

```bash
O2T/tools/cv-llm-import-candidates.py \
  --input /tmp/model-candidates.jsonl \
  --out /tmp/o2t-llm-findings.json \
  --rejected-out /tmp/o2t-llm-rejected.jsonl \
  --unsupported-out /tmp/o2t-llm-unsupported.jsonl
```

Validated LLM findings have the same shape as static miner findings and can be
merged into a campaign:

```bash
O2T/tools/cv-run-campaign.py \
  --out /tmp/o2t-campaign \
  --llm-findings /tmp/o2t-llm-findings.json \
  --llm-rejected /tmp/o2t-llm-rejected.jsonl \
  --llm-unsupported /tmp/o2t-llm-unsupported.jsonl \
  --host-opt /path/to/llvm-build/bin/opt \
  --host-llvm-as /path/to/llvm-build/bin/llvm-as \
  /path/to/llvm/lib/Transforms/InstCombine
```

Campaigns can also execute the whole prompt-run-import-review path directly:

```bash
O2T/tools/cv-run-campaign.py \
  --out /tmp/o2t-campaign \
  --llm-command '/path/to/model-adapter --json' \
  --host-opt /path/to/llvm-build/bin/opt \
  --host-llvm-as /path/to/llvm-build/bin/llvm-as \
  /path/to/llvm/lib/Transforms/InstCombine
```

LLM candidates are suggestion-only in this workflow. Unknown markers, invalid
locations, and unsupported constraints are rejected before config generation or
instrumentation. When rejected or unsupported files are provided, the campaign
also writes `llm-review.txt` summarizing accepted, duplicate, invalid,
unsupported, and generated-case candidates.

## Run a Campaign

`cv-run-campaign.py` automates the source-mining to replay loop:

```bash
O2T/tools/cv-run-campaign.py \
  --out /tmp/o2t-campaign \
  --host-opt /path/to/llvm-build/bin/opt \
  --host-llvm-as /path/to/llvm-build/bin/llvm-as \
  /path/to/llvm/lib/Transforms/InstCombine
```

The campaign writes:

```text
campaign/
  findings.json
  cases/
  cases/opt/manifest.jsonl
  instrumentation/       # when --emit-instrumentation is used
  summary.txt
  commands.log
```

Add `--alive2` to run the optional Alive2 IR refinement check for every
generated before/after pair. Use `--alive2-bin /path/to/alive-tv` when the
binary is not on `PATH`.

Add `--emit-intent-evidence` with `--validate-intents` to join inferred intent
proofs with replay evidence from semantic checks, probe matching, and Alive2.
The campaign writes `intent-evidence.jsonl` and `intent-evidence-summary.txt`.
Use `--require-intent-evidence` when blocked or uncovered proved intents should
fail the run.

Add `--audit-intent-coverage` with `--validate-intents` to measure how mined
intents reached proof: source-derived formal IR, registry fallback, unsupported
semantic lowering, or blocked proof states. The campaign writes
`intent-coverage.json` and `intent-coverage.txt`; the audit is informational and
does not change promotion or proof failures.

Scalar, CFG, memory, loop, and vector intent records can carry a
machine-checkable `formal` block. The validator lowers supported `scalar-bv32`,
`cfg-bv32`, `memory-bv32`, `loop-bv32`, `vector-bv32x4`, and
`scalable-vector-bv32` expression trees to SMT-LIB before falling back to legacy
marker-specific proof rules for inferred scalar candidates. Fixed vector values
are sort-checked as four-lane tuples; scalable vectors are bounded by
`base_lanes * vscale` instances, currently using `vscale_values` such as
`[1, 2, 4]`. Vectors are packed only at the SMT equality boundary. Formal
records can optionally include `poison_variables` and
`refinement: "refinement"` to prove LLVM-style definedness preservation;
`poison`, `freeze`, and raw-`undef` rejection are handled in the shared lowerer.
The checked semantics live in
`tools/cv_formal_ir.py` and are shared with registry validation:

```bash
O2T/scripts/check-registries.sh
```

This checks JSON syntax for `constraints/pass_constraints.json`,
`constraints/semantic_facts.json`, and
`constraints/optimization_intents.json`, verifies semantic fact coverage against
the pass and intent registries, then runs formal registry proofs when `z3` is on
`PATH`. Set `PYTHON3=/path/to/python3` or `Z3=/path/to/z3` to pin the tools; set
`CV_SKIP_Z3=1` for syntax and semantic-contract checks only.

You can run only the formal proof gate with:

```bash
O2T/tools/cv-validate-intent-registry.py \
  --intents O2T/constraints/optimization_intents.json \
  --out /tmp/intent-registry.jsonl \
  --emit-smt /tmp/intent-registry-smt
```

The current registry has formal proofs for every scalar, `simplifycfg` CFG,
memory, loop, and vector intent record. Scalar algebra and vector lane/shuffle
records use poison-aware refinement in the registry so their proofs account for
LLVM definedness, not just raw bit-vector equality.

The registry proofs are at a fixed 32-bit width, but the generator emits
i8/i16/i32/i64. `cv-prove-multiwidth.py` re-encodes each width-parametric scalar
identity at every generated width (rescaling width-relative constants like
all-ones and the sign bit) and re-proves it with Z3, so the formal guarantee
matches what is generated. If Z3 finds a width where an identity fails, the model
is reported as a counterexample:

```bash
O2T/tools/cv-prove-multiwidth.py --widths 8,16,32,64 --require-all
```

All 11 scalar identities prove at all four widths. The source-modelcheck bridge
also replays supported CFG, loop, memory, DCE, and SLP obligations at selected
widths and reports width-specific domains such as `cfg-bv8`, `loop-bv16`,
`memory-bv16`, and `vector-bv8xN`.

The formal track also *disproves* bad rewrites. `cv-check-negative-intents.py`
takes a registry of known-unsound rewrites (`constraints/negative_intents.json`)
that must be rejected, extracts a counterexample (a concrete input where
before != after), and lowers it to a runnable before/after `.ll`. Its `--mutate`
teeth-test perturbs each sound scalar intent (wrapping the result in `+1`) and
confirms the prover now rejects it -- proving the prover is not vacuous:

```bash
O2T/tools/cv-check-negative-intents.py --mutate --emit-witness /tmp/w
```

Z3 is authoritative (bv32, poison-aware); `--no-z3` falls back to a brute-force
i8 search that also yields the witness.

`cv-prove-identities.py` broadens the *formal* coverage to transform families the
intent registry does not model -- Reassociate (associativity/commutativity),
InstSimplify (absorbing/idempotent folds), and shift-by-zero -- as a standalone
library (`constraints/extended_identities.json`), proved at i8/i16/i32/i64:

```bash
O2T/tools/cv-prove-identities.py --widths 8,16,32,64 --require-all
```

`scripts/check-registries.sh` runs all five formal layers together when z3 is
present: the 51 positive registry proofs, the multi-width scalar proofs, the
negative+mutation soundness checks, and the extended-identity family proofs.
For scalar, CFG, memory, loop, vector, and scalable-vector findings, source
miners emit `semantic_facts` with a compact operation/identity/rewrite model.
Intent inference lowers those facts to formal IR first and records
`evidence.semantic_lowering` so generated proofs explain which source-derived
semantics were used.

Intent inference can synthesize formal IR directly for source-mined scalar and
vector candidates. Vector markers derive fixed-lane formal IR from mined
constraints such as `m_SplatOrPoison(m_Zero())`, `VectorXorSelf`, vector
sub/or/and identities, signed and unsigned min/max, absolute value, identity
shuffle masks, reductions, and same-lane extract/insert evidence; those records
are tagged with `evidence.formal_inference: "source-derived-vector"`.
Parameterized vector
constraints such as `vector.shuffle.mask`, `vector.shuffle.splat_lane`, and
`vector.extract_insert.lane` are copied into `evidence.formal_parameters` and
drive the generated `vshuffle`, `vextract`, and `vinsert` formal IR. CFG,
memory, and loop markers still attach registry-backed formal IR directly.
Scalar markers use rewrite-sensitive inference because operand direction
matters, then fall back to registry formal IR only when the mined predicate has
strong marker-specific evidence, such as `m_Zero`, `m_One`, `Op0 == Op1`, or
`isInstructionTriviallyDead`. Empty rewrite bodies still remain unsupported.

When `--promote-intents` is combined with `--emit-intent-evidence`, promotion is
deferred until evidence is built. Only candidates with `evidence_status:
verified` are promotable; blocked, uncovered, unsupported, or missing evidence is
reported as `evidence-blocked`. Add `--require-promotable-intent` to fail when no
ready candidate also has verified replay evidence.

For transaction graphs that are blocked by helper expansion, coverage and
pass-source audit reports include top helper-slice diagnostics. Each diagnostic
keeps the file, line, marker, helper name, role, reason, source snippet, and
expansion stack; evidence and promotion reports preserve the same fields for
blocked or unsupported records.

Use `--mode verify` when replay failures should make the campaign fail
immediately. The default `discover` mode keeps artifacts and writes a summary
even when replay finds semantic failures. Add `--require-observed-probes` when
using an instrumented host LLVM build and missing probe events should fail
replay. Summarize any existing manifest with:

Add `--emit-instrumentation` to also write source instrumentation artifacts
under `campaign/instrumentation/`. Use `--instrumentation-dry-run` when the
Clang LibTooling instrumenter is not available and only candidate manifests are
needed. Add `--audit-instrumentation` to write `instrumentation-audit.json` and
`instrumentation-audit.txt`; the audit classifies findings as `rewritten`,
`skipped`, `error`, `missing-from-manifest`, or `candidate-only`. Add
`--recommend-instrumentation` to also write
`instrumentation-recommendations.jsonl` with suggested causes and actions for
uncovered candidates, such as adding an AST matcher or checking compile
commands. Add `--repair-instrumentation-candidates` to write
`instrumentation-repaired-findings.json` and
`instrumentation-repair-report.txt`; repair handles nearby-line and exact
predicate text drift. Add `--retry-repaired-instrumentation` to run one
additional instrumentation pass under `instrumentation-repaired/`. Add
`--require-instrumentation-coverage` when skipped, errored, or missing rewrite
coverage should fail the campaign.

```bash
O2T/tools/cv-summarize-manifest.py \
  /tmp/o2t-campaign/cases/opt/manifest.jsonl \
  --out /tmp/o2t-campaign/summary.txt
```

Build an evidence bundle from existing artifacts:

```bash
O2T/tools/cv-build-intent-evidence.py \
  --validated /tmp/o2t-campaign/intent-validated.jsonl \
  --opt-manifest /tmp/o2t-campaign/cases/opt/manifest.jsonl \
  --out /tmp/o2t-campaign/intent-evidence.jsonl \
  --report /tmp/o2t-campaign/intent-evidence-summary.txt
```

When a focused GlobalOpt coverage run has emitted witness evidence, pass it
through the campaign evidence step:

```bash
O2T/tools/cv-run-campaign.py \
  --emit-intent-evidence \
  --globalopt-coverage /tmp/globalopt-coverage/globalopt-coverage.json \
  --require-globalopt-witnesses \
  --max-globalopt-witness-failures 0 \
  /path/to/pass.cpp \
  --out /tmp/o2t-campaign
```

## Instrument Pass Sources With Clang

When Clang LibTooling development packages are available, build the AST-based
source miner and instrumenter:

```bash
cmake -S O2T -B O2T/build-clang-tools \
  -DO2T_BUILD_CLANG_TOOLS=ON
cmake --build O2T/build-clang-tools --target cv-mine-pass-source-ast cv-instrument-pass-source
```

The AST miner emits the same findings schema as `cv-mine-pass-source.py`, with
extra `finding_source`, `predicate_source`, and `rewrite_source` fields:

```bash
O2T/build-clang-tools/cv-mine-pass-source-ast \
  --registry O2T/constraints/pass_constraints.json \
  /path/to/pass.cpp -- -std=c++17 -IO2T/include
```

Use it in a campaign without replacing the default text miner:

```bash
O2T/tools/cv-run-campaign.py \
  --out /tmp/o2t-campaign \
  --ast-miner O2T/build-clang-tools/cv-mine-pass-source-ast \
  --infer-intents /path/to/pass.cpp
```

For real LLVM pass files, use the AST-first source audit runner with a build
tree `compile_commands.json`. It expands source directories, skips files missing
from the compilation database, mines with LibTooling, validates inferred formal
intent with Z3, and writes `intent-coverage.{json,txt}`:

```bash
O2T/tools/cv-run-pass-source-audit.py \
  --compile-commands /path/to/llvm-build/compile_commands.json \
  --out /tmp/o2t-pass-audit \
  --ast-miner O2T/build-clang-tools/cv-mine-pass-source-ast \
  --z3 /path/to/z3 \
  /path/to/llvm/lib/Transforms/InstCombine
```

The runner also writes `run-summary.{json,txt}` for CI and review. Use
`--marker probe.slp.vectorize-binop` or `--marker-prefix probe.slp.` to narrow
findings after mining, and add budget gates such as `--min-proved 1`,
`--max-unsupported 0`, `--max-proof-failures 0`,
`--max-fallback-transactions 0`, or `--max-mining-errors 0`. Budget failures
return exit code `1` after writing the normal findings, validation, coverage,
and run-summary artifacts.
Each run writes `audit-baseline.json` plus `baseline-diff.{json,txt}`. Pass a
previous `--baseline` to compare runs and use `--max-new-unsupported 0` or
`--max-new-fallback-transactions 0` to fail only on newly introduced gaps.
When `--modelcheck-intents` is enabled, the baseline diff also tracks new,
resolved, and changed modelcheck findings; the text report lists the top
new/resolved/changed obligations and shows an explicit `... N more` line when
the finding list is truncated. `--write-baseline` can redirect the current
baseline artifact.

Run it on a file using either a real `compile_commands.json` or explicit compile
flags after `--`:

```bash
O2T/build-clang-tools/cv-instrument-pass-source \
  O2T/tests/fixtures/llvm_pass_snippet.cpp \
  -- -std=c++17 -IO2T/include
```

Validated static or LLM findings can drive exact line-aware rewrites:

```bash
O2T/build-clang-tools/cv-instrument-pass-source \
  --candidate-file /tmp/o2t-findings.json \
  /path/to/pass.cpp -- -std=c++17 -IO2T/include
```

The tool writes rewritten source to stdout. It is intentionally opt-in because
normal framework builds do not require Clang tooling libraries. A Docker helper
is available for environments that provide the right image:

```bash
O2T/scripts/clang-tooling-shell.sh clang++ --version
```

For a whole LLVM source tree, use the patch driver. Dry-run mode is available in
normal builds:

```bash
O2T/tools/cv-instrument-llvm-tree.py \
  --dry-run \
  --out-dir /tmp/o2t-instrumentation \
  /path/to/llvm/lib/Transforms
```

With `cv-instrument-pass-source` built, rewrite selected predicates out-of-tree
and generate a patch:

```bash
O2T/tools/cv-instrument-llvm-tree.py \
  --out-dir /tmp/o2t-instrumentation \
  --instrumenter O2T/build-clang-tools/cv-instrument-pass-source \
  --llm-findings /tmp/o2t-llm-findings.json \
  --compile-commands /path/to/llvm/build/compile_commands.json \
  --passes instcombine,simplifycfg \
  /path/to/llvm/lib/Transforms
```

The generated patch can be carried into an LLVM checkout with the conservative
playbook script. It validates paths and prints commands by default:

```bash
O2T/scripts/instrumented-llvm-playbook.sh \
  check /path/to/llvm-project /path/to/llvm-build
O2T/scripts/instrumented-llvm-playbook.sh \
  apply /path/to/llvm-project /tmp/o2t-instrumentation/instrumentation.patch
O2T/scripts/instrumented-llvm-playbook.sh \
  configure /path/to/llvm-project /path/to/llvm-build
O2T/scripts/instrumented-llvm-playbook.sh \
  run-opt --require-observed-probes /path/to/llvm-build O2T/examples
```

Add `--execute` to run side-effecting steps. `apply` refuses dirty LLVM
checkouts unless `--allow-dirty` is provided.

For a completed campaign directory, use the instrumented campaign runner to
chain those playbook steps and log the command sequence:

```bash
O2T/tools/cv-run-instrumented-campaign.py \
  --campaign /tmp/o2t-campaign \
  --llvm-source /path/to/llvm-project \
  --llvm-build /path/to/llvm-build
```

Add `--execute` to apply the campaign patch, configure/build `opt` and
`llvm-as`, replay the packaged cases, and write both `instrumented-summary.txt`
and `verification-summary.{txt,json}`. The verification summary joins replay
status back to packaged case origins such as KLEE or backfill.
`llvm-as`, and rerun `campaign/cases` with strict observed-probe checking.
