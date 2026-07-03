#!/usr/bin/env python3
"""Execute a per-pass check plan and aggregate verdicts.

Given pass source(s) (and optional pass-name hints), the orchestrator classifies each pass
(`classify`), plans its checks (`plan_for`), then DISPATCHES the feasible ones to the real
O2T verifiers and merges their verdicts. Three strategies are wired end-to-end here:

  * scev-intent             -> cv-mine-pass-scev        (source recurrence proof)
  * symexec-fold-cascade    -> cv-extract-pass-model    (peephole fold cascade, needs miner)
  * translation-validation  -> cv-translation-validate  (real opt output, standard passes)

Strategies whose runner is not wired are reported as `planned` (with the intended tool), so
the scheduler's coverage is explicit. Each check yields a verdict in
{proved, sound, validated, refuted, inconclusive, skipped, error}.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from o2t.orchestrate.classify import classify
from o2t.orchestrate.plan import ROOT, TOOLS, DEFAULT_LOOP_LL, STRATEGIES, plan_for

DEFAULT_AST_MINER = ROOT / "build-clang-tools" / "cv-mine-pass-source-ast"


def resolve_context(z3_bin="z3", opt_bin="opt", clang_bin="clang",
                    ast_miner: Path | None = None, model_checker_bin: str | None = None,
                    force_pass_runner=False) -> dict:
    """Resolve the binaries the strategies need into a run context (absent -> None)."""
    miner = Path(ast_miner) if ast_miner else DEFAULT_AST_MINER
    return {
        "z3": shutil.which(z3_bin),
        "opt": shutil.which(opt_bin) or _fallback("opt"),
        "clang": shutil.which(clang_bin) or _fallback("clang"),
        "ast-miner": str(miner) if miner.exists() else None,
        "klee": _klee_available(),
        "model-checker": _model_checker_available(model_checker_bin),
        "force_pass_runner": force_pass_runner,
    }


def _klee_available():
    from o2t.symexec.klee_driver import available
    return "klee" if available() else None


def _model_checker_available(model_checker_bin: str | None = None):
    if model_checker_bin:
        return shutil.which(model_checker_bin)
    return shutil.which("cbmc") or shutil.which("esbmc")


_HOMEBREW_LLVM = Path("/opt/homebrew/opt/llvm@18/bin")


def _fallback(tool: str) -> str | None:
    cand = _HOMEBREW_LLVM / tool
    return str(cand) if cand.exists() else None


def _run_json(argv: list[str]) -> dict:
    """Run a tool with `--report <tmp>` appended; return the parsed JSON report (or {})."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rep = Path(tf.name)
    try:
        proc = subprocess.run(argv + ["--report", str(rep)], capture_output=True, text=True)
        if rep.stat().st_size:
            return json.loads(rep.read_text())
        # some tools print the summary to stdout when --report is empty
        return json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    except (OSError, json.JSONDecodeError):
        return {}
    finally:
        rep.unlink(missing_ok=True)


def _verdict_scev(report: dict) -> dict:
    proved, total = report.get("proved", 0), report.get("transforms", 0)
    status = "proved" if report.get("ok") and proved else ("inconclusive" if total else "error")
    return {"verdict": status, "proved": proved, "transforms": total}


def _verdict_symexec(report: dict) -> dict:
    reports = report.get("reports") or report.get("model_reports") or []
    funcs = report.get("functions", len(reports))
    miscompiles = sum(int(r.get("miscompiles", 0) or 0) for r in reports)
    if not funcs:
        return {"verdict": "inconclusive", "reason": "no fold functions mined"}
    return {"verdict": "miscompile" if miscompiles else "sound",
            "functions": funcs, "miscompiles": miscompiles}


