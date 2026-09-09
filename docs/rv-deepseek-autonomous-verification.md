# O2T + DeepSeek autonomous RV campaign

Date: 2026-09-08. This experiment separates model-directed execution from the
earlier [Codex-operated RV verification](rv-third-party-verification.md).

## Ownership and scope

Codex supplied the already fetched/configured RV checkout, the build recipe,
twelve existing input cases and their checking harnesses, the DeepSeek transport,
and an experimental action adapter. O2T's existing `run_pass_agent`, action schema
validation, budget/strike handling, transcript capture and optional tool staging
operate the loop. DeepSeek chooses actions and receives their actual results.

This is autonomous execution of a configured campaign. It is **not** autonomous
discovery of a GitHub project, creation of its build integration, or selection of
the initial twelve cases. It is also not a feature already exposed by the stock
`cv-agent.py` CLI: the adapter lives under ignored build artifacts. No production
O2T source was changed in this experiment.

Artifacts: [`build/rv-deepseek-campaign-20260908/`](../build/rv-deepseek-campaign-20260908/).
The adapter is `campaign.py`; its configured commands, dependencies, prompt and
harness hashes are in `adapter-provenance.json`. Every request and reply is in
`llm-transcript.jsonl`; `events.jsonl` records campaign operations.

## Attempts and interventions

1. **Stock source-tree baseline.** Codex launched unchanged cv-agent against RV's
   src directory. It expanded to 78 source/header files and 260 feasible checks,
   including repeated canonical checks. Codex interrupted this attempt while
   deterministic orchestration was still running, before any model call. The
   orchestrator subprocess has no overall deadline, and its results are not
   incrementally exposed by cv-agent. The plan is preserved in
   `build/rv-deepseek-autonomous-20260908/plan.json`. This was not a completed run.
2. **Network failure.** The first campaign launch made two transport attempts
   blocked by the sandbox. O2T degraded cleanly and executed no model action.
   Records are in `network-failed-attempt/`. The retry used approved network access.
3. **Live attempt 1.** DeepSeek inspected and built RV, classified rv.cpp, then
   selected translation-validation, canonical loop simulation and loop induction.
   It tried reading a formal report it had not generated and concluded inconclusive
   after eight calls. It incorrectly said the remaining budget was insufficient;
   the final request advertised thirteen remaining calls/steps. This attempt did
   not prepare inputs or execute RV on them. All artifacts and the original adapter
   are retained in `attempt-1/`.
4. **Codex integration correction.** The generic source-residue instruction and
   action registry were inappropriate for a compiled-target campaign. Codex replaced
   that instruction and limited the registry to named campaign operations, bounded
   artifact reading, optional synthesis and advisory conclusion. No check result
   was supplied in advance or manually selected as the model's next action.
5. **Live attempt 2.** The revised campaign starts with fresh input/output artifacts.
   The model itself chooses operations from their dependencies. Its first malformed
   JSON reply was rejected; it recovered on the next call without manual repair.
   It selected inspection, build, preparation, vectorization and formal checks,
   then concluded early after seven calls, leaving native checks and negative
   controls unexecuted despite fourteen calls remaining at its final request.
   Its transcript, report and adapter snapshot are preserved in `attempt-2/`.
6. **Codex completion-policy correction.** The adapter now rejects conclusion
   while required operations remain unattempted and no operation has failed.
   Genuine execution failures still permit an honest blocked conclusion. A
   continuation loaded the earlier agent-produced operation records; Codex did
   not run the missing checks. Gate tests exercised unfinished, completed and
   execution-blocked campaigns.
7. **Live continuation.** DeepSeek reselected the five completed jobs and received
   their cached observations, without rerunning commands. It then selected native
   checks, negative controls and an inconclusive conclusion: eight calls. All seven
   required campaign operations were now completed. The continuation is the root
   `report.json` and `llm-transcript.jsonl`.

## Measured outcome

The completed campaign comprises attempt 2 plus its continuation. Both used
DeepSeek V4 Flash through the O2T loop. No Codex intervention occurred inside
either live segment; the integration corrections above occurred between segments.

