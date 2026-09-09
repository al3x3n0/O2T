# Full RV campaign with autonomous supplemental checks

The RV recipe connects fresh compilation and verification to model-selected gap
checks in one production `cv-agent.py` session. It targets the existing clean RV
checkout at commit `44e0bdb78889da1261596a0cacfdf12693e19e4e`, on macOS AArch64 with
LLVM 16 for RV/IR and LLVM 18 for native wrappers. Toolchain installation and
checkout acquisition remain operator prerequisites.

## Run

Create a manifest with explicit local toolchain paths:

```sh
python3 tools/cv-agent-rv-campaign.py \
  --rv-source build/third-party-rv \
  --llvm16 /opt/homebrew/opt/llvm@16 \
  --llvm18 /opt/homebrew/opt/llvm@18 \
  --z3 /opt/homebrew/bin/z3 --alive2 /opt/homebrew/bin/alive-tv \
  --directory build/my-rv-campaign

python3 tools/cv-agent.py \
  --campaign build/my-rv-campaign/campaign.json \
  --out-dir build/my-rv-campaign/out \
  --llm-command 'python3 tools/cv-agent-deepseek.py --model deepseek-v4-flash --thinking enabled --max-tokens 16000' \
  --enable-synthesis --budget 30 --max-steps-per-pass 30 \
  --llm-timeout 200 --action-timeout 1200 \
  --report build/my-rv-campaign/report.json \
  --summary-text build/my-rv-campaign/summary.txt
```

Supply `DEEPSEEK_API_KEY` through the process environment or a credential prompt;
never store it in the manifest. Use a new output directory for a fresh run.
Completed checkpoint resume reuses validated outputs and is explicitly a resume,
rather than a fresh experiment. See [campaign configuration](agent-campaign.md)
for checkpoint integrity and execution permissions.

## Execution and responsibility

The operator-supplied recipe declares nine jobs:

1. Check that the RV checkout is clean and matches the pinned revision.
2. Configure RV under the run's new `rv-build/` directory.
3. Build its actual `rvTool` executable.
4. Compile eight upstream inputs and four targeted inputs.
5. Transform them with that new executable, verify the LLVM IR, and compile scalar/vector libraries and formal obligations.
6. Run O2T refinement with solver cross-checking and independent Alive2 checks.
7. Run the supplied native differential corpus.
8. Run planted formal defects and metadata-compatibility retries.
9. Build generic planted native defects from this run's vector IR.

DeepSeek chooses ready jobs and inspects recorded artifacts through O2T. Once
formal gaps and native bindings are available, it chooses a case and writes its
own checker and fixture. O2T independently validates native calls, controls and
witnesses, returning rejection diagnostics for bounded repair. Completion requires
all required jobs and at least one accepted supplemental check, unless no eligible
formal gaps remain. Codex does not supply the gap-specific checker.

The helper scripts in `tools/rv_campaign/` use the explicit config and output
directory. They do not read earlier experiment artifacts; formal verification is
executed afresh without a per-case result cache. The manifest fingerprints its
helper scripts, configuration and tracked RV files. Installed toolchain contents
and transitive O2T imports are not exhaustively fingerprinted.

## Report

The single JSON report includes `evidence_summary` with separate lists for:

- `formal`: observed O2T results and independent Alive2 outcomes, with scope.
- `native`: finite samples, counts, differences and witnesses.
- `controls`: deliberately planted defects and compatibility retries.
- `supplemental`: accepted/rejected generated checkers and observed native traces.
- `unresolved`: formal cases still unsupported, unknown or inconclusive.
- `execution_issues`: jobs that did not complete.

These are summaries of recorded tool output, not additions to the agent's trusted
formal headline. Accepted sampling leaves a case in `unresolved` if its formal
result remains unsupported. Bounded loop checks and abstract helper calls retain
their scope; a planted defect is not reported as an RV bug.

## Validation and live runs

The first full run is preserved under `build/rv-full-agent-20260908/`. It stopped
after five model calls because the recipe omitted the working build's
`LLVM_BUILD_LLVM_DYLIB` / `LLVM_LINK_LLVM_DYLIB` settings. Static LLVM dependencies
introduced C-header search paths ahead of libc++, and `rvTool` compilation failed.
O2T recorded the failed job, completed independent input preparation, and left
verification blocked. Codex corrected the recipe and validated `rvTool.cpp` with
the resulting compiler flags before starting another run; RV source was unchanged.

