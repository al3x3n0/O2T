#!/usr/bin/env python3
"""Proof-gate inferred optimization intent candidates with SMT-LIB/Z3."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cv_formal_ir import FormalIrError, FormalPair, equivalence_smt, pair_instances_for_record_intent
from cv_optimization_registry import (
    BV_OP_FOR_OPERATION,
    CONSTANT_FOR_IDENTITY,
    marker_has_supported_formal_path,
    scalar_instcombine_spec,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--emit-smt", type=Path)
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []
    return [
        record
        for record in (json.loads(line) for line in text.splitlines() if line.strip())
        if isinstance(record, dict)
    ]


def marker_filename(marker: str, index: int) -> str:
    return f"{index:04d}-" + marker.replace("probe.", "").replace(".", "_").replace("-", "_") + ".smt2"


def instance_filename(marker: str, index: int, vscale: int | None) -> str:
    base = marker_filename(marker, index)
    if vscale is None:
        return base
    return base.replace(".smt2", f"-vscale{vscale}.smt2")


def scalar_legacy_pair_for(marker: str, rewrite_source: str) -> tuple[list[tuple[int | None, FormalPair]], str] | None:
    spec = scalar_instcombine_spec(marker)
    facts = spec.get("semantic_facts") if isinstance(spec.get("semantic_facts"), dict) else {}
    if not facts:
        return None
    operation = str(facts.get("operation") or "")
    identity = str(facts.get("identity") or "")
    rewrite = str(facts.get("rewrite") or "")
    bvop = BV_OP_FOR_OPERATION.get(operation)
    if not bvop:
        return None
    if rewrite == "replace-with-lhs":
        if "replaceInstUsesWith" not in rewrite_source:
            return None
        if identity == "same-value":
            return [(None, FormalPair(f"({bvop} a a)", "a", ("a", "b")))], "registry"
        constant = CONSTANT_FOR_IDENTITY.get(identity)
        if constant is None:
            return None
        literal = f"#x{constant & 0xFFFFFFFF:08x}"
        if "Op0" in rewrite_source:
            return [(None, FormalPair(f"({bvop} a {literal})", "a", ("a", "b")))], "registry"
        if "Op1" in rewrite_source:
            return [(None, FormalPair(f"({bvop} {literal} a)", "a", ("a", "b")))], "registry"
        return None
    if rewrite == "replace-with-zero":
        if "getNullValue" in rewrite_source or "ConstantInt::get" in rewrite_source:
            return [(None, FormalPair(f"({bvop} a a)", "#x00000000", ("a", "b")))], "registry"
    return None


def smt_pairs_for(record: dict[str, Any]) -> tuple[list[tuple[int | None, FormalPair]], str] | None:
    formal_pairs = pair_instances_for_record_intent(record)
    if formal_pairs is not None:
        return formal_pairs, "formal"

    marker = str(record.get("marker", ""))
    evidence = record.get("evidence", {})
    params = evidence.get("formal_parameters") if isinstance(evidence, dict) else {}
    if isinstance(params, dict) and params.get("semantic.unsupported"):
        return None
    rewrite_source = str(record.get("rewrite_source", ""))
    intent = record.get("intent_candidate", {})
    if isinstance(intent, dict):
        before = intent.get("smt_before")
        after = intent.get("smt_after")
        if isinstance(before, str) and isinstance(after, str):
            return [(None, FormalPair(before, after, ("a", "b")))], "legacy"

    scalar_pair = scalar_legacy_pair_for(marker, rewrite_source)
    if scalar_pair is not None:
        return scalar_pair
    if marker == "probe.dce.dead-instruction":
        if "eraseFromParent" in rewrite_source:
            return [(None, FormalPair("a", "a", ("a", "b")))], "legacy"
        return None
    if marker == "probe.globalopt.dead-initializer":
        if "setInitializer" in rewrite_source:
            return [(None, FormalPair("a", "a", ("a", "b")))], "legacy"
        return None
    return None


def smt_instances_for(record: dict[str, Any]) -> list[tuple[int | None, str]] | None:
    pairs = smt_pairs_for(record)
    if pairs is None:
        return None
    marker = str(record.get("marker", ""))
    formal_pairs, source = pairs
    return [
        (
            vscale,
            equivalence_smt(
                marker,
                f"{record.get('file', '')}:{record.get('line', '')}",
                formal_pair,
                source if vscale is None else f"{source}-vscale-{vscale}",
            ),
        )
        for vscale, formal_pair in formal_pairs
    ]


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


def unsupported_record(record: dict[str, Any], reason: str) -> dict[str, Any]:
    out = dict(record)
    out["proof_status"] = "unsupported"
    out["proof_result"] = reason
    out["promotion_status"] = "blocked"
    return out


def attach_assumption_contradictions(out: dict[str, Any], message: str) -> None:
    prefix = "formal contradictory assumptions: "
    if not message.startswith(prefix):
        return
    contradictions = [item.strip() for item in message[len(prefix) :].split(";") if item.strip()]
    evidence = out.setdefault("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
        out["evidence"] = evidence
    formal_parameters = evidence.setdefault("formal_parameters", {})
    if not isinstance(formal_parameters, dict):
        formal_parameters = {}
        evidence["formal_parameters"] = formal_parameters
    formal_parameters.setdefault("assumption_algebra.contradictions", contradictions)


def promotion_status(record: dict[str, Any], proof_status: str) -> str:
    if (
        proof_status == "proved"
        and record.get("confidence") == "high"
        and not record.get("side_conditions")
    ):
        return "ready"
    return "blocked"


def validate_relaxed_fp_policy(record: dict[str, Any]) -> dict[str, Any] | None:
    intent = record.get("intent_candidate", {})
    if not isinstance(intent, dict) or not isinstance(intent.get("relaxed_fp_policy"), dict):
        return None
    policy = intent["relaxed_fp_policy"]
    out = dict(record)
    if policy.get("kind") not in {"fp-reduction-policy", "fp-reduction-reassociation"}:
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    if policy.get("semantics") not in {"relaxed-reassoc", "unordered-fp-reduction", "fast-math-fp-reduction"}:
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    if policy.get("operation") not in {"fadd", "fmul"}:
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    if policy.get("element_type") != "fp32":
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    lanes = policy.get("lanes")
    lane_mapping = policy.get("lane_mapping")
    lane_map = lane_mapping.get("map") if isinstance(lane_mapping, dict) else None
    if not isinstance(lanes, int) or lanes <= 0:
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    if not isinstance(lane_map, list) or len(lane_map) != lanes or sorted(lane_map) != list(range(lanes)):
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    if policy.get("semantics") == "relaxed-reassoc" and lane_map == list(range(lanes)):
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    evidence = policy.get("evidence")
    if not isinstance(evidence, list) or not any(isinstance(item, dict) for item in evidence):
        return unsupported_record(record, "unsupported-relaxed-fp-policy")
    out["proof_status"] = "proved"
    out["proof_result"] = "policy-contract"
    out["proof_instances"] = []
    out["counterexample"] = ""
    out["promotion_status"] = promotion_status(record, "proved")
    return out


def validate_record(record: dict[str, Any], z3: str, emit_smt: Path | None, index: int) -> dict[str, Any]:
    marker = str(record.get("marker", ""))
    intent = record.get("intent_candidate", {})
    has_formal = isinstance(intent, dict) and isinstance(intent.get("formal"), dict)
    has_relaxed_fp_policy = isinstance(intent, dict) and isinstance(intent.get("relaxed_fp_policy"), dict)
    if (
        not marker_has_supported_formal_path(marker)
        and not has_formal
        and not has_relaxed_fp_policy
    ):
        return unsupported_record(record, "unsupported-marker")
    if record.get("side_conditions"):
        return unsupported_record(record, "unsupported-side-conditions")

    policy_record = validate_relaxed_fp_policy(record)
    if policy_record is not None:
        return policy_record

    try:
        smt_instances = smt_instances_for(record)
    except FormalIrError as exc:
        reason = "unsupported-contradictory-assumptions" if str(exc).startswith("formal contradictory assumptions:") else "unsupported-formal-ir"
        out = unsupported_record(record, reason)
        out["proof_message"] = str(exc)
        attach_assumption_contradictions(out, str(exc))
        return out
    if smt_instances is None:
        return unsupported_record(record, "unsupported-rewrite")

    out = dict(record)
    proof_instances: list[dict[str, Any]] = []
    smt_files: list[str] = []
    for vscale, smt in smt_instances:
        smt_file = ""
        if emit_smt is not None:
            emit_smt.mkdir(parents=True, exist_ok=True)
            smt_path = emit_smt / instance_filename(marker, index, vscale)
            smt_path.write_text(smt, encoding="utf-8")
            smt_file = str(smt_path)
            smt_files.append(smt_file)
        try:
            result, z3_output = run_z3(z3, smt)
        except Exception as exc:
            out["proof_status"] = "error"
            out["proof_result"] = "error"
            out["proof_message"] = str(exc)
            out["promotion_status"] = "blocked"
            out["smt_file"] = smt_file
            return out
        proof_instances.append(
            {
                "vscale": vscale,
                "result": result,
                "counterexample": "" if result == "unsat" else z3_output.replace("\n", "\\n"),
                "smt_file": smt_file,
            }
        )
        if result != "unsat":
            proof_status = "failed" if result == "sat" else "error"
            out["proof_status"] = proof_status
            out["proof_result"] = result
            out["proof_instances"] = proof_instances
            out["counterexample"] = z3_output.replace("\n", "\\n")
            out["promotion_status"] = promotion_status(record, proof_status)
            out["smt_file"] = smt_file
            return out

    out["proof_status"] = "proved"
    out["proof_result"] = "unsat"
    out["proof_instances"] = proof_instances
    out["counterexample"] = ""
    out["promotion_status"] = promotion_status(record, "proved")
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
        records = load_records(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    z3_path = args.z3 if Path(args.z3).is_file() else shutil.which(args.z3)
    if z3_path is None:
        print(f"z3 not found: {args.z3}", file=sys.stderr)
        return 1

    validated = [
        validate_record(record, str(z3_path), args.emit_smt, index)
        for index, record in enumerate(records)
    ]
    write_jsonl(args.out, validated)
    counts: dict[str, int] = {}
    for record in validated:
        status = str(record.get("proof_status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"validated": len(validated), "proof_status": counts}, sort_keys=True))
    return 1 if any(record.get("proof_status") in {"failed", "error"} for record in validated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
