# Autonomous repair of RV supplemental checkers

This experiment strengthens qualification of DeepSeek-generated native checkers
from the [full RV campaign](rv-full-agent-campaign.md). It targets two concrete
weaknesses found by the [follow-up controls](rv-autonomous-gap-checks.md): numerical
float comparison missed a signed-zero output change; odd-only divisors never
exercised the zero-divisor guard.

## Workflow and ownership

Codex implemented the production validation gate, explicit input coverage
requirements, mutation library builders, transport feedback and regression
fixtures. The RV recipe now declares both the original control and the targeted
control for these cases. O2T independently requires detection of every mutation,
replays witnesses, measures required inputs, and runs the generated fixture
against each control. It also verifies that the fixture fails with a broken
sibling checker.

The live trials use the unchanged previous model-generated checker and fixture as
readable artifacts. O2T first runs them under the stronger gate and returns the
rejection evidence. DeepSeek selects the sole configured gap in each trial and
writes a repaired standalone checker and fixture through `synthesize-gap-check`.
O2T validates the candidate and returns any rejection for another attempt. The
budget is three staged candidates and sixteen model calls per trial. Generated
repairs stay staged; they are not automatically promoted into the test suite.

The model is `deepseek-v4-flash`, initially thinking enabled, with a 16,000-token
response limit. A float-only retry disabled thinking after two truncated replies.
The provider rejects token-limit-truncated responses and O2T supplies
sanitized feedback on the next call. The API key is supplied only through the
provider environment, not stored in experiment files or passed to native jobs.

## Cases and requirements

| Case | Additional mutation | Required inputs |
| --- | --- | --- |
| `upstream_simple` | Flip the sign of a zero result in lane 0 | Positive and negative zero in every lane of both input arrays |
| `guarded_division` | Corrupt outputs only where the divisor is zero | Zero and nonzero divisors in every lane |

Coverage counts only distinct paired native inputs. These requirements establish
specific input coverage; they do not measure program branch coverage or exhaust
all possible edge cases. Float output comparison distinguishes signed zeros even
with a one-ULP allowance, while paired NaNs agree.

## Artifact scope

Artifacts are under `build/rv-checker-repair-20260908/`, with separate
`upstream_simple/` and `guarded_division/` campaigns. Each contains the manifest,
checkpoint, provider transcript, old rejection evidence, staged candidates and
validation reports. `prepare.py` copies the old checkers unchanged and rebuilds
controls using the production mutation factory.

The before/after libraries and formal results are reused from
`build/rv-full-agent-fixed-20260908/`, generated from RV commit
`44e0bdb78889da1261596a0cacfdf12693e19e4e`. These trials do not rebuild RV or repeat
formal verification. Both cases retain their original unsupported O2T formal
status. Acceptance means finite native evidence against the configured control
bank, not a correctness proof or an independent discovery of new formal gaps.

The strengthened validator invalidates historical accepted checkpoints. Old
reports remain historical evidence; these are fresh qualification campaigns.

## Live outcomes

| Trial | Model calls | Staged candidates | Outcome |
| --- | ---: | ---: | --- |
| Float, thinking enabled | 5 | 0 | Incomplete: two replies consumed the entire response budget in reasoning |
| Float, resumed with thinking disabled | 5 additional | 3 | Incomplete: all candidates rejected |
| Guarded division, thinking enabled | 9 | 1 | Complete: repaired checker accepted |

The float retry was a Codex transport-setting intervention after the initial
session stopped. It was not an autonomous model choice. No generated checker or
fixture was edited by Codex. Both original trials' reports and the float
`retry-report.json` are preserved; `audit.json` describes the final outcomes.

The old integer checker failed the new zero-divisor coverage requirement.
DeepSeek's replacement used **512 distinct input pairs**, with **80 zero** and
**432 nonzero** divisors per lane. Baseline and identity had no mismatches. The
original mutation differed on all 512 pairs, and the zero-divisor mutation on
128. O2T replayed both witnesses, passed the fixture against both controls, and
confirmed that the fixture rejected a broken sibling checker. Resuming the
completed checkpoint with `/usr/bin/false` as provider used **zero model calls**.

The old float checker failed because its reported comparison disagreed with
O2T's independently observed signed-zero outputs. The first two replacement
candidates covered signed-zero inputs but generated no witness for the
zero-result mutation (512 and 272 distinct pairs respectively). The third
candidate used 1,024 distinct pairs but missed required signs in several lanes.
O2T rejected all three and preserved formal status as unsupported. Input coverage
alone did not ensure that the relevant output condition occurred.

The remaining repair gap is selecting inputs that actually reach the mutated
output condition while retaining all required per-lane coverage. A future
extension can make native output observations more useful to input generation.
Simply increasing sample counts or disabling reasoning did not solve this trial.

The final audit verified every staged checker and fixture byte-for-byte against
a model reply, campaign fingerprints, job-output and validation-artifact hashes,
unchanged original checkers, and unchanged reused native libraries. The RV
checkout was clean and the experiment credential scan found no key material.

## Regression validation

`ctest --test-dir build --output-on-failure -LE slow -j 8` passed all **501** tests
with zero failures in 326.14 seconds. Five nightly slow tests are outside this
per-change gate. Logs and a machine-readable summary are in the experiment's
`validation/` directory.

Focused fixtures cover missed coverage, repeated-input counting, controls outside
the old input distribution, signed-zero comparison, fixture execution per control,
autonomous rejection/repair through the CLI, action-name validation and truncated
provider replies. The existing resume integrity and unchanged-formal-result
checks remain covered.
