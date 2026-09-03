#!/usr/bin/env python3
"""A `proved` headline must rest on a check that actually read this pass.

THE INCIDENT. A vendor pass carrying a PLANTED unsound fold was reported **proved** by the
deterministic orchestrator -- the tool's primary output, not an advisory layer.

`cross_family_unattributed_snippet.cpp` emits an FP horizontal reduction with no reassoc guard
(the same defect `agent_positive_control_fixture` catches), but it is written in peephole idiom:
`replaceInstUsesWith` (classifier weight 4) and `Builder.CreateFAddReduce` (weight 2). It therefore
classifies `peephole`, score 18, and `vectorize-slp` never matches -- so `slp-source`, the strategy
that can actually mine the reduction, is never planned. Of the seven peephole checks:

  - `symexec-fold-cascade`, the ONLY source-targeted one, answered `inconclusive` ("no fold
    functions mined") -- the single check that read the pass had nothing to say;
  - `instcombine-ir` / `reassociate-ir` / `early-cse-ir` answered `proved` -- pass-runners that,
    because the pass under verification is not theirs, fell back to their `canonical_pass` and
    validated real InstCombine/Reassociate/EarlyCSE on canonical IR;
  - `symexec-real-pass` / `klee-symexec` answered `proved` -- canonical, fixed contracts.

Five true proofs about other code, collapsed into a certificate for this one. The agent could not
help either: `proved` is not residue, so it was never invoked (`attempted: 0`).

WHY IT SURVIVED. `f79398b` fixed exactly this on the AGENT headline and left the deterministic one
alone, and the agent's own rule excluded only `canonical` -- not a pass-runner that fell back. The
judgement now lives once, in `o2t.orchestrate.attribution`, and both paths call it.

WHAT STAYS TRUE. Attribution gates POSITIVE verdicts only. A refutation still dominates from any
strategy, and an unknown strategy still counts as attributable, because both alternatives lose
negative evidence -- and losing a refutation hides a miscompile.

Needs z3 + opt.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.orchestrate.attribution import is_about_pass  # noqa: E402

SNIPPET = Path(__file__).resolve().parent / "cross_family_unattributed_snippet.cpp"


def main() -> int:
    opt = shutil.which("opt") or "/opt/homebrew/opt/llvm@18/bin/opt"
    if shutil.which("z3") is None or not Path(opt).exists():
        print("headline_attribution_fixture: z3/opt not found, skipped")
        return 0

    # 1) THE RULE, unit-level. A pass-runner counts for the pass it RUNS and not for another.
    assert is_about_pass("instcombine-ir", "instcombine") is True, \
        "verifying InstCombine with instcombine-ir IS attributable -- the real pass ran"
    assert is_about_pass("instcombine-ir", None) is False, \
        ("a pass-runner that fell back to its canonical pass proves something about LLVM, not "
         "about the pass under verification")
    assert is_about_pass("symexec-real-pass", "instcombine") is False, "canonical is never attributable"
    assert is_about_pass("symexec-fold-cascade", None) is True, "a source-targeted check reads the pass"
    assert is_about_pass("a-brand-new-strategy", None) is True, \
        ("an unknown strategy must count -- excluding it by default would silently drop verdicts, "
         "and dropping a refutation hides a miscompile")

    # 2) END TO END: the planted-bug pass must NOT come back `proved`.
    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "r.json"
        subprocess.run([sys.executable, str(ROOT / "tools" / "cv-orchestrate.py"),
                        "--source", str(SNIPPET), "--opt-bin", opt, "--report", str(report)],
                       capture_output=True, text=True, timeout=900)
        entry = json.loads(report.read_text())["passes"][0]
    head = entry["headline"]

    assert head["status"] != "proved", \
        ("a pass whose only source-targeted check was INCONCLUSIVE must never be certified proved; "
         "this snippet carries a planted unguarded FP reduction", head)
    #    The five proofs are real and must still be RECORDED -- suppressing them would trade an
    #    over-claim for a different dishonesty.
    assert head["verdicts"].get("proved") == 5, ("the checks still ran and are still reported", head)
    assert set(head["unattributed_proofs"]) == {
        "instcombine-ir", "reassociate-ir", "early-cse-ir", "symexec-real-pass", "klee-symexec"}, head
    assert "not about this pass" in head["reason"], \
        ("the headline must SAY why it withheld the proof, not merely withhold it", head)

    # 3) THE GUARD MUST NOT EAT REAL PROOFS. Verifying a pass a pass-runner actually runs still
    #    yields an attributable proof -- otherwise this fix would have silently disabled Track B's
    #    contribution to every real-pass headline.
    assert is_about_pass("reassociate-ir", "reassociate") and is_about_pass("early-cse-ir", "early-cse"), \
        "the canonical-fallback rule must key on the PASS, not disable pass-runners wholesale"

    print("headline_attribution_fixture OK: a vendor pass with a planted unguarded FP reduction is "
          "no longer certified `proved` by five checks that never read it (three canonical-fallback "
          "pass-runners, two canonical) while its only source-targeted check answered inconclusive; "
          "the proofs are still recorded and named as unattributed, the headline says why, and a "
          "pass-runner verifying its OWN pass still attributes normally")
    return 0


if __name__ == "__main__":
    sys.exit(main())
