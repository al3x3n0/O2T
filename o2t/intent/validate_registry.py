#!/usr/bin/env python3
"""Proof-gate formal records in the optimization intent registry."""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from o2t.formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal, premise_smt
from o2t.transaction_formal import TEMPLATE_DOMAIN, registry_transaction_template_formals_for


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"
PREDICATE_PROVENANCE_MODEL = "predicate-provenance-contract-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intents", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--emit-smt", type=Path)
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def validate_predicate_provenance_contract(marker: str, formal: dict[str, Any]) -> None:
    contract = formal.get("predicate_provenance")
    if contract is None:
        return
    if not isinstance(contract, dict):
        raise ValueError(f"intent {marker} predicate_provenance must be an object")
    model = contract.get("model")
    if model != PREDICATE_PROVENANCE_MODEL:
        raise ValueError(
            f"intent {marker} predicate_provenance.model must be {PREDICATE_PROVENANCE_MODEL}"
        )
    facts = contract.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValueError(f"intent {marker} predicate_provenance.facts must be a non-empty array")
    sources = contract.get("provenance_sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"intent {marker} predicate_provenance.provenance_sources must be a non-empty array")
    seen_sources: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, str) or not source:
            raise ValueError(
                f"intent {marker} predicate_provenance.provenance_sources[{index}] must be a non-empty string"
            )
        if source in seen_sources:
            raise ValueError(f"intent {marker} predicate_provenance repeats provenance source {source}")
        seen_sources.add(source)
    seen: set[str] = set()
    for index, item in enumerate(facts):
        if not isinstance(item, dict):
            raise ValueError(f"intent {marker} predicate_provenance.facts[{index}] must be an object")
        fact = item.get("fact")
        predicate_family = item.get("predicate_family")
        if not isinstance(fact, str) or not fact:
            raise ValueError(f"intent {marker} predicate_provenance.facts[{index}].fact must be a non-empty string")
        if not isinstance(predicate_family, str) or not predicate_family:
            raise ValueError(
                f"intent {marker} predicate_provenance.facts[{index}].predicate_family must be a non-empty string"
            )
        if fact in seen:
            raise ValueError(f"intent {marker} predicate_provenance repeats fact {fact}")
        seen.add(fact)
    required = formal.get("required_safety_facts")
    if isinstance(required, list):
        required_set = {str(item) for item in required if str(item)}
        if seen != required_set:
            missing = ",".join(sorted(required_set - seen)) or "none"
            extra = ",".join(sorted(seen - required_set)) or "none"
            raise ValueError(
                f"intent {marker} predicate_provenance facts must match required_safety_facts "
                f"(missing={missing}; extra={extra})"
            )