def _run_intent_validation(source: Path, ctx: dict) -> dict:
    """Generic source-intent -> formal-obligation -> Z3 pipeline (infer | validate), used for
    DSE / GlobalOpt / SLP. Returns a verdict from the candidates' proof statuses."""
    infer = str(TOOLS / "cv-infer-optimization-intent.py")
    validate = str(TOOLS / "cv-validate-intent-candidates.py")
    py = sys.executable
    with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as cf, \
         tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as vf:
        cand, val = Path(cf.name), Path(vf.name)
    try:
        subprocess.run([py, infer, str(source), "--format", "jsonl", "--out", str(cand)],
                       capture_output=True, text=True)
        if not cand.stat().st_size:
            return {"verdict": "inconclusive", "reason": "no intent candidates inferred"}
        subprocess.run([py, validate, "--z3", ctx["z3"], "--input", str(cand), "--out", str(val)],
                       capture_output=True, text=True)
        records = [json.loads(l) for l in val.read_text().splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        return {"verdict": "error", "reason": str(exc)}
    finally:
        cand.unlink(missing_ok=True)
        val.unlink(missing_ok=True)
    if not records:
        return {"verdict": "inconclusive", "reason": "no candidates validated"}
    statuses = [r.get("proof_status") for r in records]
    proved = sum(s == "proved" for s in statuses)
    refuted = sum(s in ("refuted", "unsound") for s in statuses)
    if refuted:
        verdict = "refuted"
    elif proved == len(statuses):
        verdict = "proved"
    elif proved:
        verdict = "partial"
    else:
        verdict = "inconclusive"           # all unsupported -> intent modeled but not discharged
    return {"verdict": verdict, "candidates": len(records), "proved": proved, "refuted": refuted}


def _verdict_translation(report: dict) -> dict:
    results = report.get("results", [])
    if not results:
        return {"verdict": "inconclusive", "reason": "no functions validated"}
    refuted = [r for r in results if r["status"] == "output-not-preserved"]
    proved = [r for r in results if r["status"] in ("proved", "proved-closed-form")]
    status = "refuted" if refuted else ("validated" if proved else "inconclusive")
    return {"verdict": status, "proved": len(proved), "refuted": len(refuted),
            "functions": len(results)}


def execute_check(check, source: Path, pass_name: str | None, ctx: dict) -> dict:
    """Run one feasible check and return its verdict dict (or a skip/error)."""
    if not check.feasible:
        return {"strategy": check.strategy, "verdict": "planned", "reason": check.reason}
    tool = str(TOOLS / STRATEGIES[check.strategy].tool)
    py = sys.executable
    try:
        if check.strategy == "scev-intent":
            r = _run_json([py, tool, "--source", str(source), "--z3-bin", ctx["z3"]])
            out = _verdict_scev(r)
        elif check.strategy == "symexec-fold-cascade":
            r = _run_json([py, tool, "--mine", str(source), "--miner", ctx["ast-miner"],
                           "--z3-bin", ctx["z3"]])
            out = _verdict_symexec(r)
        elif check.strategy == "translation-validation":
            argv = [py, tool, "--passes", pass_name, "--source", str(DEFAULT_LOOP_LL),
                    "--z3-bin", ctx["z3"], "--opt-bin", ctx["opt"]]
            if ctx.get("clang"):
                argv += ["--clang-bin", ctx["clang"]]
            r = _run_json(argv)
            out = _verdict_translation(r)
        elif check.strategy in ("memory-source", "slp-source", "globalopt-source", "dce-source",
                                 "licm-source", "cfg-source"):
            # Mine the pass's own transforms (memory ops / SLP reductions / dead-initializer
            # folds / LICM hoists / CFG if-conversions) and discharge each; any refuted transform
            # means the pass is unsound there.
            r = _run_json([py, tool, "--source", str(source), "--z3-bin", ctx["z3"]])
            refuted, proved = r.get("refuted", 0), r.get("proved", 0)
            out = {"verdict": ("refuted" if refuted else ("proved" if proved else "inconclusive")),
                   "transforms": r.get("transforms", 0), "proved": proved, "refuted": refuted}
        elif check.strategy == "memory-model":
            # Deep theory-of-arrays contracts (canonical DSE/forwarding); z3-only, no source.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "contracts": len(r.get("results", []))}
        elif check.strategy == "slp-model":
            # Deep SLP lane-mapping + reduction contracts (incl. FP teeth); z3-only.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "contracts": len(r.get("results", []))}
        elif check.strategy == "globalopt-model":
            # Deep GlobalOpt dead-initializer contracts (observability, with teeth); z3-only.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "contracts": r.get("contracts", 0)}
        elif check.strategy == "dce-model":
            # Deep DCE dead-instruction erasure contracts (live-use/effect observability); z3-only.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "contracts": r.get("contracts", 0)}
        elif check.strategy in ("loop-simulation", "loop-multiexit", "loop-nested"):
            # Canonical loop-equivalence contracts (simulation / multi-exit / nested); z3-only.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "contracts": r.get("contracts", 0)}
        elif check.strategy in ("symexec-real-pass", "klee-symexec"):
            # Symbolic execution of the real fold (per-path refinement); enumeration (symexec-real-
            # pass) or KLEE (klee-symexec). The tools resolve their own toolchain.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "proved": r.get("proved", 0), "refuted": r.get("refuted", 0)}
        elif check.strategy == "modelcheck-real-pass":
            # Optional CBMC/ESBMC cross-check for real fold C++; no Z3 dependency.
            r = _run_json([py, tool])
            refuted, errors = r.get("refuted", 0), r.get("errors", 0)
            verdict = "refuted" if refuted else ("proved" if r.get("ok") else "inconclusive")
            if errors:
                verdict = "error"
            out = {"verdict": verdict, "proved": r.get("proved", 0), "refuted": refuted,
                   "errors": errors}
        elif check.strategy == "licm-model":
            # Deep LICM hoist contracts (loop-invariance + trap-safety, with teeth); z3-only.
            r = _run_json([py, tool, "--z3-bin", ctx["z3"]])
            out = {"verdict": "proved" if r.get("ok") else "inconclusive",
                   "contracts": r.get("contracts", 0)}
        elif check.strategy in ("dse-facts", "globalopt-witness", "slp-transaction"):
            out = _run_intent_validation(source, ctx)
        elif check.strategy == "cfg-shape":
            # Validates the canonical simplifycfg if-conversion contract on a bundled diamond.
            r = _run_json([py, tool, "--opt-bin", ctx["opt"], "--z3-bin", ctx["z3"]])
            n = r.get("proved", 0)
            out = {"verdict": ("refuted" if r.get("refuted") else ("proved" if n else "inconclusive")),
                   "proved": n, "refuted": r.get("refuted", 0)}
        elif check.strategy in ("dse-ir", "instcombine-ir", "slp-ir", "mem2reg-ir", "loop-cfg-ir",
                                 "loop-induction", "loop-rotate-ir"):
            # Closed-loop TV of the real opt output (dse / instcombine / slp / mem2reg / loop-cfg /
            # unbounded loop-induction / loop-rotate).
            r = _run_json([py, tool, "--opt-bin", ctx["opt"], "--z3-bin", ctx["z3"]])
            n = r.get("proved", 0)
            out = {"verdict": ("refuted" if r.get("refuted") else ("proved" if n else "inconclusive")),
                   "proved": n, "refuted": r.get("refuted", 0)}
        elif check.strategy in ("reassociate-ir", "early-cse-ir"):
            # Closed-loop TV of a value-preserving scalar pass via the generic scalar translator.
            r = _run_json([py, tool, "--passes", STRATEGIES[check.strategy].canonical_pass,
                           "--opt-bin", ctx["opt"], "--z3-bin", ctx["z3"]])
            n = r.get("proved", 0)
            out = {"verdict": ("refuted" if r.get("refuted") else ("proved" if n else "inconclusive")),
                   "proved": n, "refuted": r.get("refuted", 0)}
        else:
            return {"strategy": check.strategy, "verdict": "planned", "reason": "no runner"}
    except Exception as exc:  # noqa: BLE001 -- a tool crash is a check error, not a verifier crash
        return {"strategy": check.strategy, "verdict": "error", "reason": str(exc)}
    out["strategy"] = check.strategy
    out["label"] = check.label
    return out


def orchestrate(inputs: list[dict], ctx: dict, execute=True) -> dict:
    """Classify, plan, and (optionally) run checks for each input.

    `inputs` is a list of {"source": Path|None, "pass_name": str|None}. Returns a report with
    a per-pass classification, the planned checks, and -- when `execute` -- the verdicts."""
    passes = []
    for item in inputs:
        src = item.get("source")
        src_text = Path(src).read_text() if src and Path(src).exists() else ""
        cls = classify(src_text, item.get("pass_name"))
        plan = plan_for(cls, ctx, has_source=bool(src_text))
        entry = {
            "source": str(src) if src else None,
            "pass_name": item.get("pass_name"),
            "primary_family": cls.primary,
            "families": cls.families,
            "scores": cls.scores,
            "planned_checks": [{"strategy": c.strategy, "feasible": c.feasible,
                                "target": c.target, "reason": c.reason} for c in plan],
        }
        if execute:
            entry["checks"] = [execute_check(c, Path(src) if src else None,
                                             item.get("pass_name"), ctx) for c in plan]
        passes.append(entry)
    return {"context": {k: bool(v) for k, v in ctx.items() if k != "force_pass_runner"},
            "passes": passes}
