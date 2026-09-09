# RV third-party verification experiment

Date: 2026-09-08. Target: [RV Region Vectorizer](https://github.com/cdl-saarland/rv),
release/16.x commit `44e0bdb78889da1261596a0cacfdf12693e19e4e`.
This revision retains the standalone rvTool and upstream test suite. The checkout
contains 66 C++ implementation files (23,389 lines under src). No upstream source
was modified. This experiment checks selected transformations, not the whole pass.

## Execution and evidence

Built rvTool with LLVM 16.0.6 for AArch64/AdvSIMD. A local CMake include supplies
LLVM package integration and the source include path; it changes no pass logic.
Artifacts, scripts, build logs and SHA-256 provenance are in
[`build/rv-verification-20260908/`](../build/rv-verification-20260908/).
The checkout and executable are in `build/third-party-rv` and `build/rv-build`.

Eight unchanged upstream inputs exercise floating-point arithmetic, conditionals,
aggregate-returning calls, divergent loops, interleaved memory, masked stores and
strided stores. Four additional inputs exercise unsigned integer branches,
guarded division, varying loop counts and early exits. `cases.json` identifies
every source and vectorization shape; `transformations.json` records commands.

Each scalar input was compiled using Clang 16 at O1 with automatic vectorization,
loop unrolling and FP contraction disabled. The real rvTool then generated a
four-lane `foo_SIMD`. All 12 outputs passed LLVM's IR verifier. A reference wrapper
invokes scalar `foo` once per lane; the target wrapper invokes generated
`foo_SIMD`. Uniform arguments and contiguous memory indices follow RV's declared
shapes. Checking unchanged scalar `foo` against itself would miss the actual
transformation; the wrappers prevent that mistake.

For formal checking, llvm-link and always-inline embed foo/foo_SIMD into the
common `verify` interface, followed by SROA and simplifycfg. Helper functions in
the aggregate-call case remain calls. Native libraries use Clang 18 at O0.

## Results

| Case | O2T | Alive2 |
| --- | --- | --- |
| Integer branches | Proved; nonvacuous; Bitwuzla agrees | Accepted |
| Aggregate calls | Unsupported extractelement | Accepted with call abstractions |
| Integer loop | Unsupported extractelement | Accepted, bounded loop checking |
| Integer early exit | Unsupported extractelement | Accepted, bounded loop checking |
| Guarded division | Unsupported multi-block division/UB | Timeout |
| Floating arithmetic | Unsupported extractelement | Timeout |
| Floating conditional | Unsupported extractelement | Timeout |
| Loop with break | Unsupported extractelement | Wall timeout |
| Loop with varying count | Unsupported extractelement | Timeout |
| Interleaved memory | Escaped/uninitialized pointer unsupported | Timeout after TBAA removal |
| Masked stores | Unsupported load | Timeout after TBAA removal |
| Strided stores | Unsupported getelementptr | Failed to prove after TBAA removal |

Original memory modules hit Alive2's unsupported metadata error. A separate retry
removed only TBAA attachments; it produced no additional proof. Original modules
and logs remain available. Alive2 used default loop unrolling options and a
10-second SMT timeout, with an outer 50-second deadline (45 for retries).
Its loop successes are not claimed as unbounded proofs. The aggregate-call result
checks the call arrangement under Alive2's call model, not helper internals.

Native execution agreed on all **116,161 batches / 810,052 compared values**:

- Each integer case covered all 65,536 pairs of 8-bit values, 32-bit boundary
  inputs, all 16 lane mask patterns, and 4,096 random full-width batches.
- Each nonmemory floating case covered boundary inputs, mask patterns and 4,096
  deterministic random batches. The observed maximum numeric ULP difference was
  zero. The sqrt case allows one ULP; paired NaNs ignore payload differences.
- Each memory case compared 4,112 arrays of 32 floats, including untouched tail
  sentinels. Selected memory cases have disjoint per-lane accesses, making the
  serial scalar reference appropriate.

These samples are not exhaustive over 32-bit inputs, floating-point values,
pointer layouts or lane combinations. See `native-summary.json` and
`native_check.py` for exact counts, domains and comparison rules.

## Deliberately broken controls

Two isolated modifications to generated formal IR validate error detection:

1. Swapping result lanes 0 and 1: O2T refuted it with a witness, Bitwuzla agreed,
   and Alive2 reported a value mismatch.
2. Replacing the protected divisor with the original vector divisor: Alive2
   reported “Source is more defined than target,” exposing division by zero in
   inactive lanes. O2T still declined because its multi-block division model is
   incomplete.

Both mutated modules passed LLVM's structural verifier. They are planted errors,
not defects in RV. Witnesses and raw logs are in `negative-controls/`.

## Verification gaps and next work

No defect was detected in these RV outputs. O2T alone established one of twelve
refinements. The other eleven must remain explicitly unsupported, regardless of
native agreement or an independent oracle's result.

Priorities are path-conditioned division/UB, vector extraction across control
flow, loop-carried lane masks and reductions, and masked gather/scatter pointer
semantics. Agent-created checks should bind to the pinned executable and actual
generated output, retain negative controls, and report native evidence separately
from formal coverage. Floating-point solver timeouts also need explicit budgets.

This run added experimental harnesses and documentation only. It did not change
O2T behavior, run the complete upstream RV suite, or invoke DeepSeek. The checks
were authored during this session and executed against the compiled third-party
pass. No API credential was written into these artifacts.

## Replay

With the recorded checkout, rvTool and installed toolchains available:

```sh
python3 build/rv-verification-20260908/prepare_cases.py
python3 build/rv-verification-20260908/run_transformations.py
python3 build/rv-verification-20260908/native_check.py integer_branches
python3 build/rv-verification-20260908/formal_checks.py
python3 build/rv-verification-20260908/additional_checks.py
```

Run native_check.py once for each name in cases.json to replay all native checks.
formal_checks.py reuses per-case o2t.json caches; remove those cache files when
testing a changed validator or regenerated IR. Full commands and tool versions
are preserved in the experiment directory. Build artifacts are ignored by Git.
