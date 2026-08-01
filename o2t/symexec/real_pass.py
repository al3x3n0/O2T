#!/usr/bin/env python3
"""Symbolically execute the REAL compiled C++ of a pass fold and discharge soundness per path.

The fold is built against the `symbolic_llvm.h` shim (Values are SMT terms; analysis queries are
choice points). We compile the actual C++, enumerate the pass's real control-flow paths (one per
assignment of the query outcomes), and for each path that performs a rewrite prove

    (the facts the taken branches established)  =>  out(X..)  ==  in(X..)   for all inputs

So the proof is tied to the genuine branches of the implementation -- a fold that rewrites on a
path where the established facts are insufficient (an under-guarded pass) is refuted with a witness.
Each query is grounded by its semantic precondition via the shared `facts/value_tracking` encoder,
so the symbolic execution and the rest of O2T agree on what a query means.
"""

from __future__ import annotations

import json
import subprocess
from itertools import product
from pathlib import Path

from o2t.facts.value_tracking import scalar_assumption_smt

ROOT = Path(__file__).resolve().parents[2]
HEADER_DIR = ROOT / "o2t" / "symexec"

# analysis query name -> the value-fact it establishes about its argument (when it returns true).
_QUERY_FACT = {
    "power-of-two": {"op": "power-of-two"},
    # `isKnownToBeAPowerOfTwo(V, /*OrZero=*/true)` establishes a STRICTLY WEAKER fact: the value may
    # be zero. Grounding it as the plain query would assert non-zero, i.e. assume more than the
    # caller proved. Leaving it unmapped would be sound but weaker still (the fact is dropped, so the
    # obligation must hold for all inputs), which would silently fail folds that need it.
    "power-of-two-or-zero": {"op": "power-of-two", "or_zero": True},
    "nonzero": {"op": "not-eq", "value": 0},
    "nonneg": {"op": "cmp", "predicate": "sge", "value": 0},
    "negative": {"op": "cmp", "predicate": "slt", "value": 0},
}


def compile_harness(cpp_path, clang="clang++", out=None):
    # The binary goes NEXT TO THE SOURCE, not into a fixed /tmp path keyed on the stem. Callers
    # already write their source into a private temp dir; the old default sent every one of them to
    # the same `/tmp/<stem>_symexec`, so two concurrent runs -- two fixtures, `ctest -j`, or a
    # developer probing while the suite runs -- would truncate a binary another process was
    # executing. That surfaced as a harness "crash", i.e. a fold that mysteriously stopped rewriting.
    out = out or (Path(cpp_path).with_suffix("").as_posix() + "_symexec")
    r = subprocess.run([clang, "-std=c++17", "-I", str(HEADER_DIR), str(cpp_path), "-o", str(out)],
                       capture_output=True, text=True)
    return str(out) if r.returncode == 0 else None


def _run(exe, fold, choices):
    """One concrete execution of the fold. Returns (record, crash_reason)."""
    r = subprocess.run([exe, fold, *[str(c) for c in choices]], capture_output=True, text=True)
    if r.returncode != 0:
        # A harness that CRASHES is not a fold that declines. `m_Deferred` used to snapshot its
        # binding at matcher-construction time instead of reading it at match time, so the canonical
        # `(A & B) ^ (A | B)` pattern dereferenced a null and SEGFAULTED -- and because a crashed run
        # was silently dropped, the fold simply looked like it never rewrote. Surface it instead.
        why = f"exit {r.returncode}" + (f": {r.stderr.strip().splitlines()[-1]}" if r.stderr.strip() else "")
        return None, why
    if not r.stdout.strip():
        return None, "no output"
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"unparseable output: {exc}"


def explore(exe, fold, max_queries=4):
    """Enumerate the fold's distinct control-flow paths, plus any executions that CRASHED."""
    paths, seen, crashes = [], set(), []
    for combo in product((0, 1), repeat=max_queries):
        rec, crash = _run(exe, fold, combo)
        if crash is not None:
            crashes.append({"choices": list(combo), "reason": crash})
            continue
        if rec is None:
            continue
        key = (json.dumps(rec["decisions"], sort_keys=True), rec["output"])
        if key not in seen:
            seen.add(key)
            paths.append(rec)
    return paths, crashes


def _path_condition(decisions):
    """The conjunction of facts the taken branches established (the queries that returned true)."""
    facts = []
    for d in decisions:
        if d["v"] != 1:
            continue
        fact = _QUERY_FACT.get(d["q"])
        smt = scalar_assumption_smt(fact, d["arg"]) if fact else None
        if smt:
            facts.append(smt)
    return facts


