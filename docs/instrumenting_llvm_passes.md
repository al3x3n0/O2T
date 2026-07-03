# Instrumenting LLVM Pass Predicates

O2T exposes lightweight macros for marking optimization predicates in
pass code:

```cpp
#include "o2t/PassInstrumentation.h"

if (CV_PASS_PROBE_IF("probe.instcombine.add-zero",
                     match(Op1, m_Zero()))) {
  // Existing optimization body.
}
```

`CV_PASS_PROBE_IF(marker, condition)` returns `condition`, so it can wrap an
existing `if` predicate without changing pass behavior. Under KLEE it emits the
marker through `klee_print_expr`; in native builds it records the marker in a
thread-local event list when the predicate is true.

Native probe events can be inspected with:

```cpp
cv::clearPassProbeEvents();
// Run instrumented code.
for (const std::string &marker : cv::passProbeEvents()) {
  // Compare against expected probe markers.
}
```

`cv-llvm-probe` performs that comparison for generated configs. In a plain LLVM
backend build it may report `oracle_status=not-instrumented`; once instrumented
pass predicates are linked in, `--require-observed` turns missing expected
markers into a failing probe run.

For unconditional reachability markers:

```cpp
CV_PASS_PROBE("probe.simplifycfg.unreachable-block");
```

## Example Targets

InstCombine-style folds:

```cpp
if (CV_PASS_PROBE_IF("probe.instcombine.add-zero",
                     match(Op1, m_Zero()))) {
  return replaceInstUsesWith(I, Op0);
}

if (CV_PASS_PROBE_IF("probe.instcombine.mul-one",
                     match(Op1, m_One()))) {
  return replaceInstUsesWith(I, Op0);
}

if (CV_PASS_PROBE_IF("probe.instcombine.xor-self", Op0 == Op1)) {
  return replaceInstUsesWith(I, Constant::getNullValue(I.getType()));
}
```

DCE-style predicates:

```cpp
if (CV_PASS_PROBE_IF("probe.dce.dead-instruction",
                     isInstructionTriviallyDead(&I, TLI))) {
  I.eraseFromParent();
}

if (CV_PASS_PROBE_IF("probe.globalopt.dead-initializer",
                     isGlobalInitializerDead(GV) &&
                         GV->hasLocalLinkage() && GV->use_empty())) {
  GV->setInitializer(Constant::getNullValue(GV->getValueType()));
}
```

SimplifyCFG-style predicates:

```cpp
if (CV_PASS_PROBE_IF("probe.simplifycfg.unreachable-block",
                     isa<UnreachableInst>(BB.getTerminator()))) {
  // Existing cleanup path.
}
```

## Build Shape

For a real LLVM source checkout, add O2T's `include` directory to the
instrumented pass compile and define `O2T_WITH_KLEE` only when building
bitcode for KLEE:

```text
clang++ -DO2T_WITH_KLEE=1 -I/path/to/O2T/include ...
```

The current harness uses the same macros over the abstract pass probes. The next
LLVM-backed step is to apply these macros to selected LLVM pass predicates and
compile that instrumented pass code into the KLEE harness.

## AST Instrumentation Tool

`cv-instrument-pass-source` is an optional Clang LibTooling tool that rewrites
selected `if` predicates to use `CV_PASS_PROBE_IF`. It is disabled in normal
builds and enabled with:

```bash
cmake -S O2T -B O2T/build-clang-tools \
  -DO2T_BUILD_CLANG_TOOLS=ON
```

The tool currently targets the first predicate family:

- `m_Zero` -> `probe.instcombine.add-zero`
- `m_One` -> `probe.instcombine.mul-one`
- equality predicates -> `probe.instcombine.xor-self`
- `isInstructionTriviallyDead` -> `probe.dce.dead-instruction`
- `isGlobalInitializerDead` + local/no-use checks -> `probe.globalopt.dead-initializer`
- `UnreachableInst` -> `probe.simplifycfg.unreachable-block`
- `getSinglePredecessor` -> `probe.simplifycfg.diamond`
- `SwitchInst` -> `probe.simplifycfg.branch-chain`
- `isAllocaPromotable` -> `probe.mem2reg.promotable-alloca`
- `rewriteSingleStoreAlloca` -> `probe.mem2reg.store-load-forward`
- `isRemovable` -> `probe.dse.dead-store`
- `isOverwrite` -> `probe.dse.overwritten-store`
- `FindAvailableLoadedValue` -> `probe.instcombine.redundant-load`
- `use_empty` -> `probe.cleanup.unused-alloca`
- `getHeader` -> `probe.loop.canonical-header`
- `PHINode` -> `probe.loop.induction-phi`
- `getSmallConstantTripCount` -> `probe.loop.simple-trip-count`
- `isLoopInvariant` / `makeLoopInvariant` -> `probe.licm.invariant-op`
- `isDeadLoopInstruction` -> `probe.dce.dead-loop-instruction`
- `getExitBlock` -> `probe.simplifycfg.loop-exit`