| Evidence | Result |
| --- | --- |
| Real RV transformations and LLVM structural verification | 12/12 outputs generated and accepted |
| O2T refinement | 1 proved, 11 unsupported; integer branches also confirmed by Bitwuzla |
| Independent Alive2 | 4 accepted, 8 inconclusive/timeout; two acceptances involve bounded loops and one abstracts helper calls |
| Native scalar/SIMD comparison | 116,161 batches, 810,052 values, zero mismatches |
| Planted lane swap | O2T and Alive2 refuted; Bitwuzla confirmed O2T's satisfiable result |
| Planted unsafe divisor | Alive2 refuted introduced UB; O2T declined |
| Optional generated checks | Available, but DeepSeek did not choose synthesis |

No RV bug was detected. These are selected-input results, not a full vectorizer
proof. Input domains, wrappers and semantic limitations match the earlier RV
experiment; the compiled artifacts and observations were generated anew under
agent-selected operations. The continuation reused only those new observations.

The model's final prose says formal verification proved integer_branches “and one
other case”; that is not an accurate enumeration of either validator's results.
The table above comes from raw JSON, not that prose. Its advisory inconclusive
conclusion is appropriate. The formal and native summaries are retained separately.

There were **23 provider responses / 140,641 provider-reported tokens** across
the three live segments (8 + 7 + 8 calls). The two sandbox transport failures had
no provider response and are excluded. Credentials were kept in memory; the
post-run text-artifact scan found no key-like values.

Codex's separate post-run audit is `audit_results.py` and `audit.json`. It checks
operation completion, native counts, both negative controls, the clean upstream
checkout, artifact hashes and credential absence. It does not feed evidence back
into the model or change any verdict. No O2T behavior changed, so this experiment
did not rerun the production CTest suite.

## Action and trust boundaries

The compiled campaign offers target inspection, build, input preparation, actual
RV vectorization, O2T/Alive2 refinement checks, native differential execution and
planted negative controls. Dependencies are checked before commands execute.
Commands are fixed by the adapter; the model supplies a job ID, never shell text.
The reader restricts paths to campaign text artifacts or RV's src directory.

Commands execute with an allowlisted environment that excludes the API key.
The provider keeps the key in process memory. Optional synthesized checks use
O2T's existing staging mechanism and remain advisory; that staging mechanism is
not a security sandbox. No generated check is automatically promoted.

The adapter records tool outputs as evidence and does not turn an exit-zero
campaign job into a formal pass proof. Native agreement, bounded Alive2 loop
results, source-independent contracts and actual O2T refinement verdicts must
remain separate. See the per-case JSON and raw logs for semantic scope.

## Remaining integration gaps

- The stock source-tree CLI has no compiled RV campaign action; the adapter must
  be packaged and tested before this becomes an ordinary product workflow.
- The original residue prompt and broad registry encouraged irrelevant canonical
  checks. Campaign-specific capability selection improved routing.
- A model can conclude prematurely even with ample budget. Required operation
  completion is now enforced by the experimental adapter, rather than relying on
  the model to interpret the remaining budget correctly.
- Continuation status needs clearer presentation: DeepSeek spent five calls
  reselecting already completed operations. They were not re-executed.
- Model prose is not a reliable source of proof counts. Reports should render
  counts and scope directly from structured verifier output.
- No autonomous supplemental-check synthesis was demonstrated in this campaign.
  It was offered, and the model chose not to use it.

## Replay

With the recorded RV checkout, configured build and toolchains present:

```sh
python3 build/rv-deepseek-campaign-20260908/campaign.py --selftest
python3 build/rv-deepseek-campaign-20260908/campaign.py
```

The second command prompts for the DeepSeek key without echo. Archive existing
outputs before another run; formal_checks.py caches per-case o2t.json results.
The supplied transport currently lives in the earlier experiment's
`build/deepseek-synthesis-20260908/deepseek_live.py`. This dependency is another
reason to describe this as an experimental integration rather than a packaged CLI.
