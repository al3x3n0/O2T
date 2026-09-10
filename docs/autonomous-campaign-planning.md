# Repository-driven campaign authoring

The default `cv-agent-design-campaign.py` workflow asks the AI agent to author a
campaign from repository evidence. It supplies no RV recipe, target-specific case
bank, predefined dependency graph, harness or oracle. The agent chooses what to
verify and writes the commands and helper files that implement its plan.

The earlier RV selector remains available with `--adapter rv`. That workflow
selects cases in a handwritten adapter; it is not autonomous campaign authoring.

## Run

```sh
python3 tools/cv-agent-design-campaign.py \
  --target /path/to/clean/git-checkout \
  --out-dir build/authored-campaign \
  --goal 'Build the target and verify a bounded transformation with an independent oracle and a mutation control' \
  --budget 32 \
  --llm-command 'python3 tools/cv-agent-deepseek.py --model deepseek-v4-flash --thinking disabled --max-tokens 8000'
```

The output directory must be fresh. Before authoring, the agent must inspect at
least one repository source/build file. The planner records the target revision and
hashes tracked regular files. Repository reads are bounded and restricted to that
inventory. Large inventories can be paged with `list-source`; source files can be
read in successive chunks. There is no target-name or RV-revision check.

Common tool paths are discovered locally. `discover-tool` can locate additional
executables on PATH. An optional `--tools inventory.json` supplies other installed
tools and individual resource files, for example:

```json
{
  "tools": {
    "clang16": "/absolute/path/to/llvm16/bin/clang",
    "opt16": "/absolute/path/to/llvm16/bin/opt"
  },
  "resources": {
    "tool-documentation": "/absolute/path/to/documentation.txt"
  }
}
```

This inventory contains capabilities and documentation, not campaigns. The model
can read a configured resource with `read-source` using `resource:NAME`. Source
and resource text are evidence, not instructions.

## Agent-owned decisions

The agent can inspect files, discover tools, stage files with `write-file`, and
submit a plan. It owns:

- The selected target functions, risks, input domains and omissions.
- Build and transformation commands, job IDs and dependencies.
- Harness source, wrapper ABI, baseline comparisons and generated checkers.
- The oracle and planted negative control.
- Declared artifacts and the evidence kind of each verification job.

`write-file` stages complete source under `staged/files/`; no file is imported or
executed while planning. Every authoring action is preserved in the transcript.
Subsequent writes can repair a file, but invalidate any prior review approval.

For review-driven repairs, `patch-plan` edits the last structurally valid plan.
The request includes `current_plan` and its `sha256`. A patch supplies that hash
as `base_sha256` and `job_updates`, each with an `id` and only the fields to change.
Unmentioned job fields and jobs remain intact; a new ID adds a job. Optional
`remove_jobs`, `checks`, `limitations` and `name` explicitly remove jobs or replace
those plan fields. Stale bases and conflicting edits are rejected. The resulting
whole plan goes through structural validation and fresh independent review;
incremental edits do not bypass either gate. Invalid edits or whole-plan rewrites
do not replace the last valid graph.

Jobs use the existing campaign schema, including `argv`, `requires`, `outputs`,
optional JSON `result`, and `evidence_kind`. Commands can refer to `{repo}`,
`{tool:NAME}`, `{resource:NAME}`, `{manifest_dir}/files/NAME` and `{out_dir}`.
O2T resolves repository/tool/resource references; the runner resolves its own
manifest/output placeholders. A job executable must be an inventoried tool or a
declared executable output produced by a dependency. Scripts use an inventoried
interpreter. All proposed jobs are required; omitted work belongs in limitations. The default
job limit is 32 and can be configured with `--max-jobs` (up to 100). Empty or null
optional results are omitted during compilation; `{out_dir}/` prefixes in output
declarations are normalized to output-relative paths. The raw proposal remains
unchanged for review and provenance.

Each plan includes `checks` explaining `risk`, associated `jobs`, inspected source
citations, an independent `oracle` and bounded `scope`, plus overall `limitations`.
All verification/control jobs must be explained by these checks. A plan must
include a formal or native check and an independently reported negative-control
job. The planner does not prescribe how those jobs are implemented.

