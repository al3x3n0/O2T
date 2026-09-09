# Compiled-target campaigns

For agent-selected cases and source-grounded risk planning before execution, use
the [automatic campaign designer](agent-campaign-design.md). Its initial RV
adapter compiles model choices into this manifest format.

`cv-agent.py --campaign manifest.json` runs a configured campaign through the
existing O2T agent loop. It bypasses source-residue triage and exposes only ready
campaign jobs, declared artifact reading, advisory conclusion and optional tool
synthesis. The operator supplies commands and initial cases; the model chooses
operations and follow-up investigation. This does not fetch or configure a GitHub
project automatically.
Existing O2T commands, including the mining-to-replay `cv-run-campaign.py` driver,
can be exposed as named jobs alongside external build and verification tools.

```sh
python3 tools/cv-agent.py --campaign examples/agent_campaign.json \
  --out-dir build/instcombine-campaign \
  --llm-command 'python3 tools/cv-agent-deepseek.py --model deepseek-v4-flash' \
  --budget 16 --max-steps-per-pass 16 --llm-timeout 200 \
  --report build/instcombine-campaign-report.json
```

The example checks the installed LLVM instcombine pass on a bundled corpus; it
requires LLVM and Z3. It does not stand in for an external pass such as RV.
The DeepSeek transport reads `DEEPSEEK_API_KEY` from the environment. Supply that
through your credential mechanism; do not put keys in manifests or command-line
arguments. Any existing JSON-stdin/JSON-stdout `--llm-command` also works. Model
identifiers are explicit, rather than hardcoded into the agent.
The transport also accepts `--thinking enabled` and `--max-tokens 16000` for
reasoning-heavy synthesis. Defaults remain reasoning disabled and 6,000 output
tokens. Model usage and the requested reasoning mode are recorded in stderr.

## Manifest version 1

The JSON root has `version: 1`, a string `name`, optional string `goal`, optional
`inputs` (file paths relative to the manifest, or absolute paths), and 1–100 `jobs`.
The manifest is an **operator-supplied executable configuration**, not model output.
Commands have the same filesystem privileges as O2T.

Each job has:

| Field | Meaning |
| --- | --- |
| `id` | Unique lowercase identifier, letters/digits/hyphens |
| `argv` | Nonempty array of command arguments; executed without a shell |
| `description` | Optional explanation for the model |
| `requires` | IDs that must complete before this job becomes ready |
| `required` | Defaults to true; dependencies of required jobs are required too |
| `timeout` | Positive seconds; capped by CLI `--action-timeout` |
| `outputs` | Files relative to `--out-dir`, with one producer per file |
| `result` | Optional declared output to parse as JSON, at most 1 MB |
| `evidence_kind` | `execution` (default), `formal`, `native`, or `negative-control` |
| `success_exit_codes` | Defaults to `[0]`; use `[0, 1]` when a verifier returns 1 for a valid refutation |

Arguments support the literal substitutions `{python}`, `{manifest_dir}` and
`{out_dir}`. Commands run with the manifest directory as cwd. No interpolation of
model text, shell expansion, arbitrary environment variables or arbitrary inherited
provider credentials occurs. The job environment carries PATH, locale, temporary
and compiler SDK settings, with a temporary HOME. Jobs receive closed standard
input and must run noninteractively.

Declare every source/script input whose changes should invalidate a checkpoint,
and every intermediate artifact consumed by later jobs as an output of its
producer. O2T hashes declared files; it does not infer compiler includes, Python
imports or every toolchain dependency. Pin toolchain versions and external source
revisions in your setup. Unknown fields, duplicate IDs, unknown dependencies,
cycles and paths escaping the output root are rejected.

## Completion, failures and resume

Job states are explicit: ready, pending, completed, failed or blocked. Only ready
IDs appear in the action schema. Completed and failed jobs cannot consume another
execution. An interrupted operation without a checkpoint may run again on resume;
configure operations that can tolerate this. Independent required jobs still
need to run when another branch fails. A conclusion before that point is rejected.
Failed jobs and dependents blocked by them can be reported honestly, but the
campaign remains incomplete and exits 2. Complete execution exits 0; neither code
is a formal correctness verdict. Source-mode refutation exit gates cannot be mixed
with campaign mode.

