# Legacy RV campaign selection and review

The default CLI now uses [repository-driven campaign authoring](autonomous-campaign-planning.md).
This document records the earlier handwritten-adapter workflow, available with
`--adapter rv`; it does not autonomously author the job graph or harnesses.

`tools/cv-agent-design-campaign.py --adapter rv` lets the model inspect source and select a
verification scope before the execution agent runs. The first adapter supports
the pinned RV workflow on macOS AArch64 with the configured LLVM 16/18 toolchains,
Z3 and Alive2. It selects from the existing twelve-case corpus; arbitrary GitHub
projects, new build recipes, new harness source and new mutation implementations
are not yet supported.

## Run

Supply a clean pinned checkout and the same toolchain configuration used by the
[RV recipe](rv-full-agent-campaign.md):

```sh
python3 tools/cv-agent-design-campaign.py --adapter rv \
  --target build/third-party-rv \
  --toolchain-config /absolute/path/to/config.json \
  --out-dir build/my-designed-campaign \
  --goal 'Cover control flow, guarded division and floating-point edge cases' \
  --max-cases 4 --budget 14 --llm-timeout 150 \
  --llm-command 'python3 tools/cv-agent-deepseek.py --model deepseek-v4-flash --thinking disabled --max-tokens 6000'
```

The configuration contains absolute paths for `rv`, `llvm16`, `llvm18`, `z3` and
`alive2`, plus the pinned `revision`. CMake must be on PATH. The designer checks
executable availability and checkout identity; this is not a toolchain version
compatibility test. Supply `DEEPSEEK_API_KEY` through the environment as usual.

By default, design does not execute target jobs. Add `--execute` to hand the
accepted manifest directly to the existing execution agent with supplemental
checker synthesis enabled. Design and execution each receive their own
`--budget` call allowance, so the combined ceiling is twice that value. They use
separate reports and transcripts. A design may be accepted while execution later
fails or remains incomplete.

Alternatively, run the emitted manifest with `tools/cv-agent.py --campaign ...`
using the reported `execution/` output directory. Existing campaign checkpoint
and artifact-integrity checks apply during execution.

## Decisions and validation

The model receives a bounded source inventory, available cases, mandatory
controls, coverage requirements and the user's goal. It has two actions:

- `read-source`: read an inventory path or an adapter-provided targeted input.
- `submit-design`: submit a name, selected cases and scope limitations. Every case
  names its risk, cites a source excerpt (whitespace formatting may differ) from its inspected source, and may add
  input-coverage requirements.

For example, a selected case entry is:

```json
{
  "id": "guarded_division",
  "risk": "Exercise the guard that protects unsigned division from a zero divisor.",
  "evidence": {
    "path": "@targeted/guarded_division",
    "quote": "if (y == 0) return x;"
  },
  "coverage": []
}
```

Empty additional coverage keeps all mandatory requirements. The model cannot
replace a mandatory coverage ID, remove controls, change the ABI, supply commands,
or weaken the fixture and witness gates. Additional requirements use the
[existing coverage schema](agent-campaign.md). Invalid submissions return concrete
feedback within the call budget. Acceptance requires source citations, valid case
selection, consistent coverage and unchanged source/configuration/adapter hashes.
It also requires a separate model review of the exact submitted plan. O2T sends
the inspected case sources, ABI, mandatory controls, proposed coverage, goal and
limitations to the reviewer without the designer's conversation. Each case and
the overall goal receive `supported`, `revise` or `uncertain`. Every case needs a
matching source citation and a coverage assessment. Only all-supported reviews
permit compilation; malformed replies, provider errors, uncertainty and exhausted
budgets leave the campaign uncompiled. Review findings return to the designer for
repair, and any changed plan must be reviewed again.

An unusable review receives at most one automatic retry on the unchanged plan,
if budget remains. The retry gets a bounded validation error and a complete JSON
example, not raw provider output or the designer's conversation. Both attempts
are recorded. Valid `revise` and `uncertain` assessments are never retried in
search of approval; they return to the designer immediately. O2T does not repair
malformed JSON or infer approval from a partial response. Reasons and coverage
assessments are limited to 500 characters to keep reviews compact.