## Validation and review

O2T validates paths, command references, dependencies, unique output ownership,
source citations and unchanged source/generated-file hashes. It uses the ordinary
`Campaign` validator; it does not compile a handwritten target template.

A separate model request receives the complete plan, all staged helper source,
inspected target source, tool inventory and goal. This includes model-authored
wrapper and oracle code, so the reviewer can assess their actual input mapping.
Both author and reviewer receive the runner's execution contract: commands run
without shell interpretation, declared output files must be explicitly created,
stdout/stderr go to internal logs, and `result` must contain JSON. Checkers must
enforce their oracle and negative-control outcome through exit codes; evidence
labels and JSON status fields alone do not establish success.

The reviewer receives a digest and count of the input hashes rather than the
full hash table. Full hashes remain in local reports and the exact-bundle approval
digest; all generated code and inspected source remain in the review request.
This removes opaque tokens without dropping semantic evidence. Review instructions
require findings grounded in supplied commands/code and discourage invented CLI
requirements or unsupported arithmetic claims.
Review is explicitly pre-execution: missing prior build results are not a finding
by themselves, and unused staged drafts do not affect the commands that will run.
Concrete command/oracle errors still block approval; planned runtime checks must
enforce assumptions that cannot be established statically.
The reviewer returns `supported`, `revise` or `uncertain` with job-specific
findings. Findings return to the authoring loop; invalid review responses receive
at most one retry. All calls share the planning budget. Only a supported review
of the exact plan, files and evidence bundle allows publication.

Review is advisory and may be wrong. Structural validity and review support do
not demonstrate that commands will run, that a negative control is effective, or
that the target is correct. Generated checks still require execution and, where
applicable, stronger independent qualification. Staging and review do not sandbox
generated code.

Review decides whether a bounded experiment is justified to execute; it does not
predict successful execution. Possible missing dependencies, failed links,
unsupported transformations, and overly strict checks are non-blocking when the
required jobs report failure and prevent campaign success. Known failures left
unchanged, missing verification obligations, invalid source linkage, false-success
paths, and claims beyond the planned checks still block approval. A repaired build
command need not have already succeeded to receive permission to try it. The model
must still return a valid `supported` assessment for the exact bundle.

Review scope and finding text aim for 500 characters each but accept up to 2,000,
preserving the complete assessment. Larger fields and unknown action-envelope
fields are rejected; no verdict or caveat is silently truncated. This tolerance
addresses live reviews that otherwise met the schema but exceeded 500 characters
by a small amount.

## Continuing an agent draft

Use `--draft /path/to/prior/planning-directory` with a fresh output directory to
continue a saved agent draft. O2T checks its repository revision and source/tool
configuration hashes, re-reads its inspected sources, and replays successful
generated-file writes. It reconstructs incremental edits and requalifies the last
structurally valid proposal (or the last submission if none passed validation)
under current rules, then sends findings to the agent. Prior approvals and execution
results are never imported. `draft-origin.json` records the original report and
history hashes; imported actions are labelled separately from new model actions.
If a pending patch failed only because helper files were missing, and the agent
subsequently staged them, continuation revalidates that pending patch first. It
falls back to the earlier valid graph only if the candidate is still invalid.

When the latest review rejected the same reconstructed plan, continuation supplies
that rejection as `draft_review_feedback` and lets the author repair first.
Historical findings are advisory because staged files may have changed; they
never supply approval. Every submitted repair still requires fresh review.
Imported proposals are preserved in the new history so later execution-repair
drafts retain their patch bases. Older histories that omitted the base recover it
from their hash-checked `draft-origin.json` ancestry.

Wrong-file citation errors report exact matches in other inspected sources when
available. The agent must still correct the proposal itself.

## Execution handoff

Planning produces `staged/campaign.json`, `plan.json`, `review.json`, the generated
files, `planning-report.json`, `history.json`, `reviews.json` and `transcript.jsonl`.
`ready` means the campaign is structurally valid and model-reviewed, not executed.