Checkpoints are written atomically after each operation, under
`<out-dir>/.campaign/checkpoint.json`. Per-segment transcripts are appended after
each model exchange; command logs and reports have separate session directories.
No previous transcript is overwritten. Interrupted commands are terminated with
their process group on POSIX, including children that remain in that group. Model transport retains its
existing timeout behavior.

```sh
python3 tools/cv-agent.py --campaign campaign.json --out-dir build/campaign \
  --resume build/campaign/.campaign/checkpoint.json \
  --llm-command 'python3 tools/cv-agent-deepseek.py --model deepseek-v4-flash'
```

Resume accepts a checkpoint or campaign report. It rejects changed manifests,
declared inputs, output directories or completed output hashes. A complete
checkpoint returns without calling the model. Pending work resumes with completed
observations visible. Failed jobs are retained as failed; retry an execution failure
with a fresh output directory. A new campaign refuses existing declared outputs,
preventing accidental acceptance of stale results. Do not run concurrent campaigns
in the same output directory.

## Evidence and synthesis

Reports retain each job's parsed result, exit code, evidence kind, command, elapsed
time and output hashes. The text summary renders execution states from those
records, not from model prose. A successful command or JSON field saying `proved`
does **not** enter O2T's trusted formal headline. The same applies to a model's
conclusion. Inspect verifier results for the actual obligations and scope; keep
native samples, bounded loop checking and planted controls distinct from proofs.

`--enable-synthesis` adds O2T's existing quarantined candidate-tool action. Generated
fixtures remain advisory; promotion is not automatic. Artifact reading is limited
to completed declared outputs, up to 10,000 bytes per call with byte-offset paging.
It does not expose arbitrary files or source trees.

## Autonomous supplemental checks

An optional `gap_checks` manifest field lets the model select an observed formal
gap and write a native differential checker with its own input strategy:

```json
"gap_checks": {
  "report_job": "formal-checks", "required": true,
  "max_attempts": 3, "min_unique": 256,
  "cases": [{
    "id": "example", "before": "example/before.dylib",
    "after": "example/after.dylib", "negative": "example/negative.dylib",
    "abi": {"kind": "lanes", "type": "uint32", "width": 4,
            "symbol": "run_batch"}
  }]
}
```

The report job's JSON result must be a list of
`{"name": "example", "o2t": {"status": "unsupported", "reason": "..."}}`
records. Only unsupported, unknown, timeout and inconclusive cases are eligible.
All three native libraries must be declared job outputs. The operator supplies
the ABI and a planted defective library; the model chooses the gap and checker.
`lanes` uses `void symbol(T *x, T *y, T *out)` with arrays of `width` elements;
`memory` uses `void symbol(T *array)` and compares the whole mutated array.
Types are `uint32` and `float32`. Float bindings require finite `min`/`max` input
bounds and permit `max_ulp` of zero or one; paired output NaNs agree.

With `--enable-synthesis`, `select-gap` and `synthesize-gap-check` replace generic
tool synthesis. Each candidate and fixture is preserved in a numbered staging
directory. Rejections become model feedback, allowing up to `max_attempts`
candidates across the campaign. Required checks need at least one accepted
candidate to complete, unless there are no eligible gaps. Exhaustion or missing
native bindings permits an honest conclusion but leaves the campaign incomplete.
Optional checks (`required: false`) do not block completion.

The standalone Python checker takes `--before LIB --after LIB`. It prints one
JSON object with `status` (`agree`/`disagree`), `checked` (paired native calls),
`mismatches` (differing pairs), and `witness` (null or input arrays and both output
arrays). Exit codes are zero for agreement and one for disagreement. The fixture
must invoke the actual sibling checker using the supplied `O2T_CHECK_BEFORE`,
`O2T_CHECK_AFTER`, `O2T_CHECK_NEGATIVE` and JSON `O2T_CHECK_ABI` environment values.
These environment values are provided to the fixture, not direct checker runs.

