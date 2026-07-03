#!/usr/bin/env python3
"""Infer reviewable optimization intent candidates from mined pass findings."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

from o2t.assumption_algebra import normalize_assumptions
from o2t.facts.analysis_facts import (
    dse_analysis_fact_contract,
    dse_analysis_fact_parameters,
    missing_dse_analysis_fact_recommendation,
    normalize_analysis_facts,
)
from o2t.facts.guard_semantics import load_guard_semantics, normalize_guard_record, text_guard_for_source
from o2t.registry.optimization_registry import (
    BV_OP_FOR_OPERATION,
    CONSTANT_FOR_IDENTITY,
    OPERATION_FOR_BUILDER_CALL,
    builder_tokens_for_registered_operations,
    registry_diagnostic,
    rewrite_tokens,
    scalar_instcombine_spec,
    vector_inference_template_for_marker,
    vector_emission_tokens,
)
from o2t.facts.semantic_facts import semantic_facts_valid_for_marker
from o2t.registry import lift_rules as cv_lift_rules
from o2t.facts.source_graph_contract import (
    source_graph_contract_parameters,
    source_graph_contract_summary,
)


DEFAULT_MINER = ROOT / "tools" / "cv-mine-pass-source.py"
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"
DEFAULT_GUARDS = ROOT / "constraints" / "guard_semantics.json"

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

GLOBAL_INITIALIZER_SAFETY_FACTS = ["initializer-dead", "local-linkage", "no-uses"]

REWRITE_TOKENS = list(dict.fromkeys([*rewrite_tokens(), *builder_tokens_for_registered_operations(), *vector_emission_tokens(), "return"]))

SIDE_CONDITION_TOKENS = [
    "hasNo",
    "hasPoison",
    "poison",
    "undef",
    "freeze",
    "isSafe",
    "isGuaranteed",
    "isKnown",
    "MaskedValueIsZero",
    "isValid",
    "isLegal",
    "hasOneUse",
    "dominates",
    "Dominator",
    "MemorySSA",
    "AA.",
    "alias",
    "TargetTransformInfo",
    "TTI",
    "should",
    "can",
]

INTENT_TEMPLATES = {
    "probe.instcombine.add-zero": {
        "precondition": "instruction.opcode == add && rhs == 0",
        "rewrite": "replace add operand with the non-zero operand",
        "intent": "result-equivalence",
    },
    "probe.instcombine.sub-zero": {
        "precondition": "instruction.opcode == sub && rhs == 0",
        "rewrite": "replace subtract-zero with the left operand",
        "intent": "result-equivalence",
    },
    "probe.instcombine.mul-one": {
        "precondition": "instruction.opcode == mul && rhs == 1",
        "rewrite": "replace multiply operand with the non-one operand",
        "intent": "result-equivalence",
    },
    "probe.instcombine.xor-self": {
        "precondition": "instruction.opcode == xor && operands are the same value",
        "rewrite": "replace xor-self with zero",
        "intent": "result-equivalence",
    },
    "probe.instcombine.or-zero": {
        "precondition": "instruction.opcode == or && rhs == 0",
        "rewrite": "replace or-zero with the non-zero operand",
        "intent": "result-equivalence",
    },
    "probe.instcombine.and-allones": {
        "precondition": "instruction.opcode == and && rhs == -1",
        "rewrite": "replace and-allones with the non-allones operand",
        "intent": "result-equivalence",
    },
    "probe.instcombine.and-self": {
        "precondition": "instruction.opcode == and && operands are the same value",
        "rewrite": "replace and-self with the preserved operand",
        "intent": "result-equivalence",
    },
    "probe.dce.dead-instruction": {
        "precondition": "instruction is dead and has no observable effect",
        "rewrite": "remove the dead instruction",
        "intent": "observable-result-equivalence",
    },
    "probe.cleanup.unused-alloca": {
        "precondition": "alloca has no loads, stores, escapes, or lifetime-visible uses",
        "rewrite": "remove the unused alloca",
        "intent": "observable-result-equivalence",
    },
    "probe.globalopt.dead-initializer": {
        "precondition": "global initializer is proven unobservable",
        "rewrite": "replace the global initializer with a default null initializer",
        "intent": "global-initializer-observable-equivalence",
    },
    "probe.slp.vectorize-binop": {
        "precondition": "SLP tree contains same-opcode scalar binary operations with legal vector element type",
        "rewrite": "pack scalar operands, emit vector binary operation, and replace scalar users with lanes",
        "intent": "vector-result-equivalence",
    },
    "probe.slp.vectorize-reduction": {
        "precondition": "SLP tree contains scalar reduction lanes with legal vector element type",
        "rewrite": "pack scalar lanes, emit vector reduction operation, and replace scalar reduction result",
        "intent": "result-equivalence",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--findings", type=Path)
    parser.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    parser.add_argument("--intent-registry", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--format", choices=["json", "jsonl"], default="jsonl")
    parser.add_argument("--require-marker", action="append", default=[])
    parser.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--context", type=int, default=8)
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


def global_initializer_formal_parameters() -> dict[str, Any]:
    return {
        "global.initializer.contract": "remove-global-initializer-if-dead-v1",
        "global.initializer.observability_model": "local-unobservable-initializer-v1",
        "global.initializer.safety_facts": list(GLOBAL_INITIALIZER_SAFETY_FACTS),
        "global.initializer.required_safety_facts": list(GLOBAL_INITIALIZER_SAFETY_FACTS),
        "global.initializer.rewrite_api": "setInitializer",
        "global.initializer.replacement_kind": "default-null-initializer",
        "global.initializer.required_witness_cases": ["i32", "ptr", "array"],
    }


def is_default_null_global_initializer_replacement(text: str) -> bool:
    return (
        "Constant::getNullValue" in text
        or "ConstantAggregateZero" in text
        or "zeroinitializer" in text
        or text.strip() in {"0", "nullptr", "null"}
    )


def global_initializer_rewrite_provenance(finding: dict[str, Any]) -> dict[str, Any]:
    graph = finding.get("source_intent_graph")
    graph = graph if isinstance(graph, dict) else {}
    rewrite_nodes = graph_rewrite_nodes(graph)
    rewrite_node = next(
        (
            node
            for node in rewrite_nodes
            if str(node.get("action") or "") == "remove-global-initializer-if-dead-v1"
        ),
        {},
    )
    source_intent = finding.get("source_intent")
    source_intent = source_intent if isinstance(source_intent, dict) else {}
    rewrite = source_intent.get("rewrite")
    rewrite = rewrite if isinstance(rewrite, dict) else {}

    replacement_expr = str(
        rewrite_node.get("replacement_expr")
        or graph.get("replacement_expr")
        or rewrite.get("replacement_expr")
        or ""
    )
    value_type_expr = str(
        rewrite_node.get("value_type_expr")
        or graph.get("value_type_expr")
        or rewrite.get("value_type_expr")
        or ""
    )
    subject = str(
        rewrite_node.get("subject")
        or graph.get("subject")
        or rewrite.get("subject")
        or source_intent.get("global_symbol")
        or "GV"
    )
    rewrite_callee = str(
        rewrite_node.get("callee")
        or graph.get("rewrite_callee")
        or rewrite.get("api")
        or ""
    )
    replacement_kind = str(
        rewrite_node.get("replacement_kind")
        or rewrite.get("replacement_kind")
        or rewrite.get("replacement")
        or ""
    )
    if replacement_expr and is_default_null_global_initializer_replacement(replacement_expr):
        replacement_kind = "default-null-initializer"
    status = "unknown"
    reason = ""
    if replacement_expr:
        if is_default_null_global_initializer_replacement(replacement_expr):
            status = "complete"
        else:
            status = "unsupported"
            reason = "unsupported-global-initializer-replacement"
    return {
        "global.initializer.rewrite_callee": rewrite_callee,
        "global.initializer.replacement_expr": replacement_expr,
        "global.initializer.value_type_expr": value_type_expr,
        "global.initializer.subject": subject,
        "global.initializer.replacement_kind": replacement_kind,
        "global.initializer.rewrite_provenance_status": status,
        **({"global.initializer.rewrite_provenance_reason": reason} if reason else {}),
    }


def global_initializer_observed_safety_facts(finding: dict[str, Any], predicate_source: str) -> list[str]:
    evidence_sources: list[str] = [
        str(finding.get("matched_pattern") or ""),
        predicate_source,
    ]
    for container_name in ("source_intent", "source_intent_graph"):
        container = finding.get(container_name)
        if isinstance(container, dict):
            value = container.get("observed_safety_facts")
            if isinstance(value, list):
                observed = [str(item) for item in value if str(item)]
                return [fact for fact in GLOBAL_INITIALIZER_SAFETY_FACTS if fact in set(observed)]
            guards = container.get("guards")
            if isinstance(guards, list):
                evidence_sources.extend(
                    str(guard.get("source") or "")
                    for guard in guards
                    if isinstance(guard, dict)
                )
    evidence_text = " ".join(evidence_sources)
    observed: list[str] = []
    if "isGlobalInitializerDead" in evidence_text:
        observed.append("initializer-dead")
    if "hasLocalLinkage" in evidence_text:
        observed.append("local-linkage")
    if "use_empty" in evidence_text:
        observed.append("no-uses")
    return observed


def global_initializer_safety_parameters(
    finding: dict[str, Any],
    predicate_source: str,
) -> dict[str, Any]:
    provenance = global_initializer_safety_provenance(finding)
    if provenance:
        observed = [
            str(item.get("fact") or "")
            for item in provenance
            if isinstance(item, dict) and str(item.get("status") or "") == "observed" and str(item.get("fact") or "")
        ]
    else:
        observed = global_initializer_observed_safety_facts(finding, predicate_source)
    observed_set = set(observed)
    missing = [fact for fact in GLOBAL_INITIALIZER_SAFETY_FACTS if fact not in observed_set]
    out = {
        "global.initializer.required_safety_facts": list(GLOBAL_INITIALIZER_SAFETY_FACTS),
        "global.initializer.observed_safety_facts": observed,
        "global.initializer.missing_safety_facts": missing,
        "global.initializer.safety_status": "complete" if not missing else "incomplete",
    }
    if provenance:
        out["global.initializer.safety_provenance"] = provenance
        out["global.initializer.safety_provenance_status"] = (
            "complete" if not missing and all(
                isinstance(item, dict)
                and str(item.get("status") or "") == "observed"
                and isinstance(item.get("source_range"), dict)
                and bool(str(item.get("source") or ""))
                for item in provenance
            ) else "incomplete"
        )
    return out


def global_initializer_safety_provenance(finding: dict[str, Any]) -> list[dict[str, Any]]:
    for container_name in ("source_intent", "source_intent_graph"):
        container = finding.get(container_name)
        if not isinstance(container, dict):
            continue
        raw = container.get("safety_provenance")
        if not isinstance(raw, list):
            continue
        out = [dict(item) for item in raw if isinstance(item, dict)]
        if out:
            return out
    return []


def load_intent_registry(path: Path) -> dict[str, dict[str, Any]]:
    records = load_records(path)
    return {
        str(record["marker"]): record
        for record in records
        if isinstance(record.get("marker"), str)
    }


def run_miner(miner: Path, paths: list[Path]) -> list[dict[str, Any]]:
    if not paths:
        raise ValueError("paths are required when --findings is not provided")
    completed = subprocess.run(
        [str(miner), *[str(path) for path in paths]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"miner exited with {completed.returncode}")
    data = json.loads(completed.stdout)
    return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []


def source_lines(path: Path) -> list[str]:
    return path.read_text(errors="replace").splitlines()


def window(lines: list[str], line: int, radius: int) -> tuple[int, int, list[str]]:
    index = max(0, line - 1)
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return start + 1, end, lines[start:end]


def if_body_window(lines: list[str], line: int, radius: int) -> tuple[int, int, list[str]]:
    start = max(0, line - 1)
    end_limit = min(len(lines), start + radius + 1)
    depth = 0
    saw_brace = False
    end = end_limit
    for index in range(start, end_limit):
        text = lines[index]
        depth += text.count("{")
        if "{" in text:
            saw_brace = True
        depth -= text.count("}")
        if saw_brace and depth <= 0 and index > start:
            end = index + 1
            break
    return start + 1, end, lines[start:end]


def enclosing_function_start(lines: list[str], line: int) -> int:
    index = max(0, line - 1)
    for candidate in range(index, -1, -1):
        stripped = lines[candidate].strip()
        if not stripped.endswith("{") or "(" not in stripped or ")" not in stripped:
            continue
        if stripped.startswith(("if", "for", "while", "switch", "else")):
            continue
        return candidate + 1
    return max(1, line)


def first_matching_line(lines: list[str], tokens: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if any(token in stripped for token in tokens):
            return stripped
    return ""


def side_conditions(context: list[str], predicate_line: int, context_start: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for offset, line in enumerate(context):
        line_number = context_start + offset
        if line_number > predicate_line:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if any(token in stripped for token in SIDE_CONDITION_TOKENS):
            records.append({"line": line_number, "source": stripped})
    return records


def modeled_side_condition(record: dict[str, Any]) -> dict[str, Any] | None:
    source = str(record.get("source") or "")
    return text_guard_for_source(source, GUARD_SEMANTICS, record.get("line"))


GUARD_SEMANTICS = load_guard_semantics(DEFAULT_GUARDS)


def catalog_guard(kind: str, source: str, line: Any = None, subject: str = "") -> dict[str, Any]:
    record: dict[str, Any] = {"kind": kind, "source": source, "line": line}
    if subject:
        record["subject"] = subject
    return normalize_guard_record(record, GUARD_SEMANTICS, line)


def partition_side_conditions(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    modeled: list[dict[str, Any]] = []
    unmodeled: list[dict[str, Any]] = []
    for record in records:
        lowered = modeled_side_condition(record)
        if lowered is None:
            unmodeled.append(record)
        else:
            modeled.append(lowered)
    return modeled, unmodeled


def source_intent_guard_side_conditions(
    finding: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]] | None:
    source_intent = finding.get("source_intent")
    if not isinstance(source_intent, dict):
        return None
    guards = source_intent.get("guards")
    if not isinstance(guards, list):
        return None
    modeled: list[dict[str, Any]] = []
    unmodeled: list[dict[str, Any]] = []
    profitability: list[dict[str, Any]] = []
    fallback_line = finding.get("line")
    for guard in guards:
        if not isinstance(guard, dict):
            continue
        role = str(guard.get("role") or "")
        if role == "semantic":
            continue
        record = normalize_guard_record({
            "kind": str(guard.get("kind") or "unknown"),
            "source": str(guard.get("source") or ""),
            "line": guard.get("line", fallback_line),
            "role": role,
            "subject": guard.get("subject"),
            "proof_effect": guard.get("proof_effect"),
        }, GUARD_SEMANTICS, fallback_line)
        if guard.get("subject"):
            record["subject"] = str(guard.get("subject"))
        if guard.get("proof_effect"):
            record["proof_effect"] = str(guard.get("proof_effect"))
        role = str(record.get("role") or role)
        if role == "modeled-side-condition":
            modeled.append(record)
        elif role == "profitability":
            profitability.append(record)
        elif role == "unmodeled-side-condition":
            unmodeled.append(record)
    return modeled, unmodeled, profitability


def lower_guard_effects(
    formal: dict[str, Any],
    parameters: dict[str, Any],
    guards: list[dict[str, Any]],
    subject_to_symbol: Any,
    parameter_prefix: str,
) -> bool:
    assumptions = list(formal.get("assumptions") or [])
    structural: list[dict[str, Any]] = []
    for guard in guards:
        if not isinstance(guard, dict) or guard.get("role") != "modeled-side-condition":
            continue
        formal_effect = str(guard.get("formal_effect") or "")
        if formal_effect in {"", "none", "semantic-side-condition"}:
            continue
        kind = str(guard.get("kind") or "unknown")
        subject = str(guard.get("subject") or "")
        effect_args = guard.get("formal_effect_args")
        if formal_effect in {
            "cmp-assumption",
            "known-bits-assumption",
            "not-poison-assumption",
            "not-eq-zero-assumption",
            "power-of-two-assumption",
        }:
            if not isinstance(effect_args, dict) or not isinstance(effect_args.get("assumption"), dict):
                return False
            assumption_args = effect_args["assumption"]
            symbol = subject_to_symbol(subject)
            if symbol is None:
                return False
            op = str(assumption_args.get("op") or "")
            if op == "not-poison":
                assumption = {"op": "not-poison", "name": symbol}
                assumptions.append(assumption)
                poison_variables = list(formal.get("poison_variables") or [])
                if assumption_args.get("requires_poison_variable") is True and symbol not in poison_variables:
                    poison_variables.append(symbol)
                formal["poison_variables"] = poison_variables
            elif op == "not-eq":
                value = assumption_args.get("value")
                if not isinstance(value, int):
                    return False
                assumptions.append({"op": "not-eq", "name": symbol, "value": value})
            elif op == "cmp":
                predicate = assumption_args.get("predicate")
                value = assumption_args.get("value")
                if not isinstance(predicate, str) or not isinstance(value, int):
                    return False
                assumptions.append({"op": "cmp", "predicate": predicate, "name": symbol, "value": value})
            elif op == "known-bits":
                zero_mask = guard.get("zero_mask", assumption_args.get("zero_mask"))
                one_mask = guard.get("one_mask", assumption_args.get("one_mask"))
                if zero_mask is None and one_mask is None:
                    return False
                assumption = {"op": "known-bits", "name": symbol}
                if zero_mask is not None:
                    if not isinstance(zero_mask, int):
                        return False
                    assumption["zero_mask"] = zero_mask
                if one_mask is not None:
                    if not isinstance(one_mask, int):
                        return False
                    assumption["one_mask"] = one_mask
                assumptions.append(assumption)
            elif op == "power-of-two":
                if assumption_args.get("nonzero") is not True:
                    return False
                assumptions.append({"op": "power-of-two", "name": symbol, "nonzero": True})
            else:
                return False
        elif formal_effect == "relation-assumption":
            # Relational guard between two SSA values (e.g. isKnownNonEqual(A, B)
            # or a dominating icmp): lift to a `rel` assumption over both symbols.
            if not isinstance(effect_args, dict) or not isinstance(effect_args.get("assumption"), dict):
                return False
            assumption_args = effect_args["assumption"]
            if str(assumption_args.get("op") or "") != "rel":
                return False
            predicate = assumption_args.get("predicate")
            left = subject_to_symbol(str(guard.get("left_subject") or ""))
            right = subject_to_symbol(str(guard.get("right_subject") or ""))
            if left is None or right is None or not isinstance(predicate, str):
                return False
            assumptions.append({"op": "rel", "predicate": predicate, "left": left, "right": right})
        elif formal_effect == "structural-only":
            if not isinstance(effect_args, dict) or effect_args.get("structural") is not True:
                return False
            record = {"kind": kind, "subject": subject}
            proof_effect = guard.get("proof_effect")
            if proof_effect:
                record["proof_effect"] = str(proof_effect)
            structural.append(record)
        else:
            return False
    if assumptions:
        assumption_algebra = normalize_assumptions(assumptions)
        normalized_assumptions = assumption_algebra.get("assumptions", assumptions)
        formal["assumptions"] = normalized_assumptions
        parameters[f"{parameter_prefix}.assumptions"] = normalized_assumptions
        derived = assumption_algebra.get("derived") or []
        if derived:
            parameters[f"{parameter_prefix}.assumption_algebra.derived"] = derived
        contradictions = assumption_algebra.get("contradictions") or []
        if contradictions:
            parameters[f"{parameter_prefix}.assumption_algebra.contradictions"] = contradictions
    if structural:
        parameters[f"{parameter_prefix}.structural_preconditions"] = structural
    return True


def known_rewrite_agrees(marker: str, rewrite_source: str) -> bool:
    spec = scalar_instcombine_spec(marker)
    facts = spec.get("semantic_facts") if isinstance(spec.get("semantic_facts"), dict) else {}
    rewrite = str(facts.get("rewrite") or "")
    if rewrite == "replace-with-lhs":
        return "replaceInstUsesWith" in rewrite_source and ("Op0" in rewrite_source or "Op1" in rewrite_source)
    if rewrite == "replace-with-zero":
        return "getNullValue" in rewrite_source or "ConstantInt::get" in rewrite_source
    if marker == "probe.dce.dead-instruction":
        return "eraseFromParent" in rewrite_source
    if marker == "probe.cleanup.unused-alloca":
        return "eraseFromParent" in rewrite_source
    if marker == "probe.globalopt.dead-initializer":
        return "setInitializer" in rewrite_source
    return False


def confidence_for(marker: str, rewrite_source: str, side_conditions_found: list[dict[str, Any]]) -> str:
    if known_rewrite_agrees(marker, rewrite_source) and not side_conditions_found:
        return "high"
    if rewrite_source:
        return "medium"
    return "low"


def inferred_rewrite(marker: str, rewrite_source: str) -> str:
    if rewrite_source:
        return rewrite_source
    template = INTENT_TEMPLATES.get(marker)
    return str(template.get("rewrite", "")) if template else ""


def var(name: str) -> dict[str, Any]:
    return {"op": "var", "name": name}


def memvar(name: str) -> dict[str, Any]:
    return {"op": "memvar", "name": name}


def mem_address_expr(address: int | dict[str, Any]) -> dict[str, Any]:
    return address if isinstance(address, dict) else bvconst(address, 32)


def mem_load(memory: dict[str, Any], address: int | dict[str, Any]) -> dict[str, Any]:
    return {"op": "mem_load", "args": [memory, mem_address_expr(address)]}


def mem_store(memory: dict[str, Any], address: int | dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    return {"op": "mem_store", "args": [memory, mem_address_expr(address), value]}


def address_symbol(base: str, offset: int | str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in base)
    cleaned = cleaned or "base"
    if isinstance(offset, int):
        suffix = str(offset)
    else:
        suffix = "sym_" + "_".join(f"{ord(ch):02x}" for ch in str(offset))
    return f"addr_{cleaned}_{suffix}"


def svar(name: str) -> dict[str, Any]:
    return {"op": "svar", "name": name}


def sfpvar(name: str) -> dict[str, Any]:
    return {"op": "sfpvar", "name": name}


def fpvar(name: str) -> dict[str, Any]:
    return {"op": "fpvar", "name": name}


def bvconst(value: int, bits: int = 32) -> dict[str, Any]:
    return {"op": "bvconst", "value": value, "bits": bits}


def binop(op: str, *args: dict[str, Any]) -> dict[str, Any]:
    return {"op": op, "args": list(args)}


def zext(value: dict[str, Any], bits: int) -> dict[str, Any]:
    return {"op": "zext", "bits": bits, "args": [value]}


def sext(value: dict[str, Any], bits: int) -> dict[str, Any]:
    return {"op": "sext", "bits": bits, "args": [value]}


def trunc(value: dict[str, Any], bits: int) -> dict[str, Any]:
    return {"op": "trunc", "bits": bits, "args": [value]}


def vector_extend(value: dict[str, Any], extend_kind: str, bits: int) -> dict[str, Any]:
    return {"op": "vsext" if extend_kind == "sext" else "vzext", "bits": bits, "args": [value]}


def vector_trunc(value: dict[str, Any], bits: int) -> dict[str, Any]:
    return {"op": "vtrunc", "bits": bits, "args": [value]}


def ite(cond: dict[str, Any], true_value: dict[str, Any], false_value: dict[str, Any]) -> dict[str, Any]:
    return {"op": "ite", "args": [cond, true_value, false_value]}


def vector_value(prefix: str = "a", lanes: int = 4) -> dict[str, Any]:
    return {"op": "vec", "args": [var(f"{prefix}{index}") for index in range(lanes)]}


def fp_vector_value(prefix: str = "a", lanes: int = 4) -> dict[str, Any]:
    return {"op": "fpvec", "args": [fpvar(f"{prefix}{index}") for index in range(lanes)]}


def svshuffle(value: dict[str, Any], base_mask: list[int]) -> dict[str, Any]:
    return {"op": "svshuffle", "base_mask": list(base_mask), "args": [value]}


def extended_vector_value(prefix: str, extend_kind: str, bits: int, lanes: int = 4) -> dict[str, Any]:
    extender = sext if extend_kind == "sext" else zext
    return {"op": "vec", "args": [extender(var(f"{prefix}{index}"), bits) for index in range(lanes)]}


def vector_with_lane_zeroes(lane: int, lanes: int = 4) -> dict[str, Any]:
    return {
        "op": "vec",
        "args": [var(f"a{index}") if index == lane else bvconst(0) for index in range(lanes)],
    }


def vsplat(value: dict[str, Any]) -> dict[str, Any]:
    return {"op": "vsplat", "args": [value]}


def vshuffle(value: dict[str, Any], mask: list[int]) -> dict[str, Any]:
    return {"op": "vshuffle", "args": [value], "mask": mask}


def inverse_permutation(mapping: list[int]) -> list[int]:
    inverse = [0] * len(mapping)
    for index, lane in enumerate(mapping):
        inverse[lane] = index
    return inverse


def vextract(value: dict[str, Any], index: int) -> dict[str, Any]:
    return {"op": "vextract", "args": [value], "index": index}


def vinsert(value: dict[str, Any], lane: dict[str, Any], index: int) -> dict[str, Any]:
    return {"op": "vinsert", "args": [value, lane], "index": index}


def svextract(value: dict[str, Any], index: int) -> dict[str, Any]:
    return {"op": "svextract", "args": [value], "index": index}


def svinsert(value: dict[str, Any], lane: dict[str, Any], index: int) -> dict[str, Any]:
    return {"op": "svinsert", "args": [value, lane], "index": index}


def vector_mask_param(constraints: dict[str, Any], key: str, default: list[int]) -> list[int] | None:
    raw = constraints.get(key, default)
    if not isinstance(raw, list) or len(raw) != 4 or not all(isinstance(index, int) for index in raw):
        return None
    if any(index < 0 or index >= 4 for index in raw):
        return None
    return list(raw)


def vector_lane_param(constraints: dict[str, Any], key: str, default: int) -> int | None:
    raw = constraints.get(key, default)
    if not isinstance(raw, int) or raw < 0 or raw >= 4:
        return None
    return raw


def scalar_formal_for(marker: str, rewrite_source: str) -> dict[str, Any] | None:
    spec = scalar_instcombine_spec(marker)
    facts = spec.get("semantic_facts") if isinstance(spec.get("semantic_facts"), dict) else {}
    if facts:
        operation = str(facts.get("operation") or "")
        rewrite = str(facts.get("rewrite") or "")
        identity = str(facts.get("identity") or "")
        bvop = BV_OP_FOR_OPERATION.get(operation)
        if not bvop:
            return None
        if rewrite == "replace-with-lhs":
            if "replaceInstUsesWith" not in rewrite_source:
                return None
            if identity == "same-value":
                before = binop(bvop, var("a"), var("a"))
            else:
                constant = CONSTANT_FOR_IDENTITY.get(identity)
                if constant is None:
                    return None
                if "Op0" in rewrite_source:
                    before = binop(bvop, var("a"), bvconst(constant))
                elif "Op1" in rewrite_source:
                    before = binop(bvop, bvconst(constant), var("a"))
                else:
                    return None
            after = var("a")
        elif rewrite == "replace-with-zero":
            if "getNullValue" not in rewrite_source and "ConstantInt::get" not in rewrite_source:
                return None
            before = binop(bvop, var("a"), var("a"))
            after = bvconst(0)
        else:
            return None
    elif marker == "probe.dce.dead-instruction":
        if "eraseFromParent" not in rewrite_source:
            return None
        before = var("a")
        after = var("a")
    else:
        return None
    formal = {
        "domain": "scalar-bv32",
        "variables": ["a", "b"],
        "before": before,
        "after": after,
        "equivalence": "result",
    }
    if facts:
        formal["registry_spec"] = registry_diagnostic(marker)
    return formal


def normalize_source_symbol(raw: str) -> str:
    lowered = re.sub(r"[^A-Za-z0-9_]", "_", raw.strip()).lower().strip("_")
    if not lowered:
        lowered = "v"
    if lowered[0].isdigit():
        lowered = f"v_{lowered}"
    return lowered


def source_intent_symbol_name(raw: str, symbols: dict[str, str]) -> str:
    if raw not in symbols:
        base = normalize_source_symbol(raw)
        candidate = base
        used = set(symbols.values())
        index = 1
        while candidate in used:
            index += 1
            candidate = f"{base}_{index}"
        symbols[raw] = candidate
    return symbols[raw]


# Integer comparison predicates lifted to boolean DSL ops (used as select
# conditions). Casts are intentionally absent: they change bit width, which the
# single-width scalar-bv32 domain cannot model (a future multi-width domain).
ICMP_PREDICATE_OP = {
    "eq": "eq", "ne": "ne",
    "slt": "bvslt", "sle": "bvsle", "sgt": "bvsgt", "sge": "bvsge",
    "ult": "bvult", "ule": "bvule", "ugt": "bvugt", "uge": "bvuge",
}


def source_intent_condition_expr(value: Any, symbols: dict[str, str]) -> dict[str, Any] | None:
    """Lift an i1 condition (currently an icmp) to a boolean DSL node."""
    if not isinstance(value, dict):
        return None
    operation = value.get("operation")
    operands = value.get("operands")
    if operation == "icmp" and isinstance(operands, list) and len(operands) == 2:
        op_name = ICMP_PREDICATE_OP.get(str(value.get("predicate") or ""))
        if not op_name:
            return None
        lhs = source_intent_value_expr(operands[0], symbols)
        rhs = source_intent_value_expr(operands[1], symbols)
        if lhs is None or rhs is None:
            return None
        return binop(op_name, lhs, rhs)
    return None


def source_intent_value_expr(value: Any, symbols: dict[str, str]) -> dict[str, Any] | None:
    if isinstance(value, str):
        if value == "0":
            return bvconst(0)
        if value == "1":
            return bvconst(1)
        if value:
            return var(source_intent_symbol_name(value, symbols))
        return None
    if isinstance(value, dict):
        if "symbol" in value:
            raw = value.get("symbol")
            if not isinstance(raw, str) or not raw:
                return None
            return var(source_intent_symbol_name(raw, symbols))
        if "constant" in value:
            try:
                return bvconst(int(value.get("constant")))
            except (TypeError, ValueError):
                return None
        raw = value.get("result")
        if raw is not None:
            return source_intent_value_expr(raw, symbols)
        operation = value.get("operation")
        operands = value.get("operands")
        if isinstance(operation, str) and isinstance(operands, list):
            # binary bitvector ops: add/sub/mul/and/or/xor/shl/lshr/ashr
            op_name = BV_OP_FOR_OPERATION.get(operation)
            if op_name and len(operands) == 2:
                lhs = source_intent_value_expr(operands[0], symbols)
                rhs = source_intent_value_expr(operands[1], symbols)
                if lhs is None or rhs is None:
                    return None
                return binop(op_name, lhs, rhs)
            # unary negate: neg x  ==>  bvneg x
            if operation == "neg" and len(operands) == 1:
                inner = source_intent_value_expr(operands[0], symbols)
                return binop("bvneg", inner) if inner is not None else None
            # bitwise complement: not x  ==>  xor x, all-ones
            if operation == "not" and len(operands) == 1:
                inner = source_intent_value_expr(operands[0], symbols)
                allones = CONSTANT_FOR_IDENTITY.get("allones")
                if inner is None or allones is None:
                    return None
                return binop("bvxor", inner, bvconst(allones))
            # select cond, t, f  ==>  ite(cond, t, f) with an icmp condition
            if operation == "select" and len(operands) == 3:
                cond = source_intent_condition_expr(operands[0], symbols)
                tval = source_intent_value_expr(operands[1], symbols)
                fval = source_intent_value_expr(operands[2], symbols)
                if cond is None or tval is None or fval is None:
                    return None
                return ite(cond, tval, fval)
    return None


def graph_matcher_value_symbols(graph: Any) -> list[str]:
    if not isinstance(graph, dict):
        return []
    seen: list[str] = []
    for node in graph_predicate_nodes(graph):
        source = str(node.get("source") or "")
        for match in re.finditer(r"\bm_(?:Value|Deferred)\s*\(\s*([A-Za-z_]\w*)\s*\)", source):
            symbol = match.group(1)
            if symbol not in seen:
                seen.append(symbol)
    return seen


def fill_unknown_scalar_operands_from_graph(value: Any, graph: Any) -> Any:
    matcher_symbols = graph_matcher_value_symbols(graph)
    if len(matcher_symbols) != 1:
        return value
    replacement = {"symbol": matcher_symbols[0]}

    def fill(item: Any) -> Any:
        if isinstance(item, dict):
            if item.get("unknown") is True:
                return dict(replacement)
            return {key: fill(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [fill(nested) for nested in item]
        return item

    return fill(value)


def source_intent_vector_formal_for(
    finding: dict[str, Any],
    source_intent: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    rewrite: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    shape = str(before.get("shape") or "")
    if shape not in {"fixed-vector", "scalable-vector"}:
        return None
    parameters = before.get("parameters")
    constraints = copy.deepcopy(parameters) if isinstance(parameters, dict) else {}
    pseudo = {
        "marker": finding.get("marker"),
        "constraints": constraints,
        "semantic_facts": {
            "model": "optimization-semantic-v1",
            "shape": shape,
            "operation": before.get("operation"),
            "identity": before.get("identity"),
            "rewrite": after.get("rewrite") or rewrite.get("action"),
            "result": after.get("result"),
        },
    }
    lowered = semantic_vector_formal_for(pseudo)
    if lowered is None:
        return None
    formal, lowered_parameters = lowered
    out_parameters: dict[str, Any] = {
        "source_intent.model": str(source_intent.get("model") or ""),
        "source_intent.shape": shape,
        "source_intent.operation": str(before.get("operation") or ""),
        "source_intent.identity": str(before.get("identity") or ""),
        "source_intent.rewrite_action": str(rewrite.get("action") or ""),
    }
    out_parameters.update(lowered_parameters)
    if constraints:
        out_parameters["source_intent.parameters"] = constraints
    guards = source_intent.get("guards")
    if isinstance(guards, list):
        out_parameters["source_intent.guards"] = [
            str(guard.get("kind") or "") for guard in guards if isinstance(guard, dict) and guard.get("kind")
        ]
    return formal, out_parameters


def memory_observable_identity_formal() -> dict[str, Any]:
    return {
        "domain": "memory-bv32",
        "variables": ["a", "b"],
        "before": var("a"),
        "after": var("a"),
        "equivalence": "observable-result",
    }


def source_intent_memory_formal_for(
    finding: dict[str, Any],
    source_intent: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    rewrite: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if before.get("shape") != "memory":
        return None
    if (
        str(before.get("operation") or "") != "remove-alloca"
        or str(before.get("identity") or "") != "unused-alloca"
        or str(before.get("target") or "") != "alloca"
        or str(rewrite.get("action") or "") != "remove-unused-alloca"
        or str(rewrite.get("api") or "") != "eraseFromParent"
    ):
        return None
    if str(finding.get("marker") or "") != "probe.cleanup.unused-alloca":
        return None
    parameters: dict[str, Any] = {
        "source_intent.model": str(source_intent.get("model") or ""),
        "source_intent.shape": "memory",
        "source_intent.operation": "remove-alloca",
        "source_intent.identity": "unused-alloca",
        "source_intent.rewrite_action": "remove-unused-alloca",
        "source_intent.rewrite_api": "eraseFromParent",
    }
    guards = source_intent.get("guards")
    if isinstance(guards, list):
        parameters["source_intent.guards"] = [
            str(guard.get("kind") or "")
            for guard in guards
            if isinstance(guard, dict) and guard.get("kind")
        ]
    return memory_observable_identity_formal(), parameters


def apply_source_intent_guard_semantics(
    formal: dict[str, Any],
    parameters: dict[str, Any],
    source_intent: dict[str, Any],
    symbols: dict[str, str],
) -> bool:
    guards = source_intent.get("guards")
    if not isinstance(guards, list):
        return True
    modeled_guards = [
        normalize_guard_record(guard, GUARD_SEMANTICS, guard.get("line")) for guard in guards if isinstance(guard, dict)
    ]
    return lower_guard_effects(
        formal,
        parameters,
        modeled_guards,
        lambda subject: symbols.get(subject),
        "source_intent",
    )


def source_intent_graph_quality(finding: dict[str, Any]) -> tuple[str, list[str]]:
    graph = finding.get("source_intent_graph")
    if not isinstance(graph, dict):
        return "absent", []
    status = str(graph.get("status") or "unknown")
    reasons = [
        str(item)
        for item in graph.get("unsupported_reasons", [])
        if isinstance(item, (str, int, float)) and str(item)
    ]
    if status != "complete" and not reasons:
        reasons = [f"graph-status-{status}"]
    return status, reasons


def graph_bindings(graph: dict[str, Any]) -> list[dict[str, Any]]:
    bindings = graph.get("bindings")
    return [binding for binding in bindings if isinstance(binding, dict)] if isinstance(bindings, list) else []


def graph_rewrite_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = graph.get("rewrite_nodes")
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def graph_predicate_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = graph.get("predicate_nodes")
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def source_intent_symbols_from_value(value: Any) -> set[str]:
    if isinstance(value, str) and value and not value.isdigit():
        return {value}
    if isinstance(value, dict):
        symbol = value.get("symbol")
        if isinstance(symbol, str) and symbol:
            return {symbol}
        nested = value.get("result")
        return source_intent_symbols_from_value(nested)
    return set()


def source_intent_scalar_symbols(source_intent: dict[str, Any]) -> set[str]:
    before = source_intent.get("before")
    after = source_intent.get("after")
    symbols: set[str] = set()
    if isinstance(before, dict):
        if before.get("shape") != "scalar":
            return symbols
        operands = before.get("operands")
        if isinstance(operands, list):
            for operand in operands:
                symbols.update(source_intent_symbols_from_value(operand))
    if isinstance(after, dict):
        symbols.update(source_intent_symbols_from_value(after.get("result")))
    return symbols


def source_intent_vector_parameters(source_intent: dict[str, Any]) -> dict[str, Any]:
    before = source_intent.get("before")
    if not isinstance(before, dict):
        return {}
    parameters = before.get("parameters")
    return parameters if isinstance(parameters, dict) else {}


def source_intent_value_label(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        symbol = value.get("symbol")
        if isinstance(symbol, str):
            return symbol
        if "constant" in value:
            return str(value.get("constant"))
        if "result" in value:
            return source_intent_value_label(value.get("result"))
        operation = value.get("operation")
        operands = value.get("operands")
        if isinstance(operation, str) and isinstance(operands, list) and len(operands) == 2:
            lhs = source_intent_value_label(operands[0])
            rhs = source_intent_value_label(operands[1])
            if lhs and rhs:
                return f"{operation}({lhs},{rhs})"
    return ""


def split_top_level_args(text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def graph_local_definition_value(graph: dict[str, Any], name: str) -> Any:
    if not name:
        return None
    for node in graph_rewrite_nodes(graph):
        definitions = node.get("local_definitions")
        if not isinstance(definitions, list):
            continue
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            if str(definition.get("name") or "") == name:
                return definition.get("value")
    return None


def graph_replacement_label(value: Any, graph: dict[str, Any] | None = None) -> str:
    if isinstance(value, dict):
        return source_intent_value_label(value)
    text = str(value or "")
    if graph is not None:
        local_value = graph_local_definition_value(graph, text)
        if local_value is not None:
            return graph_replacement_label(local_value, graph)
    builder_pattern = "|".join(re.escape(name) for name in sorted(OPERATION_FOR_BUILDER_CALL))
    match = re.search(rf"\b({builder_pattern})\s*\((.*)\)\s*$", text)
    if match:
        op = OPERATION_FOR_BUILDER_CALL[match.group(1)]
        args = split_top_level_args(match.group(2))
        if len(args) >= 2:
            return f"{op}({graph_replacement_label(args[0], graph)},{graph_replacement_label(args[1], graph)})"
    if "Constant::getNullValue" in text or "ConstantInt::get" in text:
        return "0"
    return text


def source_intent_graph_consistency(
    finding: dict[str, Any],
    lowered_parameters: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    graph = finding.get("source_intent_graph")
    if not isinstance(graph, dict):
        return "absent", []
    source_intent = finding.get("source_intent")
    if not isinstance(source_intent, dict):
        return "failed", ["missing-source-intent"]

    errors: list[str] = []
    if not graph_predicate_nodes(graph):
        errors.append("missing-predicate-nodes")
    if not graph_rewrite_nodes(graph):
        errors.append("missing-rewrite-nodes")

    rewrite = source_intent.get("rewrite")
    expected_action = str(rewrite.get("action") or "") if isinstance(rewrite, dict) else ""
    rewrite_actions = {str(node.get("action") or "") for node in graph_rewrite_nodes(graph) if node.get("action")}
    if expected_action and rewrite_actions and expected_action not in rewrite_actions:
        errors.append("rewrite-action-mismatch")
    after = source_intent.get("after")
    expected_result = source_intent_value_label(after.get("result")) if isinstance(after, dict) else ""
    rewrite_replacements = {
        graph_replacement_label(node.get("replacement"), graph)
        for node in graph_rewrite_nodes(graph)
        if node.get("replacement") is not None
    }
    if expected_result and rewrite_replacements and expected_result not in rewrite_replacements:
        errors.append("rewrite-replacement-mismatch")

    bindings = graph_bindings(graph)
    binding_symbols = {
        str(binding.get("source_symbol") or "")
        for binding in bindings
        if str(binding.get("role") or "") in {"operand", "result"} and binding.get("source_symbol")
    }
    binding_symbols.update(graph_matcher_value_symbols(graph))
    scalar_symbols = source_intent_scalar_symbols(source_intent)
    scalar_symbols.update(graph_matcher_value_symbols(graph))
    for symbol in sorted(binding_symbols - scalar_symbols):
        errors.append(f"graph-binding-unmatched-symbol:{symbol}")
    for symbol in sorted(scalar_symbols - binding_symbols):
        errors.append(f"source-intent-symbol-missing-graph-binding:{symbol}")

    vector_parameters = source_intent_vector_parameters(source_intent)
    parameter_bindings = {
        str(binding.get("source_symbol") or ""): binding.get("value")
        for binding in bindings
        if str(binding.get("role") or "") == "parameter" and binding.get("source_symbol")
    }
    for key, value in sorted(vector_parameters.items()):
        if key not in parameter_bindings:
            errors.append(f"vector-parameter-missing-graph-binding:{key}")
        elif parameter_bindings[key] != value:
            errors.append(f"vector-parameter-binding-mismatch:{key}")

    if lowered_parameters:
        formal_symbols = lowered_parameters.get("source_intent.symbols")
        if isinstance(formal_symbols, dict):
            for symbol in sorted(str(key) for key in formal_symbols):
                if symbol not in binding_symbols:
                    errors.append(f"formal-symbol-missing-graph-binding:{symbol}")
        for key, value in sorted(vector_parameters.items()):
            if key in lowered_parameters and lowered_parameters[key] != value:
                errors.append(f"vector-formal-parameter-mismatch:{key}")

    deduped = sorted(set(errors))
    return ("ok" if not deduped else "failed"), deduped


def source_intent_graph_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    graph = finding.get("source_intent_graph")
    if not isinstance(graph, dict):
        return None
    status, reasons = source_intent_graph_quality(finding)
    if status != "complete" or reasons:
        return None
    initial_consistency, initial_errors = source_intent_graph_consistency(finding)
    if initial_consistency != "ok":
        return None
    lowered = source_intent_formal_for(finding)
    if lowered is None:
        return None
    formal, parameters = lowered
    consistency, consistency_errors = source_intent_graph_consistency(finding, parameters)
    if consistency != "ok":
        return None
    graph_parameters: dict[str, Any] = {
        "source_intent_graph.model": str(graph.get("model") or ""),
        "source_intent_graph.status": status,
        "source_intent_graph.consistency": consistency,
        "source_intent_graph.predicate_nodes": len(graph.get("predicate_nodes") or []),
        "source_intent_graph.rewrite_nodes": len(graph.get("rewrite_nodes") or []),
        "source_intent_graph.bindings": len(graph.get("bindings") or []),
    }
    if consistency_errors:
        graph_parameters["source_intent_graph.consistency_errors"] = consistency_errors
    symbols = parameters.get("source_intent.symbols")
    if isinstance(symbols, dict):
        graph_parameters["source_intent_graph.formal_symbols"] = dict(symbols)
    parameters.update(graph_parameters)
    return formal, parameters


def scalar_subject_symbol(marker: str, rewrite_source: str, subject: str) -> str | None:
    if marker in {"probe.instcombine.add-zero", "probe.instcombine.mul-one"}:
        if subject == "Op0" and "Op0" in rewrite_source:
            return "a"
        if subject == "Op1" and "Op1" in rewrite_source:
            return "a"
    return None


def apply_modeled_side_condition_semantics(
    formal: dict[str, Any],
    parameters: dict[str, Any],
    modeled_side_records: list[dict[str, Any]],
    marker: str,
    rewrite_source: str,
) -> bool:
    return lower_guard_effects(
        formal,
        parameters,
        modeled_side_records,
        lambda subject: scalar_subject_symbol(marker, rewrite_source, subject),
        "side_conditions",
    )


def source_intent_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    source_intent = finding.get("source_intent")
    if not isinstance(source_intent, dict) or source_intent.get("model") != "source-intent-v1":
        return None
    before = source_intent.get("before")
    after = source_intent.get("after")
    rewrite = source_intent.get("rewrite")
    if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(rewrite, dict):
        return None
    vector_formal = source_intent_vector_formal_for(finding, source_intent, before, after, rewrite)
    if vector_formal is not None:
        return vector_formal
    memory_formal = source_intent_memory_formal_for(finding, source_intent, before, after, rewrite)
    if memory_formal is not None:
        return memory_formal
    if before.get("shape") == "global":
        if (
            str(before.get("operation") or "") != "erase"
            or str(before.get("identity") or "") != "dead"
            or str(before.get("target") or "") != "initializer"
            or str(rewrite.get("action") or "") != "remove-global-initializer-if-dead-v1"
            or str(rewrite.get("api") or "") != "setInitializer"
        ):
            return None
        predicate_source = str(
            finding.get("predicate_source")
            or finding.get("source")
            or finding.get("matched_pattern")
            or ""
        )
        safety_params = global_initializer_safety_parameters(finding, predicate_source)
        if safety_params.get("global.initializer.safety_status") != "complete":
            return None
        rewrite_provenance = global_initializer_rewrite_provenance(finding)
        if rewrite_provenance.get("global.initializer.rewrite_provenance_status") == "unsupported":
            return None
        parameters: dict[str, Any] = {
            "source_intent.model": str(source_intent.get("model") or ""),
            "source_intent.rewrite_action": str(rewrite.get("action") or ""),
            "source_intent.rewrite_api": str(rewrite.get("api") or ""),
            "source_intent.replacement_kind": str(
                rewrite.get("replacement_kind")
                or rewrite.get("replacement")
                or ""
            ),
        }
        parameters.update(global_initializer_formal_parameters())
        parameters.update(safety_params)
        parameters.update(rewrite_provenance)
        guards = source_intent.get("guards")
        if isinstance(guards, list):
            parameters["source_intent.guards"] = [
                str(guard.get("kind") or "")
                for guard in guards
                if isinstance(guard, dict) and guard.get("kind")
            ]
        return scalar_refinement_formal(var("a"), var("a")), parameters
    if before.get("shape") != "scalar":
        return None
    operation = str(before.get("operation") or "")
    operands = before.get("operands")
    if not isinstance(operands, list):
        return None

    symbols: dict[str, str] = {}
    if operation == "dead-instruction" and rewrite.get("action") == "erase-instruction":
        name = source_intent_symbol_name("I", symbols)
        formal = scalar_refinement_formal(var(name), var(name))
    else:
        # Lift the matched root instruction through the unified value lifter so
        # binary (add/.../shl), unary (neg/not), and select befores all reduce
        # to a bitvector DSL node. A bare icmp before is rejected (its i1 result
        # is not a scalar-bv32 value), which source_intent_value_expr enforces.
        before_value: dict[str, Any] = {"operation": operation, "operands": operands}
        if before.get("predicate") is not None:
            before_value["predicate"] = before.get("predicate")
        before_value = fill_unknown_scalar_operands_from_graph(
            before_value, finding.get("source_intent_graph")
        )
        before_expr = source_intent_value_expr(before_value, symbols)
        result = source_intent_value_expr(after.get("result"), symbols)
        if before_expr is None or result is None:
            return None
        formal = scalar_refinement_formal(before_expr, result)
    variables = list(symbols.values())
    if not variables:
        return None
    formal["variables"] = variables
    formal["poison_variables"] = variables

    parameters: dict[str, Any] = {
        "source_intent.model": str(source_intent.get("model") or ""),
        "source_intent.rewrite_action": str(rewrite.get("action") or ""),
        "source_intent.symbols": dict(symbols),
    }
    guards = source_intent.get("guards")
    if isinstance(guards, list):
        parameters["source_intent.guards"] = [
            str(guard.get("kind") or "") for guard in guards if isinstance(guard, dict) and guard.get("kind")
        ]
    if not apply_source_intent_guard_semantics(formal, parameters, source_intent, symbols):
        return None
    return formal, parameters


def vector_refinement_formal(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    variables = [f"a{index}" for index in range(4)]
    return {
        "domain": "vector-bv32x4",
        "variables": variables,
        "poison_variables": variables,
        "before": before,
        "after": after,
        "equivalence": "vector-result",
        "refinement": "refinement",
    }


def vector_binary_refinement_formal(before: dict[str, Any], after: dict[str, Any], lanes: int = 4) -> dict[str, Any]:
    variables = [f"a{index}" for index in range(lanes)] + [f"b{index}" for index in range(lanes)]
    formal = {
        "domain": "vector-bv32x4",
        "variables": variables,
        "poison_variables": variables,
        "before": before,
        "after": after,
        "equivalence": "vector-result",
        "refinement": "refinement",
    }
    if lanes != 4:
        formal["domain"] = "vector-bv32xN"
        formal["vector_width"] = lanes
    return formal


def scalable_vector_refinement_formal(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": "scalable-vector-bv32",
        "base_lanes": 4,
        "vscale_values": [1, 2, 4],
        "variables": ["a"],
        "poison_variables": ["a"],
        "before": before,
        "after": after,
        "equivalence": "vector-result",
        "refinement": "refinement",
    }


def scalable_vector_binary_refinement_formal(before: dict[str, Any], after: dict[str, Any], base_lanes: int, vscale_values: list[int]) -> dict[str, Any]:
    return {
        "domain": "scalable-vector-bv32",
        "base_lanes": base_lanes,
        "vscale_values": list(vscale_values),
        "variables": ["a", "b"],
        "poison_variables": ["a", "b"],
        "before": before,
        "after": after,
        "equivalence": "vector-result",
        "refinement": "refinement",
    }


def scalable_value() -> dict[str, Any]:
    return {"op": "svar", "name": "a"}


def scalar_refinement_formal(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": "scalar-bv32",
        "variables": ["a", "b"],
        "poison_variables": ["a"],
        "before": before,
        "after": after,
        "equivalence": "result",
        "refinement": "refinement",
    }


def scalar_vector_refinement_formal(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    variables = [f"a{index}" for index in range(4)] + ["x"]
    return {
        "domain": "scalar-bv32",
        "variables": variables,
        "poison_variables": variables,
        "before": before,
        "after": after,
        "equivalence": "result",
        "refinement": "refinement",
    }


_LIFT_RULES_CACHE: list[dict[str, Any]] | None = None


def _lift_rules() -> list[dict[str, Any]]:
    global _LIFT_RULES_CACHE
    if _LIFT_RULES_CACHE is None:
        try:
            _LIFT_RULES_CACHE = cv_lift_rules.load_rules()
        except (OSError, ValueError):
            _LIFT_RULES_CACHE = []
    return _LIFT_RULES_CACHE


def rule_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Lift a scalar identity via the declarative rule engine (M2). The canonical
    scalar-identity lifter -- semantic_scalar_formal_for delegates here, so adding
    an identity is a JSON edit in constraints/lift_rules.json, born-proven by the
    lift_rules gate. Parameters match the legacy hand-written door exactly
    (semantic.operation/semantic.rewrite only) so the collapse is byte-identical."""
    facts = finding.get("semantic_facts")
    if not isinstance(facts, dict) or facts.get("shape") != "scalar":
        return None
    operation = str(facts.get("operation") or "")
    identity = str(facts.get("identity") or "")
    rewrite = str(facts.get("rewrite") or "")
    rule = cv_lift_rules.match_rule(_lift_rules(), operation, identity, rewrite)
    if rule is None:
        return None
    try:
        before, after, _vars = cv_lift_rules.rule_before_after(rule)
    except cv_lift_rules.RuleError:
        return None
    parameters = {"semantic.operation": operation, "semantic.rewrite": rewrite}
    return scalar_refinement_formal(before, after), parameters