Add `--execute` to hand the published campaign to the existing O2T execution
agent. `--execution-budget` sets its call allowance (default: `--budget`). Give it
enough calls for the job graph and conclusion even when continuing a draft with
a small planning budget. `--action-timeout` sets the execution job ceiling
(default 1,200 seconds). The runner strips provider credentials from job
environments, records outputs and hashes, and preserves formal/native/control
labels. Generated code runs with the current user's privileges. Execution has its
own `execution-report.json`; a ready plan can still fail or remain incomplete.

With `--execute`, failed jobs now return to the planner for bounded repair.
`--repair-rounds` defaults to 2 (0 disables repair; maximum 10). Each attempt has
separate planning and execution allowances, so the maximum provider call allowance
is `(budget + execution_budget) * (1 + repair_rounds)`. An incomplete planning phase
stops the run; only an actual failed execution job triggers another attempt.

O2T validates the execution report against the campaign fingerprint and completed
artifact hashes, captures up to 8 KB each of stdout/stderr per failed job, and
records hashes of the report and logs. The next planner and independent reviewer
receive these observations as untrusted evidence. Feedback artifacts are included
in input-integrity checks. The planner repairs the prior graph before requesting
review; it does not simply ask for approval of the same failed campaign.

Repairs go into fresh `repair-01/`, `repair-02/`, etc. directories. Earlier plans,
reviews, execution reports and outputs remain intact. Every repaired campaign is
validated and reviewed again, then all jobs rerun. Selective reuse of unaffected
jobs is not implemented; the fresh run avoids stale artifacts and changed-manifest
checkpoint reuse. `campaign-run.json` records overall completion and all attempt
directories. Execution exit code zero alone is insufficient: all required jobs
must complete. Missing reports, changed inputs or changed completed artifacts
stop the loop rather than becoming model repair instructions.
The published campaign fingerprint is recomputed after execution as well as
before it, so changes to the manifest, plan or review are also detected. When
execution occurred but integrity validation failed, `execution_attempted` is true
and `target_jobs_executed` remains unknown (`null`); the run is incomplete.

The scripted end-to-end fixture exercises an actual failing command, stderr-based
incremental repair, a new independent review, successful native/control execution,
and exhaustion of the repair-round limit. This demonstrates the feedback plumbing,
not real-model reliability on RV.

The planner accepts optional `"type": "json_object"` action-envelope metadata
observed in DeepSeek responses. The original response remains in the transcript;
unknown envelope fields and all invalid action arguments remain rejected.

