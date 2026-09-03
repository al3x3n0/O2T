#!/usr/bin/env python3
"""POSITIVE CONTROL: the agent must recover a REAL finding the deterministic layer never had.

`agent_fixture` covers the agent's MECHANISM thoroughly -- routing, trust quarantine, budget,
strikes, transcript -- but every proof it exercises is `memory-model` or `dce-model`, and both are
CANONICAL: they discharge fixed contracts that hold whatever pass you point them at. That fixture
even asserts, correctly, that such a proof must NOT upgrade a headline. So nothing in it pinned
that the agent can produce evidence ABOUT THE PASS IT WAS GIVEN. A harness that only ever proves
vacuous things and a harness that is broken look identical under those assertions.

This fixture closes that. It pins the agent's actual value proposition end to end:

    deterministic layer sees NOTHING (`unclassified`, "no family matched", zero checks planned)
      -> the agent routes to an ATTRIBUTABLE source strategy
        -> a real planted miscompile is REFUTED, attributed to this pass, and trips the gate.

WHY THE GAP IS CONSTRUCTIBLE HERE AND NOT FOR DCE. The agent can only add attributable value where
a miner can see a fold that the classifier's signal list cannot. For `cleanup-dce` that gap does
not exist: the miner's erase regex requires `eraseFromParent` (classifier weight 3),
`deleteDeadInstruction` (4) or `RecursivelyDeleteTriviallyDeadInstructions` (5), and the retention
threshold is 3 -- so anything the DCE miner can mine, the DCE classifier already retains, and the
agent can only re-run a check the orchestrator ran itself. Measured, not assumed: pointing the
agent at `dce_dead_instruction_folds.cpp` reproduces the deterministic refutation exactly and adds
nothing. The SLP reduction miner is different: it recognizes the reduction BUILDERS, while the
`vectorize-slp` family scores on `TreeEntry` / `vectorizeTree` / `ShuffleVectorInst`. A pass that
emits reductions without using those names is invisible to the classifier and fully visible to the
miner. That is a real classifier blind spot, and it is exactly the territory the agent exists for:
signal lists enumerate idioms someone thought of, and a vendor names things how it likes.

THE SNIPPET IS KEPT SIGNAL-FREE ON PURPOSE. If the SLP signal list is later extended to catch this
shape, the deterministic layer would classify it and this fixture would silently stop testing the
agent and start testing the classifier -- passing all the while. Assertion 1 pins the premise so
that conversion FAILS LOUDLY instead.

Needs z3 (the verdicts are real). Uses the deterministic scenario stub -- no model, no network.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.cli import run_agent  # noqa: E402
from o2t.orchestrate.classify import classify  # noqa: E402

STUB = Path(__file__).resolve().parent / "agent_llm_stub.py"
SNIPPET = Path(__file__).resolve().parent / "agent_positive_control_snippet.cpp"


def _args(tmp: Path, source: Path, **overrides) -> SimpleNamespace:
    base = dict(
        source=[source], passes=[], include=[], exclude=[],
        llm_command=f"{sys.executable} {STUB}",
        budget=10, max_steps_per_pass=8, action_timeout=300, llm_timeout=60,
        out_dir=tmp / "out", enable_synthesis=False, resume=None,
        report=None, summary_text=None,
        z3_bin="z3", opt_bin="opt", clang_bin="clang", ast_miner=None,
        fail_on_refuted=False, fail_on_agent_refuted=False, selftest=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(tmp: Path, name: str, turns: list, source: Path, **overrides):
    path = tmp / name
    path.write_text(json.dumps(turns))
    os.environ["AGENT_STUB_SCENARIO"] = str(path)
    report, exit_code = run_agent(_args(tmp, source, **overrides))
    entry = next(e for e in report["passes"]
                 if e.get("source", "").endswith(source.name))
    return report, exit_code, entry, entry.get("agent")


DISPATCH = [{"action": "run-strategy", "args": {"strategy": "slp-source"},
             "rationale": "reduction builders are present; mine and prove them"},
            {"action": "conclude", "args": {"proposal": "refuted",
                                            "rationale": "unguarded FP reduction"}}]


def main() -> int:
    if shutil.which("z3") is None:
        print("agent_positive_control_fixture: z3 not found, skipped")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) THE PREMISE: the deterministic layer must be BLIND to this pass. If this ever fails
        #    because the classifier learned the shape, the rest of this fixture is no longer
        #    testing the agent -- fix the snippet, do not relax this.
        cls = classify(SNIPPET.read_text(), pass_name=SNIPPET.stem)
        assert cls.families == [] and cls.primary is None, (
            "the positive control must be UNCLASSIFIED: if the classifier now scores it, this "
            "fixture silently becomes a classifier test that passes for the wrong reason",
            cls.scores)

        # 2) THE RECOVERY: the agent routes to an attributable strategy and refutes a real bug.
        report, exit_code, entry, record = _run(tmp, "pc.json", DISPATCH, SNIPPET)
        assert exit_code == 0, exit_code
        assert entry["headline"]["status"] == "unclassified", entry["headline"]
        assert entry["headline"]["primary_checks"] == [], \
            ("the deterministic layer must have planned NOTHING -- that is what makes the agent's "
             "finding an addition rather than a repeat", entry["headline"])

        check = record["formal_checks"][0]
        assert check["strategy"] == "slp-source" and check["origin"] == "agent"
        assert check["verdict"] == "refuted", check
        #    THE INTERNAL CONTROL. 3 folds mined, 2 proved, 1 refuted. A checker that refuted
        #    everything would also "catch" the planted bug, and would be worthless; the two proofs
        #    beside the refutation are what distinguish a detector from a stuck alarm.
        assert (check["transforms"], check["proved"], check["refuted"]) == (3, 2, 1), check

        # 3) THE ATTRIBUTION: this is about THIS pass. `canonical_only` empty is the whole
        #    difference from `agent_fixture`'s happy path, where the proof was real but about
        #    nothing in particular.
        assert record["headline"]["status"] == "refuted", record["headline"]
        assert record["headline"]["canonical_only"] == [], \
            ("the finding must be ATTRIBUTED, not filed as a canonical contract",
             record["headline"])
        assert record["headline"]["provenance"] == "deterministic+agent-formal"
        assert report["summary"]["agent"]["agent_formal"].get("refuted") == 1

        # 4) THE GATE DISCRIMINATION. A FORMAL agent refutation is actionable and must trip
        #    --fail-on-agent-refuted. An ADVISORY `conclude("refuted")` must NOT (agent_fixture
        #    case 2 pins that direction). Same flag, same proposal, opposite outcomes -- because
        #    one is a verifier's verdict and the other is a model's opinion. If these ever agree,
        #    the trust boundary has collapsed in one direction or the other.
        _, gated_exit, _, _ = _run(tmp, "pc-gate.json", DISPATCH, SNIPPET,
                                   fail_on_agent_refuted=True)
        assert gated_exit == 1, \
            ("a formal agent refutation must trip --fail-on-agent-refuted", gated_exit)
        _, opinion_exit, _, _ = _run(
            tmp, "pc-opinion.json",
            [{"action": "conclude", "args": {"proposal": "refuted",
                                             "rationale": "model merely opines"}}],
            SNIPPET, fail_on_agent_refuted=True)
        assert opinion_exit == 0, \
            ("an ADVISORY refutation must not trip the same gate -- otherwise a model's opinion "
             "has become a build failure", opinion_exit)

        # 5) THE ABLATION: the refutation must be CAUSED BY the planted defect. Restore the
        #    reassoc guard on the unsound fold and the same strategy must prove all three. Without
        #    this, assertion 2 could be satisfied by a strategy that refutes this snippet for an
        #    unrelated reason -- which is how a false refutation would masquerade as a catch.
        ablated = tmp / "ablated.cpp"
        text = SNIPPET.read_text()
        needle = "Value *foldFloatAccumulate(Value *Packed) {\n  return CreateFAddReduce(Packed, Packed);"
        assert needle in text, "ablation needle drifted from the snippet"
        ablated.write_text(text.replace(
            needle,
            "Value *foldFloatAccumulate(Value *Packed, Value *Root) {\n"
            "  if (!getFastMathFlags(Root).allowReassoc()) return nullptr;\n"
            "  return CreateFAddReduce(Packed, Packed);"))
        report_a, _, _, record_a = _run(tmp, "pc-ablate.json", DISPATCH, ablated)
        check_a = record_a["formal_checks"][0]
        assert check_a["verdict"] == "proved", \
            ("guarding the planted fold must remove the refutation -- if it does not, the "
             "refutation was never about the planted defect", check_a)
        assert (check_a["transforms"], check_a["proved"], check_a["refuted"]) == (3, 3, 0), check_a
        #    ...and an ATTRIBUTABLE proof DOES upgrade the headline, where the canonical proof in
        #    `agent_fixture` is pinned at 0 upgrades. The attributable/canonical split has teeth in
        #    both directions, not just the one that guards against over-claiming.
        assert report_a["summary"]["agent"]["headline_upgrades"] == 1, report_a["summary"]["agent"]
        assert record_a["headline"]["status"] == "proved", record_a["headline"]

        # 6) THE SECOND TERRITORY: `planned` residue and CROSS-FAMILY routing. Case 2 covers a pass
        #    the classifier could not place at all. This one it places CONFIDENTLY AND WRONGLY, and
        #    that is the harder case, because a wrong family still produces a full plan and a
        #    confident-looking report.
        #
        #    `cross_family_unattributed_snippet.cpp` carries the same planted unguarded FP reduction
        #    but wears peephole idiom (`replaceInstUsesWith`, `Builder.CreateFAddReduce`), so it
        #    classifies `peephole` at score 18 and `slp-source` is never planned. Its one
        #    source-targeted check answers `inconclusive`; the rest are canonical or pass-runners
        #    that fell back to their canonical pass. Until the attribution fix this pass was
        #    reported PROVED and the agent never saw it -- `proved` is not residue. Now it lands on
        #    `planned` and the agent can do the one thing the planner structurally cannot: dispatch
        #    a strategy from a family the classifier did not pick.
        cross = SNIPPET.parent / "cross_family_unattributed_snippet.cpp"
        report_x, exit_x, entry_x, record_x = _run(tmp, "xfam.json", DISPATCH, cross)
        assert exit_x == 0, exit_x
        assert entry_x["headline"]["status"] == "planned", \
            ("the misclassified pass must NOT be certified -- its only source-targeted check was "
             "inconclusive and the rest never read it", entry_x["headline"])
        assert entry_x["headline"]["unattributed_proofs"], \
            ("...and the proofs it did collect must be named, not dropped", entry_x["headline"])
        assert record_x is not None, \
            ("`planned` must be RESIDUE. It is the plainest residue there is -- checks ran and "
             "produced no attributable verdict -- and excluding it left this pass untriaged by "
             "both layers at once", entry_x["headline"])
        check_x = record_x["formal_checks"][0]
        assert check_x["strategy"] == "slp-source" and check_x["verdict"] == "refuted", \
            ("the agent must recover the finding by routing OUTSIDE the classified family -- the "
             "planner only ever runs the matched family's strategies", check_x)
        assert (check_x["transforms"], check_x["proved"], check_x["refuted"]) == (3, 2, 1), check_x
        assert record_x["headline"]["status"] == "refuted", record_x["headline"]
        #    The five unattributed deterministic proofs must stay OUT of the agent headline too --
        #    the same rule, one implementation, both paths.
        assert set(record_x["headline"]["canonical_only"]) >= {
            "instcombine-ir", "reassociate-ir", "early-cse-ir", "symexec-real-pass",
            "klee-symexec"}, record_x["headline"]

    del os.environ["AGENT_STUB_SCENARIO"]
    print("agent_positive_control_fixture OK: on a pass the deterministic layer cannot classify "
          "(zero checks planned), the agent routes to slp-source and REFUTES a real planted "
          "unguarded FP reduction -- attributed to this pass (canonical_only empty), 2 sibling "
          "folds still proving, tripping --fail-on-agent-refuted where an advisory 'refuted' does "
          "not; restoring the reassoc guard turns the same check green (3/3, headline upgraded). "
          "AND on a pass the classifier places CONFIDENTLY AND WRONGLY (peephole idiom, SLP fold): "
          "the headline is `planned` with its unattributed proofs named, that IS residue, and the "
          "agent recovers the same refutation by dispatching outside the classified family -- the "
          "one move the planner structurally cannot make")
    return 0


if __name__ == "__main__":
    sys.exit(main())
