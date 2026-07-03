#!/usr/bin/env python3
"""Cover the orchestrator-driven sweep (sweep.py + cv-orchestrate-sweep.py).

Asserts the front door, swept over a curated multi-family pass-set, (1) routes every source to
its expected family, (2) reaches the expected headline verdict per source -- proved for the
sound sources, REFUTED (teeth) for the planted/under-guarded ones, advisory for the known gap,
(3) keeps the headline decided by PRIMARY-family checks only (a secondary cross-family dispatch
never flips a source's verdict), and (4) rolls up the expected breadth (families, deep verifiers,
teeth). Needs z3; the peephole (symexec) case is gated on the AST miner being present."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.orchestrate.run import resolve_context
from o2t.orchestrate.sweep import run_sweep, headline, primary_checks, MANIFEST


def main() -> int:
    if shutil.which("z3") is None:
        print("orchestrate_sweep_fixture: z3 not found, skipped")
        return 0
    ctx = resolve_context()
    rep = run_sweep(ctx)
    rows = {r["source"]: r for r in rep["rows"]}
    s = rep["summary"]

    # 1) every case routes to its expected family and reaches its expected headline.
    for r in rep["rows"]:
        assert r["family_ok"], ("misrouted", r["source"], r["primary_family"], r["expected_family"])
        assert r["ok"], ("wrong headline", r["source"], r["observed"], r["expect"])

    # 2) the teeth fire from SOURCE on every unsound/under-guarded case (and a refutation, not a
    #    mere inconclusive, is what makes the headline).
    for src in ("dse_memory_folds.cpp", "third_party_dse_like_pass.cpp",
                "slp_reduction_folds.cpp", "slp_pack_folds.cpp",
                "global_dead_initializer_unsafe_snippet.cpp", "dce_dead_instruction_folds.cpp",
                "licm_hoist_folds.cpp",
                "cfg_ifconv_folds.cpp"):
        assert rows[src]["observed"] == "refuted", (src, rows[src])
        assert any(c["verdict"] == "refuted" for c in rows[src]["primary"]), rows[src]

    # 3) sound sources prove via a DEEP primary verifier (not just a planned/advisory check).
    assert any(c["verdict"] == "proved" for c in rows["vector_pass_snippet.cpp"]["primary"])
    assert rows["llvm_pass_snippet.cpp"]["observed"] == "proved"     # cfg if-conversion
    assert rows["loop_pass_scev.cpp"]["observed"] == "proved"        # scev intent
    # GlobalOpt is no longer an advisory gap: the sound dead-initializer source now PROVES via a
    # deep semantic verifier (globalopt-source + globalopt-model), not a syntactic witness.
    gp = rows["third_party_globalopt_like_pass.cpp"]
    assert gp["observed"] == "proved", gp
    assert {"globalopt-source", "globalopt-model"} <= {c["strategy"] for c in gp["primary"]}, gp
    assert any(c["strategy"] == "globalopt-model" and c["verdict"] == "proved"
               for c in gp["primary"]), ("deep globalopt model must prove", gp)

    dc = rows["dce_dead_instruction_sound.cpp"]
    assert dc["observed"] == "proved", dc
    assert {"dce-source", "dce-model"} <= {c["strategy"] for c in dc["primary"]}, dc
    assert any(c["strategy"] == "dce-model" and c["verdict"] == "proved"
               for c in dc["primary"]), ("deep DCE model must prove", dc)

    # 3b) PRIMARY-only headline: llvm_pass_snippet is multi-family (cfg primary) and DOES dispatch
    #     secondary memory/peephole checks that return refuted/miscompile -- those must NOT flip
    #     its headline, which stays `proved` from the cfg-shape primary check.
    lp = rows["llvm_pass_snippet.cpp"]
    assert lp["primary_family"] == "cfg" and lp["observed"] == "proved", lp
    assert any(c["verdict"] in ("refuted", "miscompile") for c in lp["secondary"]), \
        ("expected a noisy secondary cross-check to be present and ignored", lp)

    # 3c) loop-structural is no longer uncovered: a sound LICM source PROVES via the deep hoist
    #     model, while the loop-invariance-only hoist is refuted (above).
    lc = rows["licm_hoist_sound.cpp"]
    assert lc["primary_family"] == "loop-structural" and lc["observed"] == "proved", lc
    assert any(c["strategy"] == "licm-model" and c["verdict"] == "proved"
               for c in lc["primary"]), ("deep LICM model must prove", lc)

    # 3d) cfg now has a SOURCE-RECOVERY tier too (matrix fully symmetric): a sound if-conversion
    #     source proves via cfg-source, the swapped one is refuted (above).
    cs = rows["cfg_ifconv_sound.cpp"]
    assert cs["primary_family"] == "cfg" and cs["observed"] == "proved", cs
    assert any(c["strategy"] == "cfg-source" and c["verdict"] == "proved"
               for c in cs["primary"]), ("cfg source recovery must prove", cs)

    # 3e) the promotion family (mem2reg) reaches its multi-block + phi translation validator.
    mp = rows["third_party_mem2reg_like_pass.cpp"]
    assert mp["primary_family"] == "promotion" and mp["observed"] == "proved", mp
    assert any(c["strategy"] == "mem2reg-ir" and c["verdict"] == "proved"
               for c in mp["primary"]), ("mem2reg translation validation must prove", mp)

    # 4) roll-up breadth: ALL 9 classifier families covered, a deep verifier per family, 8 teeth,
    #    and NO advisory gaps -- every modeled family reaches a deep verifier.
    assert len(s["families_exercised"]) >= 9, s
    for v in ("slp-source", "slp-model", "memory-source", "memory-model", "cfg-shape",
              "cfg-source", "scev-intent", "globalopt-source", "globalopt-model",
              "dce-source", "dce-model", "licm-source", "licm-model", "mem2reg-ir"):
        assert v in s["deep_verifiers_dispatched"], (v, s)
    assert len(s["teeth_fired"]) == 8, s
    assert s["advisory_gaps"] == [], ("every modeled family should reach a deep verifier", s)
    assert s["all_ok"], s

    # 5) the headline helper is teeth-dominant and the manifest is internally consistent.
    assert headline([{"strategy": "x", "verdict": "proved"},
                     {"strategy": "y", "verdict": "refuted"}]) == "refuted"
    assert headline([{"strategy": "x", "verdict": "inconclusive"}]) == "advisory"
    assert {c.expect for c in MANIFEST} <= {"proved", "refuted", "advisory"}

    # 6) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-orchestrate-sweep.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"all_ok": true' in proc.stdout, proc.stdout
    assert '"teeth": 8' in proc.stdout and '"gaps": 0' in proc.stdout, proc.stdout
    assert '"families": 9' in proc.stdout, proc.stdout

    print("orchestrate_sweep_fixture OK: front door swept over all 9 families, each reaching a "
          "deep verifier (DCE cleanup added) -- sound sources proved, 8 unsound/"
          "under-guarded sources refuted from source; primary-family verdicts authoritative, "
          "cross-family noise ignored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
