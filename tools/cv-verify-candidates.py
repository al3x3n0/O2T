#!/usr/bin/env python3
"""Born-proven candidates: prove each lifted formal at inference time.

The inference pipeline (cv-infer-optimization-intent.py) emits intent candidates,
each (when liftable) carrying a ``formal`` DSL block. Proving has historically
been a SEPARATE gate over the committed registry. This tool closes that gap: it
reads inference candidates (or a registry array) and attaches a ``formal_status``
to each, so a candidate is born proven-or-refuted.

Two layers, both reusing the existing proof engine -- no new SMT logic:
  * z3 refinement via cv-validate-intent-registry.validate_record (authoritative,
    all domains + transaction templates): proved | failed | error | unsupported.
  * for scalar-bv32 proved formals, a multi-width recheck at i8/i16/i32/i64
    (cv-prove-multiwidth.formal_at_width) catches width-specific unsoundness a
    single bv32 proof would miss.

(mini-Alive2 is deliberately NOT invoked here: it validates *real* .ll, whereas a
candidate's formal is already synthetic DSL -- the z3 refinement above IS the
check. mini-alive remains the tool for observed before/after .ll pairs.)

  --candidates FILE   inference jsonl ({marker, intent_candidate:{formal,...}})
  --registry FILE     a constraints/*.json array ({marker, formal, ...})
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal  # noqa: E402

MULTIWIDTH = [8, 16, 32, 64]




sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.intent import validate_registry as VALIDATOR
from o2t.prove import multiwidth as MULTIWIDTH_MOD


def record_for_validation(candidate: dict) -> dict | None:
    """Normalize an inference candidate or registry record into the shape
    validate_record expects (marker/category/precondition/rewrite/intent/formal)."""
    inner = candidate.get("intent_candidate")
    source = inner if isinstance(inner, dict) else candidate
    formal = source.get("formal")
    if not isinstance(formal, dict):
        return None
    return {
        "marker": str(candidate.get("marker") or source.get("marker") or ""),
        "category": str(source.get("category") or candidate.get("category") or ""),
        "precondition": str(source.get("precondition") or ""),
        "rewrite": str(source.get("rewrite") or ""),
        "intent": str(source.get("intent") or ""),
        "formal": formal,
    }


def multiwidth_recheck(formal: dict, marker: str, z3_bin: str) -> dict:
    """Re-prove a scalar-bv32 formal at i8/i16/i32/i64. Returns per-width status."""
    widths: dict[str, str] = {}
    for width in MULTIWIDTH:
        try:
            scaled = MULTIWIDTH_MOD.formal_at_width(formal, width)
            instances = pair_instances_for_formal(scaled)
        except FormalIrError as exc:
            widths[str(width)] = f"unsupported:{exc}"[:60]
            continue
        verdict = "proved"
        for _label, pair in instances:
            smt = equivalence_smt(marker, "multiwidth", pair)
            status, _ = MULTIWIDTH_MOD.run_z3(z3_bin, smt, False)
            if status != "unsat":
                verdict = "refuted" if status == "sat" else "error"
                break
        widths[str(width)] = verdict
    return widths


def verify(candidate: dict, z3_bin: str, index: int) -> dict:
    record = record_for_validation(candidate)
    annotation: dict = {"marker": str(candidate.get("marker") or "")}
    if record is None:
        annotation["formal_status"] = "no-formal"
        return annotation

    result = VALIDATOR.validate_record(record, z3_bin, None, index)
    status = str(result.get("formal_status") or "error")
    annotation["formal_status"] = status
    annotation["formal_result"] = str(result.get("formal_result") or "")
    if result.get("counterexample"):
        annotation["counterexample"] = result["counterexample"]
    if result.get("formal_message"):
        annotation["formal_message"] = result["formal_message"]

    formal = record["formal"]
    if status == "proved" and formal.get("domain") == "scalar-bv32":
        widths = multiwidth_recheck(formal, record["marker"], z3_bin)
        annotation["formal_widths"] = widths
        if any(v not in {"proved"} and not v.startswith("unsupported") for v in widths.values()):
            annotation["formal_status"] = "width-refuted"
    return annotation


def load_candidates(args) -> list[dict]:
    if args.candidates:
        out = []
        for line in args.candidates.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out
    data = json.loads(args.registry.read_text())
    return data if isinstance(data, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--candidates", type=Path, help="inference jsonl output")
    src.add_argument("--registry", type=Path, help="constraints/*.json array")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--out", type=Path, help="write annotated jsonl here")
    ap.add_argument("--report", type=Path, help="write summary json here")
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(f"verify-candidates: z3 not found: {args.z3_bin}", file=sys.stderr)
        return 2

    candidates = load_candidates(args)
    annotations = [verify(candidate, z3_bin, index) for index, candidate in enumerate(candidates)]

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as fh:
            for candidate, annotation in zip(candidates, annotations):
                merged = dict(candidate)
                merged["formal_verification"] = annotation
                fh.write(json.dumps(merged, sort_keys=True) + "\n")

    counts = collections.Counter(a["formal_status"] for a in annotations)
    with_formal = sum(1 for a in annotations if a["formal_status"] != "no-formal")
    summary = {
        "candidates": len(annotations),
        "with_formal": with_formal,
        "formal_status": dict(counts),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    bad = {"failed", "error", "width-refuted"}
    failures = sum(counts[k] for k in bad if k in counts)
    print(f"born-proven: {counts.get('proved', 0)} proved, {failures} unsound/error, "
          f"{with_formal} with formal of {len(annotations)} candidates", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