Designer and reviewer calls share the design budget. The same configured provider
is used in separate contexts; this is not an independent verifier or a different
model. Review is advisory screening and can itself be mistaken.
A correct citation does not establish that the model's risk interpretation is
correct or that the campaign adequately covers the user's goal.

Use `--initial-plan /path/to/design.json` to requalify a saved plan under the
current review gate. O2T imports it unchanged, reads the selected sources through
the same bounded reader, and requests review before any designer repair. Imported
plans and source reads are labelled separately from model-authored actions. The
output directory must still be fresh.

O2T compiles the selected cases into the established dependency graph: inspect,
configure, build, prepare, transform, formal checks, native checks and controls.
All these jobs remain required. Input preparation and case-specific additional
checks honor the selected subset. Applicable per-case native mutations and
supplemental-check bindings are retained. The model chooses verification scope
and added input requirements; the adapter supplies executable implementations,
ABIs, baseline controls and dependency rules.

The inspected inventory contains tracked source/build/documentation files up to
200 KB, excludes symlinked files and paths outside the target, and returns at most
16,000 characters per read. The prompt lists at most 1,000 paths and indicates
truncation. Targeted corpus entries are explicitly labelled adapter inputs, not
upstream RV source. Unsupported targets fail explicitly rather than receiving an
invented build recipe.

## Artifacts

- `design-report.json`: acceptance/incompletion, selected and omitted cases,
  limitations, source/configuration hashes and the compiled campaign fingerprint.
- `transcript.jsonl` and `design-history.json`: model replies, reads and validation
  feedback, including rejected submissions.
- `compiled/design.json`: the accepted model plan.
- `reviews.json`: every review, its exact plan digest and model-reply sequence.
- `compiled/review.json`: the supported review, fingerprinted as a campaign input.
- `initial-plan.json`: the imported plan when requalification is requested.
- `compiled/campaign.json`, `config.json`, `coverage.json`: executable campaign
  and its selected inputs/requirements.
- `execution-report.json` and `execution/`: produced when execution is requested.

The output directory must be fresh. Designs do not resume partial conversations;
execution of an accepted design uses ordinary campaign resume. Accepted design is
advisory configuration, never proof. Execution completion, finite agreement and
formal results remain separate.

## Validation

`agent_campaign_design_fixture` exercises the public CLI with a scripted provider
that first submits an unread citation, receives rejection, reads the source and
repairs its plan. It checks unknown cases, invented quotes, duplicate selection,
command injection fields, missing limitations, weakened mandatory coverage,
incompatible predicates, out-of-inventory reads, changed planning inputs and
budget exhaustion. It compiles the accepted manifest and runs the real input
preparer with a fixture compiler to verify that only the selected case is built.
Review fixtures also cover missing cases, fabricated reviewer citations, invalid
statuses, uncertainty, budget exhaustion, rejection and repair of an imported
false rationale, and prevention of approval reuse for a changed plan.
Additional coverage exercises malformed-response retry through the actual CLI
transport, unchanged plan hashes across retries, isolation of feedback from raw
provider stderr, and the two-attempt ceiling.

## Live RV trial, 2026-09-08

Artifacts are in `build/rv-campaign-design-20260908/`. The initial 14-call trial
failed because the model collapsed whitespace in multi-statement citations and
the original validator required exact formatting. That report and transcript
remain under `design/`. After changing citation matching to ignore whitespace
formatting and making unread-source feedback explicit, a fresh trial completed
in **11 calls**, including four source reads and six rejected submissions.
The accepted model plan is unchanged in `design-retry/compiled/design.json`.

DeepSeek selected four cases:

| Case | Actual source behavior |
| --- | --- |
| `upstream_ifelse` | Uniform branch choosing `sqrtf` or `fabs`, then scaling |
| `integer_branches` | Unsigned branches, wraparound arithmetic and constant shifts |
| `integer_loop` | Input-dependent bounded loop with unsigned updates |
| `upstream_strided_store` | Load, multiply and store at odd array indices |

The model added no extra coverage predicates. O2T preserved applicable baseline
controls and bindings and listed the eight omitted corpus cases. The audit
verified the plan against model reply 11, all recorded source/configuration/adapter
hashes and the compiled campaign fingerprint. No target job or fresh RV build was
run during this live design test, and the credential scan was clean. This trial
validates design and compilation; it does not establish that the selected
campaign will complete or prove RV correct.

