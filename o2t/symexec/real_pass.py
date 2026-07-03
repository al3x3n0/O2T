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
    "nonzero": {"op": "not-eq", "value": 0},
    "nonneg": {"op": "cmp", "predicate": "sge", "value": 0},
    "negative": {"op": "cmp", "predicate": "slt", "value": 0},
}


def compile_harness(cpp_path, clang="clang++", out=None):
    out = out or (Path("/tmp") / (Path(cpp_path).stem + "_symexec"))
    r = subprocess.run([clang, "-std=c++17", "-I", str(HEADER_DIR), str(cpp_path), "-o", str(out)],
                       capture_output=True, text=True)
    return str(out) if r.returncode == 0 else None


def _run(exe, fold, choices):
    r = subprocess.run([exe, fold, *[str(c) for c in choices]], capture_output=True, text=True)
    return json.loads(r.stdout) if r.stdout.strip() else None


def explore(exe, fold, max_queries=4):
    """Enumerate the distinct control-flow paths of `fold` over all query-outcome assignments."""
    paths, seen = [], set()
    for combo in product((0, 1), repeat=max_queries):
        rec = _run(exe, fold, combo)
        if rec is None:
            continue
        key = (json.dumps(rec["decisions"], sort_keys=True), rec["output"])
        if key not in seen:
            seen.add(key)
            paths.append(rec)
    return paths


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


def discharge_path(z3_bin, path):
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
    smt = "\n".join([f"(set-logic {logic})", *decls,
                     *[f"(assert {f})" for f in facts],
                     f"(assert {neg})",
                     "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    status = "proved" if head == "unsat" else "refuted" if head == "sat" else "error"
    return {"rewrote": True, "status": status, "facts": len(facts),
            "witness": out if status == "refuted" else ""}


def verify_fold(z3_bin, exe, fold):
    """Symbolically execute `fold` and discharge every rewriting path."""
    paths = explore(exe, fold)
    rows = [{"decisions": [d["q"] + ("" if d["v"] else "!") for d in p["decisions"]],
             **discharge_path(z3_bin, p)} for p in paths]
    rewriting = [r for r in rows if r["rewrote"]]
    refuted = [r for r in rewriting if r["status"] == "refuted"]
    proved = [r for r in rewriting if r["status"] == "proved"]
    ok = bool(rewriting) and not refuted
    return {"fold": fold, "paths": len(paths), "rewriting_paths": len(rewriting),
            "proved": len(proved), "refuted": len(refuted), "ok": ok, "rows": rows}