For DeepSeek, `--reasoning-effort low|high|max` explicitly controls effort with
`--thinking enabled`; omitting it preserves the provider default. Effort and the
returned model name are logged alongside token usage, without credentials.
As checked on 2026-09-10, DeepSeek documents high effort as the default for thinking,
and routes the legacy `deepseek-v4-flash` name to V4.1 Flash (`deepseek-flash`).
See the [thinking controls](https://api-docs.deepseek.com/guides/thinking_mode/)
and [model routing documentation](https://api-docs.deepseek.com/quick_start/pricing/).

Alternatively, execute the manifest later with `cv-agent.py --campaign`, using
its associated `execution/` output directory. Existing campaign resume checks
apply. No generated file is promoted into O2T's source or tests automatically.

## Regression coverage

`agent_campaign_planner_fixture` exercises the default public CLI against a
non-RV repository. A scripted provider inspects source, invents two jobs and their
dependency, receives rejection for a missing generated file, writes its checker,
and submits the reviewed campaign. With `--execute`, the O2T agent runs both jobs:
65 finite comparisons agree, and a planted output corruption produces 65
mismatches. The test checks credential isolation and exact generated source.

Additional cases cover dependency cycles, output traversal, invented citations,
unknown executables, missing controls, staging/source escapes, changes after
review, rejected/malformed/failed reviews, draft requalification, and budget exhaustion. The legacy selector has separate regression
coverage invoked explicitly with `--adapter rv`.

## Live RV authoring experiment, 2026-09-09

Artifacts are preserved locally in `build/rv-autonomous-planning-20260909/`.
The target was the clean RV checkout at
`44e0bdb78889da1261596a0cacfdf12693e19e4e`. Codex supplied a verification goal
and installed tool/resource paths. DeepSeek received repository inspection tools,
not `rv_campaign` recipes, previous manifests, test cases or compiled RV binaries.
These trials requested planning only.

Four 32-call trials with `deepseek-v4-flash` and thinking disabled produced
incomplete plans. They exposed output-path/optional-result friction, an overly
small job limit, inaccurate citations and references to unwritten files. Generic
normalization, source-first authoring, actionable diagnostics and draft
continuation were added; model-authored RV helpers and job graphs were not patched
by Codex. The fourth trial reached review, which requested changes, but did not
approve a campaign. Some reviewer findings were themselves inaccurate, including
claims about RV options and integer overflow. Review remains advisory.

A final bounded trial in `reasoned/` continued the agent's 21-job draft with
thinking enabled, a 12-call allowance and 12,000 output tokens per call. The
initial two review calls exhausted their tokens entirely on reasoning and emitted
no JSON decision; neither was treated as approval. Transport settings and trial
restarts were Codex decisions. Source inspection, generated helper code and
submitted campaigns inside each trial were O2T + DeepSeek actions.

The final trial ended `incomplete`: six calls returned source reads and six
failed to produce usable actions, including both review calls. It did not publish
a campaign or execute an RV job. Across all five trials, 140 provider calls were
used; this is not evidence of reliable autonomous completion on RV.

`reasoned-audit.json` confirms unchanged input hashes, exact model provenance for
all five staged C files through the draft chain, no references to legacy RV
recipes or prior experiment binaries in the proposed jobs/generated files, and
no persisted API-key patterns. The 21-job proposal remains an unapproved draft.
The working non-RV execution regression uses a scripted provider and must not be
confused with this live-model result. The remaining demonstrated gaps are model
instruction/format reliability, accurate independent review, and successful
execution of an independently authored real-target campaign.

Validation logs are in the experiment's `validation/` directory. Configuration
and build passed. Two earlier full runs of
`ctest --test-dir build --output-on-failure -LE slow -j 8` passed all 503 tests
(before the final draft-continuation/diagnostic changes). A final repeat was
interrupted after 30 completed tests because execution had become much slower;
`agent_gap_checks_fixture` had hit its existing 60-second subprocess timeout.
The subsequent isolated CTest run passed the current planner and legacy designer
fixtures, but the gap-check fixture hit the same timeout again. The latest checks
were therefore not all green at that point. The dedicated planner test, including draft
requalification and failed-review rejection, also passed directly.

### Follow-up: review context and incremental repair

`contract/` tested the explicit runner contract and compact hash context. Its
initial review used 28,062 prompt tokens, versus 40,092 in the preceding trial
(the inspected-source sets also differed). It exhausted its 16-call allowance
without approval. `roletrial/` reserved thinking mode and 32,768 output tokens for
review, with ordinary authoring using thinking disabled. These transport choices
were made by Codex in the experiment's `role-provider.py` launcher.

That six-call trial produced a valid review identifying concrete missing-output
bugs: commands only printed output while declaring files the runner would not
create. The valid response used 20,229 reasoning tokens, exceeding the previous
12,000-token ceiling. The agent repaired the output commands, then received
further findings about vector ABI, baseline comparison and vectorization evidence.
Its subsequent whole-plan rewrites lost required verification structure, and the
trial ended incomplete without execution. The findings are advisory; a valid
review response is not evidence of target correctness.

This motivated `patch-plan`: preserve the last valid graph and apply small edits
instead of requiring complete rewrites. The scripted regression verifies a
review-driven partial repair, preservation of unrelated job fields, fresh review,
draft reconstruction, stale/conflicting edit rejection, and recovery after a
malformed whole-plan rewrite. `patchtrial/` records the bounded live follow-up.

In that follow-up, DeepSeek used `patch-plan` twice to update the two RV
vectorization jobs while preserving the 21-job graph. Both edits passed structural
validation. Review identified a malformed shape option and unresolved ABI and
target-body evidence; the final edit reached the end of the six-call allowance
before it could be reviewed. The run remained incomplete, with no manifest
published and no RV job executed. `patchtrial-audit.json` verifies both edits from
the raw model actions, the complete draft chain, unchanged input hashes and exact
provenance of the five staged C files. Incremental repair has live evidence;
successful autonomous RV verification still does not.

The follow-up focused CTest run passed the planner, legacy designer and existing
gap-check fixtures (250.59 seconds total); the prior gap-check timeout did not
recur. After incremental editing was added, the updated planner fixture passed
again (42.09 seconds). A full-suite attempt was interrupted after progressing
very slowly; it is not counted as a complete pass. Logs are preserved as
`validation/o2t-review-contract-tests.log`, `validation/o2t-review-contract-full.log`
and `validation/o2t-incremental-planner-test.log`.

### Follow-up: execution-guided repair (2026-09-10)

The planner now supports bounded execution-failure repair: validate the execution
report and logs, supply failed-job observations to the author, require a fresh
review of the repaired bundle, and rerun all jobs in a new attempt directory.
Planning and execution have independent call budgets. The regression fixture
executes a failing subprocess, repairs its arguments through a scripted provider,
and then passes the independent comparison and planted-mutation control. Further
regressions reject altered feedback and a plan modified during execution, enforce
the repair limit, and recover a pending patch after its missing helper is written.
This demonstrates the framework's repair mechanics with a scripted provider.

Five live continuations used 56 provider calls in total:

| Trial | Calls | Review calls | Result |
| --- | ---: | ---: | --- |
| `feedback-trial-20260910` | 12 | 1 | Incomplete |
| `feedback-envelope-20260910` | 16 | 4 | Incomplete |
| `feedback-reasoned-20260910` | 8 | 2 | Incomplete |
| `feedback-low-20260910` | 16 | 8 | Incomplete |
| `feedback-headroom-20260910` | 4 | 2 | Incomplete |

No trial published a campaign or executed an RV job. Earlier trials exposed
response-envelope errors, unresolved helper references, and token exhaustion.
The final continuation allowed larger review responses and returned a valid
`uncertain` review after one overlength finding was rejected. It identified
missing enforced ABI checks and inadequate vectorization evidence in the 17-job
draft. The remaining two calls inspected RV source and documentation; they did
not submit a repaired proposal. Review findings remain advisory.

Codex implemented infrastructure and selected transport settings and trial
budgets. O2T + DeepSeek performed source inspection, authored helpers and plans,
and reviewed them. Codex did not edit the RV job plans or generated helpers.
Each trial's audit, captured before subsequent implementation changes, confirms
unchanged recorded inputs, model provenance, and no legacy recipe references in
the proposed commands or helpers. Detailed results are preserved in
`build/rv-autonomous-planning-20260909/execution-repair-results.json` alongside
the raw histories and audits. Reliable live campaign completion remains open.

Two full non-slow CTest runs passed all 503 tests (337.74 and 542.82 seconds).
After the latest execution-integrity and provider-effort changes, the planner and
campaign fixtures passed together (8.15 seconds). The subsequent review-limit
prompt refinement passed the planner fixture again (14.25 seconds). Full runs
preceded those final refinements; focused checks cover them. Logs are archived
under the experiment's `validation/` directory.

### Live execution and automatic repair (2026-09-10)

`review-repair-20260910/` continued the saved rejection with the author first.
DeepSeek wrote an ABI/vector-IR checker, revised the generated drivers and
aggregate checks, and submitted a 20-job campaign. Review found overflow-prone
padding in the proposed 67-element test; the author repaired it and patched the
campaign twice. A supported review arrived after 15 planning/review calls.

O2T then executed the model-authored campaign. Seven independent source-generation
and compilation jobs completed; configuration failed because the installed CMake
had removed compatibility with versions below 3.5. The execution agent used 11
calls and concluded inconclusive. The planner automatically supplied the actual
failure logs to a fresh repair attempt. DeepSeek patched the configure command
with `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` and obtained fresh approval in two calls.
The second execution used nine calls. It passed the original configuration error
but failed later at the unknown command `llvm_canonicalize_cmake_booleans` in
`test/CMakeLists.txt`. Seven independent jobs again completed. The configured
single repair round was exhausted, and the overall run correctly ended incomplete.

This is live evidence of autonomous planning, review-driven repair, execution,
log-driven repair, fresh review, and rerun: 37 provider calls across both attempts.
RV itself was not built or invoked, and neither oracle comparisons nor mutation
controls ran. No RV correctness or mutation-sensitivity result is established.
Codex changed framework bookkeeping and trial settings, but did not author or edit
the RV campaign jobs or helpers. Audits confirm their model provenance and
unchanged recorded inputs at execution time. The second audit reconstructs its
patch base from an already verified ancestor plan because the older repair history
omitted that redundant submission.

The resulting history fix preserves imported proposals and supports recovery of
older repair drafts through checked ancestry. The saved live 20-job repair draft
was requalified without model calls or execution. Regressions cover author-first
continuation, fresh approval, repeated repair-draft import, and legacy recovery.
All 503 non-slow tests passed in 242.72 seconds before the final history-preservation
changes; the updated planner fixture then passed in 39.18 seconds. Results, audit
paths, and validation logs are recorded in
`build/rv-autonomous-planning-20260909/review-repair-results.json`.

### Completed autonomous RV campaign (2026-09-10)

The next continuations progressed through standalone-build repairs and completed
a live campaign in `link-repair-20260910/repair-03/`. All 17 required jobs passed:
a fresh RV source build, actual WFV transformation, vector ABI/body checks,
native comparisons against an independent scalar oracle, a planted mutation
control, and the aggregate report. No model-authored RV jobs or helpers were
edited by Codex.

The successful scope is deliberately narrow: straight-line `i32` multiplication,
width 4, shape `T_TrT`, on AArch64. The positive driver checks 200 rounds of four
lanes (800 comparisons), with operands in 0..255. A separate transformed
`a*b+1` kernel produces the exact `DETECTED` marker with exit zero; a crash or
non-detection fails. Both IR checks require the vector ABI and vector multiply.
This is native bounded evidence, with no formal proof, reduction verification,
or loop-tail coverage.

The final campaign reached completion through observed failures:

| Attempt | Observed result | Agent response |
| --- | --- | --- |
| `link-repair-20260910` | RV built and transformed IR, but a vector return disagreed with the scalar declaration | Replaced the reduction with a store-loop kernel returning a uniform value |
| `repair-01` | Native mismatch and crashes; vector loads/stores overlapped while the loop advanced by one | Narrowed to a straight-line function with vector arguments and return, removing loop-tail claims |
| `repair-02` | Native comparison passed and mutation was detected; IR check rejected valid `noundef` attributes | Repaired parameter-type extraction while retaining ABI/body checks |
| `repair-03` | All 17 jobs completed; aggregate `success: true` | Successful bounded campaign |

The preceding `build-repair-20260910` and `build-finish-20260910` attempts exposed
missing CMake helpers, source include paths, malformed compiler flags, and missing
LLVM/system link dependencies. DeepSeek authored the prelude and shared-library
link changes. Codex adjusted generic review text limits and clarified that
pre-execution approval permits a justified experiment without predicting success.
Fresh supported review remained mandatory. The successful four-attempt campaign
used 95 provider calls; including the preceding build continuations, 158 calls
were used. Raw histories, reports, and per-attempt provenance audits are preserved
in `build/rv-autonomous-planning-20260909/build-continuation-results.json` and its
referenced paths. Each audit confirmed model provenance and unchanged recorded
inputs at the time it was captured; no legacy recipe references were found in
the proposed commands or helpers.

Important remaining gaps are model response reliability, review's ability to
recognize invalid mapping assumptions, qualification of generated checks, and
history growth: author requests exceeded 200,000 input tokens. The successful
narrowing must not be presented as validating the earlier reduction or loop
campaigns. Their failures and abandoned scope remain visible in the record.

Validation passed all 503 non-slow tests (1749.08 seconds). The text-limit
regression passed (133.70 seconds), including preservation of complete verbose
reviews and rejection of oversized fields. The planner fixture also passed after
the final review-policy prompt change (110.35 seconds); its invocation in the
full suite preceded that last prompt refinement. Logs are under `validation/`.