Codex's post-design review found a false rationale about variable shifts in
`integer_branches` (the source shifts by a constant three). The float rationale
also discusses NaN/Inf without clearly distinguishing possible NaN outputs from
direct special-value inputs, which the finite input ABI excludes. Those model
claims remain preserved and labelled advisory. The next design-quality gate
should check risk explanations and proposed coverage against actual source and
ABI constraints; citation matching alone does not establish semantic adequacy.

The final configure/build completed successfully, and
`ctest --test-dir build --output-on-failure -LE slow -j 8` passed all **502** tests
in **295.06 seconds**. Five nightly slow tests are outside the documented
per-change gate. Logs and the validation summary are under the experiment's
`validation/` directory.

## Source/ABI review follow-up, 2026-09-08

The review gate described above was added after the first design trial. Its live
requalification artifacts are under `build/rv-design-review-20260908/`. O2T
imported the previous four-case plan unchanged and read its selected sources.
DeepSeek used separate design and review requests through the same
`deepseek-v4-flash` provider with thinking disabled.

The first review required revision: it challenged the direct-NaN/Inf claim,
questioned the shift rationale, and marked loop coverage uncertain. The designer
then proposed repairs. Four subsequent review replies were malformed JSON and
were rejected. The **16-call** budget comprised **5 review calls** and **11 design
calls**. The final status was **incomplete**, and **no executable campaign or RV
build was produced**. `reviews.json`, `design-history.json` and `transcript.jsonl`
preserve the full sequence; `audit.json` records unchanged seed/source/configuration
hashes, separate reviewer contexts and a clean credential scan.

This demonstrates rejection and bounded handling of review failures, not a
successful live repair. The scripted regression fixture does exercise successful
review rejection, designer repair and compilation of the exact reviewed plan.

The reviewer also made mistakes: it suggested a direct NaN input despite the
finite-input ABI and digressed into shift-width behavior instead of clearly
identifying the constant shift amount in the actual source. These findings remain
model advice. They cannot replace ABI validation or promote a formal verdict.
Further work should improve reviewer response reliability and supply stronger
source-derived facts for assessing risk explanations. A schema-valid model review
alone cannot certify semantic adequacy.

The review follow-up passed all **502** tests in the same per-change gate, with
zero failures in **802.34 seconds**. The five nightly slow tests were excluded.
Focused and full-gate logs are under `build/rv-design-review-20260908/validation/`;
`results.json` combines the live outcome, audit and regression summary.

## Bounded reviewer retry, 2026-09-09

Artifacts are under `build/rv-design-review-retry-20260909/`. The trial again
started from the unchanged original four-case plan. O2T used **18 calls**, including
**7 review calls**, and ended **incomplete** without compiling a campaign.

Three automatic retries occurred at model replies **2, 12 and 18**, each on the
same plan and source bundle as its preceding review, with no intervening designer
call. Replies 2 and 12 recovered oversized reviews into valid revision requests.
The last pair remained unusable: malformed JSON followed by an oversized
assessment. A valid semantic rejection at reply 15 was returned directly to the
designer and was not automatically retried.

The reviewer at reply 2 correctly identified constant `>> 3` shifts and rejected
the direct NaN/Inf claim. Later reviews still contained unsupported assertions.
Memory review also exposed missing context: the supplied kernel takes an index,
but the actual adapter wrapper fixes scalar indices to 0..3 and supplies a
32-element buffer. Reviewing the kernel and ABI without that wrapper is
insufficient to assess this execution scope. The next grounding improvement is
to include the actual adapter wrapper and its input mapping in review evidence.

This result demonstrates bounded recovery and retained rejection gates, not a
successful semantic repair. `audit.json` verifies unchanged retry plans, original
seed and source/configuration hashes, reviewer-context isolation and a clean
credential scan. All review attempts and proposed designs remain preserved.

All **502** tests passed in the per-change gate, with zero failures in
**1811.05 seconds**; the five nightly slow tests were excluded. Logs and the
validation summary are in the experiment's `validation/` directory, and
`results.json` combines the audit with these test results.
