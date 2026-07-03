#!/usr/bin/env python3
"""Cover the pass-aware orchestrator: classify -> plan -> dispatch, plus the LLM brain hook.

Asserts the classifier routes each known pass-source family correctly, the planner marks
checks feasible/skipped with the right reasons, the wired verifiers return the expected
verdicts (scev-intent proves the LSR source; symexec finds the peephole folds sound), and the
optional LLM brain is consulted ONLY for an ambiguous classification (advisory, never
overriding). The heavy translation-validation run is checked for DISPATCH only (it is exercised
end-to-end by translation_validation_fixture)."""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FX = ROOT / "tests" / "fixtures"

from o2t.orchestrate.classify import classify
from o2t.orchestrate.plan import plan_for
from o2t.orchestrate.run import resolve_context, orchestrate, execute_check
from o2t.orchestrate.brain import is_ambiguous, maybe_llm_classify


def write_compile_db(path: Path, sources: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([
            {
                "directory": str(ROOT),
                "command": "clang++ -std=c++17 " + str(source),
                "file": str(source),
            }
            for source in sources
        ]),
        encoding="utf-8",
    )


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("orchestrate_fixture: z3 not found, skipped")
        return 0
    ctx = resolve_context()

    # 1) CLASSIFICATION: each known pass-source family routes correctly (source + name hint).
    expect = {
        "loop_pass_scev.cpp": "loop-scev-recurrence",
        "intent_inference_snippet.cpp": "peephole",
        "third_party_dse_like_pass.cpp": "memory-dse",
        "third_party_globalopt_like_pass.cpp": "global",
        "dce_dead_instruction_folds.cpp": "cleanup-dce",
    }
    for fname, fam in expect.items():
        c = classify((FX / fname).read_text())
        assert c.primary == fam, (fname, c.primary, fam)
    assert classify("void f(){ if (AI.users().empty()) AI.eraseFromParent(); }").primary == "cleanup-dce"
    assert classify("void f(){ if (!AI->hasNUsesOrMore(1)) AI->eraseFromParent(); }").primary == "cleanup-dce"
    assert classify("", pass_name="indvars").primary == "loop-scev-recurrence"
    assert classify("", pass_name="dse").primary == "memory-dse"
    assert classify("", pass_name="dce").primary == "cleanup-dce"

    # 2) PLANNING: a loop-scev source plans scev-intent (feasible w/ z3) + translation-validation.
    cls = classify((FX / "loop_pass_scev.cpp").read_text(), "indvars")
    plan = plan_for(cls, ctx, has_source=True)
    by = {c.strategy: c for c in plan}
    assert by["scev-intent"].feasible, by["scev-intent"]
    # name-only (no source): a source strategy must be skipped, not run.
    plan_nosrc = plan_for(classify("", "indvars"), ctx, has_source=False)
    assert not next(c for c in plan_nosrc if c.strategy == "scev-intent").feasible

    # 3) DISPATCH to real verifiers -- light checks executed end to end.
    rep = orchestrate([
        {"source": str(FX / "loop_pass_scev.cpp"), "pass_name": None},
        {"source": str(FX / "intent_inference_snippet.cpp"), "pass_name": "instcombine"},
        {"source": str(FX / "third_party_dse_like_pass.cpp"), "pass_name": "dse"},
    ], ctx)
    v = {p["primary_family"]: {c["strategy"]: c.get("verdict") for c in p["checks"]}
         for p in rep["passes"]}
    assert v["loop-scev-recurrence"]["scev-intent"] == "proved", v
    if ctx["ast-miner"]:
        assert v["peephole"]["symexec-fold-cascade"] == "sound", v
    # peephole also runs closed-loop translation validation of the real `opt -passes=instcombine`
    # output (scalar IR -> SMT, returned value proved equal for all inputs).
    if ctx["opt"]:
        assert v["peephole"]["instcombine-ir"] == "proved", v
    assert "modelcheck-real-pass" in v["peephole"], v
    if ctx["model-checker"]:
        assert v["peephole"]["modelcheck-real-pass"] == "proved", v
    # memory-dse dispatches the deep theory-of-arrays memory-model AND the source-intent
    # dse-facts -- both proved on the (sound) third-party DSE-like source.
    assert v["memory-dse"]["memory-model"] == "proved", v
    assert v["memory-dse"]["dse-facts"] == "proved", v
    # memory-dse also runs closed-loop translation validation of the real `opt -passes=dse`
    # output (theory of arrays over the literal surviving instructions).
    if ctx["opt"]:
        assert v["memory-dse"]["dse-ir"] == "proved", v

    # 3a2) the vectorize-slp family dispatches the DEEP slp-model (lane mapping + reduction
    #      contracts, incl. the FP teeth) alongside the source-intent slp-transaction.
    rep_slp = orchestrate([{"source": str(FX / "vector_pass_snippet.cpp"),
                            "pass_name": "slp-vectorizer"}], ctx)
    sv = {c["strategy"]: c["verdict"] for c in rep_slp["passes"][0]["checks"]}
    assert sv.get("slp-model") == "proved", ("deep SLP model not proved", sv)
    # vectorize-slp also runs closed-loop translation validation of the real `opt
    # -passes=slp-vectorizer` output (vector IR -> SMT, per output cell).
    if ctx["opt"]:
        assert sv.get("slp-ir") == "proved", ("SLP translation validation not proved", sv)
    # source-driven SLP catches an unsound vectorizer: the slp_reduction_folds source emits an
    # FP reduction without a fast-math guard -> slp-source must REFUTE it.
    rep_slpbad = orchestrate([{"source": str(FX / "slp_reduction_folds.cpp"),
                               "pass_name": "slp-vectorizer"}], ctx)
    ss = next(c for c in rep_slpbad["passes"][0]["checks"] if c["strategy"] == "slp-source")
    assert ss["verdict"] == "refuted" and ss.get("refuted", 0) >= 1, ("unsound FP reduction not caught", ss)
    assert ss.get("proved", 0) >= 3, ("sound reductions should still prove/allow", ss)
    # source-driven SLP also recovers binop-PACK lane mappings: a fold whose extract lanes don't
    # match its insert (pack) lanes is a lane-bookkeeping bug -> slp-source must REFUTE it.
    rep_pack = orchestrate([{"source": str(FX / "slp_pack_folds.cpp"),
                             "pass_name": "slp-vectorizer"}], ctx)
    ps = next(c for c in rep_pack["passes"][0]["checks"] if c["strategy"] == "slp-source")
    assert ps["verdict"] == "refuted" and ps.get("refuted", 0) >= 1, ("swapped-lane pack not caught", ps)
    assert ps.get("proved", 0) >= 2, ("consistent packs should still prove", ps)

    # 3b) SOURCE-DRIVEN memory verification catches an unsound pass from its source: the
    #     dse_memory_folds.cpp source has a planted fold that removes a store without an
    #     overwrite guard -> memory-source must REFUTE it.
    rep_bad = orchestrate([{"source": str(FX / "dse_memory_folds.cpp"), "pass_name": "dse"}], ctx)
    ms = next(c for c in rep_bad["passes"][0]["checks"] if c["strategy"] == "memory-source")
    assert ms["verdict"] == "refuted" and ms.get("refuted", 0) >= 1, ("unsound fold not caught", ms)
    assert ms.get("proved", 0) >= 2, ("sound folds should still prove", ms)

    # 3c) SOURCE-DRIVEN DCE verification catches an unsafe erasure: a bare eraseFromParent without
    #     a trivially-dead guard may remove a live use or side effect -> dce-source must REFUTE it.
    rep_dce = orchestrate([{"source": str(FX / "dce_dead_instruction_folds.cpp"),
                            "pass_name": "dce"}], ctx)
    ds = next(c for c in rep_dce["passes"][0]["checks"] if c["strategy"] == "dce-source")
    assert ds["verdict"] == "refuted" and ds.get("refuted", 0) == 1, ("unguarded DCE not caught", ds)
    assert ds.get("proved", 0) == 3, ("guarded DCE erasures should prove", ds)
    assert any(c["strategy"] == "dce-model" and c["verdict"] == "proved"
               for c in rep_dce["passes"][0]["checks"]), rep_dce

    # 4) translation-validation DISPATCH (heavy proof not run here): a known pass is feasible.
    tv = plan_for(classify("", "indvars"), ctx, has_source=False)
    tvc = next(c for c in tv if c.strategy == "translation-validation")
    assert tvc.feasible == bool(ctx["opt"] and ctx["z3"]), tvc

    # 4b) cfg-shape: a canonical-pass strategy is feasible from z3+opt alone (no user pass name),
    #     and dispatches the diamond→select if-conversion contract to a `proved` verdict.
    if ctx["opt"]:
        cfg_plan = plan_for(classify("", "simplifycfg"), ctx, has_source=False)
        cfgc = next(c for c in cfg_plan if c.strategy == "cfg-shape")
        assert cfgc.feasible, ("cfg-shape should be feasible with z3+opt", cfgc)
        rep_cfg = orchestrate([{"source": str(FX / "llvm_pass_snippet.cpp"),
                                "pass_name": "simplifycfg"}], ctx)
        cfg_verdict = next(c["verdict"] for c in rep_cfg["passes"][0]["checks"]
                           if c["strategy"] == "cfg-shape")
        assert cfg_verdict == "proved", ("cfg-shape if-conversion not proved", cfg_verdict)

    # 5) LLM BRAIN: consulted ONLY for an ambiguous classification; advisory, never overrides.
    rep2 = orchestrate([
        {"source": str(FX / "llvm_pass_snippet.cpp"), "pass_name": None},      # multi-family -> ambiguous
        {"source": str(FX / "loop_pass_scev.cpp"), "pass_name": None},          # clear -> not ambiguous
    ], ctx, execute=False)
    amb = {Path(p["source"]).name: is_ambiguous(p) for p in rep2["passes"]}
    assert amb["llvm_pass_snippet.cpp"] and not amb["loop_pass_scev.cpp"], amb
    stub = ROOT / "tests" / "fixtures" / "orchestrate_llm_stub.py"
    maybe_llm_classify(rep2, f"{sys.executable} {stub}")
    e_amb = next(p for p in rep2["passes"] if Path(p["source"]).name == "llvm_pass_snippet.cpp")
    e_clear = next(p for p in rep2["passes"] if Path(p["source"]).name == "loop_pass_scev.cpp")
    assert e_amb["llm"]["family"] in {f for f in e_amb["scores"]} or e_amb["llm"]["family"], e_amb
    deterministic_primary = classify((FX / "llvm_pass_snippet.cpp").read_text()).primary
    assert e_amb["primary_family"] == deterministic_primary, "LLM must NOT override deterministic primary"
    assert "llm" not in e_clear, "clear classification must not consult the LLM"

    # 6) CLI intake: a source directory expands to pass-like C++ files, supports filters, and
    #    writes a compact summary in plan-only mode for third-party tree triage.
    with tempfile.TemporaryDirectory() as td:
        report_path = Path(td) / "orchestrate-third-party.json"
        summary_path = Path(td) / "orchestrate-third-party.txt"
        cli = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "cv-orchestrate.py"),
                "--source",
                str(FX),
                "--include",
                "third_party_",
                "--no-execute",
                "--report",
                str(report_path),
                "--summary-text",
                str(summary_path),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert cli.returncode == 0, (cli.stdout, cli.stderr)
        cli_report = json.loads(report_path.read_text(encoding="utf-8"))
        summary = cli_report["summary"]
        assert summary["passes"] == 4, summary
        assert summary["classified"] == 4 and summary["unclassified"] == 0, summary
        assert summary["planned_or_skipped"] == 21, summary
        assert summary["by_headline"] == {"planned": 4}, summary
        assert summary["attention"] == {}, summary
        assert summary["by_family"] == {
            "global": 1,
            "memory-dse": 1,
            "peephole": 1,
            "promotion": 1,
        }, summary
        matrix = summary["readiness_matrix"]["families"]
        assert matrix["global"]["planned_checks"] == 5, matrix
        assert matrix["memory-dse"]["sources"] == 1, matrix
        assert matrix["memory-dse"]["planned_checks"] == 6, matrix
        assert matrix["memory-dse"]["headlines"] == {"planned": 1}, matrix
        assert matrix["peephole"]["planned_checks"] == 7, matrix
        assert matrix["promotion"]["planned_checks"] == 3, matrix
        assert summary["next_actions"] == [], summary
        assert ":planned]" in cli.stderr and "third_party_dse_like_pass.cpp" in cli.stderr
        summary_text = summary_path.read_text(encoding="utf-8")
        assert "O2T Orchestrator Summary" in summary_text
        assert "headlines: planned=4" in summary_text
        assert "Readiness Matrix" in summary_text
        assert "Next Actions" in summary_text
        assert "  none" in summary_text
        assert "[planned] memory-dse" in summary_text
        assert "third_party_dse_like_pass.cpp" in summary_text

    orchestrate_helpers = runpy.run_path(str(ROOT / "tools" / "cv-orchestrate.py"))
    synthetic_report = {
        "passes": [],
        "deep_audit": {
            "enabled": True,
            "exit_code": 0,
            "out": "/tmp/o2t-synthetic-deep-audit",
            "artifacts": {"real_pass_readiness": ""},
            "summary": {
                "sources": {"selected": 1},
                "findings": {"total": 1},
                "intents": {"total": 1, "proof_status": {"proved": 1}},
                "pass_impl_ir": {"intent_check_status": {"matched": 1}},
                "modelcheck": {
                    "generated": 2,
                    "proved": 2,
                    "refuted": 0,
                    "error": 0,
                    "width_mode": "8,16",
                    "selected_widths": [8, 16],
                    "widths": {
                        "8": {"proved": 1, "refuted": 0, "unsupported": 0, "skipped": 0, "error": 0},
                        "16": {"proved": 1, "refuted": 0, "unsupported": 0, "skipped": 0, "error": 0},
                    },
                    "components": [
                        {
                            "source_kind": "intent",
                            "summary": "/tmp/o2t-synthetic-deep-audit/modelcheck-intents/modelcheck-summary-intent.json",
                            "records": 1,
                            "generated": 2,
                            "proved": 2,
                            "refuted": 0,
                            "unsupported": 0,
                            "skipped": 0,
                            "error": 0,
                            "selected_widths": [8, 16],
                        }
                    ],
                    "findings": [
                        {
                            "status": "refuted",
                            "marker": "probe.dse.overwritten-store",
                            "reason": "counterexample",
                            "file": "VendorDSEPass.cpp",
                            "line": 42,
                            "width": 16,
                            "domain": "memory-bv16",
                            "source_function": "eliminateStoreNoOverwriteGuard",
                        }
                    ]
                    + [
                        {
                            "status": "refuted",
                            "marker": f"probe.synthetic.extra-{index}",
                            "reason": "counterexample",
                            "file": "VendorDSEPass.cpp",
                            "line": 50 + index,
                            "width": 16,
                            "domain": "memory-bv16",
                            "source_function": f"extraFinding{index}",
                        }
                        for index in range(11)
                    ],
                },
                "coverage": {},
                "budget_violations": [],
            },
        },
    }
    orchestrate_helpers["_annotate_headlines"](synthetic_report)
    synthetic_summary = orchestrate_helpers["_summarize"](synthetic_report)
    synthetic_deep = synthetic_summary["readiness_matrix"]["deep_audit"]
    assert synthetic_deep["modelcheck_width_mode"] == "8,16", synthetic_deep
    assert synthetic_deep["modelcheck_selected_widths"] == [8, 16], synthetic_deep
    assert synthetic_deep["modelcheck_widths"]["8"]["proved"] == 1, synthetic_deep
    assert synthetic_deep["modelcheck_findings"] == 12, synthetic_deep
    assert synthetic_deep["modelcheck_omitted_findings"] == 7, synthetic_deep
    assert synthetic_deep["modelcheck_components"][0]["selected_widths"] == [8, 16], synthetic_deep
    assert synthetic_deep["modelcheck_components"][0]["width_mode"] == "", synthetic_deep
    assert synthetic_deep["modelcheck_components"][0]["summary"].endswith("modelcheck-summary-intent.json"), synthetic_deep
    assert synthetic_deep["modelcheck_top_findings"][0] == (
        {
            "status": "refuted",
            "marker": "probe.dse.overwritten-store",
            "reason": "counterexample",
            "file": "VendorDSEPass.cpp",
            "line": 42,
            "width": 16,
            "domain": "memory-bv16",
            "source_function": "eliminateStoreNoOverwriteGuard",
        }
    ), synthetic_deep
    assert len(synthetic_deep["modelcheck_top_findings"]) == 5, synthetic_deep
    synthetic_actions = synthetic_summary["next_actions"]
    assert any(
        action["kind"] == "modelcheck-finding"
        and "@16b memory-bv16 probe.dse.overwritten-store" in action["detail"]
        for action in synthetic_actions
    ), synthetic_actions
    assert any(
        action["kind"] == "modelcheck-findings-omitted"
        and "2 additional modelcheck findings omitted" in action["detail"]
        for action in synthetic_actions
    ), synthetic_actions
    synthetic_report["summary"] = synthetic_summary
    synthetic_text = orchestrate_helpers["_render_summary_text"](synthetic_report)
    assert "modelcheck_widths=8,16" in synthetic_text, synthetic_text
    assert (
        "intent: records=1 generated=2 proved=2 refuted=0 unsupported=0 "
        "skipped=0 error=0 selected=8,16"
    ) in synthetic_text, synthetic_text
    assert (
        "refuted: @16b memory-bv16 probe.dse.overwritten-store "
        "eliminateStoreNoOverwriteGuard VendorDSEPass.cpp:42 (counterexample)"
    ) in synthetic_text, synthetic_text
    assert "    ... 7 more" in synthetic_text, synthetic_text
    assert orchestrate_helpers["_selected_widths_label"](
        {"selected_widths": [], "width_mode": "8,bad"}
    ) == "none"

    # 7) Deep-audit failures are surfaced as next actions without changing the fast
    #    orchestrator headline semantics.
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        source = FX / "third_party_globalopt_like_pass.cpp"
        compile_db = td_path / "db" / "compile_commands.json"
        write_compile_db(compile_db, [source])
        report_path = td_path / "orchestrate-deep-fail.json"
        cli = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "cv-orchestrate.py"),
                "--source",
                str(source),
                "--no-execute",
                "--compile-commands",
                str(compile_db),
                "--audit-out",
                str(td_path / "deep"),
                "--ast-miner",
                str(ROOT / "build-clang-tools" / "cv-mine-pass-source-ast"),
                "--ir-miner",
                str(td_path / "missing-cv-mine-pass-impl-ir"),
                "--mine-pass-impl-ir",
                "--fail-on-deep-audit-error",
                "--report",
                str(report_path),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert cli.returncode == 1, (cli.stdout, cli.stderr)
        deep_fail = json.loads(report_path.read_text(encoding="utf-8"))
        assert deep_fail["summary"]["deep_audit"]["exit_code"] != 0, deep_fail
        actions = deep_fail["summary"]["next_actions"]
        assert any(action["kind"] == "deep-audit-error" for action in actions), actions
        assert deep_fail["summary"]["readiness_matrix"]["deep_audit"]["enabled"] is True

    print("orchestrate_fixture OK: classify -> plan -> dispatch to real verifiers; "
          "LLM brain advisory on ambiguous cases only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