For real LLVM source, run it with LLVM's `compile_commands.json` so Clang sees
the same include paths and defines as the LLVM build.

Validated findings can also drive exact line-aware rewrites:

```bash
O2T/build-clang-tools/cv-instrument-pass-source \
  --candidate-file /tmp/o2t-findings.json \
  /path/to/pass.cpp -- -std=c++17 -I/path/to/O2T/include
```

Candidate records use the same `file`, `line`, `marker`, and
`predicate_source` or `matched_pattern` fields produced by the miner and LLM
importer. When a candidate file is provided, the tool instruments matching
one-line `if` conditions by source line and predicate text instead of falling
back to the built-in marker matchers.

## LLVM Tree Patch Workflow

`cv-instrument-llvm-tree.py` wraps mining and rewriting for a source tree. It
never edits the LLVM checkout in place; artifacts are written under `--out-dir`.

Dry-run mode needs no Clang tooling:

```bash
O2T/tools/cv-instrument-llvm-tree.py \
  --dry-run \
  --out-dir /tmp/o2t-instrumentation \
  /path/to/llvm/lib/Transforms
```

That writes:

```text
/tmp/o2t-instrumentation/
  instrumentation-candidates.json
  instrumentation-manifest.jsonl
  instrumentation.patch
```

Rewrite mode requires `cv-instrument-pass-source`:

```bash
O2T/tools/cv-instrument-llvm-tree.py \
  --out-dir /tmp/o2t-instrumentation \
  --instrumenter O2T/build-clang-tools/cv-instrument-pass-source \
  --compile-commands /path/to/llvm/build/compile_commands.json \
  --passes instcombine,simplifycfg \
  --markers probe.instcombine.add-zero,probe.simplifycfg.diamond \
  /path/to/llvm/lib/Transforms
```

The script writes rewritten files under `rewritten/`, preserves originals under
`original/`, emits `instrumentation.patch`, and records rewritten/skipped/error
status per marker in `instrumentation-manifest.jsonl`.
When `--llm-findings` is provided, imported LLM findings are merged with static
findings and passed to the Clang tool as per-file candidate files so model-found
predicates can produce source patches.

## Build and Run Instrumented LLVM

`scripts/instrumented-llvm-playbook.sh` is the guarded handoff from generated
instrumentation patches to an LLVM build. It prints commands by default:

```bash
O2T/scripts/instrumented-llvm-playbook.sh \
  check /path/to/llvm-project /path/to/llvm-build
O2T/scripts/instrumented-llvm-playbook.sh \
  apply /path/to/llvm-project /tmp/o2t-instrumentation/instrumentation.patch
O2T/scripts/instrumented-llvm-playbook.sh \
  configure /path/to/llvm-project /path/to/llvm-build
O2T/scripts/instrumented-llvm-playbook.sh \
  run-opt --require-observed-probes /path/to/llvm-build /tmp/o2t-cases
```

Use `--execute` when you want the script to apply the patch, configure/build
`opt` and `llvm-as`, or run generated cases through the instrumented host tools.
Patch application checks that the LLVM checkout is clean unless `--allow-dirty`
is explicitly passed.

`run-opt` delegates to `opt-check-cases.sh` with:

```bash
O2T_HOST_OPT=/path/to/llvm-build/bin/opt
O2T_HOST_LLVM_AS=/path/to/llvm-build/bin/llvm-as
```

This keeps the same replay and manifest workflow as Docker-based checking, but
uses the instrumented LLVM binaries whose pass predicates record probe events.
For each case, the checker also sets `O2T_PASS_PROBE_LOG` to a
case-local file under `opt/`. Native `CV_PASS_PROBE*` calls append marker names
to that file, `cv-probe-oracle` compares them against the config's expected
markers, and the manifest records the observed markers plus oracle status.
Mismatches are informational by default. Pass `--require-observed-probes` to
`opt-check-cases.sh` or `cv-run-campaign.py` when missing or unexpected observed
markers should fail replay.

For a generated campaign, `cv-run-instrumented-campaign.py` chains `check`,
`apply`, `configure`, and strict `run-opt` through the playbook. It defaults to
dry-run command printing and writes `instrumented-commands.log` in the campaign
directory; `--execute` performs the LLVM checkout/build/replay steps.