The corrected full run uses `build/rv-full-agent-fixed-20260908/`. It starts from
a new build directory. Its `validation/` directory contains the regression logs:
all 501 tests passed in `ctest --test-dir build --output-on-failure -LE slow -j 8`.
The focused campaign/gap fixtures and the regression for shared-LLVM configuration
also passed. Five nightly tests labelled `slow` were outside the per-change gate.

The corrected campaign completed all nine jobs in fourteen DeepSeek calls.
All twelve transformations used its newly built executable. Results were one O2T
proof, eleven unsupported cases, four independently accepted Alive2 obligations
with their limited scope, 810,052 agreeing native values, and two refuted planted
formal defects. Three metadata compatibility retries remained skipped.

DeepSeek selected `upstream_simple` and generated
`cv-agent-upstream-simple-gap.py`. It corrected an invalid tool-name prefix using
O2T feedback. The first staged candidate passed: 4,096 distinct four-lane input
pairs agreed on the actual libraries, and the planted output defect produced
4,071 differing pairs with a replayed witness. Inputs mix fixed boundary values
and seeded finite float32 samples. Codex did not edit the checker or its fixture.
The audit verifies exact model-source correspondence, declared artifact hashes,
the clean RV checkout, and use of the fresh executable. Completed resume used zero
model calls. Formal unsupported cases remain unresolved in the combined report.

## Held-out control finding

Codex then supplied an additional native control that flips only the sign of a
zero output. This control was not part of the original acceptance test. The
generated checker uses numerical float equality, which treats positive and
negative zero as equal. It reported zero differences; O2T's independent trace
observed eighteen and rejected the candidate under the stronger control.

The concrete witness includes inputs `[-1, 127, -1, 128]` and
`[1, -0.0, 128, 0.0]`, with lane zero changing from `-0.0` to `+0.0` in the planted
variant. This is a checker limitation and a deliberately introduced defect, not
an RV bug. The result is preserved in `heldout-zero-sign/validation.json` under
the corrected experiment directory. The original checker remains untouched.

Acceptance is therefore scoped to the supplied artifacts, sampled inputs and
controls. A broader control bank is needed before considering generated checkers
for standalone reuse. Keeping O2T's independent trace in the execution path
detects this reporting error.

## Repeatability pilot

Two additional sessions reused only the full run's reference artifacts and formal
results. Previously selected cases were excluded; DeepSeek chose each remaining
case itself. The model and limits were unchanged: DeepSeek v4 Flash with reasoning
enabled, 16,000 output tokens, and at most three staged candidates. The repeats
had twenty-call budgets. No Codex instructions or checker edits intervened between
model calls within any of these three sessions.

| Session | Selected case | Calls | Staged candidates | Result under original controls |
| --- | --- | ---: | ---: | --- |
| Fresh full campaign | `upstream_simple` | 14 | 1 | Accepted |
| Repeat 1 | `integer_loop` | 8 | 0 | Incomplete |
| Repeat 2 | `guarded_division` | 6 | 1 | Accepted |

The first repeat proposed an invalid tool name, then exhausted the output-token
limit on two replies and degraded without staging a checker. Naming constraints
need to be clearer in the action schema, and truncated-response recovery needs
improvement. This is a small, case-diverse pilot, not an estimated general success
rate.

The division checker passed 512 distinct input pairs and detected the original
planted defect in all 512. However, its generator forces every divisor odd with
`v | 1`, excluding zero. Codex supplied a held-out control that changes results
only when a divisor is zero. The checker never exercised the faulty branch, and
O2T rejected it for failing to detect the planted defect. Independent native replay
on numerators `[1, 2, 3, 4]` and zero divisors confirmed that the control changes
results from `[1, 2, 3, 4]` to `[0, 3, 2, 5]`.

Thus both accepted checkers failed an additional targeted control. The next
validation improvements are a broader mutation bank, explicit boundary/branch
coverage requirements, and clearer action-name/output constraints. The checkers
remain staged; their original finite agreement results remain valid.

`integration-results.json` under the corrected experiment directory combines the
three session outcomes, both held-out findings, artifact/source audits and test
results. `repeatability.json` records the two reuse trials; each `repeat-*`
directory preserves its own manifest, checkpoint, transcript and report.

The [next integration](rv-checker-repair.md) implements multiple mutation controls,
measured input coverage, strict signed-zero comparison and transport feedback.
Fresh qualification accepted DeepSeek's integer repair; the float repair remained
incomplete after three rejected candidates. The full 501-test per-change gate
passed. Those trials reuse this campaign's binaries and formal results.