O2T independently traces ctypes calls on copied libraries with opaque paths,
requires the configured number of distinct inputs, compares reported counts and
outputs against that trace, checks identity and planted-defect sensitivity, and
replays reported witnesses in a separate native process. It also requires the
fixture to fail when its sibling checker is replaced with a broken program.
Resume checks candidate, library and validator hashes. This catches common
generated-code mistakes; Python instrumentation is not a security sandbox against
hostile code. Acceptance adds finite, advisory native evidence and never changes
an unsupported formal result into a proof.

Each binding may now declare additional mutation controls and explicit input
coverage requirements, for example:

```json
"controls": [{"id": "zero-divisor", "artifact": "example/negative-zero-divisor.dylib"}],
"coverage": [
  {"id": "zero-divisor", "array": 1, "predicate": "zero", "lanes": "each", "minimum": 1},
  {"id": "nonzero-divisor", "array": 1, "predicate": "nonzero", "lanes": "each", "minimum": 1}
]
```

Control artifacts must be declared job outputs. All controls must be available
before the gap can be selected. O2T requires detection and witness replay for
every control, and reruns the fixture with each control supplied separately as
`O2T_CHECK_NEGATIVE`. The original `negative` field remains required.

Coverage counts come from distinct paired native inputs observed by O2T, on
identity, baseline and every control run. Repeating an input does not increase
coverage. `lanes: "each"` requires the minimum independently for every lane;
`"any"` counts inputs satisfying the predicate in at least one lane. Supported
predicates are `zero`, `nonzero`, `positive_zero`, `negative_zero`, `positive`,
and `negative`, subject to ABI compatibility. These are input predicates, not
measured program branch coverage. Signed-zero outputs must agree in sign, even
when one ULP is permitted; paired NaNs still agree.

Generated action names must match `^cv-agent-[a-z0-9][a-z0-9-]{2,40}$` before
execution. The DeepSeek transport rejects responses ending at the token limit,
including a parseable prefix, and the next request receives sanitized truncation
feedback. Validator changes invalidate old accepted checkpoints; qualify old
checkers in a fresh campaign rather than resuming their historical acceptance.
See [the checker repair experiment](rv-checker-repair.md).

## Validation

`agent_campaign_fixture` exercises actual command execution through the public CLI
with a scripted provider: premature conclusion rejection, partial and completed
resume, output/source mutation detection, failure propagation without skipping
independent work, credential isolation, path boundaries, invalid dependencies and
POSIX descendant cleanup on timeout. It requires neither an API key nor Z3.

The live RV integration run is recorded under
`build/rv-cli-campaign-20260908/`. DeepSeek used the production CLI and packaged
transport, completed seven operations in nine calls, and recovered from one
premature conclusion via the completion gate. RV setup and the twelve initial
harnesses were operator-supplied. This run required no intervention between model
calls, unlike the preceding experimental adapter run.

Validation for this integration: all six agent-focused fixtures passed, and
`ctest --test-dir build --output-on-failure -LE slow -j 8` passed all 500 tests
in the documented per-change gate. Five nightly tests labelled `slow` are outside
that gate. Logs and a validation summary are in `build/agent-campaign-validation/`.
The supplied example manifest also completed against the installed LLVM toolchain.

The gap extension adds `agent_gap_checks_fixture`, covering invented native
results, missing calls, witness replay, insensitive/embedded fixtures, autonomous
repair, unchanged formal results and resume integrity. The 501-test per-change
run passed 500 tests and exposed a temporary test filename that shadowed Python's
standard-library `copy` module. After correcting the filename, all six
`agent_.*fixture` tests passed, including the new fixture. The broad gate was not
repeated; logs are `/tmp/o2t-gap-fast-gate.log` and `/tmp/o2t-gap-agent-final.log`.
See [the live gap experiment](rv-autonomous-gap-checks.md) for model outcomes and
the distinction between operator infrastructure and generated checkers.

For a single session that builds RV and verifies fresh outputs before synthesizing
gap checks, use the [full RV recipe](rv-full-agent-campaign.md).
Campaign reports now include `evidence_summary`, separating observed formal
results, native samples, controls, supplemental checks, unresolved formal cases
and execution issues. These fields never promote observed results into the trusted
formal headline.

The subsequent multi-control and input-coverage strengthening passed all **501**
tests in the same per-change gate. Its live repair trials accepted the guarded
division repair and rejected all three float candidates; see the
[recorded outcomes and audit](rv-checker-repair.md).
