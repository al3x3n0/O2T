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
PROMO = Path(__file__).resolve().parent / "promotion_vendor_snippet.cpp"


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

    # 3) THE STRUCTURAL FORM, which is worse than the incidental one above. `promotion` has exactly
    #    ONE strategy -- `mem2reg-ir`, a pass-runner with `canonical_pass=mem2reg` -- so for any
    #    pass that is not mem2reg itself the family has NO check that can say anything at all. Case
    #    2 needed a coincidence (its source check happening to answer `inconclusive`); this needed
    #    nothing to go wrong, and certified 100% of vendor passes reaching this family.
    with tempfile.TemporaryDirectory() as td:
        report2 = Path(td) / "p.json"
        subprocess.run([sys.executable, str(ROOT / "tools" / "cv-orchestrate.py"),
                        "--source", str(PROMO), "--opt-bin", opt, "--report", str(report2)],
                       capture_output=True, text=True, timeout=900)
        promo = json.loads(report2.read_text())["passes"][0]
    ph = promo["headline"]
    assert promo["primary_family"] == "promotion", promo.get("primary_family")
    assert ph["status"] != "proved", \
        ("a family with no attributable check must never certify a vendor pass", ph)
    assert ph["unattributed_proofs"] == ["mem2reg-ir"], ph
    #    AND THE EXPOSURE IS MEASURED, not just this one case: no family may silently become one
    #    where a vendor pass cannot be spoken about without that being visible here.
    from o2t.orchestrate.classify import FAMILIES
    blind = sorted(f.name for f in FAMILIES
                   if not any(is_about_pass(s, None) for s in f.strategies))
    assert blind == ["promotion"], \
        ("exactly one family has no attributable check for a vendor pass today; if this list grows, "
         "a new family has been added that can only ever report proofs about other code -- and if "
         "it shrinks, promotion gained a source-targeted strategy and this fixture should say so",
         blind)

    # 4) THE GUARD MUST NOT EAT REAL PROOFS. Verifying a pass a pass-runner actually runs still
    #    yields an attributable proof -- otherwise this fix would have silently disabled Track B's
    #    contribution to every real-pass headline.
    assert is_about_pass("reassociate-ir", "reassociate") and is_about_pass("early-cse-ir", "early-cse"), \
        "the canonical-fallback rule must key on the PASS, not disable pass-runners wholesale"

    # 4b) THE INVERSE ERROR: a proof ABOUT THIS PASS must not be discarded for belonging to a
    #     retained SECONDARY family. The classifier picks one primary family; when that family has
    #     no attributable check, the pass reads as uncertified while a sibling family has actually
    #     proved something about it.
    #
    #     Found by fixing the classifier rather than by looking: once the reduction builders became
    #     an SLP signal, `vectorize-slp` was retained on VeGen's VectorPackSet.cpp and `slp-source`
    #     proved getReifiedBackEdgeCond's CreateOrReduce sound -- and the headline went on saying
    #     "proved only by checks that are not about this pass", holding a real attributable proof it
    #     had thrown away. The two bugs masked each other: a pass certified by canonical fallbacks
    #     never needed a secondary proof to be noticed.
    #
    #     The fallback is deliberately narrow. The primary family still decides whenever it has an
    #     attributable verdict, and attribution still gates the fallback -- a secondary canonical or
    #     canonical-fallback pass-runner proof is no more about the pass than a primary one.
    entry_sec = {
        "primary_family": "promotion", "pass_name": None,
        "checks": [{"strategy": "mem2reg-ir", "verdict": "proved"},        # primary, unattributable
                   {"strategy": "slp-source", "verdict": "proved"}],       # secondary, ABOUT this pass
    }
    from importlib import import_module
    cv = import_module("importlib.util")
    spec = cv.spec_from_file_location("cvorch", ROOT / "tools" / "cv-orchestrate.py")
    mod = cv.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h_sec = mod._headline_for_pass(entry_sec)
    assert h_sec["status"] == "proved", \
        ("an attributable proof from a retained secondary family must certify the pass -- "
         "discarding it is the mirror of certifying with proofs that read nothing", h_sec)
    assert "secondary family" in h_sec["reason"] and "slp-source" in h_sec["reason"], h_sec
    #     ...and a secondary proof that is NOT about the pass must still not certify it.
    h_sec_bad = mod._headline_for_pass({
        "primary_family": "promotion", "pass_name": None,
        "checks": [{"strategy": "mem2reg-ir", "verdict": "proved"},
                   {"strategy": "symexec-real-pass", "verdict": "proved"}]})   # secondary CANONICAL
    assert h_sec_bad["status"] != "proved", \
        ("attribution must gate the secondary fallback too, or this reopens the same hole from the "
         "other side", h_sec_bad)

    # 5) ALL THREE HEADLINE IMPLEMENTATIONS MUST AGREE. There are three: `cv-orchestrate`'s
    #    `_headline_for_pass` (the tool's report), `o2t.agent.report.agent_headline`, and
    #    `o2t.orchestrate.sweep.headline` (the coverage sweep). Two of them had already drifted --
    #    f79398b taught the agent to discount canonical proofs and left the deterministic one
    #    certifying them, which is how a planted miscompile got a `proved` -- and the sweep was a
    #    third copy that discounted nothing. Duplication is the root cause here, not an incidental
    #    detail, so this asserts the shared rule is actually shared.
    from o2t.orchestrate.sweep import headline as sweep_headline
    from o2t.agent.report import agent_headline
    unattributed = [{"strategy": "mem2reg-ir", "verdict": "proved"}]
    assert sweep_headline(unattributed) == "advisory", \
        ("the sweep must discount a proof from a pass-runner that fell back, exactly as the "
         "report does -- otherwise the coverage sweep certifies what the tool declines", 
         sweep_headline(unattributed))
    assert sweep_headline(unattributed, "mem2reg") == "proved", \
        "...and must NOT discount it when the pass under verification is the one being run"
    assert sweep_headline([{"strategy": "mem2reg-ir", "verdict": "refuted"}]) == "refuted", \
        "a refutation counts from any strategy in every implementation"
    ah = agent_headline({"checks": unattributed, "pass_name": None}, {"formal_checks": []})
    assert ah["status"] != "proved" and ah["canonical_only"] == ["mem2reg-ir"], \
        ("the agent headline must reach the same conclusion from the same rule", ah)

    print("headline_attribution_fixture OK: a vendor pass with a planted unguarded FP reduction is "
          "no longer certified `proved` by five checks that never read it (three canonical-fallback "
          "pass-runners, two canonical) while its only source-targeted check answered inconclusive; "
          "the proofs are still recorded and named as unattributed, the headline says why, and a "
          "pass-runner verifying its OWN pass still attributes normally. The structural form is "
          "pinned too: `promotion` has ONE strategy and it is a canonical-fallback pass-runner, so "
          "the family could never say anything about a vendor pass yet certified every one -- and "
          "the set of such families is asserted, so a new blind family cannot be added quietly. "
          "All THREE headline implementations (report, agent, sweep) are checked to agree, since "
          "two of them drifting apart is what let the planted miscompile be certified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