def discharge_path(z3_bin, path, rlimit=200_000_000, wall=600):
    """Prove the rewrite on one path refines the input under the path's established facts."""
    if path["output"] is None:
        return {"rewrote": False, "status": "no-rewrite"}     # no rewrite -> trivially refines
    # facts the branches established, plus defining constraints for APInt-derived values (e.g. the
    # exponent K of logBase2(C)) and established facts (e.g. no-signed-overflow).
    facts = _path_condition(path["decisions"]) + list(path.get("constraints", []))
    # default i32 vars, plus any extra declarations the fold needed (i1 operands / Bool operand-poison
    # flags for poison-contagion folds).
    decls = [f"(declare-const {s} (_ BitVec 32))"
             for s in ("X", "Y", "P", "A", "B", "C", "C1", "C2", "K")]
    decls = list(path.get("decls", [])) + decls
    # REFINEMENT (poison/UB-aware): where the input is defined (not poison), the output must equal
    # it AND be defined. A counterexample is a defined input where the output differs or is poison
    # -- e.g. a fold that sets `nsw` introducing poison on overflow.
    in_poison = path.get("input_poison", "false")
    out_poison = path.get("output_poison", "false")
    neg = (f"(and (not {in_poison}) (or (not (= {path['output']} {path['input']})) {out_poison}))")
    logic = path.get("logic", "QF_BV")               # FP/fast-math folds raise this to QF_FPBV
    # `(set-option :rlimit N)` is z3's DETERMINISTIC work budget -- unlike a wall-clock bound it
    # gives the same verdict on a loaded machine as on an idle one, which is what a gate needs.
    smt = "\n".join([f"(set-option :rlimit {rlimit})", f"(set-logic {logic})", *decls,
                     *[f"(assert {f})" for f in facts],
                     f"(assert {neg})",
                     "(check-sat)", "(get-model)", ""])
    # A solver timeout is MANDATORY, not a convenience. Some real folds carry obligations that are
    # genuinely hard for a bit-blasting solver -- `foldBoxMultiply` reassociates a 32x32 multiply, and
    # without a bound z3 runs indefinitely and hangs the whole run. `unknown` from a timeout is a
    # NON-ANSWER: it maps to "error", which `verify_fold` refuses to count as sound. Never "assume it
    # would have proved".
    # The bound is a DETERMINISTIC resource limit, not a stopwatch. z3's `-T:` is wall-clock, so a
    # loaded machine turns a query that needs ~9s of CPU (`bvsdiv` vs `bvudiv` is one) into a
    # "timeout" -> `error` -> a fold that silently stops being refuted. Gate results must not depend
    # on what else the machine is doing. `rlimit` counts solver work instead, so the verdict is
    # reproducible; the wall-clock arguments remain only as a backstop against a genuine hang, which
    # is what this bound was introduced for (`foldBoxMultiply` ran unbounded for hours).
    try:
        out = subprocess.run([z3_bin, "-in", f"-T:{wall}"], input=smt,
                             capture_output=True, text=True, timeout=wall + 30).stdout
    except subprocess.TimeoutExpired:
        out = ""
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    status = "proved" if head == "unsat" else "refuted" if head == "sat" else "error"
    return {"rewrote": True, "status": status, "facts": len(facts), "solver_output": head,
            "witness": out if status == "refuted" else ""}


def verify_fold(z3_bin, exe, fold, rlimit=200_000_000, wall=600):
    """Symbolically execute `fold` and discharge every rewriting path."""
    paths, crashes = explore(exe, fold)
    rows = [{"decisions": [d["q"] + ("" if d["v"] else "!") for d in p["decisions"]],
             **discharge_path(z3_bin, p, rlimit=rlimit, wall=wall)} for p in paths]
    rewriting = [r for r in rows if r["rewrote"]]
    refuted = [r for r in rewriting if r["status"] == "refuted"]
    proved = [r for r in rewriting if r["status"] == "proved"]
    # SOUND requires every rewriting path to be PROVED, not merely "not refuted": a path whose
    # discharge errored or returned `unknown` is a non-answer, and counting it as sound reports a
    # fold verified when nothing was decided about it.
    # A crashed execution is a non-answer about a path that was never explored, so it blocks SOUND
    # exactly as an errored discharge does.
    ok = bool(rewriting) and not crashes and all(r["status"] == "proved" for r in rewriting)
    return {"fold": fold, "paths": len(paths), "rewriting_paths": len(rewriting),
            "proved": len(proved), "refuted": len(refuted), "crashes": crashes, "ok": ok,
            "rows": rows}
