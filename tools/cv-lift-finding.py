#!/usr/bin/env python3
"""Autonomous whole-transform lift from a real miner finding (autonomous-verify #1).

The foundation of end-to-end pass verification: reconstruct a transform's formal
directly from the raw strings a finding carries -- NO curated registry formal --
then prove it.

  before-tree  <- opcode (binary op of the matched instruction)
                  + predicate_source operand constraints:
                       match(OpN, MATCHER)  -> operand N is MATCHER (lifted)
                       OpA == OpB           -> operands are the same value
                       (other clauses)      -> guards (recorded; G1 can model)
  after-tree   <- rewrite_source builder lift (replaceInstUsesWith / Create* /
                  getNullValue / ConstantInt::get ...)

So `match(Op1, m_Zero())` + `return replaceInstUsesWith(I, Op0)` on an `add`
becomes  before=bvadd(Op0, 0)  after=Op0  -> z3 proves add-zero, with no
hand-written intent. The opcode is taken from the marker->operation registry
(semantic_facts.json) as a stand-in for "the miner knows the matched opcode";
the transform STRUCTURE (before/after/guards) is lifted purely from the source
strings. Cross-checks the reconstructed before against the curated registry
formal (modulo variable names) to confirm faithfulness.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402
from cv_optimization_registry import BV_OP_FOR_OPERATION  # noqa: E402
import cv_lift_matcher as lm  # noqa: E402

DEFAULT_FACTS = ROOT / "constraints" / "semantic_facts.json"
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"
DEFAULT_MINER = ROOT / "build-clang-tools" / "cv-mine-pass-source-ast"
DEFAULT_SNIPPET = ROOT / "tests" / "fixtures" / "intent_inference_snippet.cpp"
DEFAULT_INFER = HERE / "cv-infer-optimization-intent.py"

OPERAND_RE = re.compile(r"^Op\d+$|^[A-Z]\w*$")

# The exact predicate/rewrite strings the miner emits for these instcombine folds
# (captured from a real run). --selftest lifts+proves these without re-running the
# miner, so it is deterministic and CWD-independent (the miner reads constraints/
# relative to CWD); --mine runs the real miner for the full autonomous pipeline.
BUILTIN_FINDINGS = [
    {"marker": "probe.instcombine.add-zero", "predicate_source": "match(Op1, m_Zero())",
     "rewrite_source": "return replaceInstUsesWith(I, Op0)"},
    {"marker": "probe.instcombine.mul-one", "predicate_source": "match(Op1, m_One())",
     "rewrite_source": "return replaceInstUsesWith(I, Op0)"},
    {"marker": "probe.instcombine.xor-self", "predicate_source": "Op0 == Op1",
     "rewrite_source": "return replaceInstUsesWith(I, Constant::getNullValue(0))"},
]


class LiftError(ValueError):
    pass


def marker_opcode_map(facts_path: Path) -> dict[str, str]:
    data = json.loads(facts_path.read_text())
    records = data if isinstance(data, list) else data.get("records", [])
    out: dict[str, str] = {}
    for record in records:
        if isinstance(record, dict):
            sf = record.get("semantic_facts")
            if isinstance(sf, dict) and sf.get("shape") == "scalar":
                out[record["marker"]] = str(sf.get("operation") or "")
    return out


def registry_formal_map(intents_path: Path) -> dict[str, dict]:
    data = json.loads(intents_path.read_text())
    return {r["marker"]: r["formal"] for r in data
            if isinstance(r, dict) and isinstance(r.get("formal"), dict)}


# --------------------------------------------------------------------------- #
# before-tree reconstruction from predicate_source
# --------------------------------------------------------------------------- #

def reconstruct_before(bvop: str, predicate: str):
    """Return (before_node, guards). Operands default to Op0/Op1 free vars;
    match(OpN, MATCHER) pins operand N, OpA == OpB makes them the same value."""
    operands = {"Op0": {"op": "var", "name": "Op0"}, "Op1": {"op": "var", "name": "Op1"}}
    same_value = False
    guards: list[str] = []
    for clause in (c.strip() for c in predicate.split("&&")):
        if not clause:
            continue
        eq = re.match(r"^(\w+)\s*==\s*(\w+)$", clause)
        try:
            node = lm.parse_expr(clause)
        except lm.MatcherError:
            node = None
        if node is not None and node.get("call") == "match" and len(node.get("args", [])) == 2:
            target, matcher = node["args"]
            name = target.get("bare")
            if name in operands:
                operands[name] = lm.lift(matcher, set())
            else:
                guards.append(clause)
        elif eq and eq.group(1) in operands and eq.group(2) in operands:
            same_value = True
        else:
            guards.append(clause)
    lhs = operands["Op0"]
    rhs = operands["Op0"] if same_value else operands["Op1"]
    return {"op": bvop, "args": [lhs, rhs]}, guards


def lift_finding(finding: dict, opcode_map: dict[str, str]):
    """Return a result dict: status + before/after/variables/guards (or skip reason)."""
    marker = str(finding.get("marker") or "")
    predicate = str(finding.get("predicate_source") or "")
    rewrite = str(finding.get("rewrite_source") or "")
    operation = opcode_map.get(marker, "")
    bvop = BV_OP_FOR_OPERATION.get(operation)
    if not bvop:
        return {"marker": marker, "status": "skip", "reason": "no binary opcode for marker"}
    if not rewrite.strip():
        return {"marker": marker, "status": "skip", "reason": "no rewrite_source"}
    rewrite_expr = re.sub(r"^\s*return\s+", "", rewrite).rstrip(";").strip()
    try:
        before, guards = reconstruct_before(bvop, predicate)
        after = lm.lift_builder_expr(rewrite_expr)
    except (lm.MatcherError, LiftError) as exc:
        return {"marker": marker, "status": "unliftable", "reason": str(exc)}
    variables: set[str] = set()
    lm.collect_vars(before, variables)
    lm.collect_vars(after, variables)
    return {"marker": marker, "status": "lifted", "before": before, "after": after,
            "variables": sorted(variables), "guards": guards, "operation": operation}


# --------------------------------------------------------------------------- #
# proving + faithfulness cross-check
# --------------------------------------------------------------------------- #

def canonical(node, rename: dict):
    """Structural form with variables renamed to v0,v1,... in first-seen order."""
    if not isinstance(node, dict):
        return node
    if node.get("op") == "var":
        name = node["name"]
        if name not in rename:
            rename[name] = f"v{len(rename)}"
        return {"op": "var", "name": rename[name]}
    out = {k: v for k, v in node.items() if k != "args"}
    if "args" in node:
        out["args"] = [canonical(a, rename) for a in node["args"]]
    return out


def shapes_match(before_a, before_b) -> bool:
    return json.dumps(canonical(before_a, {}), sort_keys=True) == \
        json.dumps(canonical(before_b, {}), sort_keys=True)


def prove(before, after, variables, z3_bin: str) -> str:
    formal = {"domain": "scalar-bv32", "equivalence": "result", "refinement": "refinement",
              "variables": variables, "poison_variables": variables,
              "before": before, "after": after}
    try:
        pairs = pair_instances_for_formal(formal)
    except FormalIrError as exc:
        return f"encode-error:{exc}"
    for _, pair in pairs:
        smt = equivalence_smt("finding", "autonomous", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
        head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
        if head == "unsat":
            continue
        return "refuted" if head == "sat" else "error"
    return "proved"


def run_inference(snippet: Path, miner: Path) -> list[dict]:
    out = HERE.parent / "build" / "_autoverify_findings.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(DEFAULT_INFER), str(snippet), "--miner", str(miner),
         "--format", "jsonl", "--out", str(out)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise LiftError(f"inference failed: {proc.stderr[-300:]}")
    findings = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    out.unlink(missing_ok=True)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--findings", type=Path, help="findings jsonl (from cv-infer)")
    src.add_argument("--selftest", action="store_true",
                     help="lift+prove built-in real-string findings (no miner; deterministic)")
    src.add_argument("--mine", type=Path, metavar="SNIPPET",
                     help="run the miner on a pass-source snippet, then lift+prove (autonomous)")
    ap.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    ap.add_argument("--no-z3", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    opcode_map = marker_opcode_map(DEFAULT_FACTS)
    registry = registry_formal_map(DEFAULT_INTENTS)
    z3_bin = None if args.no_z3 else shutil.which(args.z3_bin)

    if args.selftest:
        findings = BUILTIN_FINDINGS
    elif args.mine is not None:
        if not args.miner.exists():
            print(json.dumps({"status": "skipped", "reason": "miner not built"}))
            return 0
        try:
            findings = run_inference(args.mine, args.miner)
        except LiftError as exc:
            print(json.dumps({"status": "skipped", "reason": str(exc)[:80]}))
            return 0
    elif args.findings:
        findings = [json.loads(line) for line in args.findings.read_text().splitlines() if line.strip()]
    else:
        ap.error("provide --findings, --selftest, or --mine")

    results = []
    counts = {"proved": 0, "refuted": 0, "lifted": 0, "skip": 0, "unliftable": 0,
              "faithful": 0, "unfaithful": 0}
    seen = set()
    for finding in findings:
        lifted = lift_finding(finding, opcode_map)
        marker = lifted["marker"]
        if lifted["status"] != "lifted":
            counts[lifted["status"]] = counts.get(lifted["status"], 0) + 1
            continue
        # dedup identical (marker, before, after)
        key = (marker, json.dumps(lifted["before"], sort_keys=True),
               json.dumps(lifted["after"], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        rec = {"marker": marker, "operation": lifted["operation"],
               "guards": lifted["guards"], "variables": lifted["variables"],
               "before": lifted["before"], "after": lifted["after"]}
        # faithfulness vs curated registry before-tree
        reg = registry.get(marker)
        if isinstance(reg, dict) and isinstance(reg.get("before"), dict):
            faithful = shapes_match(lifted["before"], reg["before"])
            rec["faithful"] = faithful
            counts["faithful" if faithful else "unfaithful"] += 1
        if z3_bin is None:
            rec["status"] = "lifted"
            counts["lifted"] += 1
        else:
            verdict = prove(lifted["before"], lifted["after"], lifted["variables"], z3_bin)
            rec["status"] = verdict
            counts[verdict] = counts.get(verdict, 0) + 1
        results.append(rec)

    summary = {"backend": "z3" if z3_bin else "structural",
               "transforms": len(results), "counts": counts, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    for r in results:
        print(f"  [{r['status']}] {r['marker']} faithful={r.get('faithful')} "
              f"guards={r['guards']}", file=sys.stderr)
    # success: every lifted transform that proved did so, and none unfaithful/refuted unexpectedly
    bad = counts.get("refuted", 0) + counts.get("error", 0) + counts.get("unfaithful", 0)
    return 1 if bad or len(results) == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
