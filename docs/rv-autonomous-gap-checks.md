# RV autonomous supplemental-check experiment

The production campaign CLI now supports model-selected formal gaps, generated
native checkers, independent validation and bounded repair. Configuration and
the trust boundary are documented in [agent-campaign.md](agent-campaign.md).

This experiment uses the twelve previously compiled RV cases from
`build/rv-cli-campaign-20260908/`, pinned to upstream commit
`44e0bdb78889da1261596a0cacfdf12693e19e4e`. It reuses their formal results and
scalar/vector native libraries; it does not freshly rebuild RV or rerun the
formal campaign. Eleven cases remain formally unsupported by O2T and one was
proved. The new experiment tests whether DeepSeek can add a useful native check
for a gap of its choice.

## Responsibility and artifacts

Codex implemented the reusable campaign extension and independent validator,
provided the existing native artifacts and ABI bindings, and built generic
planted output defects. Codex also wrote repository regression fixtures using
tiny synthetic native functions. Those fixture checkers were not supplied to
DeepSeek as the RV solution.

DeepSeek receives observed gaps and the checker contract. It chooses the RV case,
input strategy, checker source and companion fixture. O2T executes that code,
measures native calls, checks controls and witnesses, and supplies rejection
feedback. Generated RV checkers are preserved without Codex edits or promotion.

All experiment files are under `build/rv-gap-agent-20260908/`: `campaign.json`
is the operator manifest, `prepare.py` prepares artifacts and defects, and the
credential-only launchers invoke `tools/cv-agent.py` with the packaged DeepSeek
transport. Keys are passed in process memory, not stored in these files.
Each output directory retains `.campaign/checkpoint.json`, a session transcript,
and numbered `agent-staging/gap-attempt-*` directories.

## First run

`report.json`, `summary.txt` and `out/` preserve the initial unsuccessful run.
DeepSeek selected `upstream_masked_store`, but its candidate required an ABI
environment variable unavailable during direct checker execution. O2T rejected
it. Subsequent malformed JSON responses exhausted the loop's invalid-response
limit after six model calls. The campaign remained incomplete.

Before a fresh run, Codex clarified the generic contract: exact pointer signatures,
array lengths, nested witness inputs, finite float bounds, configuration ownership
and staged filenames. The validator's acceptance criteria were not relaxed, and
the failed candidate was not repaired by Codex. The retry artifacts use the
`retry-` filename prefix and `out-retry/` directory.

The retry selected `guarded_division` and exhausted three candidates. The first
two loaded original paths instead of the supplied blinded libraries. The third
used the supplied paths but reported a one-element witness for a four-lane ABI;
independent replay rejected it. The campaign remained incomplete after eight
model calls. `failed-runs-audit.json` preserves the rejection summaries.

A third run uses the same manifest and validation rules with the transport's
`--thinking enabled --max-tokens 16000` options. Its artifacts have the
`reasoning-` prefix and use `out-reasoning/`. It is recorded separately from
the two unsuccessful runs.

## Reasoning-enabled result

The third run completed in nine model calls. DeepSeek inspected case metadata and
the prior native summary, selected `integer_loop`, and generated
`cv-agent-integer-loop.py` plus its sibling fixture. It recovered from an invalid
artifact-read request and a rejected tool name through O2T feedback. Its first
staged candidate passed independent validation; Codex made no edits to that code
and sent no instructions between model calls in this run.

The checker takes the Cartesian product of sixteen four-lane uint32 vectors,
including zero, one, all-ones, sign-boundary values, single-lane sentinels,
alternating bits and mixed constants. O2T measured:

| Validation | Observed result |
| --- | --- |
| Identity libraries | 256 distinct input pairs; no differences |
| Actual scalar/vector libraries | 256 distinct input pairs; all 1,024 output values agree |
| Planted output defect | 256 differing pairs; concrete witness independently replayed |
| Generated fixture | Pass |
| Fixture with broken sibling checker | Fail, as required |
| Completed checkpoint resume | Zero model calls |

`reasoning-report.json` contains the complete campaign result;
`reasoning-audit.json` verifies that the checker and fixture exactly match
DeepSeek's response, all twelve original native pairs and the formal report are
unchanged, the RV checkout is clean, and credential-pattern scanning passed.
The checker is in
`out-reasoning/agent-staging/gap-attempt-1/cv-agent-integer-loop/` beneath the
experiment directory. It remains staged and advisory.

Regression logs and the test outcome summary are archived in the experiment's
`validation/` directory. The broad gate passed 500 of 501 tests; its sole failure
was the synthetic fixture filename issue described above in the integration
documentation. After that fix, all six agent-focused fixtures passed.

Acceptance means independently validated finite native evidence, not proof of
RV correctness. Existing formal unsupported results remain unchanged. Distinct
inputs are counted within the new checker; novelty relative to every input in
the earlier native campaign is not measured.