def semantic_scalar_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Scalar-identity lifting (② collapsed): the enumerated add-zero/mul-one/...
    identities are now declarative rules (constraints/lift_rules.json) instantiated
    by rule_formal_for -- no hand-written if/elif chain. Only the structural
    dead-instruction case (no algebraic before/after) stays here."""
    facts = finding.get("semantic_facts")
    marker = str(finding.get("marker", ""))
    ok, _ = semantic_facts_valid_for_marker(marker, facts)
    if not ok:
        return None
    if facts.get("shape") != "scalar":
        return None
    rule_formal = rule_formal_for(finding)
    if rule_formal is not None:
        return rule_formal
    if (facts.get("operation") == "erase" and facts.get("identity") == "dead"
            and facts.get("rewrite") == "remove-dead-instruction"):
        parameters = {"semantic.operation": "erase", "semantic.rewrite": "remove-dead-instruction"}
        return scalar_refinement_formal(var("a"), var("a")), parameters
    return None


def semantic_registry_formal_for(
    finding: dict[str, Any],
    registry_record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    facts = finding.get("semantic_facts")
    marker = str(finding.get("marker", ""))
    ok, _ = semantic_facts_valid_for_marker(marker, facts)
    if not ok or not isinstance(facts, dict):
        return None
    shape = str(facts.get("shape") or "")
    expected_domain = {
        "cfg": "cfg-bv32",
        "memory": "memory-bv32",
        "loop": "loop-bv32",
        "global": "global-initializer-observable-v1",
    }.get(shape)
    formal = registry_record.get("formal")
    if expected_domain is None or not isinstance(formal, dict):
        return None
    if formal.get("domain") != expected_domain:
        return None
    if marker == "probe.globalopt.dead-initializer":
        predicate_source = str(
            finding.get("predicate_source")
            or finding.get("source")
            or finding.get("matched_pattern")
            or ""
        )
        safety_params = global_initializer_safety_parameters(finding, predicate_source)
        if not scalar_predicate_evidence_is_strong(marker, finding, predicate_source):
            return None
    parameters = {
        "semantic.shape": shape,
        "semantic.operation": facts.get("operation"),
        "semantic.rewrite": facts.get("rewrite"),
    }
    if marker == "probe.globalopt.dead-initializer":
        parameters.update(global_initializer_formal_parameters())
        parameters.update(safety_params)
        rewrite_provenance = global_initializer_rewrite_provenance(finding)
        parameters.update(rewrite_provenance)
        if rewrite_provenance.get("global.initializer.rewrite_provenance_status") == "unsupported":
            return None
    return copy.deepcopy(formal), parameters, f"source-derived-{shape}"


def semantic_vector_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    facts = finding.get("semantic_facts")
    marker = str(finding.get("marker", ""))
    ok, message = semantic_facts_valid_for_marker(marker, facts)
    if not ok:
        return None
    shape = facts.get("shape")
    operation = facts.get("operation")
    identity = facts.get("identity")
    rewrite = facts.get("rewrite")
    result = facts.get("result")
    constraints = finding.get("constraints", {})
    constraints = constraints if isinstance(constraints, dict) else {}
    if shape not in {"fixed-vector", "scalable-vector"}:
        return None
    is_scalable = shape == "scalable-vector"
    value = scalable_value() if is_scalable else vector_value()
    splat = vsplat
    if is_scalable:
        op_names = {
            "add": "svadd",
            "mul": "svmul",
            "xor": "svxor",
            "sub": "svsub",
            "or": "svor",
            "and": "svand",
        }
        formal_builder = scalable_vector_refinement_formal
    else:
        op_names = {
            "add": "vadd",
            "mul": "vmul",
            "xor": "vxor",
            "sub": "vsub",
            "or": "vor",
            "and": "vand",
        }
        formal_builder = vector_refinement_formal

    parameters: dict[str, Any] = {"semantic.operation": operation, "semantic.rewrite": rewrite}
    if is_scalable:
        parameters["vector.vscale_values"] = [1, 2, 4]

    if operation in {"add", "mul", "sub", "or", "and"} and rewrite == "replace-with-lhs":
        constants = {"zero": 0, "one": 1, "allones": 0xFFFFFFFF}
        if identity not in constants or operation not in op_names:
            return None
        return formal_builder(binop(op_names[operation], value, splat(bvconst(constants[identity]))), value), parameters
    if operation == "xor" and identity == "same-value" and rewrite == "replace-with-zero":
        return formal_builder(binop(op_names["xor"], value, value), splat(bvconst(0))), parameters
    if not is_scalable and operation in {"min", "max"} and rewrite == "preserve-vector":
        vector_ops = {
            ("min", "signed-min"): "vsmin",
            ("max", "signed-max"): "vsmax",
            ("min", "unsigned-min"): "vumin",
            ("max", "unsigned-max"): "vumax",
        }
        op_name = vector_ops.get((operation, identity))
        if op_name is None:
            return None
        lhs = vector_value("a")
        rhs = vector_value("b")
        expression = binop(op_name, lhs, rhs)
        return vector_binary_refinement_formal(expression, expression), parameters
    if not is_scalable and operation == "abs" and identity == "absolute-value" and rewrite == "preserve-vector":
        expression = {"op": "vabs", "args": [value]}
        return vector_refinement_formal(expression, expression), parameters
    if not is_scalable and operation == "shuffle" and identity == "identity-mask" and rewrite == "preserve-vector":
        mask = vector_mask_param(constraints, "vector.shuffle.mask", [0, 1, 2, 3])
        if mask is None:
            return None
        parameters["vector.shuffle.mask"] = mask
        return vector_refinement_formal(vshuffle(value, mask), value), parameters
    if not is_scalable and operation == "shuffle" and identity == "splat-mask" and rewrite == "replace-with-lane-splat":
        mask = vector_mask_param(constraints, "vector.shuffle.mask", [2, 2, 2, 2])
        lane = vector_lane_param(constraints, "vector.shuffle.splat_lane", mask[0] if mask is not None else 2)
        if mask is None or lane is None or any(index != lane for index in mask):
            return None
        parameters["vector.shuffle.mask"] = mask
        parameters["vector.shuffle.splat_lane"] = lane
        return vector_refinement_formal(vshuffle(value, mask), vsplat(vextract(value, lane))), parameters
    if not is_scalable and operation == "extract-insert" and identity == "same-lane" and rewrite == "replace-with-inserted-scalar":
        lane = vector_lane_param(constraints, "vector.extract_insert.lane", 1)
        if lane is None:
            return None
        parameters["vector.extract_insert.lane"] = lane
        return scalar_vector_refinement_formal(vextract(vinsert(value, var("x"), lane), lane), var("x")), parameters
    if not is_scalable and operation == "insert-extract" and identity == "same-lane" and rewrite == "preserve-vector":
        lane = vector_lane_param(constraints, "vector.insert_extract.lane", 1)
        if lane is None:
            return None
        parameters["vector.insert_extract.lane"] = lane
        return vector_refinement_formal(vinsert(value, vextract(value, lane), lane), value), parameters
    if not is_scalable and operation == "reduction-add" and identity == "zero-vector" and rewrite == "reduce-to-zero":
        return {
            "domain": "scalar-bv32",
            "variables": ["a"],
            "before": {"op": "vreduce_add", "args": [vsplat(bvconst(0))]},
            "after": bvconst(0),
            "equivalence": "result",
        }, parameters
    if not is_scalable and operation == "reduction-add" and identity == "single-live-lane" and rewrite == "replace-with-lane":
        lane = vector_lane_param(constraints, "vector.reduction.lane", 0)
        if lane is None:
            return None
        parameters["vector.reduction.lane"] = lane
        return {
            "domain": "scalar-bv32",
            "variables": [f"a{index}" for index in range(4)],
            "poison_variables": [f"a{index}" for index in range(4)],
            "before": {"op": "vreduce_add", "args": [vector_with_lane_zeroes(lane)]},
            "after": var(f"a{lane}"),
            "equivalence": "result",
            "refinement": "refinement",
        }, parameters
    if is_scalable and operation == "reduction-add" and identity == "zero-vector" and rewrite == "reduce-to-zero-splat":
        return {
            "domain": "scalable-vector-bv32",
            "base_lanes": 4,
            "vscale_values": [1, 2, 4],
            "variables": ["a"],
            "before": {"op": "svsplat", "args": [{"op": "svreduce_add", "args": [vsplat(bvconst(0))]}]},
            "after": {"op": "svsplat", "args": [bvconst(0)]},
            "equivalence": "vector-result",
        }, parameters
    return None


def transaction_source_slice_parameters(transaction: dict[str, Any]) -> dict[str, Any]:
    source_slice = transaction.get("source_slice")
    if not isinstance(source_slice, dict):
        return {}
    parameters: dict[str, Any] = {}
    control_root = source_slice.get("control_root_function")
    if isinstance(control_root, str) and control_root:
        parameters["transaction.source_slice.control_root_function"] = control_root
    completeness = source_slice.get("completeness")
    if isinstance(completeness, dict):
        parameters["transaction.source_slice.completeness"] = {
            str(key): bool(value)
            for key, value in completeness.items()
            if isinstance(value, bool)
        }
    predicate_expansion = source_slice.get("predicate_expansion")
    if isinstance(predicate_expansion, list):
        cleaned = [dict(item) for item in predicate_expansion if isinstance(item, dict)]
        parameters["transaction.source_slice.predicate_expansion"] = cleaned
        parameters["transaction.source_slice.predicate_expansion_roles"] = [
            str(item.get("role"))
            for item in cleaned
            if isinstance(item.get("role"), (str, int, float)) and str(item.get("role"))
        ]
    contract = source_slice.get("contract")
    if isinstance(contract, dict):
        status = contract.get("status")
        if isinstance(status, str) and status:
            parameters["transaction.source_slice.contract.status"] = status
        missing_roles = contract.get("missing_roles")
        if isinstance(missing_roles, list):
            parameters["transaction.source_slice.contract.missing_roles"] = [
                str(role)
                for role in missing_roles
                if isinstance(role, (str, int, float)) and str(role)
            ]
        role_paths = contract.get("role_paths")
        if isinstance(role_paths, list):
            parameters["transaction.source_slice.contract.role_paths"] = [
                dict(item) for item in role_paths if isinstance(item, dict)
            ]
        checks = contract.get("checks")
        if isinstance(checks, list):
            parameters["transaction.source_slice.contract.checks"] = [
                dict(item) for item in checks if isinstance(item, dict)
            ]
    return parameters


def transaction_formal_source(
    role: str,
    field: str,
    evidence: Any = None,
    **context: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"role": role, "transaction_field": field}
    for key, value in context.items():
        if value is not None and value != "":
            out[key] = value
    if isinstance(evidence, dict):
        out["evidence"] = dict(evidence)
    elif isinstance(evidence, list):
        out["evidence"] = [dict(item) if isinstance(item, dict) else item for item in evidence]
    elif evidence is not None:
        out["evidence"] = evidence
    return out


def formal_contains_op(value: Any, op: str) -> bool:
    if isinstance(value, dict):
        if value.get("op") == op:
            return True
        return any(formal_contains_op(child, op) for child in value.values())
    if isinstance(value, list):
        return any(formal_contains_op(child, op) for child in value)
    return False


def transaction_formal_provenance(
    transaction: dict[str, Any],
    formal: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    graph = transaction.get("transaction_graph")
    if isinstance(graph, dict) and graph.get("model") == "optimization-transaction-graph-v1":
        return transaction_graph_formal_provenance(transaction, graph, formal)

    kind = str(transaction.get("kind") or "")
    opcode_sources = transaction.get("opcode_sources")
    opcode_evidence = opcode_sources if isinstance(opcode_sources, list) else None
    lane_mapping = transaction.get("lane_mapping")
    result_lane_mapping = transaction.get("result_lane_mapping")
    scalar_lane_pairs = transaction.get("scalar_lane_pairs")
    reduction_sources = transaction.get("reduction_sources")
    operand_lane_mappings = transaction.get("operand_lane_mappings")
    lane_evidence = lane_mapping if isinstance(lane_mapping, dict) else None
    result_evidence = result_lane_mapping if isinstance(result_lane_mapping, dict) else None
    scalar_pairs = scalar_lane_pairs if isinstance(scalar_lane_pairs, list) else []
    reduction_evidence = reduction_sources if isinstance(reduction_sources, list) else None
    provenance: dict[str, dict[str, Any]] = {}

    if "domain" in formal:
        provenance["domain"] = transaction_formal_source("domain", "kind", kind=kind)
    for key in ("vector_width", "base_lanes", "vscale_values"):
        if key in formal:
            provenance[key] = transaction_formal_source("width", key, formal.get(key))

    opcode_role = "reduction-source" if kind == "slp-vectorize-reduction" else "opcode"
    opcode_field = "reduction_sources" if kind == "slp-vectorize-reduction" else "opcode_sources"
    opcode_payload = reduction_evidence if kind == "slp-vectorize-reduction" else opcode_evidence
    opcode_source = transaction_formal_source(opcode_role, opcode_field, opcode_payload, opcode=transaction.get("opcode"))
    lane_source = transaction_formal_source("lane-mapping", "lane_mapping", lane_evidence)
    result_source = transaction_formal_source("result-lane-mapping", "result_lane_mapping", result_evidence)
    operand_source = transaction_formal_source("operand-pack", "operand_lane_mappings", operand_lane_mappings)

    def add(path: str, source: dict[str, Any]) -> None:
        if path and path not in provenance:
            provenance[path] = source

    def visit(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            return
        op = str(value.get("op") or "")
        if op:
            if op in {
                "vadd",
                "vsub",
                "vmul",
                "vxor",
                "vor",
                "vand",
                "vsmin",
                "vsmax",
                "vumin",
                "vumax",
                "svadd",
                "svsub",
                "svmul",
                "svxor",
                "svor",
                "svand",
                "svsmin",
                "svsmax",
                "svumin",
                "svumax",
                "bvadd",
                "bvsub",
                "bvmul",
                "bvxor",
                "bvor",
                "bvand",
                "bvslt",
                "bvsgt",
                "bvult",
                "bvugt",
                "ite",
            }:
                add(path, opcode_source)
                add(f"{path}.op", opcode_source)
            elif op in {
                "vreduce_add",
                "vreduce_mul",
                "vreduce_and",
                "vreduce_or",
                "vreduce_xor",
                "vreduce_smin",
                "vreduce_smax",
                "vreduce_umin",
                "vreduce_umax",
                "fpreduce_add",
                "fpreduce_mul",
                "svreduce_add",
                "svreduce_mul",
                "svreduce_and",
                "svreduce_or",
                "svreduce_xor",
                "svreduce_smin",
                "svreduce_smax",
                "svreduce_umin",
                "svreduce_umax",
                "svfpreduce_add",
                "svfpreduce_mul",
                "fpadd",
                "fpmul",
            }:
                add(path, opcode_source)
                add(f"{path}.op", opcode_source)
            elif op in {"vshuffle", "svshuffle"}:
                source = result_source if path == "after" else lane_source
                add(path, source)
                add(f"{path}.op", source)
                add(f"{path}.mask", source)
                add(f"{path}.base_mask", source)
            elif op in {"zext", "sext", "trunc", "vzext", "vsext", "vtrunc"}:
                add(path, transaction_formal_source("width", "reduction_width", {
                    "input_bits": transaction.get("reduction_input_bits"),
                    "accumulator_bits": transaction.get("reduction_accumulator_bits"),
                    "result_bits": transaction.get("reduction_result_bits"),
                    "extend_kind": transaction.get("reduction_extend_kind"),
                }))
                add(f"{path}.op", provenance[path])
            elif op == "vec" and path == "before":
                source = transaction_formal_source("scalar-lane-pair", "scalar_lane_pairs", scalar_pairs)
                add(path, source)
                add(f"{path}.op", source)
            elif op in {"vec", "fpvec", "svar", "sfpvar", "var", "fpvar"} and path.startswith("after"):
                add(path, operand_source)
                add(f"{path}.op", operand_source)
        args = value.get("args")
        if isinstance(args, list):
            for index, child in enumerate(args):
                child_path = f"{path}.args[{index}]" if path else f"args[{index}]"
                if path == "before" and index < len(scalar_pairs):
                    add(child_path, transaction_formal_source(
                        "scalar-lane-pair",
                        "scalar_lane_pairs",
                        scalar_pairs[index],
                        lane=index,
                    ))
                visit(child, child_path)

    visit(formal.get("before"), "before")
    visit(formal.get("after"), "after")
    return provenance


def transaction_graph_formal_provenance(
    transaction: dict[str, Any],
    graph: dict[str, Any],
    formal: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    node_by_id: dict[str, dict[str, Any]] = {}
    node_by_opcode: dict[str, dict[str, Any]] = {}
    constant_operands = [
        operand
        for node in nodes
        for operand in node.get("operands", [])
        if isinstance(operand, dict) and str(operand.get("kind") or "") == "const"
    ]
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id:
            node_by_id[node_id] = node
        opcode = str(node.get("opcode") or "")
        if opcode and opcode not in node_by_opcode:
            node_by_opcode[opcode] = node
    provenance: dict[str, dict[str, Any]] = {}

    def add(path: str, role: str, field: str, evidence: Any = None, **context: Any) -> None:
        if path and path not in provenance:
            provenance[path] = transaction_formal_source(role, field, evidence, **context)

    add("domain", "domain", "kind", kind=transaction.get("kind"))
    if "vector_width" in formal:
        add("vector_width", "width", "lanes", transaction.get("lanes"))
    if "base_lanes" in formal:
        add("base_lanes", "width", "base_lanes", transaction.get("base_lanes"))
    if "vscale_values" in formal:
        add("vscale_values", "width", "vscale_values", transaction.get("vscale_values"))

    scalar_ops = {
        "bvadd": "add",
        "bvsub": "sub",
        "bvmul": "mul",
        "bvxor": "xor",
        "bvor": "or",
        "bvand": "and",
        "bvshl": "shl",
        "bvlshr": "lshr",
        "bvashr": "ashr",
        "eq": "icmp",
        "ne": "icmp",
        "bvslt": "smin",
        "bvsle": "icmp",
        "bvsgt": "smax",
        "bvsge": "icmp",
        "bvult": "umin",
        "bvule": "icmp",
        "bvugt": "umax",
        "bvuge": "icmp",
        "zext": "zext",
        "sext": "sext",
        "trunc": "trunc",
    }
    vector_ops = {
        "vadd": "add",
        "vsub": "sub",
        "vmul": "mul",
        "vxor": "xor",
        "vor": "or",
        "vand": "and",
        "vshl": "shl",
        "vlshr": "lshr",
        "vashr": "ashr",
        "vzext": "zext",
        "vsext": "sext",
        "vtrunc": "trunc",
        "vsmin": "smin",
        "vsmax": "smax",
        "vumin": "umin",
        "vumax": "umax",
        "vicmp": "icmp",
        "vselect": "select",
        "vshuffle": "shuffle",
        "vextract": "extract",
        "vinsert": "insert",
        "svextract": "extract",
        "svinsert": "insert",
    }
    vector_ops.update({
        "svadd": "add",
        "svsub": "sub",
        "svmul": "mul",
        "svxor": "xor",
        "svor": "or",
        "svand": "and",
        "svshl": "shl",
        "svlshr": "lshr",
        "svashr": "ashr",
        "svsmin": "smin",
        "svsmax": "smax",
        "svumin": "umin",
        "svumax": "umax",
        "svicmp": "icmp",
        "svselect": "select",
        "svshuffle": "shuffle",
    })

    def visit(value: Any, path: str, side: str) -> None:
        if not isinstance(value, dict):
            return
        op = str(value.get("op") or "")
        if op:
            opcode = scalar_ops.get(op) or vector_ops.get(op)
            if opcode:
                node_id = str(value.get("node_id") or "")
                node = node_by_id.get(node_id) or node_by_opcode.get(opcode, {})
                source_opcode = str(node.get("opcode") or opcode)
                add(path, "transaction-graph-node", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
                add(f"{path}.op", "transaction-graph-node", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
                if op in {"zext", "sext", "trunc", "vzext", "vsext", "vtrunc"} and "bits" in value:
                    add(f"{path}.bits", "transaction-graph-width", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
                if op in {"vicmp", "svicmp"} and "predicate" in value:
                    add(f"{path}.predicate", "transaction-graph-predicate", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
                if op in {"vshuffle", "svshuffle"}:
                    add(f"{path}.mask", "transaction-graph-shuffle-mask", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
                    add(f"{path}.base_mask", "transaction-graph-shuffle-mask", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
                if op in {"vextract", "vinsert", "svextract", "svinsert"} and "index" in value:
                    add(f"{path}.index", "transaction-graph-lane-index", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=source_opcode)
            elif op == "ite":
                node_id = str(value.get("node_id") or "")
                node = node_by_id.get(node_id, {})
                add(path, "transaction-graph-node", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=node.get("opcode"))
                add(f"{path}.op", "transaction-graph-node", "transaction_graph.nodes", node, node_id=node.get("id"), opcode=node.get("opcode"))
            elif op in {"and", "or"}:
                add(path, "transaction-graph-memory-mask", "transaction_graph.operands", graph.get("operands"), side=side)
                add(f"{path}.op", "transaction-graph-memory-mask", "transaction_graph.operands", graph.get("operands"), side=side)
            elif op in {"svmask_and", "svmask_or", "svmask_not", "svmask_select", "svindexed_mask", "svmask_tuple"}:
                add(path, "transaction-graph-memory-mask", "transaction_graph.operands", graph.get("operands"), side=side)
                add(f"{path}.op", "transaction-graph-memory-mask", "transaction_graph.operands", graph.get("operands"), side=side)
            elif op == "vec":
                add(path, "scalar-lane-pair", "transaction_graph.scalar_lane_pairs", graph.get("scalar_lane_pairs"), side=side)
                add(f"{path}.op", "scalar-lane-pair", "transaction_graph.scalar_lane_pairs", graph.get("scalar_lane_pairs"), side=side)
            elif op in {"var", "svar"}:
                add(path, "transaction-graph-operand", "transaction_graph.operands", graph.get("operands"), name=value.get("name"))
                add(f"{path}.op", "transaction-graph-operand", "transaction_graph.operands", graph.get("operands"), name=value.get("name"))
                add(f"{path}.name", "transaction-graph-operand", "transaction_graph.operands", graph.get("operands"), name=value.get("name"))
            elif op == "memvar":
                add(path, "transaction-graph-memory", "transaction_graph.store_sinks", graph.get("store_sinks"), name=value.get("name"))
                add(f"{path}.op", "transaction-graph-memory", "transaction_graph.store_sinks", graph.get("store_sinks"), name=value.get("name"))
                add(f"{path}.name", "transaction-graph-memory", "transaction_graph.store_sinks", graph.get("store_sinks"), name=value.get("name"))
            elif op in {"mem_load", "mem_store"}:
                add(path, "transaction-graph-memory", "transaction_graph.store_sinks", graph.get("store_sinks"), side=side)
                add(f"{path}.op", "transaction-graph-memory", "transaction_graph.store_sinks", graph.get("store_sinks"), side=side)
            elif op == "bvconst":
                const = next(
                    (
                        item
                        for item in constant_operands
                        if item.get("value") == value.get("value") and item.get("bits") == value.get("bits")
                    ),
                    constant_operands,
                )
                add(path, "transaction-graph-constant", "transaction_graph.nodes.operands", const, value=value.get("value"), bits=value.get("bits"))
                add(f"{path}.op", "transaction-graph-constant", "transaction_graph.nodes.operands", const, value=value.get("value"), bits=value.get("bits"))
                add(f"{path}.value", "transaction-graph-constant", "transaction_graph.nodes.operands", const, value=value.get("value"), bits=value.get("bits"))
                add(f"{path}.bits", "transaction-graph-constant-width", "transaction_graph.nodes.operands", const, value=value.get("value"), bits=value.get("bits"))
            elif op in {"vsplat", "svsplat"}:
                add(path, "transaction-graph-constant-splat", "transaction_graph.nodes.operands", constant_operands, side=side)
                add(f"{path}.op", "transaction-graph-constant-splat", "transaction_graph.nodes.operands", constant_operands, side=side)
            elif op in {"vshuffle", "svshuffle"}:
                if path == "after":
                    outputs = graph.get("outputs")
                    add(path, "result-lane-mapping", "transaction_graph.outputs", outputs)
                    add(f"{path}.op", "result-lane-mapping", "transaction_graph.outputs", outputs)
                    add(f"{path}.mask", "result-lane-mapping", "transaction_graph.outputs", outputs)
                    add(f"{path}.base_mask", "result-lane-mapping", "transaction_graph.outputs", outputs)
                else:
                    add(path, "lane-mapping", "transaction_graph.operands", graph.get("operands"))
                    add(f"{path}.op", "lane-mapping", "transaction_graph.operands", graph.get("operands"))
                    add(f"{path}.mask", "lane-mapping", "transaction_graph.operands", graph.get("operands"))
                    add(f"{path}.base_mask", "lane-mapping", "transaction_graph.operands", graph.get("operands"))
        args = value.get("args")
        if isinstance(args, list):
            for index, child in enumerate(args):
                child_path = f"{path}.args[{index}]" if path else f"args[{index}]"
                if side == "after" and isinstance(child, dict) and child.get("op") in vector_ops:
                    child_id = str(child.get("node_id") or "")
                    edge = next(
                        (
                            item
                            for item in edges
                            if str(item.get("from") or "") == child_id
                            and str(item.get("to") or "") == str(value.get("node_id") or "")
                            and item.get("operand") == index
                        ),
                        None,
                    )
                    add(child_path, "transaction-graph-edge", "transaction_graph.edges", edge or edges)
                visit(child, child_path, side)

    visit(formal.get("before"), "before", "before")
    visit(formal.get("after"), "after", "after")
    return provenance


def transaction_formal_result(
    transaction: dict[str, Any],
    formal: dict[str, Any],
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if formal_contains_op(formal, "svmask_tuple"):
        parameters["transaction.graph.scalable_mask_tuple"] = True
    parameters["transaction.formal_provenance"] = transaction_formal_provenance(transaction, formal)
    return formal, parameters


def transaction_graph_formal_for(
    transaction: dict[str, Any],
    graph: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if graph.get("model") != "optimization-transaction-graph-v1":
        return None
    if str(graph.get("kind") or "") != "slp-binop-chain":
        return None
    is_scalable_transaction = transaction.get("scalable") is True
    lanes = int(transaction.get("lanes") or graph.get("lanes") or 0)
    base_lanes = int(transaction.get("base_lanes") or lanes or 0)
    vscale_values = transaction.get("vscale_values") if isinstance(transaction.get("vscale_values"), list) else [1, 2, 4]
    if is_scalable_transaction:
        if base_lanes <= 0:
            return None
        lanes = base_lanes
    elif lanes not in {2, 4, 8, 16, 32, 64}:
        return None
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    if len(nodes) < 2:
        return None
    scalar_ops = {
        "add": "bvadd",
        "sub": "bvsub",
        "mul": "bvmul",
        "xor": "bvxor",
        "or": "bvor",
        "and": "bvand",
        "shl": "bvshl",
        "lshr": "bvlshr",
        "ashr": "bvashr",
    }
    cast_ops = {
        "zext": "zext",
        "sext": "sext",
        "trunc": "trunc",
    }
    minmax_cmp_ops = {
        "smin": "bvslt",
        "smax": "bvsgt",
        "umin": "bvult",
        "umax": "bvugt",
    }
    icmp_ops = {
        "eq": "eq",
        "ne": "ne",
        "slt": "bvslt",
        "sle": "bvsle",
        "sgt": "bvsgt",
        "sge": "bvsge",
        "ult": "bvult",
        "ule": "bvule",
        "ugt": "bvugt",
        "uge": "bvuge",
    }
    vector_ops = {
        "add": "vadd",
        "sub": "vsub",
        "mul": "vmul",
        "xor": "vxor",
        "or": "vor",
        "and": "vand",
        "shl": "vshl",
        "lshr": "vlshr",
        "ashr": "vashr",
        "smin": "vsmin",
        "smax": "vsmax",
        "umin": "vumin",
        "umax": "vumax",
    }
    scalable_vector_ops = {
        "add": "svadd",
        "sub": "svsub",
        "mul": "svmul",
        "xor": "svxor",
        "or": "svor",
        "and": "svand",
        "shl": "svshl",
        "lshr": "svlshr",
        "ashr": "svashr",
        "smin": "svsmin",
        "smax": "svsmax",
        "umin": "svumin",
        "umax": "svumax",
    }
    node_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in node_by_id:
            return None
        kind = str(node.get("kind") or "")
        opcode = str(node.get("opcode") or "")
        operands = node.get("operands")
        if not isinstance(operands, list) or not all(isinstance(item, dict) for item in operands):
            return None
        if kind == "binop":
            if opcode not in scalar_ops and opcode not in minmax_cmp_ops:
                return None
            if len(operands) != 2:
                return None
        elif kind == "cast":
            if opcode not in cast_ops or len(operands) != 1:
                return None
            bits = node.get("bits")
            if not isinstance(bits, int) or bits <= 0 or bits % 4 != 0:
                return None
        elif kind == "icmp":
            predicate = str(node.get("predicate") or "")
            if opcode != "icmp" or predicate not in icmp_ops or len(operands) != 2:
                return None
        elif kind == "select":
            if opcode != "select" or len(operands) != 3:
                return None
        elif kind == "shuffle":
            if opcode != "shuffle" or len(operands) not in {1, 2}:
                return None
            mask = node.get("base_mask") if is_scalable_transaction else node.get("mask")
            if not isinstance(mask, list) or len(mask) != lanes or not all(isinstance(index, int) for index in mask):
                return None
            source_lanes = lanes * len(operands)
            if any(index < 0 or index >= source_lanes for index in mask):
                return None
        elif kind == "extract":
            if opcode != "extract" or len(operands) != 1:
                return None
            index = node.get("index")
            if not isinstance(index, int) or index < 0 or index >= lanes:
                return None
        elif kind == "insert":
            if opcode != "insert" or len(operands) != 2:
                return None
            index = node.get("index")
            if not isinstance(index, int) or index < 0 or index >= lanes:
                return None
        else:
            return None
        node_by_id[node_id] = node
    seen: set[str] = set()
    for node in nodes:
        for operand in node.get("operands", []):
            kind = str(operand.get("kind") or "")
            if kind == "node":
                producer = str(operand.get("id") or "")
                if producer not in seen:
                    return None
            elif kind in {"pack", "memory-pack"}:
                if not str(operand.get("name") or ""):
                    return None
            elif kind == "const":
                value = operand.get("value")
                bits = operand.get("bits")
                if not isinstance(value, int) or not isinstance(bits, int) or bits <= 0 or bits % 4 != 0:
                    return None
                if value < 0 or value >= (1 << bits):
                    return None
            else:
                return None
        seen.add(str(node.get("id") or ""))
    outputs = [item for item in graph.get("outputs", []) if isinstance(item, dict)]
    if len(outputs) != 1:
        return None
    root_id = str(outputs[0].get("node") or "")
    if root_id not in node_by_id:
        return None
    store_sinks = [item for item in graph.get("store_sinks", []) if isinstance(item, dict)]
    for sink in store_sinks:
        if str(sink.get("kind") or "") != "memory-store":
            return None
        if str(sink.get("node") or "") != root_id:
            return None
        if str(sink.get("store_safety_status") or "") != "complete":
            return None
        store_contract = str(sink.get("store_contract") or "")
        if store_contract not in {
            "contiguous-store-pack-v1",
            "static-scatter-store-pack-v1",
            "masked-contiguous-store-pack-v1",
            "masked-static-scatter-store-pack-v1",
            "symbolic-store-pack-v1",
            "masked-symbolic-store-pack-v1",
        }:
            return None
        address_order = sink.get("address_order")
        if not isinstance(address_order, list) or len(address_order) != lanes:
            return None
        if store_contract in {"symbolic-store-pack-v1", "masked-symbolic-store-pack-v1"}:
            address_terms = sink.get("store_address_terms")
            if not isinstance(address_terms, list) or len(address_terms) != lanes:
                return None
            if str(sink.get("store_address_model") or "") != "lane-index-expression-v1":
                return None
            seen_lanes: set[int] = set()
            for lane, term in enumerate(address_terms):
                if not isinstance(term, dict):
                    return None
                kind = str(term.get("kind") or "")
                if kind not in {"static", "symbolic"}:
                    return None
                term_lane = term.get("lane")
                if term_lane != lane or term_lane in seen_lanes:
                    return None
                seen_lanes.add(term_lane)
                if kind == "static" and not isinstance(term.get("index"), int):
                    return None
                if kind == "symbolic" and not str(term.get("index") or "").strip():
                    return None
        else:
            if not all(isinstance(index, int) and index >= 0 for index in address_order):
                return None
            if len(set(address_order)) != len(address_order):
                return None
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    expected_edges: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        for index, operand in enumerate(node.get("operands", [])):
            if str(operand.get("kind") or "") == "node":
                expected_edges.append({"from": str(operand.get("id") or ""), "to": node_id, "operand": index})
    for expected in expected_edges:
        if not any(
            edge.get("from") == expected["from"]
            and edge.get("to") == expected["to"]
            and edge.get("operand") == expected["operand"]
            for edge in edges
        ):
            return None
    lane_mapping = transaction.get("lane_mapping")
    result_lane_mapping = transaction.get("result_lane_mapping")
    if not isinstance(lane_mapping, dict) or not isinstance(result_lane_mapping, dict):
        return None
    raw_lane_map = lane_mapping.get("map")
    raw_result_map = result_lane_mapping.get("map")
    if (
        not isinstance(raw_lane_map, list)
        or len(raw_lane_map) != lanes
        or not all(isinstance(index, int) for index in raw_lane_map)
        or sorted(raw_lane_map) != list(range(lanes))
    ):
        return None
    if (
        not isinstance(raw_result_map, list)
        or len(raw_result_map) != lanes
        or not all(isinstance(index, int) for index in raw_result_map)
        or sorted(raw_result_map) != list(range(lanes))
        or list(raw_result_map) != list(raw_lane_map)
    ):
        return None
    lane_map = list(raw_lane_map)
    raw_inverse = lane_mapping.get("inverse_map")
    if isinstance(raw_inverse, list) and len(raw_inverse) == lanes and all(isinstance(index, int) for index in raw_inverse):
        inverse_map = list(raw_inverse)
    else:
        inverse_map = inverse_permutation(lane_map)
    if sorted(inverse_map) != list(range(lanes)):
        return None

    graph_operands = [
        item for item in graph.get("operands", []) if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    memory_operands = [
        dict(item)
        for item in graph_operands
        if str(item.get("kind") or "") == "memory-pack"
    ]
    memory_alias_conditions = [
        dict(item)
        for item in graph.get("memory_alias_conditions", [])
        if isinstance(item, dict)
    ]
    operand_names = [str(item.get("name") or "") for item in graph_operands]
    if not operand_names:
        operand_names = sorted({
            str(operand.get("name") or "")
            for node in nodes
            for operand in node.get("operands", [])
            if str(operand.get("kind") or "") in {"pack", "memory-pack"} and str(operand.get("name") or "")
        })
    if len(set(operand_names)) != len(operand_names):
        return None
    for operand_record in graph_operands:
        mapping = operand_record.get("mapping")
        if isinstance(mapping, dict):
            operand_map = mapping.get("map")
            if operand_map != lane_map:
                return None
        if str(operand_record.get("kind") or "") == "memory-pack":
            if str(operand_record.get("memory_safety_status") or "") != "complete":
                return None

    operand_record_by_name = {str(item.get("name") or ""): item for item in graph_operands}

    def address_record(base: str, offset: int | str) -> dict[str, Any]:
        record: dict[str, Any] = {"base": base, "symbol": address_symbol(base, offset)}
        if isinstance(offset, int):
            record["offset"] = offset
        else:
            record["index"] = offset
            record["kind"] = "symbolic"
        return record

    def address_expr(base: str, offset: int | str) -> dict[str, Any]:
        return var(address_symbol(base, offset))

    def address_record_for_term(default_base: str, term: Any, fallback_offset: Any = None) -> dict[str, Any] | None:
        if isinstance(term, dict):
            base = str(term.get("base") or default_base or "memory")
            kind = str(term.get("kind") or "")
            index = term.get("index")
            if kind == "static" and isinstance(index, int):
                return address_record(base, index)
            if kind == "symbolic":
                index_text = str(index or "").strip()
                if index_text:
                    return address_record(base, index_text)
            return None
        if isinstance(fallback_offset, int) and fallback_offset >= 0:
            return address_record(default_base, fallback_offset)
        return None

    def address_records_for(
        record: dict[str, Any],
        terms_key: str,
        order_key: str = "address_order",
    ) -> list[dict[str, Any]] | None:
        base = str(record.get("base") or "memory")
        terms = record.get(terms_key)
        order = record.get(order_key)
        if isinstance(terms, list) and len(terms) == lanes:
            result: list[dict[str, Any]] = []
            for lane, term in enumerate(terms):
                fallback = order[lane] if isinstance(order, list) and lane < len(order) else None
                address = address_record_for_term(base, term, fallback)
                if address is None:
                    return None
                result.append(address)
            return result
        if isinstance(order, list) and len(order) == lanes:
            result = []
            for offset in order:
                if not isinstance(offset, int) or offset < 0:
                    return None
                result.append(address_record(base, offset))
            return result
        return None

    def indexed_lane_var(
        operand_record: dict[str, Any],
        operand_key: str,
        order_key: str,
        source_lane: int,
    ) -> dict[str, Any] | None:
        operand_name = str(operand_record.get(operand_key) or "")
        order = operand_record.get(order_key)
        if not operand_name or not isinstance(order, list) or source_lane < 0 or source_lane >= len(order):
            return None
        index = order[source_lane]
        if not isinstance(index, int) or index < 0:
            return None
        return var(f"{operand_name}{index}")

    def mask_condition_operand_expr(text: Any) -> dict[str, Any] | None:
        token = str(text or "").strip()
        if not token:
            return None
        try:
            value = int(token, 0)
            if value < 0 or value >= (1 << 32):
                return None
            return bvconst(value)
        except ValueError:
            pass
        match = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]", token)
        if match:
            return var(f"{match.group(1)}{int(match.group(2))}")
        if re.fullmatch(r"[A-Za-z_]\w*", token):
            return var(token)
        return None

    def canonical_mask_index_name(index: str) -> str | None:
        text = index.strip()
        if not text:
            return None
        if re.fullmatch(r"[A-Za-z_]\w*|\d+", text):
            return text
        if re.search(r"[,\[\].=!?/:]", text):
            return None
        if re.search(r"\b[A-Za-z_]\w*\s*\(", text):
            return None
        if not re.fullmatch(r"(?:[A-Za-z_]\w*|\d+|[()+\-*&|^~<>\s]+)+", text):
            return None
        while text.startswith("(") and text.endswith(")"):
            depth = 0
            balanced_outer = True
            for index, char in enumerate(text):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0 and index != len(text) - 1:
                        balanced_outer = False
                        break
                if depth < 0:
                    return None
            if depth != 0:
                return None
            if not balanced_outer:
                break
            text = text[1:-1].strip()
        replacements = [
            (r"<<", "_shl_"),
            (r">>", "_shr_"),
            (r"\+", "_plus_"),
            (r"-", "_minus_"),
            (r"\*", "_mul_"),
            (r"&", "_and_"),
            (r"\|", "_or_"),
            (r"\^", "_xor_"),
            (r"~", "_not_"),
            (r"[()<> \t\r\n]+", "_"),
        ]
        result = text
        for pattern, replacement in replacements:
            result = re.sub(pattern, replacement, result)
        result = re.sub(r"_+", "_", result).strip("_")
        if not result or not re.fullmatch(r"[A-Za-z_]\w*|\d+", result):
            return None
        return result

    def indexed_mask_variable_name(condition: dict[str, Any]) -> str | None:
        name = str(condition.get("name") or "").strip()
        index = str(condition.get("index") or "").strip()
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            return None
        canonical_index = canonical_mask_index_name(index)
        if canonical_index is None:
            return None
        return f"{name}_{canonical_index}"

    def mask_condition_expr(condition: dict[str, Any]) -> dict[str, Any] | None:
        op_name = str(condition.get("op") or "")
        if op_name == "const":
            value = condition.get("value")
            if value is True:
                return binop("eq", bvconst(1), bvconst(1))
            if value is False:
                return binop("ne", bvconst(1), bvconst(1))
            return None
        if op_name == "opaque-mask":
            name = str(condition.get("name") or condition.get("temp") or "").strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                return None
            return binop("ne", var(name), bvconst(0))
        if op_name == "indexed-mask":
            mask_var = indexed_mask_variable_name(condition)
            if mask_var is None:
                return None
            return binop("ne", var(mask_var), bvconst(0))
        if op_name in {"and", "or"}:
            args = condition.get("args")
            if not isinstance(args, list) or len(args) != 2:
                return None
            left = mask_condition_expr(args[0]) if isinstance(args[0], dict) else None
            right = mask_condition_expr(args[1]) if isinstance(args[1], dict) else None
            if left is None or right is None:
                return None
            return binop(op_name, left, right)
        if op_name == "not":
            args = condition.get("args")
            if not isinstance(args, list) or len(args) != 1:
                return None
            value = mask_condition_expr(args[0]) if isinstance(args[0], dict) else None
            if value is None:
                return None
            return binop("not", value)
        if op_name == "select":
            args = condition.get("args")
            if not isinstance(args, list) or len(args) != 3:
                return None
            cond = mask_condition_expr(args[0]) if isinstance(args[0], dict) else None
            true_value = mask_condition_expr(args[1]) if isinstance(args[1], dict) else None
            false_value = mask_condition_expr(args[2]) if isinstance(args[2], dict) else None
            if cond is None or true_value is None or false_value is None:
                return None
            return ite(cond, true_value, false_value)
        predicate = str(condition.get("predicate") or "")
        if predicate:
            op = icmp_ops.get(predicate)
            left = mask_condition_operand_expr(condition.get("lhs"))
            right = mask_condition_operand_expr(condition.get("rhs"))
            if op is None or left is None or right is None:
                return None
            return binop(str(op), left, right)
        return None

    def mined_mask_condition(operand_record: dict[str, Any], source_lane: int) -> dict[str, Any] | None:
        conditions = operand_record.get("mask_conditions")
        if not isinstance(conditions, list):
            return None
        for condition in conditions:
            if not isinstance(condition, dict) or condition.get("lane") != source_lane:
                continue
            return mask_condition_expr(condition)
        return None

    def common_lane_prefix(names: list[str]) -> str | None:
        prefix: str | None = None
        for lane, name in enumerate(names):
            match = re.fullmatch(r"([A-Za-z_]\w*)" + re.escape(str(lane)), name)
            if match is None:
                return None
            if prefix is None:
                prefix = match.group(1)
            elif prefix != match.group(1):
                return None
        return prefix

    def scalable_condition_operand_expr(conditions: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        tokens = [str(condition.get(key) or "").strip() for condition in conditions]
        if not tokens or any(not token for token in tokens):
            return None
        constants: list[int] = []
        for token in tokens:
            try:
                constants.append(int(token, 0))
            except ValueError:
                constants = []
                break
        if constants:
            if len(set(constants)) != 1 or constants[0] < 0 or constants[0] >= (1 << 32):
                return None
            return {"op": "svsplat", "args": [bvconst(constants[0])]}
        indexed: list[tuple[str, int]] = []
        for token in tokens:
            match = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]", token)
            if match is None:
                indexed = []
                break
            indexed.append((match.group(1), int(match.group(2))))
        if indexed:
            names = {name for name, _ in indexed}
            order = [index for _, index in indexed]
            if len(names) != 1 or any(index < 0 or index >= lanes for index in order):
                return None
            name = indexed[0][0]
            value = svar(name)
            return value if order == list(range(lanes)) else svshuffle(value, order)
        prefix = common_lane_prefix(tokens)
        if prefix:
            return svar(prefix)
        if len(set(tokens)) == 1 and re.fullmatch(r"[A-Za-z_]\w*", tokens[0]):
            return svar(tokens[0])
        return None

    def scalable_tuple_operand(token: Any) -> dict[str, Any] | None:
        text = str(token or "").strip()
        if not text:
            return None
        try:
            value = int(text, 0)
            if value < 0 or value >= (1 << 32):
                return None
            return {"kind": "const", "value": value}
        except ValueError:
            pass
        match = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]", text)
        if match:
            index = int(match.group(2))
            if index < 0 or index >= lanes:
                return None
            return {"kind": "indexed", "name": match.group(1), "index": index}
        match = re.fullmatch(r"([A-Za-z_]\w*)(\d+)", text)
        if match:
            index = int(match.group(2))
            if index < 0 or index >= lanes:
                return None
            return {"kind": "indexed", "name": match.group(1), "index": index}
        if re.fullmatch(r"[A-Za-z_]\w*", text):
            return {"kind": "lane", "name": text}
        return None

    def scalable_tuple_condition(condition: dict[str, Any]) -> dict[str, Any] | None:
        op_name = str(condition.get("op") or "")
        if op_name == "const":
            value = condition.get("value")
            if value is True or value is False:
                return {"op": "const", "value": bool(value)}
            return None
        if op_name == "opaque-mask":
            name = str(condition.get("name") or condition.get("temp") or "").strip()
            operand = scalable_tuple_operand(name)
            if operand is None:
                return None
            return {"op": "icmp", "predicate": "ne", "lhs": operand, "rhs": {"kind": "const", "value": 0}}
        if op_name == "indexed-mask":
            name = str(condition.get("name") or "").strip()
            index = str(condition.get("index") or "").strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                return None
            if index.isdigit():
                static_index = int(index)
                if static_index < 0 or static_index >= lanes:
                    return None
                operand = {"kind": "indexed", "name": name, "index": static_index}
            else:
                mask_var = indexed_mask_variable_name(condition)
                if mask_var is None:
                    return None
                operand = {"kind": "lane", "name": mask_var}
            return {"op": "icmp", "predicate": "ne", "lhs": operand, "rhs": {"kind": "const", "value": 0}}
        if op_name in {"and", "or"}:
            args = condition.get("args")
            if not isinstance(args, list) or len(args) != 2 or not all(isinstance(arg, dict) for arg in args):
                return None
            left = scalable_tuple_condition(args[0])
            right = scalable_tuple_condition(args[1])
            if left is None or right is None:
                return None
            return {"op": op_name, "args": [left, right]}
        if op_name == "not":
            args = condition.get("args")
            if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], dict):
                return None
            value = scalable_tuple_condition(args[0])
            return {"op": "not", "args": [value]} if value is not None else None
        if op_name == "select":
            args = condition.get("args")
            if not isinstance(args, list) or len(args) != 3 or not all(isinstance(arg, dict) for arg in args):
                return None
            values = [scalable_tuple_condition(arg) for arg in args]
            return {"op": "select", "args": values} if all(value is not None for value in values) else None
        predicate = str(condition.get("predicate") or "")
        if predicate in icmp_ops:
            lhs = scalable_tuple_operand(condition.get("lhs"))
            rhs = scalable_tuple_operand(condition.get("rhs"))
            if lhs is None or rhs is None:
                return None
            return {"op": "icmp", "predicate": predicate, "lhs": lhs, "rhs": rhs}
        return None

    def scalable_mask_condition_expr(conditions: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(conditions) != lanes:
            return None
        op_names = [str(condition.get("op") or "") for condition in conditions]
        if all(op_name == "const" for op_name in op_names):
            values = [condition.get("value") for condition in conditions]
            if len(set(values)) != 1 or values[0] not in {True, False}:
                return None
            predicate = "eq" if values[0] is True else "ne"
            one = {"op": "svsplat", "args": [bvconst(1)]}
            return {"op": "svicmp", "predicate": predicate, "args": [one, copy.deepcopy(one)]}
        if all(op_name == "opaque-mask" for op_name in op_names):
            names = [str(condition.get("name") or condition.get("temp") or "").strip() for condition in conditions]
            prefix = common_lane_prefix(names)
            if prefix:
                value = svar(prefix)
            elif len(set(names)) == 1 and re.fullmatch(r"[A-Za-z_]\w*", names[0]):
                value = svar(names[0])
            else:
                return None
            return {"op": "svicmp", "predicate": "ne", "args": [value, {"op": "svsplat", "args": [bvconst(0)]}]}
        if all(op_name == "indexed-mask" for op_name in op_names):
            names = [str(condition.get("name") or "").strip() for condition in conditions]
            indexes = [str(condition.get("index") or "").strip() for condition in conditions]
            if len(set(names)) != 1 or not re.fullmatch(r"[A-Za-z_]\w*", names[0]):
                return None
            order: list[int] = []
            all_static = True
            for index in indexes:
                if not index.isdigit():
                    all_static = False
                    break
                static_index = int(index)
                if static_index < 0 or static_index >= lanes:
                    return None
                order.append(static_index)
            if all_static:
                value = svar(names[0]) if order == list(range(lanes)) else svshuffle(svar(names[0]), order)
            else:
                entries: list[dict[str, Any]] = []
                for index in indexes:
                    if index.isdigit():
                        static_index = int(index)
                        if static_index < 0 or static_index >= lanes:
                            return None
                        entries.append({"kind": "indexed", "name": names[0], "index": static_index})
                        continue
                    mask_var = indexed_mask_variable_name({"name": names[0], "index": index})
                    if mask_var is None:
                        return None
                    entries.append({"kind": "symbolic", "name": mask_var})
                value = {"op": "svindexed_mask", "base_lanes": lanes, "entries": entries}
            return {"op": "svicmp", "predicate": "ne", "args": [value, {"op": "svsplat", "args": [bvconst(0)]}]}
        if all(op_name in {"and", "or"} and op_name == op_names[0] for op_name in op_names):
            child_groups: list[list[dict[str, Any]]] = [[], []]
            for condition in conditions:
                args = condition.get("args")
                if not isinstance(args, list) or len(args) != 2 or not all(isinstance(arg, dict) for arg in args):
                    return None
                child_groups[0].append(args[0])
                child_groups[1].append(args[1])
            left = scalable_mask_condition_expr(child_groups[0])
            right = scalable_mask_condition_expr(child_groups[1])
            if left is None or right is None:
                return None
            return {"op": "svmask_" + op_names[0], "args": [left, right]}
        if all(op_name == "not" for op_name in op_names):
            child_group: list[dict[str, Any]] = []
            for condition in conditions:
                args = condition.get("args")
                if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], dict):
                    return None
                child_group.append(args[0])
            value = scalable_mask_condition_expr(child_group)
            return {"op": "svmask_not", "args": [value]} if value is not None else None
        if all(op_name == "select" for op_name in op_names):
            child_groups: list[list[dict[str, Any]]] = [[], [], []]
            for condition in conditions:
                args = condition.get("args")
                if not isinstance(args, list) or len(args) != 3 or not all(isinstance(arg, dict) for arg in args):
                    return None
                for index, arg in enumerate(args):
                    child_groups[index].append(arg)
            values = [scalable_mask_condition_expr(group) for group in child_groups]
            return {"op": "svmask_select", "args": values} if all(value is not None for value in values) else None
        predicates = [str(condition.get("predicate") or "") for condition in conditions]
        if predicates and all(predicate and predicate == predicates[0] for predicate in predicates):
            if predicates[0] not in icmp_ops:
                return None
            left = scalable_condition_operand_expr(conditions, "lhs")
            right = scalable_condition_operand_expr(conditions, "rhs")
            if left is None or right is None:
                return None
            return {"op": "svicmp", "predicate": predicates[0], "args": [left, right]}
        tuple_entries = [scalable_tuple_condition(condition) for condition in conditions]
        if all(entry is not None for entry in tuple_entries):
            return {"op": "svmask_tuple", "base_lanes": lanes, "entries": tuple_entries}
        return None

    def scalable_effective_mask_conditions(operand_record: dict[str, Any]) -> list[dict[str, Any]] | None:
        conditions = operand_record.get("mask_conditions")
        by_lane: dict[int, dict[str, Any]] = {}
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict):
                    return None
                lane = condition.get("lane")
                if not isinstance(lane, int) or lane < 0 or lane >= lanes or lane in by_lane:
                    return None
                by_lane[lane] = condition
        if by_lane:
            mask_operand = str(operand_record.get("mask_operand") or "").strip()
            mask_order = operand_record.get("mask_order")
            if mask_operand and re.fullmatch(r"[A-Za-z_]\w*", mask_operand) and isinstance(mask_order, list):
                for lane in range(lanes):
                    if lane in by_lane or lane >= len(mask_order):
                        continue
                    index = mask_order[lane]
                    if isinstance(index, int) and index >= 0:
                        by_lane[lane] = {
                            "op": "indexed-mask",
                            "name": mask_operand,
                            "index": str(index),
                            "lane": lane,
                            "source": f"{mask_operand}[{index}]",
                        }
        if set(by_lane) != set(range(lanes)):
            return None
        return [by_lane[lane] for lane in range(lanes)]

    def scalable_mined_mask_condition(operand_record: dict[str, Any]) -> dict[str, Any] | None:
        conditions = scalable_effective_mask_conditions(operand_record)
        if conditions is None:
            return None
        return scalable_mask_condition_expr(conditions)

    def collect_scalable_mask_condition_variables(operand_record: dict[str, Any]) -> set[str]:
        conditions = operand_record.get("mask_conditions")
        if not isinstance(conditions, list):
            return set()
        names: set[str] = set()
        opaque_names: list[str] = []

        def visit_condition(condition: Any) -> None:
            if not isinstance(condition, dict):
                return
            op_name = str(condition.get("op") or "")
            if op_name == "opaque-mask":
                name = str(condition.get("name") or condition.get("temp") or "").strip()
                if name:
                    opaque_names.append(name)
            if op_name == "indexed-mask":
                name = str(condition.get("name") or "").strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    names.add(name)
                mask_var = indexed_mask_variable_name(condition)
                if mask_var is not None:
                    names.add(mask_var)
            for key in ("lhs", "rhs"):
                token = str(condition.get(key) or "").strip()
                match = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*\d+\s*\]", token)
                if match:
                    names.add(match.group(1))
                    continue
                match = re.fullmatch(r"([A-Za-z_]\w*)(\d+)", token)
                if match:
                    names.add(match.group(1))
                    continue
                if re.fullmatch(r"[A-Za-z_]\w*", token):
                    names.add(token)
            args = condition.get("args")
            if isinstance(args, list):
                for arg in args:
                    visit_condition(arg)

        for condition in conditions:
            visit_condition(condition)
        if opaque_names:
            prefix = common_lane_prefix(opaque_names)
            if prefix:
                names.add(prefix)
            elif len(set(opaque_names)) == 1 and re.fullmatch(r"[A-Za-z_]\w*", opaque_names[0]):
                names.add(opaque_names[0])
        return names

    def memory_mask_condition(operand_record: dict[str, Any], source_lane: int) -> dict[str, Any] | None:
        condition = mined_mask_condition(operand_record, source_lane)
        if condition is not None:
            return condition
        mask_value = indexed_lane_var(operand_record, "mask_operand", "mask_order", source_lane)
        if mask_value is None:
            return None
        return binop("ne", mask_value, bvconst(0))

    def masked_memory_load_value(
        operand_record: dict[str, Any],
        source_lane: int,
        loaded_value: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not bool(operand_record.get("masked")):
            return loaded_value
        if str(operand_record.get("masked_lane_policy") or "") != "passthru":
            return None
        condition = memory_mask_condition(operand_record, source_lane)
        passthru_value = indexed_lane_var(operand_record, "passthru_operand", "passthru_order", source_lane)
        if passthru_value is None and str(operand_record.get("passthru_kind") or "") == "symbolic-undef":
            symbols = operand_record.get("passthru_symbols")
            if isinstance(symbols, list) and 0 <= source_lane < len(symbols):
                symbol = str(symbols[source_lane] or "")
                if re.fullmatch(r"[A-Za-z_]\w*", symbol):
                    passthru_value = var(symbol)
        if condition is None or passthru_value is None:
            return None
        return ite(condition, loaded_value, passthru_value)

    def scalable_ordered_operand_value(
        operand_record: dict[str, Any],
        operand_key: str,
        order_key: str,
    ) -> dict[str, Any] | None:
        operand_name = str(operand_record.get(operand_key) or "")
        order = operand_record.get(order_key)
        if not operand_name or not isinstance(order, list) or len(order) != lanes:
            return None
        if not all(isinstance(index, int) and 0 <= index < lanes for index in order):
            return None
        value = svar(operand_name)
        return value if order == list(range(lanes)) else svshuffle(value, list(order))

    def scalable_symbolic_undef_passthru_value(operand_record: dict[str, Any]) -> dict[str, Any] | None:
        if str(operand_record.get("passthru_kind") or "") != "symbolic-undef":
            return None
        symbols = operand_record.get("passthru_symbols")
        if not isinstance(symbols, list) or len(symbols) != lanes:
            return None
        prefix: str | None = None
        for index, raw_symbol in enumerate(symbols):
            symbol = str(raw_symbol or "")
            match = re.fullmatch(r"([A-Za-z_]\w*)" + re.escape(str(index)), symbol)
            if match is None:
                return None
            if prefix is None:
                prefix = match.group(1)
            elif prefix != match.group(1):
                return None
        return svar(prefix) if prefix else None

    def scalable_masked_memory_load_value(
        operand_record: dict[str, Any],
        loaded_value: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not bool(operand_record.get("masked")):
            return loaded_value
        if str(operand_record.get("masked_lane_policy") or "") != "passthru":
            return None
        mask_value = scalable_ordered_operand_value(operand_record, "mask_operand", "mask_order")
        mask = scalable_mined_mask_condition(operand_record)
        passthru_value = scalable_ordered_operand_value(operand_record, "passthru_operand", "passthru_order")
        if passthru_value is None:
            passthru_value = scalable_symbolic_undef_passthru_value(operand_record)
        if mask is None and mask_value is None:
            return None
        if passthru_value is None:
            return None
        if mask is None:
            mask = {
                "op": "svicmp",
                "predicate": "ne",
                "args": [mask_value, {"op": "svsplat", "args": [bvconst(0)]}],
            }
        return {"op": "svselect", "args": [mask, loaded_value, passthru_value]}

    def scalable_mask_value(operand_record: dict[str, Any]) -> dict[str, Any] | None:
        condition = scalable_mined_mask_condition(operand_record)
        if condition is not None:
            return condition
        mask_value = scalable_ordered_operand_value(operand_record, "mask_operand", "mask_order")
        if mask_value is None:
            return None
        return {
            "op": "svicmp",
            "predicate": "ne",
            "args": [mask_value, {"op": "svsplat", "args": [bvconst(0)]}],
        }

    def scalar_expr_for_operand(
        operand: dict[str, Any],
        lane: int,
        packed_frame: bool = False,
        memory_model: bool = False,
    ) -> dict[str, Any] | None:
        kind = str(operand.get("kind") or "")
        if kind in {"pack", "memory-pack"}:
            name = str(operand.get("name") or "")
            if name not in operand_names or lane < 0 or lane >= lanes:
                return None
            source_lane = lane_map[lane] if packed_frame else lane
            if memory_model and kind == "memory-pack":
                operand_record = operand_record_by_name.get(name, {})
                addresses = address_records_for(operand_record, "address_terms")
                if addresses is None or source_lane >= len(addresses):
                    return None
                loaded_value = mem_load(memvar("M"), var(str(addresses[source_lane]["symbol"])))
                return masked_memory_load_value(operand_record, source_lane, loaded_value)
            return var(f"{name}{source_lane}")
        if kind == "node":
            return scalar_expr_for_node(str(operand.get("id") or ""), lane, packed_frame=packed_frame, memory_model=memory_model)
        if kind == "const":
            value = operand.get("value")
            bits = operand.get("bits")
            if isinstance(value, int) and isinstance(bits, int):
                return bvconst(value, bits)
        return None

    def scalar_expr_for_node(
        node_id: str,
        lane: int,
        packed_frame: bool = False,
        memory_model: bool = False,
    ) -> dict[str, Any] | None:
        node = node_by_id.get(node_id)
        if not node:
            return None
        operands = node.get("operands", [])
        opcode = str(node.get("opcode") or "")
        if str(node.get("kind") or "") == "cast":
            value = scalar_expr_for_operand(operands[0], lane, packed_frame=packed_frame, memory_model=memory_model)
            bits = node.get("bits")
            if value is None or opcode not in cast_ops or not isinstance(bits, int):
                return None
            return {"op": str(cast_ops[opcode]), "node_id": node_id, "bits": bits, "args": [value]}
        if str(node.get("kind") or "") == "icmp":
            left = scalar_expr_for_operand(operands[0], lane, packed_frame=packed_frame, memory_model=memory_model)
            right = scalar_expr_for_operand(operands[1], lane, packed_frame=packed_frame, memory_model=memory_model)
            predicate = str(node.get("predicate") or "")
            if left is None or right is None or predicate not in icmp_ops:
                return None
            return {**binop(str(icmp_ops[predicate]), left, right), "node_id": node_id}
        if str(node.get("kind") or "") == "select":
            condition = scalar_expr_for_operand(operands[0], lane, packed_frame=packed_frame, memory_model=memory_model)
            then_value = scalar_expr_for_operand(operands[1], lane, packed_frame=packed_frame, memory_model=memory_model)
            else_value = scalar_expr_for_operand(operands[2], lane, packed_frame=packed_frame, memory_model=memory_model)
            if condition is None or then_value is None or else_value is None:
                return None
            return {**ite(condition, then_value, else_value), "node_id": node_id}
        if str(node.get("kind") or "") == "shuffle":
            mask = node.get("mask")
            if not isinstance(mask, list) or lane >= len(mask) or not isinstance(mask[lane], int):
                return None
            source_index = mask[lane]
            source_operand_index = source_index // lanes
            source_lane = source_index % lanes
            if source_operand_index >= len(operands):
                return None
            return scalar_expr_for_operand(operands[source_operand_index], source_lane, packed_frame=True, memory_model=memory_model)
        if str(node.get("kind") or "") == "extract":
            index = node.get("index")
            if not isinstance(index, int):
                return None
            return scalar_expr_for_operand(operands[0], index, packed_frame=True, memory_model=memory_model)
        if str(node.get("kind") or "") == "insert":
            index = node.get("index")
            if not isinstance(index, int):
                return None
            if lane == index:
                return scalar_expr_for_operand(operands[1], lane, packed_frame=packed_frame, memory_model=memory_model)
            return scalar_expr_for_operand(operands[0], lane, packed_frame=packed_frame, memory_model=memory_model)
        left = scalar_expr_for_operand(operands[0], lane, packed_frame=packed_frame, memory_model=memory_model)
        right = scalar_expr_for_operand(operands[1], lane, packed_frame=packed_frame, memory_model=memory_model)
        if left is None or right is None:
            return None
        if opcode in minmax_cmp_ops:
            cmp_expr = {**binop(str(minmax_cmp_ops[opcode]), left, right), "node_id": node_id}
            return {**ite(cmp_expr, left, right), "node_id": node_id}
        if opcode not in scalar_ops:
            return None
        return {**binop(str(scalar_ops[opcode]), left, right), "node_id": node_id}

    def vector_expr_for_operand(
        operand: dict[str, Any],
        apply_lane_map: bool = True,
        memory_model: bool = False,
    ) -> dict[str, Any] | None:
        kind = str(operand.get("kind") or "")
        if kind in {"pack", "memory-pack"}:
            name = str(operand.get("name") or "")
            if name not in operand_names:
                return None
            if is_scalable_transaction:
                raw_value = svar(name)
                if kind == "memory-pack":
                    raw_value = scalable_masked_memory_load_value(operand_record_by_name.get(name, {}), raw_value)
                    if raw_value is None:
                        return None
                return raw_value if not apply_lane_map or lane_map == list(range(lanes)) else svshuffle(raw_value, lane_map)
            if memory_model and kind == "memory-pack":
                operand_record = operand_record_by_name.get(name, {})
                addresses = address_records_for(operand_record, "address_terms")
                if addresses is None:
                    return None
                raw_lanes = []
                for source_lane, address in enumerate(addresses):
                    loaded_value = mem_load(memvar("M"), var(str(address["symbol"])))
                    lane_value = masked_memory_load_value(operand_record, source_lane, loaded_value)
                    if lane_value is None:
                        return None
                    raw_lanes.append(lane_value)
                raw_value = {"op": "vec", "args": raw_lanes}
            else:
                raw_value = vector_value(name, lanes)
            return raw_value if not apply_lane_map or lane_map == list(range(lanes)) else vshuffle(raw_value, lane_map)
        if kind == "node":
            return vector_expr_for_node(str(operand.get("id") or ""), apply_lane_map=apply_lane_map, memory_model=memory_model)
        if kind == "const":
            value = operand.get("value")
            bits = operand.get("bits")
            if isinstance(value, int) and isinstance(bits, int):
                splat = "svsplat" if is_scalable_transaction else "vsplat"
                return {"op": splat, "args": [bvconst(value, bits)]}
        return None

    def vector_scalar_expr_for_operand(
        operand: dict[str, Any],
        lane: int,
        apply_lane_map: bool = True,
        memory_model: bool = False,
    ) -> dict[str, Any] | None:
        kind = str(operand.get("kind") or "")
        if kind == "node":
            value = vector_expr_for_node(str(operand.get("id") or ""), apply_lane_map=apply_lane_map, memory_model=memory_model)
            return value
        if kind in {"pack", "memory-pack"}:
            value = vector_expr_for_operand(operand, apply_lane_map=apply_lane_map, memory_model=memory_model)
            if value is None:
                return None
            return svextract(value, lane) if is_scalable_transaction else vextract(value, lane)
        if kind == "const":
            value = operand.get("value")
            bits = operand.get("bits")
            if isinstance(value, int) and isinstance(bits, int):
                return bvconst(value, bits)
        return None

    def vector_expr_for_node(
        node_id: str,
        apply_lane_map: bool = True,
        memory_model: bool = False,
    ) -> dict[str, Any] | None:
        node = node_by_id.get(node_id)
        if not node:
            return None
        operands = node.get("operands", [])
        opcode = str(node.get("opcode") or "")
        if str(node.get("kind") or "") == "cast":
            value = vector_expr_for_operand(operands[0], apply_lane_map=apply_lane_map, memory_model=memory_model)
            bits = node.get("bits")
            vector_cast_ops = {"zext": "vzext", "sext": "vsext", "trunc": "vtrunc"}
            if value is None or opcode not in vector_cast_ops or not isinstance(bits, int):
                return None
            return {"op": str(vector_cast_ops[opcode]), "node_id": node_id, "bits": bits, "args": [value]}
        if str(node.get("kind") or "") == "icmp":
            left = vector_expr_for_operand(operands[0], apply_lane_map=apply_lane_map, memory_model=memory_model)
            right = vector_expr_for_operand(operands[1], apply_lane_map=apply_lane_map, memory_model=memory_model)
            predicate = str(node.get("predicate") or "")
            if left is None or right is None or predicate not in icmp_ops:
                return None
            return {
                "op": "svicmp" if is_scalable_transaction else "vicmp",
                "node_id": node_id,
                "predicate": predicate,
                "args": [left, right],
            }
        if str(node.get("kind") or "") == "select":
            condition = vector_expr_for_operand(operands[0], apply_lane_map=apply_lane_map, memory_model=memory_model)
            then_value = vector_expr_for_operand(operands[1], apply_lane_map=apply_lane_map, memory_model=memory_model)
            else_value = vector_expr_for_operand(operands[2], apply_lane_map=apply_lane_map, memory_model=memory_model)
            if condition is None or then_value is None or else_value is None:
                return None
            return {
                "op": "svselect" if is_scalable_transaction else "vselect",
                "node_id": node_id,
                "args": [condition, then_value, else_value],
            }
        if str(node.get("kind") or "") == "shuffle":
            if is_scalable_transaction:
                args = [
                    vector_expr_for_operand(operand, apply_lane_map=apply_lane_map, memory_model=memory_model)
                    for operand in operands
                ]
                base_mask = node.get("base_mask")
                if any(arg is None for arg in args) or not isinstance(base_mask, list):
                    return None
                return {"op": "svshuffle", "node_id": node_id, "base_mask": list(base_mask), "args": args}
            args = [vector_expr_for_operand(operand, apply_lane_map=apply_lane_map, memory_model=memory_model) for operand in operands]
            mask = node.get("mask")
            if any(arg is None for arg in args) or not isinstance(mask, list):
                return None
            return {"op": "vshuffle", "node_id": node_id, "mask": list(mask), "args": args}
        if str(node.get("kind") or "") == "extract":
            value = vector_expr_for_operand(operands[0], apply_lane_map=apply_lane_map, memory_model=memory_model)
            index = node.get("index")
            if value is None or not isinstance(index, int):
                return None
            op = "svextract" if is_scalable_transaction else "vextract"
            return {"op": op, "node_id": node_id, "index": index, "args": [value]}
        if str(node.get("kind") or "") == "insert":
            value = vector_expr_for_operand(operands[0], apply_lane_map=apply_lane_map, memory_model=memory_model)
            index = node.get("index")
            if not isinstance(index, int):
                return None
            lane_value = vector_scalar_expr_for_operand(operands[1], index, apply_lane_map=apply_lane_map, memory_model=memory_model)
            if value is None or lane_value is None or not isinstance(index, int):
                return None
            op = "svinsert" if is_scalable_transaction else "vinsert"
            return {"op": op, "node_id": node_id, "index": index, "args": [value, lane_value]}
        left = vector_expr_for_operand(operands[0], apply_lane_map=apply_lane_map, memory_model=memory_model)
        right = vector_expr_for_operand(operands[1], apply_lane_map=apply_lane_map, memory_model=memory_model)
        op_table = scalable_vector_ops if is_scalable_transaction else vector_ops
        if left is None or right is None or opcode not in op_table:
            return None
        return {"op": str(op_table[opcode]), "node_id": node_id, "args": [left, right]}

    if is_scalable_transaction:
        if any(str(node.get("kind") or "") == "shuffle" for node in nodes) and lane_map != list(range(lanes)):
            before = vector_expr_for_node(root_id, apply_lane_map=True)
            before = svshuffle(before, inverse_map) if before is not None else None
        else:
            before = vector_expr_for_node(root_id, apply_lane_map=False)
    else:
        before_lanes = []
        for index in range(lanes):
            lane_expr = scalar_expr_for_node(root_id, inverse_map[index], packed_frame=True)
            if lane_expr is None:
                return None
            before_lanes.append(lane_expr)
        before = {"op": "vec", "args": before_lanes}
    if before is None:
        return None
    after = vector_expr_for_node(root_id)
    if after is None:
        return None
    if lane_map != list(range(lanes)):
        after = svshuffle(after, inverse_map) if is_scalable_transaction else vshuffle(after, inverse_map)
    memory_model_enabled = bool(store_sinks) and not is_scalable_transaction
    scalable_store_model_enabled = bool(store_sinks) and is_scalable_transaction
    if scalable_store_model_enabled and len(store_sinks) != 1:
        return None
    if scalable_store_model_enabled and bool(store_sinks[0].get("masked")):
        if str(store_sinks[0].get("masked_lane_policy") or "") != "preserve-old-memory":
            return None
        store_mask = scalable_mask_value(store_sinks[0])
        if store_mask is None:
            return None
        store_base = str(store_sinks[0].get("base") or "store")
        old_store_name = "old_" + re.sub(r"\W+", "_", store_base).strip("_")
        if old_store_name == "old_":
            old_store_name = "old_store"
        old_store_value = svar(old_store_name)
        before = {"op": "svselect", "args": [store_mask, before, old_store_value]}
        after = {"op": "svselect", "args": [copy.deepcopy(store_mask), after, old_store_value]}
    memory_observable_addresses: list[dict[str, Any]] = []
    memory_address_records: dict[str, dict[str, Any]] = {}
    memory_alias_assumptions: list[dict[str, Any]] = []
    if memory_model_enabled:
        if len(store_sinks) != 1:
            return None
        store_addresses = address_records_for(store_sinks[0], "store_address_terms")
        if store_addresses is None:
            return None
        store_base = str(store_sinks[0].get("base") or "store")
        memory_bases = {store_base}
        for operand_record in memory_operands:
            base = str(operand_record.get("base") or "")
            if base:
                memory_bases.add(base)
                if base != store_base and not any(
                    str(condition.get("status") or "") == "complete"
                    and str(condition.get("relation") or "") == "noalias"
                    and {
                        str(condition.get("left_base") or ""),
                        str(condition.get("right_base") or ""),
                    } == {base, store_base}
                    for condition in memory_alias_conditions
                ):
                    return None
        def remember_address(record: dict[str, Any]) -> dict[str, Any]:
            memory_address_records[record["symbol"]] = record
            return record
        for operand_record in memory_operands:
            load_addresses = address_records_for(operand_record, "address_terms")
            if load_addresses is not None:
                for address in load_addresses:
                    remember_address(address)
        for address in store_addresses:
            remembered = remember_address(address)
            memory_observable_addresses.append(remembered)
        complete_noalias_pairs = {
            tuple(sorted((str(condition.get("left_base") or ""), str(condition.get("right_base") or ""))))
            for condition in memory_alias_conditions
            if str(condition.get("status") or "") == "complete"
            and str(condition.get("relation") or "") == "noalias"
        }
        for left_base, right_base in sorted(complete_noalias_pairs):
            if not left_base or not right_base or left_base == right_base:
                continue
            left_records = [record for record in memory_address_records.values() if record["base"] == left_base]
            right_records = [record for record in memory_address_records.values() if record["base"] == right_base]
            for left in left_records:
                for right in right_records:
                    memory_alias_assumptions.append({
                        "op": "addr-diseq",
                        "left": left["symbol"],
                        "right": right["symbol"],
                        "reason": "source-noalias",
                    })
        after_value = vector_expr_for_node(root_id, memory_model=True)
        if after_value is None:
            return None
        if lane_map != list(range(lanes)):
            after_value = vshuffle(after_value, inverse_map)
        before_memory = memvar("M")
        after_memory = memvar("M")
        for lane, address in enumerate(store_addresses):
            before_value = scalar_expr_for_node(root_id, inverse_map[lane], packed_frame=True, memory_model=True)
            after_lane_value = vextract(after_value, lane)
            if before_value is None:
                return None
            store_address = var(str(address["symbol"]))
            if bool(store_sinks[0].get("masked")):
                if str(store_sinks[0].get("masked_lane_policy") or "") != "preserve-old-memory":
                    return None
                condition = memory_mask_condition(store_sinks[0], lane)
                if condition is None:
                    return None
                old_before = mem_load(before_memory, store_address)
                old_after = mem_load(after_memory, store_address)
                before_value = ite(condition, before_value, old_before)
                after_lane_value = ite(condition, after_lane_value, old_after)
            before_memory = mem_store(before_memory, store_address, before_value)
            after_memory = mem_store(after_memory, store_address, after_lane_value)
        before = {
            "op": "vec",
            "args": [mem_load(before_memory, var(str(address["symbol"]))) for address in memory_observable_addresses],
        }
        after = {
            "op": "vec",
            "args": [mem_load(after_memory, var(str(address["symbol"]))) for address in memory_observable_addresses],
        }
    scalar_lane_pairs = [
        {
            "lane": index,
            **{name: f"{name}{index}" for name in operand_names},
            "result": f"r{index}",
        }
        for index in range(lanes)
    ]
    graph.setdefault("scalar_lane_pairs", scalar_lane_pairs)
    if is_scalable_transaction:
        extra_scalable_variables: set[str] = set()
        for item in memory_operands:
            if not bool(item.get("masked")):
                continue
            for operand_key in ("mask_operand", "passthru_operand"):
                operand_name = str(item.get(operand_key) or "")
                if operand_name:
                    extra_scalable_variables.add(operand_name)
            passthru_value = scalable_symbolic_undef_passthru_value(item)
            if isinstance(passthru_value, dict):
                passthru_name = str(passthru_value.get("name") or "")
                if passthru_name:
                    extra_scalable_variables.add(passthru_name)
            extra_scalable_variables.update(collect_scalable_mask_condition_variables(item))
        if scalable_store_model_enabled and bool(store_sinks[0].get("masked")):
            store_base = str(store_sinks[0].get("base") or "store")
            old_store_name = "old_" + re.sub(r"\W+", "_", store_base).strip("_")
            if old_store_name == "old_":
                old_store_name = "old_store"
            extra_scalable_variables.add(old_store_name)
            mask_operand = str(store_sinks[0].get("mask_operand") or "")
            if mask_operand:
                extra_scalable_variables.add(mask_operand)
            extra_scalable_variables.update(collect_scalable_mask_condition_variables(store_sinks[0]))
        variables = operand_names + sorted(name for name in extra_scalable_variables if name not in operand_names)
        formal = {
            "domain": "scalable-vector-bv32",
            "base_lanes": base_lanes,
            "vscale_values": list(vscale_values),
            "variables": variables,
            "poison_variables": variables,
            "before": before,
            "after": after,
            "equivalence": "observable-result" if scalable_store_model_enabled else "vector-result",
            "refinement": "refinement",
        }
    else:
        variables = [f"{name}{index}" for name in operand_names for index in range(lanes)]
        if memory_model_enabled:
            extra_lane_names: set[str] = set()
            for item in memory_operands + store_sinks:
                for operand_key, order_key in (("mask_operand", "mask_order"), ("passthru_operand", "passthru_order")):
                    operand_name = str(item.get(operand_key) or "")
                    order = item.get(order_key)
                    if operand_name and isinstance(order, list):
                        for index in order:
                            if isinstance(index, int) and index >= 0:
                                extra_lane_names.add(f"{operand_name}{index}")
                passthru_symbols = item.get("passthru_symbols")
                if isinstance(passthru_symbols, list):
                    for symbol in passthru_symbols:
                        name = str(symbol or "").strip()
                        if re.fullmatch(r"[A-Za-z_]\w*", name):
                            extra_lane_names.add(name)
                conditions = item.get("mask_conditions")
                if isinstance(conditions, list):
                    def collect_condition_vars(condition: Any) -> None:
                        if not isinstance(condition, dict):
                            return
                        args = condition.get("args")
                        if isinstance(args, list):
                            for child in args:
                                collect_condition_vars(child)
                        for key in ("lhs", "rhs"):
                            token = str(condition.get(key) or "").strip()
                            match = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]", token)
                            if match:
                                extra_lane_names.add(f"{match.group(1)}{int(match.group(2))}")
                            elif re.fullmatch(r"[A-Za-z_]\w*", token):
                                extra_lane_names.add(token)
                        if str(condition.get("op") or "") == "opaque-mask":
                            name = str(condition.get("name") or condition.get("temp") or "").strip()
                            if re.fullmatch(r"[A-Za-z_]\w*", name):
                                extra_lane_names.add(name)
                        if str(condition.get("op") or "") == "indexed-mask":
                            mask_var = indexed_mask_variable_name(condition)
                            if mask_var is not None:
                                extra_lane_names.add(mask_var)
                    for condition in conditions:
                        collect_condition_vars(condition)
            for name in sorted(extra_lane_names):
                if name not in variables:
                    variables.append(name)
        variable_bits = {name: 32 for name in variables}
        variable_sorts = {}
        if memory_model_enabled:
            memory_operand_names = {
                str(item.get("name") or "")
                for item in memory_operands
                if str(item.get("kind") or "") == "memory-pack"
            }
            variables = [
                name
                for name in variables
                if not any(name == f"{operand}{index}" for operand in memory_operand_names for index in range(lanes))
            ]
            variable_bits = {name: 32 for name in variables}
            variables.append("M")
            variable_sorts["M"] = "memory-bv32"
            for symbol in sorted(memory_address_records):
                if symbol not in variables:
                    variables.append(symbol)
                    variable_bits[symbol] = 32
        formal = {
            "domain": "vector-bv32xN",
            "vector_width": lanes,
            "variables": variables,
            "variable_bits": variable_bits,
            "poison_variables": [name for name in variables if name != "M"],
            "before": before,
            "after": after,
            "equivalence": "observable-result" if memory_model_enabled else "vector-result",
            "refinement": "refinement",
        }
        if memory_alias_assumptions:
            formal["assumptions"] = memory_alias_assumptions
        if variable_sorts:
            formal["variable_sorts"] = variable_sorts
    parameters: dict[str, Any] = {
        "transaction.model": "optimization-transaction-v1",
        "transaction.kind": str(transaction.get("kind") or ""),
        "transaction.opcode": str(transaction.get("opcode") or ""),
        "transaction.lanes": lanes,
        "transaction.vector_width": lanes,
        "transaction.graph.model": "optimization-transaction-graph-v1",
        "transaction.graph.kind": "slp-binop-chain",
        "transaction.graph.nodes": copy.deepcopy(nodes),
        "transaction.graph.edges": copy.deepcopy(edges),
        "transaction.graph.node_count": len(nodes),
        "transaction.graph.edge_count": len(edges),
        "transaction.graph.root": root_id,
        "transaction.graph.root_opcode": str(node_by_id[root_id].get("opcode") or ""),
        "transaction.lane_mapping": dict(lane_mapping),
        "transaction.lane_mapping.kind": str(lane_mapping.get("kind") or ("identity" if lane_map == list(range(lanes)) else "permutation")),
        "transaction.lane_mapping.map": lane_map,
        "transaction.lane_mapping.inverse_map": inverse_map,
        "transaction.result_lane_mapping": dict(result_lane_mapping),
        "transaction.scalar_lane_pairs": scalar_lane_pairs,
        "transaction.consistency": str(transaction.get("consistency") or "ok"),
    }
    if any(str(node.get("kind") or "") == "shuffle" for node in nodes):
        parameters["transaction.graph.shuffle_mask_frame"] = "packed-vector-frame"
    if any(str(node.get("kind") or "") in {"extract", "insert"} for node in nodes):
        parameters["transaction.graph.lane_index_frame"] = "packed-vector-frame"
    memory_operands = [
        dict(item)
        for item in graph_operands
        if str(item.get("kind") or "") == "memory-pack"
    ]
    if memory_operands:
        memory_contracts = [
            str(item.get("memory_contract") or "contiguous-load-pack-v1")
            for item in memory_operands
        ]
        if "masked-symbolic-gather-pack-v1" in memory_contracts:
            parameters["transaction.graph.memory_contract"] = "masked-symbolic-gather-pack-v1"
        elif "symbolic-gather-pack-v1" in memory_contracts:
            parameters["transaction.graph.memory_contract"] = "symbolic-gather-pack-v1"
        elif "masked-static-gather-pack-v1" in memory_contracts:
            parameters["transaction.graph.memory_contract"] = "masked-static-gather-pack-v1"
        elif "masked-contiguous-load-pack-v1" in memory_contracts:
            parameters["transaction.graph.memory_contract"] = "masked-contiguous-load-pack-v1"
        elif "static-gather-pack-v1" in memory_contracts:
            parameters["transaction.graph.memory_contract"] = "static-gather-pack-v1"
        else:
            parameters["transaction.graph.memory_contract"] = "contiguous-load-pack-v1"
        parameters["transaction.graph.memory_lane_frame"] = "packed-vector-frame"
        parameters["transaction.graph.memory_operands"] = copy.deepcopy(memory_operands)
        if any(str(item.get("memory_address_model") or "") for item in memory_operands):
            parameters["transaction.graph.memory_address_model"] = "lane-index-expression-v1"
        if is_scalable_transaction:
            parameters["transaction.graph.scalable_memory_pack"] = True
        if any(bool(item.get("masked")) for item in memory_operands):
            parameters["transaction.graph.masked_memory"] = True
            if is_scalable_transaction:
                parameters["transaction.graph.scalable_masked_memory_pack"] = True
        parameters["transaction.graph.memory_safety_status"] = "complete"
        parameters["transaction.graph.memory_side_conditions"] = [
            copy.deepcopy(item.get("memory_side_conditions", {}))
            for item in memory_operands
        ]
        parameters["transaction.graph.memory_effect_window"] = [
            str(item.get("memory_effect_window") or "")
            for item in memory_operands
        ]
    if store_sinks:
        store_contracts = [
            str(item.get("store_contract") or "contiguous-store-pack-v1")
            for item in store_sinks
        ]
        if "masked-symbolic-store-pack-v1" in store_contracts:
            parameters["transaction.graph.store_contract"] = "masked-symbolic-store-pack-v1"
        elif "symbolic-store-pack-v1" in store_contracts:
            parameters["transaction.graph.store_contract"] = "symbolic-store-pack-v1"
        elif "masked-static-scatter-store-pack-v1" in store_contracts:
            parameters["transaction.graph.store_contract"] = "masked-static-scatter-store-pack-v1"
        elif "masked-contiguous-store-pack-v1" in store_contracts:
            parameters["transaction.graph.store_contract"] = "masked-contiguous-store-pack-v1"
        elif "static-scatter-store-pack-v1" in store_contracts:
            parameters["transaction.graph.store_contract"] = "static-scatter-store-pack-v1"
        else:
            parameters["transaction.graph.store_contract"] = "contiguous-store-pack-v1"
        parameters["transaction.graph.store_lane_frame"] = "packed-vector-frame"
        parameters["transaction.graph.store_sinks"] = copy.deepcopy(store_sinks)
        if any(bool(item.get("masked")) for item in store_sinks):
            parameters["transaction.graph.masked_memory"] = True
        parameters["transaction.graph.store_safety_status"] = "complete"
        parameters["transaction.graph.store_side_conditions"] = [
            copy.deepcopy(item.get("store_side_conditions", {}))
            for item in store_sinks
        ]
        parameters["transaction.graph.store_effect_window"] = [
            str(item.get("store_effect_window") or "")
            for item in store_sinks
        ]
        if memory_model_enabled:
            parameters["transaction.graph.memory_model"] = "bounded-lane-memory-v1"
            if (
                any(str(item.get("store_address_model") or "") == "lane-index-expression-v1" for item in store_sinks)
                or any(str(item.get("memory_address_model") or "") == "lane-index-expression-v1" for item in memory_operands)
            ):
                parameters["transaction.graph.memory_address_model"] = "lane-index-expression-v1"
                if any(str(item.get("store_address_model") or "") == "lane-index-expression-v1" for item in store_sinks):
                    parameters["transaction.graph.store_address_model"] = "lane-index-expression-v1"
            else:
                parameters["transaction.graph.memory_address_model"] = "base-offset-addresses-v1"
            parameters["transaction.graph.observable_addresses"] = copy.deepcopy(memory_observable_addresses)
            parameters["transaction.graph.memory_bases"] = sorted({
                str(record.get("base") or "")
                for record in memory_address_records.values()
                if str(record.get("base") or "")
            })
            parameters["transaction.graph.memory_alias_conditions"] = copy.deepcopy(memory_alias_conditions)
            parameters["transaction.graph.memory_before"] = "M"
            parameters["transaction.graph.memory_after"] = "M"
        elif scalable_store_model_enabled:
            parameters["transaction.graph.scalable_store_sink"] = True
            if any(bool(item.get("masked")) for item in store_sinks):
                parameters["transaction.graph.scalable_masked_store_sink"] = True
            parameters["transaction.graph.memory_model"] = "bounded-scalable-lane-memory-v1"
            parameters["transaction.graph.memory_address_model"] = "base-offset-addresses-v1"
            parameters["transaction.graph.memory_alias_conditions"] = copy.deepcopy(memory_alias_conditions)
    if is_scalable_transaction:
        parameters["transaction.scalable"] = True
        parameters["transaction.base_lanes"] = base_lanes
        parameters["transaction.vscale_values"] = list(vscale_values)
        scalable_lane_mapping = dict(lane_mapping)
        scalable_lane_mapping["base_lanes"] = base_lanes
        scalable_lane_mapping["vscale_values"] = list(vscale_values)
        parameters["transaction.scalable_lane_mapping"] = scalable_lane_mapping
    for key in ("functions", "role_provenance", "opcode_sources"):
        value = transaction.get(key)
        if isinstance(value, list):
            parameters[f"transaction.{key}"] = [
                dict(item) if isinstance(item, dict) else str(item)
                for item in value
                if isinstance(item, dict) or str(item)
            ]
    return transaction_formal_result(transaction, formal, parameters)


def transaction_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    transaction = finding.get("optimization_transaction")
    if not isinstance(transaction, dict):
        return None
    if transaction.get("model") != "optimization-transaction-v1":
        return None
    kind = str(transaction.get("kind") or "")
    if kind not in {"slp-vectorize-binop", "slp-vectorize-minmax", "slp-vectorize-reduction"}:
        return None
    if str(transaction.get("consistency") or "ok") != "ok":
        return None
    source_program_graph = transaction.get("source_program_graph")
    if isinstance(source_program_graph, dict):
        contract = source_graph_contract_summary(source_program_graph)
        if contract["status"] != "passed":
            failed_checks = set(contract.get("failed_checks") or [])
            only_missing_interprocedural = failed_checks == {
                "source-graph:interprocedural-dfg"
            }
            if (
                not only_missing_interprocedural
                or source_program_graph.get("interprocedural_dfg") is True
            ):
                return None
    graph = transaction.get("transaction_graph")
    if isinstance(graph, dict):
        graph_result = transaction_graph_formal_for(transaction, graph)
        if graph_result is not None:
            return graph_result
    opcode = str(transaction.get("opcode") or "")
    op_names = {
        "add": "bvadd",
        "sub": "bvsub",
        "mul": "bvmul",
        "xor": "bvxor",
        "or": "bvor",
        "and": "bvand",
    }
    minmax_cmp_ops = {
        "smin": "bvslt",
        "smax": "bvsgt",
        "umin": "bvult",
        "umax": "bvugt",
    }
    vector_ops = {
        "add": "vadd",
        "sub": "vsub",
        "mul": "vmul",
        "xor": "vxor",
        "or": "vor",
        "and": "vand",
        "smin": "vsmin",
        "smax": "vsmax",
        "umin": "vumin",
        "umax": "vumax",
    }
    scalable_vector_ops = {
        "add": "svadd",
        "sub": "svsub",
        "mul": "svmul",
        "xor": "svxor",
        "or": "svor",
        "and": "svand",
        "smin": "svsmin",
        "smax": "svsmax",
        "umin": "svumin",
        "umax": "svumax",
    }
    reduction_scalar_ops = {
        "add": "bvadd",
        "mul": "bvmul",
        "and": "bvand",
        "or": "bvor",
        "xor": "bvxor",
        "fadd": "fpadd",
        "fmul": "fpmul",
    }
    reduction_cmp_ops = {
        "smin": "bvslt",
        "smax": "bvsgt",
        "umin": "bvult",
        "umax": "bvugt",
    }
    reduction_ops = {
        "add": "vreduce_add",
        "mul": "vreduce_mul",
        "and": "vreduce_and",
        "or": "vreduce_or",
        "xor": "vreduce_xor",
        "smin": "vreduce_smin",
        "smax": "vreduce_smax",
        "umin": "vreduce_umin",
        "umax": "vreduce_umax",
        "fadd": "fpreduce_add",
        "fmul": "fpreduce_mul",
    }
    scalable_reduction_ops = {
        "add": "svreduce_add",
        "mul": "svreduce_mul",
        "and": "svreduce_and",
        "or": "svreduce_or",
        "xor": "svreduce_xor",
        "smin": "svreduce_smin",
        "smax": "svreduce_smax",
        "umin": "svreduce_umin",
        "umax": "svreduce_umax",
        "fadd": "svfpreduce_add",
        "fmul": "svfpreduce_mul",
    }
    scalar_op = op_names.get(opcode)
    cmp_op = minmax_cmp_ops.get(opcode)
    vector_op = vector_ops.get(opcode)
    scalable_vector_op = scalable_vector_ops.get(opcode)
    reduction_scalar_op = reduction_scalar_ops.get(opcode)
    reduction_cmp_op = reduction_cmp_ops.get(opcode)
    reduction_op = reduction_ops.get(opcode)
    scalable_reduction_op = scalable_reduction_ops.get(opcode)
    lanes = int(transaction.get("lanes") or 0)
    is_scalable_transaction = transaction.get("scalable") is True
    base_lanes = int(transaction.get("base_lanes") or lanes or 0)
    vscale_values = transaction.get("vscale_values") if isinstance(transaction.get("vscale_values"), list) else [1, 2, 4]
    if kind == "slp-vectorize-reduction":
        if is_scalable_transaction:
            if base_lanes <= 0:
                return None
            lanes = base_lanes
        elif lanes not in {2, 4, 8, 16, 32, 64}:
            return None
    elif is_scalable_transaction:
        if base_lanes <= 0:
            return None
        lanes = base_lanes
    elif lanes not in {2, 4, 8, 16, 32, 64}:
        return None
    if kind == "slp-vectorize-binop" and scalar_op is None:
        return None
    if kind == "slp-vectorize-minmax" and cmp_op is None:
        return None
    if kind == "slp-vectorize-reduction" and reduction_op is None:
        return None
    if kind == "slp-vectorize-reduction" and is_scalable_transaction and scalable_reduction_op is None:
        return None
    if kind != "slp-vectorize-reduction" and vector_op is None:
        return None
    if kind != "slp-vectorize-reduction" and is_scalable_transaction and scalable_vector_op is None:
        return None
    lane_mapping = transaction.get("lane_mapping")
    operand_lane_mappings = transaction.get("operand_lane_mappings")
    result_lane_mapping = transaction.get("result_lane_mapping")
    scalar_lane_pairs = transaction.get("scalar_lane_pairs")
    lane_map = list(range(lanes))
    inverse_map = list(range(lanes))
    lane_mapping_kind = "identity"
    if isinstance(operand_lane_mappings, dict) and kind == "slp-vectorize-reduction":
        lhs_mapping = operand_lane_mappings.get("lhs")
        if not isinstance(lhs_mapping, dict):
            return None
        lhs_map = lhs_mapping.get("map")
        if not isinstance(lhs_map, list):
            return None
        lane_mapping = lhs_mapping
    elif isinstance(operand_lane_mappings, dict):
        lhs_mapping = operand_lane_mappings.get("lhs")
        rhs_mapping = operand_lane_mappings.get("rhs")
        if not isinstance(lhs_mapping, dict) or not isinstance(rhs_mapping, dict):
            return None
        lhs_map = lhs_mapping.get("map")
        rhs_map = rhs_mapping.get("map")
        if not isinstance(lhs_map, list) or not isinstance(rhs_map, list):
            return None
        if lhs_map != rhs_map:
            return None
        lane_mapping = lhs_mapping
    if isinstance(lane_mapping, dict):
        raw_map = lane_mapping.get("map")
        if not isinstance(raw_map, list) or len(raw_map) != lanes or not all(isinstance(index, int) for index in raw_map):
            return None
        if sorted(raw_map) != list(range(lanes)):
            return None
        lane_map = list(raw_map)
        raw_inverse = lane_mapping.get("inverse_map")
        if isinstance(raw_inverse, list) and len(raw_inverse) == lanes and all(isinstance(index, int) for index in raw_inverse):
            inverse_map = list(raw_inverse)
        else:
            inverse_map = inverse_permutation(lane_map)
        if sorted(inverse_map) != list(range(lanes)):
            return None
        lane_mapping_kind = str(lane_mapping.get("kind") or ("identity" if lane_map == list(range(lanes)) else "permutation"))
        if lane_mapping_kind not in {"identity", "permutation"}:
            return None
    if kind == "slp-vectorize-reduction":
        input_bits = int(transaction.get("reduction_input_bits") or 32)
        accumulator_bits = int(transaction.get("reduction_accumulator_bits") or input_bits)
        result_bits = int(transaction.get("reduction_result_bits") or accumulator_bits)
        extend_kind = str(transaction.get("reduction_extend_kind") or "zext")
        is_fp_reduction = opcode in {"fadd", "fmul"}
        if is_fp_reduction and (input_bits, accumulator_bits, result_bits) != (32, 32, 32):
            return None
        if is_fp_reduction and lane_map != list(range(lanes)):
            return None
        if input_bits <= 0 or accumulator_bits <= 0 or result_bits <= 0:
            return None
        if accumulator_bits < input_bits or result_bits > accumulator_bits:
            return None
        if not is_fp_reduction and extend_kind not in {"zext", "sext"}:
            return None
        if is_scalable_transaction:
            scalable_source = sfpvar("a") if is_fp_reduction else svar("a")
            before_source = (
                vector_extend(scalable_source, extend_kind, accumulator_bits)
                if not is_fp_reduction and accumulator_bits != input_bits
                else scalable_source
            )
            packed_source = scalable_source if lane_map == list(range(lanes)) else svshuffle(scalable_source, lane_map)
            packed = (
                vector_extend(packed_source, extend_kind, accumulator_bits)
                if not is_fp_reduction and accumulator_bits != input_bits
                else packed_source
            )
            before = {"op": str(scalable_reduction_op), "args": [before_source]}
            after = {"op": str(scalable_reduction_op), "args": [packed]}
            if result_bits != accumulator_bits and not is_fp_reduction:
                before = trunc(before, result_bits)
                after = trunc(after, result_bits)
            parameters: dict[str, Any] = {
                "transaction.model": "optimization-transaction-v1",
                "transaction.kind": kind,
                "transaction.opcode": opcode,
                "transaction.reduction_opcode": opcode,
                "transaction.scalable": True,
                "transaction.base_lanes": base_lanes,
                "transaction.vscale_values": list(vscale_values),
                "transaction.lanes": lanes,
                "transaction.vector_width": lanes,
                "transaction.reduction_lanes": lanes,
                "transaction.reduction_input_bits": input_bits,
                "transaction.reduction_accumulator_bits": accumulator_bits,
                "transaction.reduction_result_bits": result_bits,
                "transaction.reduction_extend_kind": extend_kind,
                "transaction.actions": [
                    str(action.get("kind") or "")
                    for action in transaction.get("actions", [])
                    if isinstance(action, dict) and action.get("kind")
                ],
            }
            if isinstance(lane_mapping, dict):
                scalable_lane_mapping = dict(lane_mapping)
                scalable_lane_mapping["base_lanes"] = base_lanes
                scalable_lane_mapping["vscale_values"] = list(vscale_values)
                parameters["transaction.lane_mapping"] = dict(lane_mapping)
                parameters["transaction.lane_mapping.kind"] = lane_mapping_kind
                parameters["transaction.lane_mapping.map"] = lane_map
                parameters["transaction.lane_mapping.inverse_map"] = inverse_map
                parameters["transaction.scalable_lane_mapping"] = scalable_lane_mapping
            result_value = transaction.get("reduction_result")
            if isinstance(result_value, dict):
                parameters["transaction.reduction_result"] = dict(result_value)
            sources = transaction.get("reduction_sources")
            if isinstance(sources, list):
                parameters["transaction.reduction_sources"] = [
                    dict(item) for item in sources if isinstance(item, dict)
                ]
            if isinstance(operand_lane_mappings, dict):
                parameters["transaction.operand_lane_mappings"] = {
                    str(key): dict(value)
                    for key, value in operand_lane_mappings.items()
                    if isinstance(value, dict)
                }
            if is_fp_reduction:
                parameters["transaction.fp_semantics"] = "ordered-fp32"
                parameters["transaction.fp_rounding"] = "rne"
            for key in ("legality", "profitability", "lane_source"):
                value = transaction.get(key)
                if isinstance(value, dict):
                    parameters[f"transaction.{key}"] = dict(value)
            for key in ("functions", "role_provenance", "opcode_sources"):
                value = transaction.get(key)
                if isinstance(value, list):
                    parameters[f"transaction.{key}"] = [
                        dict(item) if isinstance(item, dict) else str(item)
                        for item in value
                        if isinstance(item, dict) or str(item)
                    ]
            parameters["transaction.consistency"] = str(transaction.get("consistency") or "ok")
            if isinstance(transaction.get("consistency_errors"), list):
                parameters["transaction.consistency_errors"] = list(transaction.get("consistency_errors") or [])
            formal = {
                "domain": "scalable-scalar-fp32" if is_fp_reduction else "scalable-scalar-bv32",
                "base_lanes": base_lanes,
                "vscale_values": list(vscale_values),
                "variables": ["a"],
                **({} if is_fp_reduction else {"variable_bits": {"a": input_bits}}),
                "poison_variables": ["a"],
                "before": before,
                "after": after,
                "equivalence": "result",
                "refinement": "refinement",
            }
            return transaction_formal_result(transaction, formal, parameters)
        extender = sext if extend_kind == "sext" else zext
        def reduction_lane(index: int) -> dict[str, Any]:
            if is_fp_reduction:
                return fpvar(f"a{index}")
            lane = var(f"a{index}")
            return extender(lane, accumulator_bits) if accumulator_bits != input_bits else lane

        before = reduction_lane(0)
        for index in range(1, lanes):
            lane = reduction_lane(index)
            before = (
                binop(str(reduction_scalar_op), before, lane)
                if reduction_scalar_op is not None
                else ite(binop(str(reduction_cmp_op), before, lane), before, lane)
            )
        if result_bits != accumulator_bits and not is_fp_reduction:
            before = trunc(before, result_bits)
        if is_fp_reduction:
            raw_packed = fp_vector_value("a", lanes)
        else:
            raw_packed = (
                extended_vector_value("a", extend_kind, accumulator_bits, lanes)
                if accumulator_bits != input_bits
                else vector_value("a", lanes)
            )
        packed = raw_packed if lane_map == list(range(lanes)) else vshuffle(raw_packed, lane_map)
        after = {"op": str(reduction_op), "args": [packed]}
        if result_bits != accumulator_bits and not is_fp_reduction:
            after = trunc(after, result_bits)
        parameters: dict[str, Any] = {
            "transaction.model": "optimization-transaction-v1",
            "transaction.kind": kind,
            "transaction.opcode": opcode,
            "transaction.reduction_opcode": opcode,
            "transaction.lanes": lanes,
            "transaction.vector_width": lanes,
            "transaction.reduction_lanes": lanes,
            "transaction.reduction_input_bits": input_bits,
            "transaction.reduction_accumulator_bits": accumulator_bits,
            "transaction.reduction_result_bits": result_bits,
            "transaction.reduction_extend_kind": extend_kind,
            "transaction.actions": [
                str(action.get("kind") or "")
                for action in transaction.get("actions", [])
                if isinstance(action, dict) and action.get("kind")
            ],
        }
        if is_fp_reduction:
            parameters["transaction.fp_semantics"] = "ordered-fp32"
            parameters["transaction.fp_rounding"] = "rne"
        result_value = transaction.get("reduction_result")
        if isinstance(result_value, dict):
            parameters["transaction.reduction_result"] = dict(result_value)
        sources = transaction.get("reduction_sources")
        if isinstance(sources, list):
            parameters["transaction.reduction_sources"] = [
                dict(item) for item in sources if isinstance(item, dict)
            ]
        if isinstance(lane_mapping, dict):
            parameters["transaction.lane_mapping"] = dict(lane_mapping)
            parameters["transaction.lane_mapping.kind"] = lane_mapping_kind
            parameters["transaction.lane_mapping.map"] = lane_map
            parameters["transaction.lane_mapping.inverse_map"] = inverse_map
        if isinstance(operand_lane_mappings, dict):
            parameters["transaction.operand_lane_mappings"] = {
                str(key): dict(value)
                for key, value in operand_lane_mappings.items()
                if isinstance(value, dict)
            }
        for key in ("legality", "profitability", "lane_source"):
            value = transaction.get(key)
            if isinstance(value, dict):
                parameters[f"transaction.{key}"] = dict(value)
        for key in ("functions", "role_provenance", "opcode_sources"):
            value = transaction.get(key)
            if isinstance(value, list):
                parameters[f"transaction.{key}"] = [
                    dict(item) if isinstance(item, dict) else str(item)
                    for item in value
                    if isinstance(item, dict) or str(item)
                ]
        parameters["transaction.consistency"] = str(transaction.get("consistency") or "ok")
        if isinstance(transaction.get("consistency_errors"), list):
            parameters["transaction.consistency_errors"] = list(transaction.get("consistency_errors") or [])
        formal = {
            "domain": "scalar-fp32" if is_fp_reduction else "scalar-bv32",
            "vector_width": lanes,
            "variables": [f"a{index}" for index in range(lanes)],
            **({} if is_fp_reduction else {"variable_bits": {f"a{index}": input_bits for index in range(lanes)}}),
            "poison_variables": [f"a{index}" for index in range(lanes)],
            "before": before,
            "after": after,
            "equivalence": "result",
            "refinement": "refinement",
        }
        return transaction_formal_result(transaction, formal, parameters)

    if not isinstance(result_lane_mapping, dict):
        return None
    result_map = result_lane_mapping.get("map")
    if not isinstance(result_map, list) or len(result_map) != lanes or not all(isinstance(index, int) for index in result_map):
        return None
    if sorted(result_map) != list(range(lanes)) or result_map != lane_map:
        return None
    if is_scalable_transaction:
        before = binop(str(scalable_vector_op), svar("a"), svar("b"))
        if lane_map == list(range(lanes)):
            after = binop(str(scalable_vector_op), svar("a"), svar("b"))
        else:
            packed_a = svshuffle(svar("a"), lane_map)
            packed_b = svshuffle(svar("b"), lane_map)
            packed_result = binop(str(scalable_vector_op), packed_a, packed_b)
            after = svshuffle(packed_result, inverse_map)
        parameters: dict[str, Any] = {
            "transaction.model": "optimization-transaction-v1",
            "transaction.kind": kind,
            "transaction.opcode": opcode,
            "transaction.scalable": True,
            "transaction.base_lanes": base_lanes,
            "transaction.vscale_values": list(vscale_values),
            "transaction.lanes": lanes,
            "transaction.vector_width": lanes,
            "transaction.actions": [
                str(action.get("kind") or "")
                for action in transaction.get("actions", [])
                if isinstance(action, dict) and action.get("kind")
            ],
        }
        for key in ("legality", "profitability", "lane_source"):
            value = transaction.get(key)
            if isinstance(value, dict):
                parameters[f"transaction.{key}"] = dict(value)
        for key in ("functions", "role_provenance", "opcode_sources"):
            value = transaction.get(key)
            if isinstance(value, list):
                parameters[f"transaction.{key}"] = [
                    dict(item) if isinstance(item, dict) else str(item)
                    for item in value
                    if isinstance(item, dict) or str(item)
                ]
        if isinstance(lane_mapping, dict):
            scalable_lane_mapping = dict(lane_mapping)
            scalable_lane_mapping["base_lanes"] = base_lanes
            scalable_lane_mapping["vscale_values"] = list(vscale_values)
            parameters["transaction.lane_mapping"] = dict(lane_mapping)
            parameters["transaction.lane_mapping.kind"] = lane_mapping_kind
            parameters["transaction.lane_mapping.map"] = lane_map
            parameters["transaction.lane_mapping.inverse_map"] = inverse_map
            parameters["transaction.scalable_lane_mapping"] = scalable_lane_mapping
        if isinstance(operand_lane_mappings, dict):
            parameters["transaction.operand_lane_mappings"] = {
                str(key): dict(value)
                for key, value in operand_lane_mappings.items()
                if isinstance(value, dict)
            }
        parameters["transaction.result_lane_mapping"] = dict(result_lane_mapping)
        if isinstance(scalar_lane_pairs, list):
            parameters["transaction.scalar_lane_pairs"] = [
                dict(item) for item in scalar_lane_pairs if isinstance(item, dict)
            ]
        if kind == "slp-vectorize-minmax":
            predicate = transaction.get("predicate")
            if predicate:
                parameters["transaction.predicate"] = str(predicate)
            select_order = transaction.get("select_order")
            if select_order:
                parameters["transaction.select_order"] = str(select_order)
            for source_key in ("compare_sources", "select_sources"):
                value = transaction.get(source_key)
                if isinstance(value, list):
                    parameters[f"transaction.{source_key}"] = [
                        dict(item) for item in value if isinstance(item, dict)
                    ]
        parameters["transaction.consistency"] = str(transaction.get("consistency") or "ok")
        if isinstance(transaction.get("consistency_errors"), list):
            parameters["transaction.consistency_errors"] = list(transaction.get("consistency_errors") or [])
        formal = scalable_vector_binary_refinement_formal(before, after, base_lanes, list(vscale_values))
        return transaction_formal_result(transaction, formal, parameters)
    before_lanes = (
        [binop(str(scalar_op), var(f"a{index}"), var(f"b{index}")) for index in range(lanes)]
        if kind == "slp-vectorize-binop"
        else [
            ite(binop(str(cmp_op), var(f"a{index}"), var(f"b{index}")), var(f"a{index}"), var(f"b{index}"))
            for index in range(lanes)
        ]
    )
    before = {"op": "vec", "args": before_lanes}
    if lane_map == list(range(lanes)):
        after = binop(vector_op, vector_value("a", lanes), vector_value("b", lanes))
    else:
        packed_a = vshuffle(vector_value("a", lanes), lane_map)
        packed_b = vshuffle(vector_value("b", lanes), lane_map)
        packed_result = binop(vector_op, packed_a, packed_b)
        after = vshuffle(packed_result, inverse_map)
    parameters: dict[str, Any] = {
        "transaction.model": "optimization-transaction-v1",
        "transaction.kind": kind,
        "transaction.opcode": opcode,
        "transaction.lanes": lanes,
        "transaction.vector_width": lanes,
        "transaction.actions": [
            str(action.get("kind") or "")
            for action in transaction.get("actions", [])
            if isinstance(action, dict) and action.get("kind")
        ],
    }
    legality = transaction.get("legality")
    if isinstance(legality, dict):
        parameters["transaction.legality"] = dict(legality)
    profitability = transaction.get("profitability")
    if isinstance(profitability, dict):
        parameters["transaction.profitability"] = dict(profitability)
    functions = transaction.get("functions")
    if isinstance(functions, list):
        parameters["transaction.functions"] = [str(function) for function in functions if str(function)]
    role_provenance = transaction.get("role_provenance")
    if isinstance(role_provenance, list):
        parameters["transaction.role_provenance"] = [
            dict(item) for item in role_provenance if isinstance(item, dict)
        ]
    opcode_sources = transaction.get("opcode_sources")
    if isinstance(opcode_sources, list):
        parameters["transaction.opcode_sources"] = [
            dict(item) for item in opcode_sources if isinstance(item, dict)
        ]
    lane_source = transaction.get("lane_source")
    if isinstance(lane_source, dict):
        parameters["transaction.lane_source"] = dict(lane_source)
    if isinstance(lane_mapping, dict):
        parameters["transaction.lane_mapping"] = dict(lane_mapping)
        parameters["transaction.lane_mapping.kind"] = lane_mapping_kind
        parameters["transaction.lane_mapping.map"] = lane_map
        parameters["transaction.lane_mapping.inverse_map"] = inverse_map
    if isinstance(operand_lane_mappings, dict):
        parameters["transaction.operand_lane_mappings"] = {
            str(key): dict(value)
            for key, value in operand_lane_mappings.items()
            if isinstance(value, dict)
        }
    parameters["transaction.result_lane_mapping"] = dict(result_lane_mapping)
    if isinstance(scalar_lane_pairs, list):
        parameters["transaction.scalar_lane_pairs"] = [
            dict(item) for item in scalar_lane_pairs if isinstance(item, dict)
        ]
    if kind == "slp-vectorize-minmax":
        predicate = transaction.get("predicate")
        if predicate:
            parameters["transaction.predicate"] = str(predicate)
        select_order = transaction.get("select_order")
        if select_order:
            parameters["transaction.select_order"] = str(select_order)
        for source_key in ("compare_sources", "select_sources"):
            value = transaction.get(source_key)
            if isinstance(value, list):
                parameters[f"transaction.{source_key}"] = [
                    dict(item) for item in value if isinstance(item, dict)
                ]
    parameters["transaction.consistency"] = str(transaction.get("consistency") or "ok")
    if isinstance(transaction.get("consistency_errors"), list):
        parameters["transaction.consistency_errors"] = list(transaction.get("consistency_errors") or [])
    formal = vector_binary_refinement_formal(before, after, lanes)
    return transaction_formal_result(transaction, formal, parameters)


def transaction_fp_policy_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    transaction = finding.get("optimization_transaction")
    if not isinstance(transaction, dict) or transaction.get("model") != "optimization-transaction-v1":
        return None
    if str(transaction.get("kind") or "") != "slp-vectorize-reduction":
        return None
    if str(transaction.get("consistency") or "ok") != "ok":
        return None
    opcode = str(transaction.get("opcode") or "")
    if opcode not in {"fadd", "fmul"}:
        return None
    policy = transaction.get("fp_policy")
    lane_mapping = transaction.get("lane_mapping")
    if not isinstance(policy, dict) or not isinstance(lane_mapping, dict):
        return None
    lanes = int(transaction.get("base_lanes") or transaction.get("lanes") or 0)
    lane_map = lane_mapping.get("map")
    if not isinstance(lane_map, list) or len(lane_map) != lanes or sorted(lane_map) != list(range(lanes)):
        return None
    evidence = policy.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return None
    semantics = str(policy.get("semantics") or "")
    if semantics not in {"relaxed-reassoc", "unordered-fp-reduction", "fast-math-fp-reduction"}:
        return None
    if semantics == "relaxed-reassoc" and lane_map == list(range(lanes)):
        # Reassociation evidence with identity order is still a policy contract
        # only when the source explicitly carries fast/relaxed evidence.
        pass
    relaxed_policy: dict[str, Any] = {
        "kind": "fp-reduction-policy",
        "semantics": semantics,
        "operation": opcode,
        "element_type": "fp32",
        "lanes": lanes,
        "lane_mapping": dict(lane_mapping),
        "evidence": [dict(item) for item in evidence if isinstance(item, dict)],
    }
    if transaction.get("scalable") is True:
        relaxed_policy["scalable"] = True
        relaxed_policy["base_lanes"] = int(transaction.get("base_lanes") or lanes)
        relaxed_policy["vscale_values"] = list(transaction.get("vscale_values") or [1, 2, 4])
    parameters: dict[str, Any] = {
        "transaction.model": "optimization-transaction-v1",
        "transaction.kind": "slp-vectorize-reduction",
        "transaction.opcode": opcode,
        "transaction.reduction_opcode": opcode,
        "transaction.lanes": lanes,
        "transaction.reduction_lanes": lanes,
        "transaction.lane_mapping": dict(lane_mapping),
        "transaction.lane_mapping.kind": str(lane_mapping.get("kind") or "permutation"),
        "transaction.lane_mapping.map": list(lane_map),
        "transaction.fp_policy": dict(relaxed_policy),
        "transaction.fp_policy.semantics": semantics,
        "transaction.fp_policy.operation": opcode,
        "transaction.fp_policy.element_type": "fp32",
        "transaction.consistency": "ok",
        "transaction.consistency_errors": [],
    }
    for key in ("functions", "role_provenance", "opcode_sources"):
        value = transaction.get(key)
        if isinstance(value, list):
            parameters[f"transaction.{key}"] = [
                dict(item) if isinstance(item, dict) else str(item)
                for item in value
                if isinstance(item, dict) or str(item)
            ]
    if isinstance(transaction.get("operand_lane_mappings"), dict):
        parameters["transaction.operand_lane_mappings"] = {
            str(key): dict(value)
            for key, value in transaction.get("operand_lane_mappings", {}).items()
            if isinstance(value, dict)
        }
    return relaxed_policy, parameters


def vector_inference_template_matches(
    template: dict[str, Any],
    evidence_text: str,
    constraints: dict[str, Any],
) -> bool:
    tokens = template.get("text_tokens")
    if isinstance(tokens, list) and any(isinstance(token, str) and token in evidence_text for token in tokens):
        return True
    expected_constraints = template.get("constraints_any")
    if isinstance(expected_constraints, list):
        for expected in expected_constraints:
            if not isinstance(expected, dict):
                continue
            key = expected.get("key")
            if isinstance(key, str) and constraints.get(key) == expected.get("value"):
                return True
    return False


def vector_formal_from_inference_template(
    template: dict[str, Any],
    constraints: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    builder = str(template.get("builder") or "")
    value = vector_value()
    scalable = scalable_value()
    if builder == "scalable-binop-identity":
        return scalable_vector_refinement_formal(
            binop(str(template["formal_op"]), scalable, vsplat(bvconst(int(template["constant"])))),
            scalable,
        ), {"vector.vscale_values": [1, 2, 4]}
    if builder == "scalable-binop-self-zero":
        return scalable_vector_refinement_formal(
            binop(str(template["formal_op"]), scalable, scalable),
            vsplat(bvconst(0)),
        ), {"vector.vscale_values": [1, 2, 4]}
    if builder == "scalable-reduction-add-zero":
        return {
            "domain": "scalable-vector-bv32",
            "base_lanes": 4,
            "vscale_values": [1, 2, 4],
            "variables": ["a"],
            "before": {"op": "svsplat", "args": [{"op": "svreduce_add", "args": [vsplat(bvconst(0))]}]},
            "after": {"op": "svsplat", "args": [bvconst(0)]},
            "equivalence": "vector-result",
        }, {"vector.vscale_values": [1, 2, 4]}
    if builder == "fixed-binop-identity":
        return vector_refinement_formal(
            binop(str(template["formal_op"]), value, vsplat(bvconst(int(template["constant"])))),
            value,
        ), {}
    if builder == "fixed-binop-self-zero":
        return vector_refinement_formal(binop(str(template["formal_op"]), value, value), vsplat(bvconst(0))), {}
    if builder == "shuffle-identity":
        mask = vector_mask_param(constraints, "vector.shuffle.mask", [0, 1, 2, 3])
        if mask is None:
            return None
        return vector_refinement_formal(vshuffle(value, mask), value), {"vector.shuffle.mask": mask}
    if builder == "shuffle-splat":
        mask = vector_mask_param(constraints, "vector.shuffle.mask", [2, 2, 2, 2])
        lane = vector_lane_param(constraints, "vector.shuffle.splat_lane", mask[0] if mask is not None else 2)
        if mask is None or lane is None or any(index != lane for index in mask):
            return None
        return vector_refinement_formal(vshuffle(value, mask), vsplat(vextract(value, lane))), {
            "vector.shuffle.mask": mask,
            "vector.shuffle.splat_lane": lane,
        }
    if builder == "extract-insert":
        lane = vector_lane_param(constraints, "vector.extract_insert.lane", 1)
        if lane is None:
            return None
        return scalar_vector_refinement_formal(vextract(vinsert(value, var("x"), lane), lane), var("x")), {
            "vector.extract_insert.lane": lane,
        }
    if builder == "reduction-add-zero":
        return {
            "domain": "scalar-bv32",
            "variables": ["a"],
            "before": {"op": "vreduce_add", "args": [vsplat(bvconst(0))]},
            "after": bvconst(0),
            "equivalence": "result",
        }, {}
    if builder == "insert-extract-identity":
        lane = vector_lane_param(constraints, "vector.insert_extract.lane", 1)
        if lane is None:
            return None
        return vector_refinement_formal(vinsert(value, vextract(value, lane), lane), value), {
            "vector.insert_extract.lane": lane,
        }
    if builder == "reduction-add-single-lane":
        lane = vector_lane_param(constraints, "vector.reduction.lane", 0)
        if lane is None:
            return None
        return {
            "domain": "scalar-bv32",
            "variables": [f"a{index}" for index in range(4)],
            "poison_variables": [f"a{index}" for index in range(4)],
            "before": {"op": "vreduce_add", "args": [vector_with_lane_zeroes(lane)]},
            "after": var(f"a{lane}"),
            "equivalence": "result",
            "refinement": "refinement",
        }, {"vector.reduction.lane": lane}
    if builder == "minmax":
        lhs = vector_value("a")
        rhs = vector_value("b")
        expression = binop(str(template["formal_op"]), lhs, rhs)
        return vector_binary_refinement_formal(expression, expression), {}
    if builder == "abs":
        expression = {"op": "vabs", "args": [value]}
        return vector_refinement_formal(expression, expression), {}
    return None


def vector_formal_for(
    marker: str,
    finding: dict[str, Any],
    predicate_source: str,
    rewrite_source: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    constraints = finding.get("constraints", {})
    constraints = constraints if isinstance(constraints, dict) else {}
    evidence_text = " ".join(
        [
            str(finding.get("matched_pattern") or ""),
            predicate_source,
            rewrite_source,
            constraints_text(constraints),
        ]
    )
    template = vector_inference_template_for_marker(marker)
    if not template or not vector_inference_template_matches(template, evidence_text, constraints):
        return None
    return vector_formal_from_inference_template(template, constraints)


def scalar_predicate_evidence_is_strong(marker: str, finding: dict[str, Any], predicate_source: str) -> bool:
    matched = str(finding.get("matched_pattern") or "")
    constraints = finding.get("constraints", {})
    constraints = constraints if isinstance(constraints, dict) else {}
    evidence_text = " ".join([matched, predicate_source])
    spec = scalar_instcombine_spec(marker)
    facts = spec.get("semantic_facts") if isinstance(spec.get("semantic_facts"), dict) else {}
    if facts:
        identity = str(facts.get("identity") or "")
        rewrite = str(facts.get("rewrite") or "")
        constant = CONSTANT_FOR_IDENTITY.get(identity)
        if constant is not None:
            matcher_name = (
                "m_Zero" if constant == 0
                else "m_One" if constant == 1
                else "m_AllOnes" if constant == 0xFFFFFFFF
                else ""
            )
            return (matcher_name and matcher_name in evidence_text) or constraints.get("rhs.value") == constant
        if rewrite == "replace-with-zero" or identity == "same-value":
            return "Op0 == Op1" in evidence_text or (
                constraints.get("lhs") == "same-value" and constraints.get("rhs") == "same-value"
            )
    if marker == "probe.dce.dead-instruction":
        return "isInstructionTriviallyDead" in evidence_text or constraints.get("instruction.is_dead") is True
    if marker == "probe.globalopt.dead-initializer":
        safety_params = global_initializer_safety_parameters(finding, predicate_source)
        return (
            safety_params.get("global.initializer.safety_status") == "complete"
            and constraints.get("global.initializer_dead") is True
        )
    return False


def primary_formal_strategy(
    finding: dict[str, Any], marker: str, rewrite_source: str
) -> tuple[str, Any, dict[str, Any], str] | None:
    """Ordered source-derived formal-derivation strategies; first hit wins.

    Returns ``(channel, value, parameters, provenance)`` where ``channel`` is
    ``"formal"`` (value is a formal dict) or ``"policy"`` (value is a relaxed-fp
    policy). ``None`` means no source-derived strategy applied and the caller
    falls back to the semantic-facts / registry paths.

    This is a behavior-preserving extraction of the legacy if/elif cascade: the
    builders are invoked lazily in priority order so the first non-empty result
    short-circuits exactly as before. The scalar fallback yields an empty
    provenance so the caller's default ("source-derived-scalar") still applies.
    """
    strategies = (
        ("formal", "source-derived-intent-graph",
         lambda: source_intent_graph_formal_for(finding)),
        ("formal", "source-derived-source-intent",
         lambda: source_intent_formal_for(finding)),
        ("policy", "source-derived-transaction-policy",
         lambda: transaction_fp_policy_for(finding)),
        ("formal", "source-derived-transaction",
         lambda: transaction_formal_for(finding)),
        ("formal", "",
         lambda: (lambda f: (f, {}) if f is not None else None)(
             scalar_formal_for(marker, rewrite_source))),
    )
    for channel, provenance, build in strategies:
        result = build()
        if result is None:
            continue
        value, parameters = result
        return channel, value, parameters, provenance
    return None


def make_candidate(
    finding: dict[str, Any],
    context_radius: int,
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    marker = str(finding.get("marker", ""))
    file_name = str(finding.get("file", ""))
    line = int(finding.get("line") or 0)
    if not marker or not file_name or line < 1:
        return None
    path = Path(file_name)
    if not path.is_file() and not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return None

    transaction_record = finding.get("optimization_transaction")
    lines = source_lines(path)
    source_line = lines[line - 1].strip() if line <= len(lines) else ""
    if not isinstance(transaction_record, dict) and not looks_like_predicate_site(source_line):
        return None
    context_start, context_end, context = window(lines, line, context_radius)
    body_start, body_end, body = if_body_window(lines, line, context_radius)
    rewrite_source = str(finding.get("rewrite_source") or "") or first_matching_line(
        body[1:] if len(body) > 1 else body, REWRITE_TOKENS
    )
    side_start = max(enclosing_function_start(lines, line), line - context_radius)
    side_context = lines[side_start - 1 : line]
    guard_side_records = source_intent_guard_side_conditions(finding)
    if guard_side_records is not None:
        modeled_side_records, side_records, profitability_records = guard_side_records
    else:
        raw_side_records = side_conditions(side_context, line, side_start)
        modeled_side_records, side_records = partition_side_conditions(raw_side_records)
        profitability_records: list[dict[str, Any]] = []
    if isinstance(transaction_record, dict):
        side_records = []
        modeled_side_records = []
        if transaction_record.get("profitability"):
            profitability_records = [
                {
                    "kind": "profitability",
                    "line": line,
                    "source": predicate_source if "predicate_source" in locals() else str(finding.get("predicate_source") or ""),
                }
            ]
    confidence = confidence_for(marker, rewrite_source, side_records)
    if isinstance(transaction_record, dict) and not side_records:
        confidence = "high"
    registry_record = registry.get(marker, {})
    template = INTENT_TEMPLATES.get(marker, registry_record)
    predicate_source = str(
        finding.get("predicate_source")
        or finding.get("source")
        or finding.get("matched_pattern")
        or ""
    )
    intent_candidate = {
        "marker": marker,
        "precondition": template.get("precondition", constraints_text(finding.get("constraints", {}))),
        "rewrite": inferred_rewrite(marker, rewrite_source),
        "intent": template.get("intent", "semantic-equivalence"),
    }
    formal_inference = ""
    formal_parameters: dict[str, Any] = {}
    graph_status, graph_reasons = source_intent_graph_quality(finding)
    graph_consistency, graph_consistency_errors = source_intent_graph_consistency(finding)
    graph_record = finding.get("source_intent_graph")
    graph_analysis_facts = (
        graph_record.get("analysis_facts")
        if isinstance(graph_record, dict)
        else None
    )
    analysis_facts = normalize_analysis_facts(
        finding.get("analysis_facts") if isinstance(finding.get("analysis_facts"), list) else graph_analysis_facts
    )
    formal = None
    strategy = primary_formal_strategy(finding, marker, rewrite_source)
    if strategy is not None:
        channel, value, parameters, provenance = strategy
        if channel == "policy":
            intent_candidate["relaxed_fp_policy"] = value
            formal_parameters = parameters
            formal_inference = provenance
        else:
            formal, formal_parameters = value, parameters
            intent_candidate["formal"] = formal
            if provenance:
                formal_inference = provenance
    if graph_status != "absent":
        formal_parameters.setdefault("source_intent_graph.status", graph_status)
        formal_parameters.setdefault("source_intent_graph.consistency", graph_consistency)
        if graph_reasons:
            formal_parameters.setdefault("source_intent_graph.unsupported_reasons", graph_reasons)
        if graph_consistency_errors:
            formal_parameters.setdefault("source_intent_graph.consistency_errors", graph_consistency_errors)
    if marker == "probe.globalopt.dead-initializer":
        safety_params = global_initializer_safety_parameters(finding, predicate_source)
        rewrite_provenance = global_initializer_rewrite_provenance(finding)
        formal_parameters.update(global_initializer_formal_parameters())
        formal_parameters.update(safety_params)
        formal_parameters.update(rewrite_provenance)
        if rewrite_provenance.get("global.initializer.rewrite_provenance_status") == "unsupported":
            formal_parameters.setdefault(
                "semantic.unsupported_reason",
                str(rewrite_provenance.get("global.initializer.rewrite_provenance_reason") or ""),
            )
    dse_contract = dse_analysis_fact_contract(marker, analysis_facts)
    if dse_contract.get("applicable") and formal is None:
        dse_params = dse_analysis_fact_parameters(marker, analysis_facts)
        if dse_contract.get("complete") and isinstance(registry_record.get("formal"), dict):
            formal = copy.deepcopy(registry_record["formal"])
            formal_parameters.update(dse_params)
            formal_parameters.setdefault("semantic.shape", "memory")
            formal_parameters.setdefault(
                "semantic.operation",
                (finding.get("semantic_facts") or {}).get("operation")
                if isinstance(finding.get("semantic_facts"), dict)
                else "",
            )
            formal_parameters.setdefault(
                "semantic.rewrite",
                (finding.get("semantic_facts") or {}).get("rewrite")
                if isinstance(finding.get("semantic_facts"), dict)
                else "",
            )
            intent_candidate["formal"] = formal
            formal_inference = "source-derived-analysis-facts"
        elif analysis_facts:
            formal_parameters.update(dse_params)
            formal_parameters["semantic.unsupported"] = True
            formal_parameters["semantic.unsupported_reason"] = (
                missing_dse_analysis_fact_recommendation(marker, analysis_facts)
                or "incomplete-dse-analysis-facts"
            )
    if formal is not None:
        intent_candidate["formal"] = formal
        if not formal_inference:
            formal_inference = "source-derived-scalar"
    else:
        has_semantic_facts = "semantic_facts" in finding
        semantic_facts = finding.get("semantic_facts")
        semantic_message = ""
        if has_semantic_facts:
            _, semantic_message = semantic_facts_valid_for_marker(marker, semantic_facts)
        semantic_formal = semantic_scalar_formal_for(finding) if has_semantic_facts else None
        if semantic_formal is not None:
            formal, formal_parameters = semantic_formal
            intent_candidate["formal"] = formal
            formal_inference = "source-derived-scalar"
        else:
            semantic_formal = semantic_vector_formal_for(finding) if has_semantic_facts else None
        if semantic_formal is not None and "formal" not in intent_candidate:
            formal, formal_parameters = semantic_formal
            intent_candidate["formal"] = formal
            formal_inference = "source-derived-vector"
        elif has_semantic_facts and not dse_contract.get("applicable"):
            registry_formal = semantic_registry_formal_for(finding, registry_record)
            if registry_formal is not None:
                formal, formal_parameters, formal_inference = registry_formal
                intent_candidate["formal"] = formal
            else:
                formal_parameters = {"semantic.unsupported": True}
                if semantic_message:
                    formal_parameters["semantic.unsupported_reason"] = semantic_message
                if marker == "probe.globalopt.dead-initializer":
                    safety_params = global_initializer_safety_parameters(finding, predicate_source)
                    formal_parameters.update(safety_params)
                    formal_parameters["global.initializer.observability_model"] = (
                        "local-unobservable-initializer-v1"
                    )
                    formal_parameters["global.initializer.rewrite_api"] = "setInitializer"
                    formal_parameters["global.initializer.replacement_kind"] = (
                        "default-null-initializer"
                    )
                    if safety_params.get("global.initializer.safety_status") != "complete":
                        formal_parameters["semantic.unsupported_reason"] = (
                            "missing-global-initializer-safety-facts"
                        )
        elif has_semantic_facts and dse_contract.get("applicable") and not analysis_facts:
            if isinstance(registry_record.get("formal"), dict):
                intent_candidate["formal"] = copy.deepcopy(registry_record["formal"])
                formal_inference = "registry-fallback"
                formal_parameters.setdefault("semantic.shape", "memory")
                formal_parameters.setdefault("semantic.unsupported_reason", "missing-dse-analysis-facts")
            else:
                formal_parameters = {
                    "semantic.unsupported": True,
                    "semantic.unsupported_reason": "missing-dse-analysis-facts",
                }
        elif not has_semantic_facts:
            vector_formal = vector_formal_for(marker, finding, predicate_source, rewrite_source)
            if vector_formal is not None:
                formal, formal_parameters = vector_formal
                intent_candidate["formal"] = formal
                formal_inference = "source-derived-vector"
    if "formal" in intent_candidate and isinstance(finding.get("semantic_facts"), dict):
        formal_parameters.setdefault("semantic.lowering", "semantic-facts")
    if (
        "formal" in intent_candidate
        and modeled_side_records
        and not isinstance(finding.get("source_intent"), dict)
        and not apply_modeled_side_condition_semantics(
            intent_candidate["formal"],
            formal_parameters,
            modeled_side_records,
            marker,
            rewrite_source,
        )
    ):
        side_records.extend(modeled_side_records)
        modeled_side_records = []
    has_semantic_facts = "semantic_facts" in finding
    has_policy = "relaxed_fp_policy" in intent_candidate
    if not has_policy and not has_semantic_facts and "formal" not in intent_candidate and (
        marker in INTENT_TEMPLATES
        and rewrite_source
        and scalar_predicate_evidence_is_strong(marker, finding, predicate_source)
        and isinstance(registry_record.get("formal"), dict)
    ):
        intent_candidate["formal"] = copy.deepcopy(registry_record["formal"])
        formal_inference = "registry-fallback"
    elif (
        not has_policy
        and not has_semantic_facts
        and "formal" not in intent_candidate
        and marker not in INTENT_TEMPLATES
        and isinstance(registry_record.get("formal"), dict)
    ):
        intent_candidate["formal"] = copy.deepcopy(registry_record["formal"])
        formal_inference = "registry-fallback"
    if graph_status != "absent":
        formal_parameters.setdefault("source_intent_graph.status", graph_status)
        formal_parameters.setdefault("source_intent_graph.consistency", graph_consistency)
        if graph_reasons:
            formal_parameters.setdefault("source_intent_graph.unsupported_reasons", graph_reasons)
        if graph_consistency_errors:
            formal_parameters.setdefault("source_intent_graph.consistency_errors", graph_consistency_errors)
    if isinstance(transaction_record, dict):
        source_program_graph = transaction_record.get("source_program_graph")
        if isinstance(source_program_graph, dict):
            for key, value in source_graph_contract_parameters(source_program_graph).items():
                formal_parameters.setdefault(key, value)
        for source_key, param_key in (
            ("model", "transaction.model"),
            ("kind", "transaction.kind"),
            ("opcode", "transaction.opcode"),
            ("lanes", "transaction.lanes"),
            ("base_lanes", "transaction.base_lanes"),
            ("vscale_values", "transaction.vscale_values"),
        ):
            if source_key in transaction_record:
                formal_parameters.setdefault(param_key, transaction_record.get(source_key))
        if transaction_record.get("scalable") is True:
            formal_parameters.setdefault("transaction.scalable", True)
        if isinstance(transaction_record.get("scalable_provenance"), list):
            formal_parameters.setdefault(
                "transaction.scalable_provenance",
                [dict(item) for item in transaction_record["scalable_provenance"] if isinstance(item, dict)],
            )
        formal_parameters.setdefault("transaction.consistency", str(transaction_record.get("consistency") or "ok"))
        if isinstance(transaction_record.get("consistency_errors"), list):
            formal_parameters.setdefault("transaction.consistency_errors", list(transaction_record.get("consistency_errors") or []))
        for key, value in transaction_source_slice_parameters(transaction_record).items():
            formal_parameters.setdefault(key, value)
        if isinstance(transaction_record.get("opcode_sources"), list):
            formal_parameters.setdefault(
                "transaction.opcode_sources",
                [dict(item) for item in transaction_record.get("opcode_sources", []) if isinstance(item, dict)],
            )
        if isinstance(transaction_record.get("lane_source"), dict):
            formal_parameters.setdefault("transaction.lane_source", dict(transaction_record["lane_source"]))
        if isinstance(transaction_record.get("lane_mapping"), dict):
            formal_parameters.setdefault("transaction.lane_mapping", dict(transaction_record["lane_mapping"]))
        if isinstance(transaction_record.get("operand_lane_mappings"), dict):
            formal_parameters.setdefault(
                "transaction.operand_lane_mappings",
                {
                    str(key): dict(value)
                    for key, value in transaction_record.get("operand_lane_mappings", {}).items()
                    if isinstance(value, dict)
                },
            )
        if isinstance(transaction_record.get("result_lane_mapping"), dict):
            formal_parameters.setdefault("transaction.result_lane_mapping", dict(transaction_record["result_lane_mapping"]))
        if isinstance(transaction_record.get("scalar_lane_pairs"), list):
            formal_parameters.setdefault(
                "transaction.scalar_lane_pairs",
                [dict(item) for item in transaction_record.get("scalar_lane_pairs", []) if isinstance(item, dict)],
            )
        for source_key, param_key in (
            ("reduction_opcode", "transaction.reduction_opcode"),
            ("reduction_lanes", "transaction.reduction_lanes"),
            ("reduction_input_bits", "transaction.reduction_input_bits"),
            ("reduction_accumulator_bits", "transaction.reduction_accumulator_bits"),
            ("reduction_result_bits", "transaction.reduction_result_bits"),
            ("reduction_extend_kind", "transaction.reduction_extend_kind"),
            ("reduction_width_status", "transaction.reduction_width_status"),
        ):
            if source_key in transaction_record:
                formal_parameters.setdefault(param_key, transaction_record.get(source_key))
        if isinstance(transaction_record.get("reduction_width_provenance"), list):
            formal_parameters.setdefault(
                "transaction.reduction_width_provenance",
                [dict(item) for item in transaction_record["reduction_width_provenance"] if isinstance(item, dict)],
            )
        if isinstance(transaction_record.get("reduction_sources"), list):
            formal_parameters.setdefault(
                "transaction.reduction_sources",
                [dict(item) for item in transaction_record.get("reduction_sources", []) if isinstance(item, dict)],
            )
        if isinstance(transaction_record.get("reduction_result"), dict):
            formal_parameters.setdefault("transaction.reduction_result", dict(transaction_record["reduction_result"]))
        if isinstance(transaction_record.get("fp_policy"), dict):
            formal_parameters.setdefault("transaction.fp_policy", dict(transaction_record["fp_policy"]))
        if isinstance(transaction_record.get("unsupported_reduction_reasons"), list):
            formal_parameters.setdefault(
                "transaction.unsupported_reduction_reasons",
                [
                    str(item)
                    for item in transaction_record.get("unsupported_reduction_reasons", [])
                    if str(item)
                ],
            )
    if marker == "probe.globalopt.dead-initializer":
        safety_params = global_initializer_safety_parameters(finding, predicate_source)
        rewrite_provenance = global_initializer_rewrite_provenance(finding)
        formal_parameters.update(global_initializer_formal_parameters())
        formal_parameters.update(safety_params)
        formal_parameters.update(rewrite_provenance)
        if rewrite_provenance.get("global.initializer.rewrite_provenance_status") == "unsupported":
            formal_parameters.setdefault(
                "semantic.unsupported_reason",
                str(rewrite_provenance.get("global.initializer.rewrite_provenance_reason") or ""),
            )
    if analysis_facts:
        formal_parameters.setdefault("analysis_facts", [dict(fact) for fact in analysis_facts])
        formal_parameters.setdefault(
            "analysis_facts.kinds",
            sorted({str(fact.get("kind") or "") for fact in analysis_facts if str(fact.get("kind") or "")}),
        )
        formal_parameters.setdefault(
            "analysis_facts.status",
            sorted({str(fact.get("status") or "") for fact in analysis_facts if str(fact.get("status") or "")}),
        )
        formal_parameters.setdefault(
            "analysis_facts.roles",
            sorted({str(fact.get("role") or "") for fact in analysis_facts if str(fact.get("role") or "")}),
        )
    evidence = {
        "context_start_line": context_start,
        "context_end_line": context_end,
        "rewrite_start_line": body_start,
        "rewrite_end_line": body_end,
        "matched_pattern": finding.get("matched_pattern", ""),
        "constraints": finding.get("constraints", {}),
        "context": context,
    }
    if formal_inference:
        evidence["formal_inference"] = formal_inference
    if formal_parameters:
        evidence["formal_parameters"] = formal_parameters
    if modeled_side_records:
        evidence["side_condition_lowering"] = "modeled"
        evidence.setdefault("formal_parameters", {})["side_conditions.modeled"] = [
            record["kind"] for record in modeled_side_records
        ]
    if profitability_records:
        evidence["profitability_guards"] = profitability_records
        evidence.setdefault("formal_parameters", {})["source_intent.profitability_guards"] = [
            record["kind"] for record in profitability_records
        ]
    if isinstance(finding.get("semantic_facts"), dict):
        evidence["semantic_facts"] = finding["semantic_facts"]
        evidence["semantic_lowering"] = "formal-ir" if "formal" in intent_candidate else "unsupported"
    if analysis_facts:
        evidence["analysis_facts"] = [dict(fact) for fact in analysis_facts]
        evidence.setdefault("formal_parameters", {})["analysis_facts"] = [dict(fact) for fact in analysis_facts]
    if isinstance(finding.get("source_intent"), dict):
        evidence["source_intent"] = finding["source_intent"]
        evidence["source_intent_lowering"] = (
            "formal-ir" if formal_inference in {"source-derived-source-intent", "source-derived-intent-graph"} else "fallback"
        )
    if isinstance(finding.get("source_intent_graph"), dict):
        evidence["source_intent_graph"] = finding["source_intent_graph"]
        evidence["source_intent_graph_lowering"] = (
            "formal-ir" if formal_inference == "source-derived-intent-graph" else "fallback"
        )
    if isinstance(finding.get("optimization_transaction"), dict):
        evidence["optimization_transaction"] = finding["optimization_transaction"]
        evidence["transaction_lowering"] = (
            "formal-ir"
            if formal_inference == "source-derived-transaction"
            else ("relaxed-fp-policy" if formal_inference == "source-derived-transaction-policy" else "fallback")
        )
    return {
        "file": file_name,
        "line": line,
        "marker": marker,
        "pass": finding.get("pass", "unknown"),
        "predicate_kind": finding.get("predicate_kind", "unknown"),
        "predicate_source": predicate_source,
        "rewrite_source": rewrite_source,
        "modeled_side_conditions": modeled_side_records,
        "side_conditions": side_records,
        "profitability_guards": profitability_records,
        "intent_candidate": intent_candidate,
        "confidence": confidence,
        "proof_tool_available": shutil.which("alive-tv") is not None,
        "evidence": evidence,
    }


def looks_like_predicate_site(source: str) -> bool:
    if not source:
        return False
    if source.endswith(";") and not source.startswith("if "):
        return False
    return "if" in source or "match(" in source or "==" in source


def constraints_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    parts = [f"{key} == {json.dumps(val)}" for key, val in sorted(value.items())]
    return " && ".join(parts)


def passes_confidence(record: dict[str, Any], minimum: str) -> bool:
    return CONFIDENCE_ORDER[str(record["confidence"])] >= CONFIDENCE_ORDER[minimum]


def validate_required(records: list[dict[str, Any]], required: list[str]) -> bool:
    found = {str(record.get("marker", "")) for record in records}
    missing = [marker for marker in required if marker not in found]
    if missing:
        print("missing required markers: " + ", ".join(missing), file=sys.stderr)
        return False
    return True


def write_output(path: Path, records: list[dict[str, Any]], fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        if fmt == "json":
            json.dump(records, output, indent=2, sort_keys=True)
            output.write("\n")
            return
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    try:
        registry = load_intent_registry(args.intent_registry)
        findings = load_records(args.findings) if args.findings else run_miner(args.miner, args.paths)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    records = [
        candidate
        for finding in findings
        for candidate in [make_candidate(finding, args.context, registry)]
        if candidate is not None and passes_confidence(candidate, args.min_confidence)
    ]
    records.sort(key=lambda record: (str(record["file"]), int(record["line"]), str(record["marker"])))
    write_output(args.out, records, args.format)
    print(f"wrote {len(records)} intent candidate(s) to {args.out}")
    return 0 if validate_required(records, args.require_marker) else 1


if __name__ == "__main__":
    raise SystemExit(main())