def load_intents(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("intent file must contain a JSON array")
    records: list[dict[str, Any]] = []
    for record in data:
        if not isinstance(record, dict):
            raise ValueError("each intent record must be an object")
        marker = record.get("marker")
        if not isinstance(marker, str) or not marker:
            raise ValueError("each intent record must include a string marker")
        for key in ("category", "precondition", "rewrite", "intent"):
            if not isinstance(record.get(key), str):
                raise ValueError(f"intent {marker} is missing string field {key}")
        formal = record.get("formal")
        if isinstance(formal, dict):
            validate_predicate_provenance_contract(marker, formal)
        records.append(record)
    return records


def marker_filename(marker: str, index: int) -> str:
    return f"{index:04d}-" + marker.replace("probe.", "").replace(".", "_").replace("-", "_") + ".smt2"


def safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_") or "formal"


def instance_filename(marker: str, index: int, vscale: int | None, label: str = "formal") -> str:
    base = marker_filename(marker, index)
    suffix = ""
    if label != "formal":
        suffix += "-" + safe_label(label)
    if vscale is None:
        return base.replace(".smt2", f"{suffix}.smt2")
    return base.replace(".smt2", f"{suffix}-vscale{vscale}.smt2")


def run_z3(z3: str, smt: str) -> tuple[str, str]:
    proc = subprocess.run([z3, "-in"], input=smt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"z3 exited with {proc.returncode}")
    result = proc.stdout.splitlines()[0].strip() if proc.stdout.splitlines() else ""
    if result != "sat":
        return result, proc.stdout
    model_proc = subprocess.run(
        [z3, "-in"],
        input=smt + "(get-model)\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if model_proc.returncode != 0:
        raise RuntimeError(model_proc.stderr.strip() or f"z3 exited with {model_proc.returncode}")
    return result, model_proc.stdout


def formal_instances_for_record(formal: dict[str, Any]) -> tuple[list[tuple[str, int | None, Any]], dict[str, Any] | None]:
    if formal.get("domain") != TEMPLATE_DOMAIN:
        return [("formal", vscale, pair) for vscale, pair in pair_instances_for_formal(formal)], None
    templates = registry_transaction_template_formals_for(formal)
    instances: list[tuple[str, int | None, Any]] = []
    lowered_templates: list[dict[str, Any]] = []
    for template in templates:
        label = str(template["label"])
        lowered_formal = template["formal"]
        parameters = template["parameters"]
        lowered_templates.append(
            {
                "label": label,
                "lowered_formal": lowered_formal,
                "formal_parameters": parameters,
            }
        )
        instances.extend(
            (label, vscale, pair) for vscale, pair in pair_instances_for_formal(lowered_formal)
        )
    return instances, {"templates": lowered_templates}


def validate_record(record: dict[str, Any], z3: str, emit_smt: Path | None, index: int) -> dict[str, Any]:
    marker = str(record.get("marker") or "")
    out = {
        "marker": marker,
        "category": str(record.get("category") or ""),
        "intent": str(record.get("intent") or ""),
    }
    formal = record.get("formal")
    if formal is None:
        out["formal_status"] = "unsupported"
        out["formal_result"] = "no-formal-ir"
        return out

    try:
        instances, lowered = formal_instances_for_record(formal)
    except FormalIrError as exc:
        out["formal_status"] = "unsupported"
        out["formal_result"] = "unsupported-contradictory-assumptions" if str(exc).startswith("formal contradictory assumptions:") else "unsupported-formal-ir"
        out["formal_message"] = str(exc)
        return out
    if lowered is not None:
        out["formal_template_domain"] = str(formal.get("domain") or "")
        templates = lowered["templates"]
        out["formal_template_count"] = len(templates)
        out["formal_templates"] = templates
        if len(templates) == 1:
            out["formal_lowered_domain"] = str(templates[0]["lowered_formal"].get("domain") or "")
            out["formal_parameters"] = templates[0]["formal_parameters"]

    proof_instances: list[dict[str, Any]] = []
    smt_files: list[str] = []
    for label, vscale, pair in instances:
        instance_label = label if vscale is None else f"{label}-vscale-{vscale}"
        smt = equivalence_smt(marker, str(record.get("rewrite") or ""), pair, instance_label)
        smt_file = ""
        if emit_smt is not None:
            emit_smt.mkdir(parents=True, exist_ok=True)
            smt_path = emit_smt / instance_filename(marker, index, vscale, label)
            smt_path.write_text(smt, encoding="utf-8")
            smt_file = str(smt_path)
            smt_files.append(smt_file)

        try:
            result, z3_output = run_z3(z3, smt)
        except Exception as exc:
            out["formal_status"] = "error"
            out["formal_result"] = "error"
            out["formal_message"] = str(exc)
            out["smt_file"] = smt_file
            return out
        proof_instances.append(
            {
                "label": label,
                "vscale": vscale,
                "result": result,
                "counterexample": "" if result == "unsat" else z3_output.replace("\n", "\\n"),
                "smt_file": smt_file,
            }
        )
        if result != "unsat":
            out["formal_status"] = "failed" if result == "sat" else "error"
            out["formal_result"] = result
            out["formal_instances"] = proof_instances
            out["counterexample"] = z3_output.replace("\n", "\\n")
            out["smt_file"] = smt_file
            return out
        # Anti-vacuity gate: an `unsat` equivalence proves nothing if the premises are jointly
        # unsatisfiable (`(and assumptions (not goal))` is then trivially unsat). The syntactic
        # `normalize_assumptions` algebra catches only a few shapes; confirm SEMANTIC satisfiability
        # with z3 before trusting the proof, and classify a contradictory premise set as unsupported
        # (consistent with the syntactic-contradiction handling above).
        premise = premise_smt(marker, str(record.get("rewrite") or ""), pair, instance_label)
        if premise is not None:
            try:
                premise_result, _ = run_z3(z3, premise)
            except Exception as exc:
                out["formal_status"] = "error"
                out["formal_result"] = "premise-check-error"
                out["formal_message"] = str(exc)
                out["formal_instances"] = proof_instances
                out["smt_file"] = smt_file
                return out
            if premise_result != "sat":
                out["formal_status"] = "unsupported"
                out["formal_result"] = "unsupported-vacuous-premises"
                out["formal_message"] = ("premises jointly unsatisfiable "
                                         f"({premise_result or 'no-model'}): vacuous proof")
                out["formal_instances"] = proof_instances
                out["smt_file"] = smt_file
                return out

    out["formal_status"] = "proved"
    out["formal_result"] = "unsat"
    out["formal_instances"] = proof_instances
    out["counterexample"] = ""
    out["smt_file"] = smt_files[0] if len(smt_files) == 1 else ""
    if len(smt_files) > 1:
        out["smt_files"] = smt_files
    return out


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    try:
        records = load_intents(args.intents)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    z3_path = args.z3 if Path(args.z3).is_file() else shutil.which(args.z3)
    if z3_path is None:
        print(f"z3 not found: {args.z3}", file=sys.stderr)
        return 1

    validated = [validate_record(record, str(z3_path), args.emit_smt, index) for index, record in enumerate(records)]
    write_jsonl(args.out, validated)
    counts = collections.Counter(str(record.get("formal_status", "unknown")) for record in validated)
    print(json.dumps({"validated": len(validated), "formal_status": dict(counts)}, sort_keys=True))
    return 1 if any(record.get("formal_status") in {"failed", "error"} for record in validated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
