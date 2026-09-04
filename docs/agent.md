# The verification agent — LLM-driven batch triage over the O2T toolchain

`tools/cv-agent.py` (package: [`o2t/agent/`](../o2t/agent/)) is the "low on HR" front door: point
it at a vendor pass tree and it (1) runs the **deterministic orchestrator** over everything, then
(2) spends a bounded LLM budget investigating the **residue** — the passes deterministic
classification/verification left open — by driving real O2T verifiers, proposing formally-gated
artifacts, and (optionally) staging new-tool candidates for human review. One merged report
replaces a human driving ~143 tools.

```sh
tools/cv-agent.py --source vendor/lib/Transforms \
  --llm-command 'claude -p --output-format json' \
  --budget 25 --out-dir agent-out \
  --report agent.json --summary-text agent.txt
```

## Trust model (the load-bearing part)

**Formal verifiers decide soundness; the agent only routes, proposes, and stages.** Concretely:

| tier | example | where it lands | verdict weight |
| --- | --- | --- | --- |
| deterministic | cv-orchestrate's checks + headline | `pass["headline"]`, `pass["checks"]` | full (unchanged semantics) |
| agent-dispatched **formal** | the LLM picks `run-strategy(memory-model)`; cv-validate-memory + Z3 run | `pass["agent"]["formal_checks"]` with `origin: "agent"`; collapsed into `pass["agent"]["headline"]` (provenance `deterministic+agent-formal`) | full, but provenance-tagged and kept OUT of the deterministic headline |
| LLM opinion | `conclude(proposal, rationale)` | `pass["agent"]["conclusion"]`, `trust: "advisory"` | none |
| staged tool result | a synthesized candidate's fixture ran | `pass["agent"]["staged_tools"][i]["fixture_result"]`, `trust: "advisory-staged"` | none |

- The deterministic `pass["headline"]` is **never rewritten** — it is byte-identical to what
  `cv-orchestrate.py` computed.
- `--fail-on-refuted` reads only deterministic headlines (same semantics as cv-orchestrate);
  `--fail-on-agent-refuted` reads only agent-dispatched **formal** refutations. Advisory content
  can trip neither.
- The LLM **never emits shell**. Each step it selects ONE action from a whitelisted registry
  ([`o2t/agent/actions.py`](../o2t/agent/actions.py)); args are schema-validated (types, enums,
  length caps) before any handler runs. An invalid reply executes nothing — it becomes an
  `invalid-action` observation the LLM sees next turn; two consecutive invalid replies degrade
  the pass. Hostile text in a pass source can therefore steer *which whitelisted verifier runs*,
  never *what counts as sound*.

## The loop

Provider-agnostic, like `brain.py`: `--llm-command` is any shell command taking a JSON request on
stdin and returning JSON on stdout (`o2t/llm_io.py` owns the transport; fixtures use the
deterministic scenario stub `tests/fixtures/agent_llm_stub.py` — no model, no network).

Request (per step): the pass (source path, mode, deterministic headline), a source excerpt
(≤6000 chars), the accumulated `evidence` log, the remaining budgets, and the advertised actions
with arg schemas. Reply: `{"action": ..., "args": {...}, "rationale": ...}`.

Actions (v1):

| action | kind | what actually runs |
| --- | --- | --- |
| `classify` | evidence | the deterministic family classifier, in-process |
| `mine-source` | evidence | `cv-mine-pass-source.py` (truncated findings) |
| `run-strategy(strategy)` | **formal** | the strategy's real O2T verifier via `orchestrate.run.execute_check` — enum'd to `plan.STRATEGIES` |
| `recover-fold(function_source?)` | **formal** | Pass-IR recovery (`pass_graph.recover_from_function`) + prove + reconcile; declines outside the fragment |
| `propose-intent-candidates(candidates)` | **formal (gated)** | each record proof-gated by Z3 via `cv-validate-intent-candidates.py` — the proof decides, not the proposal |
| `propose-fold-obligation(predicate, rewrite)` | **formal (gated)** | `pass_graph.recover_pair` + reconcile + compiler grounding (when clang present) |
| `synthesize-tool(name, purpose, sources)` | synthesis | quarantined staging (below); only with `--enable-synthesis` |
| `conclude(proposal, rationale)` | control | ends the pass; advisory |

Residue selection: deterministic `headline.status ∈ {unclassified, advisory, skipped, error,
refuted}`.

**The residue statuses are not equally reachable, and two of them are dead ground.** Measured
offline against `plan_for` with a source supplied and z3/opt/clang present: *no* family yields an
all-infeasible plan — every one of the nine has at least one feasible check. A `skipped` headline
therefore only arises when a required binary is absent, and the agent runs in the *same*
environment, so it cannot route around a missing `opt` any more than the orchestrator could. There
is nothing for it to do there. `refuted` routes to diagnose mode, which is advisory by construction
and never relitigates the refutation. That leaves the agent's real surface as `unclassified` (a
classifier blind spot — see below, and `agent_positive_control_fixture`) and `error`/`advisory`
(route around a check that crashed or answered inconclusively to a feasible sibling). Those last two
still have no positive control.

**Routing signal: what each strategy would RECOVER here, not merely what may run.** The prompt's
`actions[].strategy_catalog` carries, for every source-targeted strategy, a `would_recover` count —
how many folds that strategy's miner actually recovers from this pass's source. Mining is regex/AST
work (the expensive half of these strategies is the Z3 discharge), so asking every wired miner costs
well under a millisecond and turns 33 opaque ids into a ranked list.

This exists because listing the strategies was not enough. Given only the enum, a live model
concluded "no formal strategy is applicable without a family match" and wound down with its budget
unspent. Given the catalog with descriptions and a runnability flag, it still did not route — for a
typical pass **32 of 33 entries come back `runnable_here: true`**, which is a permissions list, not
evidence. On `agent_positive_control_snippet.cpp` exactly one strategy is now non-zero
(`slp-source: 3`), and it is the one that finds the planted bug.

Two properties matter as much as the count. `would_recover` **absent means unmeasured, never zero** —
a strategy with no wired miner must not read as inapplicable. And a reported **zero is honest**: on
the deliberately transform-free residue snippet every miner returns 0, which is why the live runs'
`inconclusive` verdicts were correct rather than a failure. `agent_routing_signal_fixture` pins all
of it, including that the instruction tells the model how to read it — evidence nobody is told to
read goes unused, which is what happened to the catalog before this.

**Where the agent can actually add attributable value.** Only where a *miner* can see a fold the
*classifier's* signal list cannot — and that gap is family-specific, not general. For `cleanup-dce`
it does not exist: the miner's erase regex requires `eraseFromParent` (classifier weight 3),
`deleteDeadInstruction` (4) or `RecursivelyDeleteTriviallyDeadInstructions` (5) against a retention
threshold of 3, so anything the DCE miner can mine the DCE classifier already retains, and the
agent can only re-run a check the orchestrator ran itself (measured: on
`dce_dead_instruction_folds.cpp` it reproduces the deterministic refutation and adds nothing). For
SLP reductions it does: the miner recognizes the reduction *builders* while `vectorize-slp` scores
on `TreeEntry` / `vectorizeTree` / `ShuffleVectorInst`, so a pass emitting reductions without those
names is invisible to the classifier and fully visible to the miner. That is a genuine classifier
blind spot rather than a contrived one — signal lists enumerate idioms someone thought of, and a
vendor names things how it likes — and closing it for SLP would not retire the agent, only move the
frontier. This is the same shape as the `recover-fold`/classifier overlap: where the two key on the
same tokens, the agent is redundant by construction. A `refuted` pass runs in **diagnose** mode (explain the witness, propose a fix
direction — synthesis disabled, the refutation is never relitigated).

Safety knobs: global `--budget` (LLM calls), `--max-steps-per-pass`, `--action-timeout` per
subprocess, strike-based degradation, and `--resume prior.json` (concluded passes with unchanged
source sha256 are skipped).

## Tool synthesis: quarantine, not a sandbox

With `--enable-synthesis`, the agent may stage a NEW candidate tool + fixture under
`<out-dir>/agent-staging/<name>/` (name must match `cv-agent-[a-z0-9-]+`; escapes are refused;
a hash-pinned `manifest.json` records every candidate). The staged fixture is executed **once**,
via `python -I` (isolated mode) in a fresh temp cwd with a minimal environment, and its result is
recorded as `advisory-staged`. Staged code is never written under `tools/` or `tests/`, never
imported, never on `sys.path`, and its results feed no headline and no gate.

**Promotion is a human act**: review the candidate, `git mv` it into `tools/`, write/adopt its
fixture under `tests/fixtures/`, register it in CMakeLists.txt. There is deliberately no
promotion API.

Stated plainly: this is quarantine against accidents and prompt-injected sloppiness, **not a
security sandbox** — the staged fixture still runs with your user's privileges. Do not run
`--enable-synthesis` over source trees you do not trust.

## Report additions

```jsonc
"passes": [{
  ...,                                   // everything cv-orchestrate wrote, untouched
  "agent": {
    "status": "concluded",               // concluded|degraded|budget-exhausted|step-cap
    "mode": "verify",                    // or "diagnose" for refuted passes
    "llm_calls": 3,
    "steps": [ {"step", "action", "args", "rationale", "observation"} ],
    "formal_checks": [ {"strategy", "verdict", "origin": "agent"} ],
    "headline": {"status": "proved", "provenance": "deterministic+agent-formal", ...},
    "conclusion": {"proposal", "rationale", "trust": "advisory"},
    "staged_tools": [ {"name", "path", "sha256", "fixture_result": {..., "trust": "advisory-staged"}} ],
    "source_sha256": "..."               // resume guard
  }
}],
"agent_run": {"budget", "llm_calls_used", "residue_selected", "attempted", "enable_synthesis", "staging_dir"},
"summary": {"agent": {"attempted", "concluded", "degraded", "budget_exhausted", "step_cap",
                       "agent_formal", "staged_tools", "headline_upgrades"}}
```

## Gating

- `agent_fixture` (z3) — a scripted LLM drives an unclassified pass to a REAL proved verdict;
  deterministic headline untouched; advisory "refuted" trips no gate; invalid/malformed replies
  execute nothing (one strike recoverable, two degrade); budget exhaustion winds down; resume
  skips settled passes.
- `agent_positive_control_fixture` (z3) — **the one that shows the agent is worth running.** On a
  pass the deterministic layer cannot classify (zero checks planned), the agent routes to
  `slp-source` and refutes a planted unguarded FP reduction — *attributed* to that pass
  (`canonical_only` empty), with two sibling folds still proving, tripping `--fail-on-agent-refuted`
  where an advisory "refuted" does not. Guarding the defect turns the same check green (3/3).
  Everything `agent_fixture` proves is canonical, so before this fixture a harness that only ever
  proved vacuous things and a broken one were indistinguishable.
- `agent_synthesis_fixture` — the quarantine invariants above, end to end.
- `agent_selftest` — registry whitelisting without any LLM or verifier.
