#!/usr/bin/env python3
"""Audit source-derived and registry-backed intent coverage."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

from cv_analysis_facts import analysis_fact_summary, missing_dse_analysis_fact_recommendation, normalize_analysis_facts
from cv_guard_semantics import DEFAULT_GUARD_SEMANTICS, load_guard_semantics, recognizer_summary
from cv_globalopt_witness import witness_contract
from cv_source_graph_contract import source_graph_contract_summary


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"
DEFAULT_SEMANTIC_FACTS = ROOT / "constraints" / "semantic_facts.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validated", type=Path, required=True)
    parser.add_argument("--intent-registry", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--semantic-facts", type=Path, default=DEFAULT_SEMANTIC_FACTS)
    parser.add_argument("--guard-semantics", type=Path, default=DEFAULT_GUARD_SEMANTICS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
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


def marker_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("marker") or ""): record for record in records if str(record.get("marker") or "")}


def nested_dict(record: dict[str, Any], key: str) -> dict[str, Any]:
    value = record.get(key)
    return value if isinstance(value, dict) else {}


def formal_domain(record: dict[str, Any], registry_record: dict[str, Any]) -> str:
    candidate = nested_dict(record, "intent_candidate")
    candidate_formal = nested_dict(candidate, "formal")
    registry_formal = nested_dict(registry_record, "formal")
    return str(candidate_formal.get("domain") or registry_formal.get("domain") or "")


def unsupported_reason(record: dict[str, Any]) -> str:
    status = str(record.get("proof_status") or "")
    result = str(record.get("proof_result") or "")
    if status == "unsupported":
        return result or "unsupported"
    if status in {"failed", "error"}:
        return result or status
    return ""


def recommendation(record: dict[str, Any], registry_record: dict[str, Any], semantic_record: dict[str, Any]) -> str:
    proof_status = str(record.get("proof_status") or "")
    reason = unsupported_reason(record)
    evidence = nested_dict(record, "evidence")
    formal_inference = str(evidence.get("formal_inference") or "")
    semantic_lowering = str(evidence.get("semantic_lowering") or "")
    marker = str(record.get("marker") or "")
    transaction = transaction_summary(record)
    transaction_errors = transaction.get("transaction_consistency_errors", [])
    source_graph_recommendation = source_program_graph_contract_recommendation(record)
    analysis_recommendation = missing_dse_analysis_fact_recommendation(
        marker, analysis_facts_for_record(record)
    )

    if proof_status in {"failed", "error"}:
        return "inspect failed proof"
    if source_graph_recommendation:
        return source_graph_recommendation
    if analysis_recommendation and proof_status != "proved":
        return analysis_recommendation
    if formal_inference == "source-derived-analysis-facts":
        if proof_status == "proved":
            return "covered by source-derived DSE analysis facts"
        if analysis_recommendation:
            return analysis_recommendation
    if formal_inference == "source-derived-transaction-policy":
        if proof_status == "proved":
            return "covered by source-derived relaxed FP policy"
        reduction_recommendation = reduction_gap_recommendation(transaction)
        if reduction_recommendation:
            return reduction_recommendation
    if formal_inference == "source-derived-transaction":
        if proof_status == "proved":
            return "covered by source-derived transaction formal IR"
        reduction_recommendation = reduction_gap_recommendation(transaction)
        if reduction_recommendation:
            return reduction_recommendation
        helper_recommendation = helper_slice_gap_recommendation(transaction)
        if helper_recommendation:
            return helper_recommendation
        transaction_recommendation = transaction_consistency_recommendation(transaction)
        if transaction_recommendation:
            return transaction_recommendation
        if transaction_errors:
            return "fix transaction consistency: " + ",".join(str(item) for item in transaction_errors)
    if not registry_record and marker:
        return "add registry formal block"
    if not semantic_record and marker:
        return "add semantic facts"
    if (
        marker == "probe.globalopt.dead-initializer"
        and formal_parameters(record).get("global.initializer.safety_status") == "incomplete"
    ):
        return "add missing global initializer safety facts"
    if semantic_lowering == "unsupported":
        return "improve source semantic lowering"
    if reason == "unsupported-side-conditions":
        unsupported = guard_kinds(record.get("side_conditions"))
        return f"model guard semantics: {','.join(unsupported)}" if unsupported else "model side conditions"
    if reason == "unsupported-formal-ir":
        return "extend formal IR"
    if reason == "unsupported-contradictory-assumptions":
        return "fix contradictory guard assumptions"
    if reason in {"unsupported-marker", "unsupported-rewrite"}:
        return "improve source intent inference"
    if proof_status == "proved" and formal_inference.startswith("source-derived-"):
        return "covered by source-derived formal IR"
    if proof_status == "proved" and formal_inference == "registry-fallback":
        return "improve source semantic lowering"
    if proof_status == "proved":
        return "covered"
    return "inspect intent candidate"


SOURCE_PROGRAM_GRAPH_CONTRACT_RECOMMENDATIONS = {
    "source-graph:interprocedural-dfg": "improve helper return/argument DFG mining",
    "source-graph:access-path-provenance": "fix source access-path provenance",
    "source-graph:node-edge-integrity": "fix source graph construction integrity",
    "source-graph:cfg-precision": "improve source CFG mining",
    "source-graph:dfg-precision": "improve source DFG mining",
    "source-graph:present": "emit source program graph evidence",
}


SOURCE_PROGRAM_GRAPH_CONTRACT_RECOMMENDATION_PRIORITY = {
    "improve helper return/argument DFG mining": 0,
    "fix source access-path provenance": 1,
    "fix source graph construction integrity": 2,
    "improve source DFG mining": 3,
    "improve source CFG mining": 4,
    "emit source program graph evidence": 5,
}


def source_program_graph_contract_recommendation(record: dict[str, Any]) -> str:
    if str(record.get("source_program_graph_contract_status") or "") != "failed":
        summary = source_program_graph_contract_summary(record)
        if str(summary.get("source_program_graph_contract_status") or "") != "failed":
            return ""
        checks = summary.get("source_program_graph_contract_failed_checks", [])
    else:
        checks = record.get("source_program_graph_contract_failed_checks", [])
    recommendations = [
        SOURCE_PROGRAM_GRAPH_CONTRACT_RECOMMENDATIONS.get(str(check), "improve source program graph mining")
        for check in checks
        if str(check)
    ]
    if not recommendations:
        return "improve source program graph mining"
    return sorted(
        set(recommendations),
        key=lambda item: SOURCE_PROGRAM_GRAPH_CONTRACT_RECOMMENDATION_PRIORITY.get(item, 100),
    )[0]


def reduction_gap_recommendation(transaction: dict[str, Any]) -> str:
    if transaction.get("transaction_kind") != "slp-vectorize-reduction":
        return ""
    errors = [str(item) for item in transaction.get("transaction_consistency_errors", [])]
    unsupported = [str(item) for item in transaction.get("transaction_unsupported_reduction_reasons", [])]
    combined = unsupported + errors
    if "unsupported-reduction-floating-point" in combined:
        return "model FP reduction semantics and fast-math policy"
    if "unsupported-reduction-fp-permutation" in combined:
        return "model ordered FP permutation or fast-math policy"
    if "unsupported-reduction-ambiguous-width" in combined:
        return "improve width provenance mining"
    if "unsupported-reduction-conflicting-width" in combined:
        return "inspect conflicting width evidence"
    if any(item.startswith("unsupported-lane-count:") for item in combined):
        return "add wider reduction lane formal coverage"
    if "unsupported-scalable-fp-reduction" in combined:
        return "recover scalable FP policy evidence"
    if "unsupported-scalable-widening-reduction" in combined:
        return "recover missing scalable widening width evidence"
    if "unsupported-scalable-base-lanes" in combined:
        return "improve scalable lane provenance mining"
    if any(item.startswith("reduction-lane-count-mismatch:") for item in combined):
        return "inspect reduction lane provenance"
    return ""


def transaction_consistency_recommendation(transaction: dict[str, Any]) -> str:
    errors = [str(item) for item in transaction.get("transaction_consistency_errors", [])]
    if "missing-expanded-legality" in errors:
        return "model source predicate helper semantics"
    if any(item.startswith("missing-contract-role:legality") for item in errors):
        return "model source predicate helper semantics"
    if "unsupported-scalable-transaction" in errors and transaction.get("transaction_kind") == "slp-vectorize-minmax":
        return "model scalable min/max vector ops"
    return ""


HELPER_SLICE_GAP_REASONS = {
    "unsupported-recursive-helper-slice",
    "unsupported-unresolved-helper-slice",
    "unsupported-multiple-return-helper-slice",
    "unsupported-incomplete-helper-arguments",
    "unsupported-helper-expansion-depth",
}


HELPER_SLICE_RECOMMENDATIONS = {
    "unsupported-recursive-helper-slice": "model recursive helper slice summaries",
    "unsupported-unresolved-helper-slice": "improve helper body resolution",
    "unsupported-multiple-return-helper-slice": "normalize non-lane-local multi-return helper slices",
    "unsupported-incomplete-helper-arguments": "improve helper argument binding",
    "unsupported-helper-expansion-depth": "summarize non-terminal helper expansion depth",
}


HELPER_SLICE_RECOMMENDATION_PRIORITY = {
    "improve helper body resolution": 0,
    "normalize non-lane-local multi-return helper slices": 1,
    "improve helper argument binding": 2,
    "summarize non-terminal helper expansion depth": 3,
    "model recursive helper slice summaries": 4,
}


def helper_slice_gap_reasons(transaction: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for reason in transaction.get("transaction_graph_absent_reasons", []):
        text = str(reason)
        if text in HELPER_SLICE_GAP_REASONS and text not in reasons:
            reasons.append(text)
    return reasons


def helper_slice_gap_recommendation(transaction: dict[str, Any]) -> str:
    recommendations = [
        HELPER_SLICE_RECOMMENDATIONS[reason]
        for reason in helper_slice_gap_reasons(transaction)
        if reason in HELPER_SLICE_RECOMMENDATIONS
    ]
    if not recommendations:
        return ""
    return sorted(
        recommendations,
        key=lambda item: (HELPER_SLICE_RECOMMENDATION_PRIORITY.get(item, 100), item),
    )[0]


def guard_kinds(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    kinds = []
    for record in value:
        if isinstance(record, dict):
            kind = str(record.get("kind") or "unknown")
            kinds.append(kind)
    return kinds


def formal_parameters(record: dict[str, Any]) -> dict[str, Any]:
    evidence = nested_dict(record, "evidence")
    params = evidence.get("formal_parameters")
    return params if isinstance(params, dict) else {}


def analysis_facts_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = nested_dict(record, "evidence")
    params = formal_parameters(record)
    facts = normalize_analysis_facts(params.get("analysis_facts"))
    if facts:
        return facts
    facts = normalize_analysis_facts(evidence.get("analysis_facts"))
    if facts:
        return facts
    source_graph = evidence.get("source_intent_graph")
    if isinstance(source_graph, dict):
        return normalize_analysis_facts(source_graph.get("analysis_facts"))
    return normalize_analysis_facts(record.get("analysis_facts"))


def source_analysis_fact_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = analysis_fact_summary(analysis_facts_for_record(record))
    return {
        "analysis_fact_count": int(summary["analysis_fact_count"]),
        "analysis_fact_kinds": dict(summary["analysis_fact_kinds"]),
        "analysis_fact_status": dict(summary["analysis_fact_status"]),
        "analysis_fact_roles": dict(summary["analysis_fact_roles"]),
        "analysis_fact_blockers": list(summary["analysis_fact_blockers"]),
    }


def list_parameter(record: dict[str, Any], key: str) -> list[Any]:
    value = formal_parameters(record).get(key)
    return value if isinstance(value, list) else []


def global_initializer_safety_summary(record: dict[str, Any]) -> dict[str, Any]:
    params = formal_parameters(record)
    return {
        "global_initializer_safety_status": str(
            params.get("global.initializer.safety_status") or "unset"
        ),
        "global_initializer_required_safety_facts": [
            str(item)
            for item in params.get("global.initializer.required_safety_facts", [])
            if str(item)
        ] if isinstance(params.get("global.initializer.required_safety_facts"), list) else [],
        "global_initializer_observed_safety_facts": [
            str(item)
            for item in params.get("global.initializer.observed_safety_facts", [])
            if str(item)
        ] if isinstance(params.get("global.initializer.observed_safety_facts"), list) else [],
        "global_initializer_missing_safety_facts": [
            str(item)
            for item in params.get("global.initializer.missing_safety_facts", [])
            if str(item)
        ] if isinstance(params.get("global.initializer.missing_safety_facts"), list) else [],
    }


def globalopt_witness_summary(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("marker") != "probe.globalopt.dead-initializer":
        return {}
    status = str(record.get("globalopt_witness_status") or "")
    witness = record.get("globalopt_witness")
    if not status and isinstance(witness, dict):
        status = str(witness.get("status") or "")
    reasons = record.get("globalopt_witness_failure_reasons")
    if not isinstance(reasons, list) and isinstance(witness, dict):
        reasons = witness.get("failure_reasons")
    model = str(record.get("globalopt_witness_model") or "")
    if not model and isinstance(witness, dict):
        model = str(witness.get("witness_model") or "")
    cases = record.get("globalopt_witness_cases")
    if not isinstance(cases, list) and isinstance(witness, dict):
        cases = witness.get("cases")
    required_cases = record.get("globalopt_required_witness_cases")
    if not isinstance(required_cases, list) and isinstance(witness, dict):
        required_cases = witness.get("required_cases")
    missing_required_cases = record.get("globalopt_missing_required_witness_cases")
    if not isinstance(missing_required_cases, list) and isinstance(witness, dict):
        missing_required_cases = witness.get("missing_required_cases")
    contract = record.get("globalopt_witness_contract")
    if not isinstance(contract, dict) and isinstance(witness, dict):
        contract = witness.get("witness_contract")
    if not isinstance(contract, dict):
        contract = {}
    structural_status = str(record.get("globalopt_witness_structural_status") or "")
    if not structural_status and isinstance(contract, dict):
        structural_status = str(contract.get("structural_status") or "")
    if not structural_status and isinstance(witness, dict):
        structural_status = str(witness.get("structural_status") or "")
    if not structural_status and isinstance(cases, list) and cases:
        structural_case_statuses = [
            str(case.get("structural_checks") or "unset")
            for case in cases
            if isinstance(case, dict)
        ]
        if any(case_status == "failed" for case_status in structural_case_statuses):
            structural_status = "failed"
        elif structural_case_statuses and all(case_status == "passed" for case_status in structural_case_statuses):
            structural_status = "passed"
        else:
            structural_status = "incomplete"
    if not contract:
        contract = witness_contract(
            {
                "status": status or "absent",
                "witness_model": model,
                "required_cases": required_cases if isinstance(required_cases, list) else [],
                "missing_required_cases": missing_required_cases if isinstance(missing_required_cases, list) else [],
                "cases": cases if isinstance(cases, list) else [],
            }
        )
    return {
        "globalopt_witness_status": status or "absent",
        "globalopt_witness_structural_status": structural_status or "absent",
        "globalopt_witness_contract": dict(contract),
        "globalopt_witness_contract_verification_status": str(
            record.get("globalopt_witness_contract_verification_status") or "absent"
        ),
        "globalopt_witness_contract_formal_status": dict(
            record.get("globalopt_witness_contract_formal_status") or {}
        ) if isinstance(record.get("globalopt_witness_contract_formal_status"), dict) else {},
        "globalopt_witness_contract_semantic_status": dict(
            record.get("globalopt_witness_contract_semantic_status") or {}
        ) if isinstance(record.get("globalopt_witness_contract_semantic_status"), dict) else {},
        "globalopt_witness_contract_failed_checks": [
            str(check) for check in record.get("globalopt_witness_contract_failed_checks", []) if str(check)
        ] if isinstance(record.get("globalopt_witness_contract_failed_checks"), list) else [],
        "globalopt_witness_contract_semantic_failed_checks": [
            str(check) for check in record.get("globalopt_witness_contract_semantic_failed_checks", []) if str(check)
        ] if isinstance(record.get("globalopt_witness_contract_semantic_failed_checks"), list) else [],
        "globalopt_witness_contract_formal_obligations": [
            dict(item) for item in record.get("globalopt_witness_contract_formal_obligations", [])
            if isinstance(item, dict)
        ] if isinstance(record.get("globalopt_witness_contract_formal_obligations"), list) else [],
        "globalopt_witness_contract_semantic_obligations": [
            dict(item) for item in record.get("globalopt_witness_contract_semantic_obligations", [])
            if isinstance(item, dict)
        ] if isinstance(record.get("globalopt_witness_contract_semantic_obligations"), list) else [],
        "globalopt_safety_provenance_status": str(record.get("globalopt_safety_provenance_status") or "absent"),
        "globalopt_safety_provenance_failed_checks": [
            str(check) for check in record.get("globalopt_safety_provenance_failed_checks", []) if str(check)
        ] if isinstance(record.get("globalopt_safety_provenance_failed_checks"), list) else [],
        "globalopt_safety_provenance": [
            dict(item) for item in record.get("globalopt_safety_provenance", []) if isinstance(item, dict)
        ] if isinstance(record.get("globalopt_safety_provenance"), list) else [],
        "globalopt_witness_failure_reasons": [
            str(reason) for reason in reasons if str(reason)
        ] if isinstance(reasons, list) else [],
        "globalopt_witness_model": model,
        "globalopt_required_witness_cases": [
            str(case) for case in required_cases if str(case)
        ] if isinstance(required_cases, list) else [],
        "globalopt_missing_required_witness_cases": [
            str(case) for case in missing_required_cases if str(case)
        ] if isinstance(missing_required_cases, list) else [],
        "globalopt_witness_cases": [
            {
                "name": str(case.get("name") or ""),
                "status": str(case.get("status") or "unset"),
                "structural_checks": str(case.get("structural_checks") or ""),
                "structural_details": dict(case.get("structural_details"))
                if isinstance(case.get("structural_details"), dict)
                else {},
                "failure_reasons": [
                    str(reason) for reason in case.get("failure_reasons", []) if str(reason)
                ] if isinstance(case.get("failure_reasons"), list) else [],
            }
            for case in cases
            if isinstance(case, dict)
        ] if isinstance(cases, list) else [],
    }


def predicate_provenance_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "predicate_provenance_verification_status": str(
            record.get("predicate_provenance_verification_status") or "absent"
        ),
        "predicate_provenance_failed_checks": [
            str(check) for check in record.get("predicate_provenance_failed_checks", []) if str(check)
        ] if isinstance(record.get("predicate_provenance_failed_checks"), list) else [],
    }


def globalopt_rewrite_provenance_summary(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("marker") != "probe.globalopt.dead-initializer":
        return {}
    params = formal_parameters(record)
    witness = record.get("globalopt_witness")
    witness_source = witness.get("source_provenance") if isinstance(witness, dict) else {}
    witness_source = witness_source if isinstance(witness_source, dict) else {}

    def value(param_key: str, witness_key: str) -> str:
        return str(params.get(param_key) or witness_source.get(witness_key) or "")

    return {
        "globalopt_rewrite_provenance_status": value(
            "global.initializer.rewrite_provenance_status",
            "rewrite_provenance_status",
        ),
        "globalopt_rewrite_callee": value("global.initializer.rewrite_callee", "rewrite_callee"),
        "globalopt_replacement_expr": value("global.initializer.replacement_expr", "replacement_expr"),
        "globalopt_value_type_expr": value("global.initializer.value_type_expr", "value_type_expr"),
        "globalopt_rewrite_subject": value("global.initializer.subject", "subject"),
    }


def transaction_source_slice_summary(params: dict[str, Any], transaction: dict[str, Any]) -> dict[str, Any]:
    source_slice = transaction.get("source_slice")
    if not isinstance(source_slice, dict):
        source_slice = {}
    completeness = params.get("transaction.source_slice.completeness", source_slice.get("completeness"))
    completeness = completeness if isinstance(completeness, dict) else {}
    missing = sorted(
        str(key)
        for key, value in completeness.items()
        if isinstance(value, bool) and value is False
    )
    has_completeness = bool(completeness)
    complete = has_completeness and not missing and all(value is True for value in completeness.values() if isinstance(value, bool))
    predicate_expansion = params.get(
        "transaction.source_slice.predicate_expansion",
        source_slice.get("predicate_expansion"),
    )
    roles = params.get("transaction.source_slice.predicate_expansion_roles")
    if not isinstance(roles, list):
        roles = [
            str(item.get("role"))
            for item in predicate_expansion
            if isinstance(item, dict) and isinstance(item.get("role"), (str, int, float)) and str(item.get("role"))
        ] if isinstance(predicate_expansion, list) else []
    roles = [str(role) for role in roles if isinstance(role, (str, int, float)) and str(role)]
    contract = source_slice.get("contract")
    if not isinstance(contract, dict):
        contract = {}
    contract_status = str(
        params.get("transaction.source_slice.contract.status")
        or contract.get("status")
        or transaction.get("source_slice_contract_status")
        or ""
    )
    contract_missing_roles = params.get(
        "transaction.source_slice.contract.missing_roles",
        contract.get("missing_roles", transaction.get("source_slice_contract_missing_roles", [])),
    )
    contract_missing_roles = [
        str(role)
        for role in contract_missing_roles
        if isinstance(role, (str, int, float)) and str(role)
    ] if isinstance(contract_missing_roles, list) else []
    contract_checks = params.get(
        "transaction.source_slice.contract.checks",
        contract.get("checks", transaction.get("source_slice_contract_checks", [])),
    )
    contract_checks = [
        dict(check) for check in contract_checks if isinstance(check, dict)
    ] if isinstance(contract_checks, list) else []
    has_source_slice = bool(source_slice) or any(
        key in params
        for key in (
            "transaction.source_slice.control_root_function",
            "transaction.source_slice.completeness",
            "transaction.source_slice.predicate_expansion",
            "transaction.source_slice.predicate_expansion_roles",
            "transaction.source_slice.contract.status",
            "transaction.source_slice.contract.missing_roles",
            "transaction.source_slice.contract.role_paths",
            "transaction.source_slice.contract.checks",
        )
    )
    has_contract = bool(contract) or any(
        key in params
        for key in (
            "transaction.source_slice.contract.status",
            "transaction.source_slice.contract.missing_roles",
            "transaction.source_slice.contract.role_paths",
            "transaction.source_slice.contract.checks",
        )
    ) or any(
        key in transaction
        for key in (
            "source_slice_contract_status",
            "source_slice_contract_missing_roles",
            "source_slice_contract_role_paths",
            "source_slice_contract_checks",
        )
    )
    return {
        "transaction_has_source_slice": has_source_slice,
        "transaction_source_slice_complete": complete,
        "transaction_source_slice_missing": missing,
        "transaction_predicate_expansion_roles": roles,
        "transaction_has_source_slice_contract": has_contract,
        "transaction_source_slice_contract_complete": contract_status == "complete" and not contract_missing_roles,
        "transaction_source_slice_contract_missing_roles": contract_missing_roles,
        "transaction_source_slice_contract_checks": contract_checks,
    }


def transaction_summary(record: dict[str, Any]) -> dict[str, Any]:
    evidence = nested_dict(record, "evidence")
    params = formal_parameters(record)
    transaction = evidence.get("optimization_transaction")
    if not isinstance(transaction, dict):
        return {
            "transaction_present": False,
            "transaction_lowering": "absent",
            "transaction_kind": "",
            "transaction_opcode": "",
            "transaction_lanes": 0,
            "transaction_consistency": "absent",
            "transaction_consistency_errors": [],
            "transaction_has_lane_mapping": False,
            "transaction_has_result_lane_mapping": False,
            "transaction_scalar_lane_pairs": 0,
            "transaction_reduction_opcode": "",
            "transaction_reduction_lanes": 0,
            "transaction_reduction_sources": 0,
            "transaction_has_reduction_result": False,
            "transaction_reduction_family": "",
            "transaction_unsupported_reduction_reasons": [],
            "transaction_reduction_accumulator_bits": 0,
            "transaction_reduction_input_bits": 0,
            "transaction_reduction_result_bits": 0,
            "transaction_reduction_width_status": "",
            "transaction_scalable": False,
            "transaction_base_lanes": 0,
            "transaction_has_source_slice": False,
            "transaction_source_slice_complete": False,
            "transaction_source_slice_missing": [],
            "transaction_predicate_expansion_roles": [],
            "transaction_has_source_slice_contract": False,
            "transaction_source_slice_contract_complete": False,
            "transaction_source_slice_contract_missing_roles": [],
            "transaction_source_slice_contract_checks": [],
            "transaction_has_graph": False,
            "transaction_graph_absent_reasons": [],
            "transaction_graph_absent_diagnostics": [],
            "transaction_graph_kind": "",
            "transaction_graph_consistency": "absent",
            "transaction_graph_node_count": 0,
            "transaction_graph_edge_count": 0,
            "transaction_graph_root_opcode": "",
            "transaction_masked_memory": False,
            "transaction_scalable_memory_pack": False,
            "transaction_memory_contract": "",
            "transaction_store_contract": "",
            "transaction_global_initializer_contract": "",
            "transaction_global_initializer_observability_model": "",
            "transaction_global_initializer_rewrite_api": "",
            "transaction_global_initializer_replacement_kind": "",
        }
    consistency_errors = [
        str(item)
        for item in params.get("transaction.consistency_errors", transaction.get("consistency_errors", []))
        if isinstance(item, (str, int, float)) and str(item)
    ]
    lane_mapping = params.get("transaction.lane_mapping", transaction.get("lane_mapping"))
    result_lane_mapping = params.get("transaction.result_lane_mapping", transaction.get("result_lane_mapping"))
    scalar_pairs = params.get("transaction.scalar_lane_pairs", transaction.get("scalar_lane_pairs"))
    reduction_sources = params.get("transaction.reduction_sources", transaction.get("reduction_sources"))
    reduction_result = params.get("transaction.reduction_result", transaction.get("reduction_result"))
    lanes = params.get("transaction.lanes", transaction.get("lanes", 0))
    reduction_lanes = params.get("transaction.reduction_lanes", transaction.get("reduction_lanes", 0))
    try:
        lane_count = int(lanes or 0)
    except (TypeError, ValueError):
        lane_count = 0
    try:
        reduction_lane_count = int(reduction_lanes or 0)
    except (TypeError, ValueError):
        reduction_lane_count = 0
    reduction_opcode = str(
        params.get("transaction.reduction_opcode") or transaction.get("reduction_opcode") or ""
    )
    def int_param(param_key: str, transaction_key: str) -> int:
        try:
            return int(params.get(param_key) or transaction.get(transaction_key) or 0)
        except (TypeError, ValueError):
            return 0

    reduction_input_bits = int_param("transaction.reduction_input_bits", "reduction_input_bits")
    reduction_accumulator_bits = int_param("transaction.reduction_accumulator_bits", "reduction_accumulator_bits")
    reduction_result_bits = int_param("transaction.reduction_result_bits", "reduction_result_bits")
    reduction_width_status = str(
        params.get("transaction.reduction_width_status")
        or transaction.get("reduction_width_status")
        or ("complete" if reduction_input_bits and reduction_accumulator_bits else "")
    )
    try:
        base_lanes = int(params.get("transaction.base_lanes") or transaction.get("base_lanes") or 0)
    except (TypeError, ValueError):
        base_lanes = 0
    reduction_family = ""
    if reduction_opcode in {"add", "mul"}:
        reduction_family = "arithmetic"
    elif reduction_opcode in {"fadd", "fmul"}:
        reduction_family = "floating-point"
    elif reduction_opcode in {"and", "or", "xor"}:
        reduction_family = "bitwise"
    elif reduction_opcode in {"smin", "smax", "umin", "umax"}:
        reduction_family = "minmax"
    elif reduction_opcode:
        reduction_family = "unsupported"
    unsupported_reduction_reasons = [
        str(item)
        for item in params.get(
            "transaction.unsupported_reduction_reasons",
            transaction.get("unsupported_reduction_reasons", []),
        )
        if isinstance(item, (str, int, float)) and str(item)
    ]
    if not unsupported_reduction_reasons:
        unsupported_reduction_reasons = [
            error for error in consistency_errors if error.startswith("unsupported-reduction-") or error.startswith("unsupported-scalable-")
        ]
    graph = transaction.get("transaction_graph")
    graph_nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    graph_edges = graph.get("edges", []) if isinstance(graph, dict) else []
    graph_absent_reasons = [
        str(item)
        for item in params.get(
            "transaction.graph.absent_reasons",
            transaction.get("transaction_graph_absent_reasons", []),
        )
        if isinstance(item, (str, int, float)) and str(item)
    ]
    graph_absent_diagnostics = [
        {
            "reason": str(item.get("reason") or ""),
            "helper": str(item.get("helper") or ""),
            "role": str(item.get("role") or ""),
            "source": str(item.get("source") or ""),
            "detail": str(item.get("detail") or ""),
            "expansion_stack": [
                str(frame) for frame in item.get("expansion_stack", [])
            ] if isinstance(item.get("expansion_stack"), list) else [],
            "depth": int(item.get("depth") or 0),
        }
        for item in transaction.get("transaction_graph_absent_diagnostics", [])
        if isinstance(item, dict)
    ]
    memory_contract = str(params.get("transaction.graph.memory_contract") or "")
    store_contract = str(params.get("transaction.graph.store_contract") or "")
    global_initializer_contract = str(params.get("global.initializer.contract") or "")
    global_initializer_observability_model = str(
        params.get("global.initializer.observability_model") or ""
    )
    global_initializer_rewrite_api = str(params.get("global.initializer.rewrite_api") or "")
    global_initializer_replacement_kind = str(params.get("global.initializer.replacement_kind") or "")
    masked_memory = bool(params.get("transaction.graph.masked_memory"))
    scalable_memory_pack = bool(params.get("transaction.graph.scalable_memory_pack"))
    scalable_mask_tuple = bool(params.get("transaction.graph.scalable_mask_tuple"))
    if isinstance(graph, dict):
        if not memory_contract:
            memory_contracts = [
                str(operand.get("memory_contract") or "")
                for operand in graph.get("operands", [])
                if isinstance(operand, dict)
            ]
            memory_contract = next((contract for contract in memory_contracts if contract), "")
        if not store_contract:
            store_contracts = [
                str(sink.get("store_contract") or "")
                for sink in graph.get("store_sinks", [])
                if isinstance(sink, dict)
            ]
            store_contract = next((contract for contract in store_contracts if contract), "")
        masked_memory = masked_memory or any(
            bool(operand.get("masked"))
            for operand in graph.get("operands", [])
            if isinstance(operand, dict)
        ) or any(
            bool(sink.get("masked"))
            for sink in graph.get("store_sinks", [])
            if isinstance(sink, dict)
        )
        scalable_memory_pack = scalable_memory_pack or (
            bool(params.get("transaction.scalable") or transaction.get("scalable"))
            and any(
                str(operand.get("kind") or "") == "memory-pack"
                for operand in graph.get("operands", [])
                if isinstance(operand, dict)
            )
        )
    blocker_input = {
        "transaction_graph_absent_reasons": graph_absent_reasons,
        "transaction_consistency_errors": consistency_errors,
        "transaction_graph_absent_diagnostics": graph_absent_diagnostics,
    }
    return {
        "transaction_present": True,
        "transaction_lowering": str(evidence.get("transaction_lowering") or "unset"),
        "transaction_kind": str(params.get("transaction.kind") or transaction.get("kind") or ""),
        "transaction_opcode": str(params.get("transaction.opcode") or transaction.get("opcode") or ""),
        "transaction_lanes": lane_count,
        "transaction_consistency": str(params.get("transaction.consistency") or transaction.get("consistency") or "unchecked"),
        "transaction_consistency_errors": consistency_errors,
        "transaction_has_lane_mapping": isinstance(lane_mapping, dict) and isinstance(lane_mapping.get("map"), list),
        "transaction_has_result_lane_mapping": isinstance(result_lane_mapping, dict)
        and isinstance(result_lane_mapping.get("map"), list),
        "transaction_scalar_lane_pairs": len(scalar_pairs) if isinstance(scalar_pairs, list) else 0,
        "transaction_reduction_opcode": reduction_opcode,
        "transaction_reduction_lanes": reduction_lane_count,
        "transaction_reduction_sources": len(reduction_sources) if isinstance(reduction_sources, list) else 0,
        "transaction_has_reduction_result": isinstance(reduction_result, dict),
        "transaction_reduction_family": reduction_family,
        "transaction_unsupported_reduction_reasons": unsupported_reduction_reasons,
        "transaction_reduction_input_bits": reduction_input_bits,
        "transaction_reduction_accumulator_bits": reduction_accumulator_bits,
        "transaction_reduction_result_bits": reduction_result_bits,
        "transaction_reduction_width_status": reduction_width_status,
        "transaction_scalable": bool(params.get("transaction.scalable") or transaction.get("scalable")),
        "transaction_base_lanes": base_lanes,
        "transaction_has_fp_policy": isinstance(params.get("transaction.fp_policy") or transaction.get("fp_policy"), dict),
        "transaction_has_graph": isinstance(graph, dict),
        "transaction_graph_absent_reasons": graph_absent_reasons,
        "transaction_graph_absent_diagnostics": graph_absent_diagnostics,
        "transaction_graph_kind": str(params.get("transaction.graph.kind") or (graph.get("kind") if isinstance(graph, dict) else "") or ""),
        "transaction_graph_consistency": str((graph.get("consistency") if isinstance(graph, dict) else "") or "absent"),
        "transaction_graph_node_count": int(params.get("transaction.graph.node_count") or (len(graph_nodes) if isinstance(graph_nodes, list) else 0)),
        "transaction_graph_edge_count": int(params.get("transaction.graph.edge_count") or (len(graph_edges) if isinstance(graph_edges, list) else 0)),
        "transaction_graph_root_opcode": str(params.get("transaction.graph.root_opcode") or ""),
        "transaction_masked_memory": masked_memory,
        "transaction_scalable_memory_pack": scalable_memory_pack,
        "transaction_scalable_mask_tuple": scalable_mask_tuple,
        "transaction_mask_blocker_kind": primary_masked_memory_blocker_kind(blocker_input),
        "transaction_mask_blocker_detail": primary_masked_memory_blocker_detail(blocker_input),
        "transaction_memory_address_blocker_kind": primary_memory_address_blocker_kind(blocker_input),
        "transaction_memory_address_blocker_detail": primary_memory_address_blocker_detail(blocker_input),
        "transaction_memory_contract": memory_contract,
        "transaction_store_contract": store_contract,
        "transaction_global_initializer_contract": global_initializer_contract,
        "transaction_global_initializer_observability_model": global_initializer_observability_model,
        "transaction_global_initializer_rewrite_api": global_initializer_rewrite_api,
        "transaction_global_initializer_replacement_kind": global_initializer_replacement_kind,
        **transaction_source_slice_summary(params, transaction),
    }


def source_intent_graph_summary(record: dict[str, Any]) -> dict[str, Any]:
    evidence = nested_dict(record, "evidence")
    params = formal_parameters(record)
    graph = evidence.get("source_intent_graph")
    if not isinstance(graph, dict):
        return {
            "source_intent_graph_status": "absent",
            "source_intent_graph_lowering": "unset",
            "source_intent_graph_unsupported_reasons": [],
            "source_intent_graph_consistency": "absent",
            "source_intent_graph_consistency_errors": [],
            "source_intent_graph_predicate_nodes": 0,
            "source_intent_graph_rewrite_nodes": 0,
            "source_intent_graph_bindings": 0,
        }
    reasons = [
        str(item)
        for item in graph.get("unsupported_reasons", [])
        if isinstance(item, (str, int, float)) and str(item)
    ]
    consistency = str(params.get("source_intent_graph.consistency") or "unchecked")
    consistency_errors = [
        str(item)
        for item in params.get("source_intent_graph.consistency_errors", [])
        if isinstance(item, (str, int, float)) and str(item)
    ]
    return {
        "source_intent_graph_status": str(graph.get("status") or "unknown"),
        "source_intent_graph_lowering": str(evidence.get("source_intent_graph_lowering") or "unset"),
        "source_intent_graph_unsupported_reasons": reasons,
        "source_intent_graph_consistency": consistency,
        "source_intent_graph_consistency_errors": consistency_errors,
        "source_intent_graph_predicate_nodes": len(graph.get("predicate_nodes") or []),
        "source_intent_graph_rewrite_nodes": len(graph.get("rewrite_nodes") or []),
        "source_intent_graph_bindings": len(graph.get("bindings") or []),
    }


def source_program_graph_from_record(record: dict[str, Any]) -> dict[str, Any]:
    transaction = nested_dict(record, "optimization_transaction")
    graph = transaction.get("source_program_graph")
    if isinstance(graph, dict):
        return graph
    evidence = nested_dict(record, "evidence")
    transaction = evidence.get("optimization_transaction")
    if isinstance(transaction, dict):
        graph = transaction.get("source_program_graph")
        if isinstance(graph, dict):
            return graph
    graph = evidence.get("source_program_graph")
    return graph if isinstance(graph, dict) else {}


def source_program_graph_contract_summary(record: dict[str, Any]) -> dict[str, Any]:
    params = formal_parameters(record)
    if "source_program_graph_contract.status" in params:
        failed_checks = params.get("source_program_graph_contract.failed_checks")
        failure_reasons = params.get("source_program_graph_contract.failure_reasons")
        return {
            "source_program_graph_contract_status": str(
                params.get("source_program_graph_contract.status") or ""
            ),
            "source_program_graph_contract_failed_checks": [
                str(item) for item in failed_checks if str(item)
            ] if isinstance(failed_checks, list) else [],
            "source_program_graph_contract_failure_reasons": dict(failure_reasons)
            if isinstance(failure_reasons, dict)
            else {},
            "source_program_graph_cfg_blocks": int(params.get("source_program_graph_contract.cfg_blocks") or 0),
            "source_program_graph_dfg_edges": int(params.get("source_program_graph_contract.dfg_edges") or 0),
            "source_program_graph_interprocedural_dfg": bool(
                params.get("source_program_graph_contract.interprocedural_dfg")
            ),
            "source_program_graph_access_path_facts": int(
                params.get("source_program_graph_contract.access_path_facts") or 0
            ),
        }
    summary = source_graph_contract_summary(source_program_graph_from_record(record))
    return {
        "source_program_graph_contract_status": summary["status"],
        "source_program_graph_contract_failed_checks": list(summary["failed_checks"]),
        "source_program_graph_contract_failure_reasons": dict(summary["failure_reasons"]),
        "source_program_graph_cfg_blocks": int(summary["cfg_blocks"]),
        "source_program_graph_dfg_edges": int(summary["dfg_edges"]),
        "source_program_graph_interprocedural_dfg": bool(summary["interprocedural_dfg"]),
        "source_program_graph_access_path_facts": int(summary["access_path_facts"]),
    }


def source_slice_contract_verification_summary(record: dict[str, Any]) -> dict[str, Any]:
    evidence = nested_dict(record, "evidence")
    verification = evidence.get("source_slice_contract_verification")
    if not isinstance(verification, dict):
        verification = {}
    status = str(evidence.get("source_slice_contract_verification_status") or verification.get("status") or "absent")
    mismatches = evidence.get("source_slice_contract_verification_mismatches", verification.get("mismatches", []))
    return {
        "source_slice_contract_verification_status": status,
        "source_slice_contract_verification_mismatches": [
            dict(item) for item in mismatches if isinstance(item, dict)
        ] if isinstance(mismatches, list) else [],
    }


def transaction_formalization_verification_summary(record: dict[str, Any]) -> dict[str, Any]:
    evidence = nested_dict(record, "evidence")
    verification = evidence.get("transaction_formalization_verification")
    if not isinstance(verification, dict):
        verification = {}
    status = str(evidence.get("transaction_formalization_verification_status") or verification.get("status") or "absent")
    mismatches = evidence.get("transaction_formalization_verification_mismatches", verification.get("mismatches", []))
    coverage = verification.get("provenance_coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    coverage_status = str(
        evidence.get("transaction_formal_provenance_coverage_status")
        or coverage.get("status")
        or "absent"
    )
    missing_paths = evidence.get("transaction_formal_provenance_missing_paths", coverage.get("missing_paths", []))
    roles = evidence.get("transaction_formal_provenance_roles", coverage.get("roles", {}))
    return {
        "transaction_formalization_verification_status": status,
        "transaction_formalization_verification_mismatches": [
            dict(item) for item in mismatches if isinstance(item, dict)
        ] if isinstance(mismatches, list) else [],
        "transaction_formal_provenance_coverage_status": coverage_status,
        "transaction_formal_provenance_missing_paths": [
            str(path) for path in missing_paths if str(path)
        ] if isinstance(missing_paths, list) else [],
        "transaction_formal_provenance_roles": {
            str(role): int(count)
            for role, count in roles.items()
            if str(role)
        } if isinstance(roles, dict) else {},
    }


def guard_summary(record: dict[str, Any], guard_catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    modeled = guard_kinds(record.get("modeled_side_conditions"))
    structural = [
        kind
        for guard in (record.get("modeled_side_conditions") or [])
        if isinstance(guard, dict)
        for kind in [str(guard.get("kind") or "unknown")]
        if str(guard_catalog.get(kind, {}).get("audit_category") or "") == "structural"
    ]
    profitability = guard_kinds(record.get("profitability_guards"))
    unsupported = guard_kinds(record.get("side_conditions"))
    return {
        "modeled_guards": modeled,
        "structural_guards": structural,
        "profitability_guards": profitability,
        "unsupported_guards": unsupported,
    }


def audit_record(
    record: dict[str, Any],
    intents_by_marker: dict[str, dict[str, Any]],
    semantic_by_marker: dict[str, dict[str, Any]],
    guard_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    marker = str(record.get("marker") or "")
    registry_record = intents_by_marker.get(marker, {})
    semantic_record = semantic_by_marker.get(marker, {})
    evidence = nested_dict(record, "evidence")
    candidate = nested_dict(record, "intent_candidate")
    domain = formal_domain(record, registry_record)
    reason = unsupported_reason(record)
    guards = guard_summary(record, guard_catalog)
    graph = source_intent_graph_summary(record)
    source_program_graph = source_program_graph_contract_summary(record)
    global_initializer_safety = global_initializer_safety_summary(record)
    globalopt_witness = globalopt_witness_summary(record)
    predicate_provenance = predicate_provenance_summary(record)
    globalopt_rewrite = globalopt_rewrite_provenance_summary(record)
    contract_verification = source_slice_contract_verification_summary(record)
    formalization_verification = transaction_formalization_verification_summary(record)
    transaction = transaction_summary(record)
    analysis_facts = source_analysis_fact_summary(record)
    derived = list_parameter(record, "source_intent.assumption_algebra.derived") + list_parameter(
        record, "side_conditions.assumption_algebra.derived"
    )
    contradictions = sorted(
        {
            str(item)
            for item in (
                list_parameter(record, "source_intent.assumption_algebra.contradictions")
                + list_parameter(record, "side_conditions.assumption_algebra.contradictions")
                + list_parameter(record, "assumption_algebra.contradictions")
            )
        }
    )
    return {
        "marker": marker,
        "file": str(record.get("file") or ""),
        "line": int(record.get("line") or 0),
        "confidence": str(record.get("confidence") or ""),
        "proof_status": str(record.get("proof_status") or "unset"),
        "proof_result": str(record.get("proof_result") or ""),
        "promotion_status": str(record.get("promotion_status") or ""),
        "semantic_lowering": str(evidence.get("semantic_lowering") or "unset"),
        "formal_inference": str(evidence.get("formal_inference") or "unset"),
        "formal_domain": domain or "unset",
        "registry_covered": bool(registry_record),
        "registry_has_formal": bool(nested_dict(registry_record, "formal")),
        "semantic_registered": bool(semantic_record),
        "unsupported_reason": reason,
        "recommendation": recommendation(record, registry_record, semantic_record),
        "assumption_algebra_derived": len(derived),
        "assumption_algebra_contradictions": len(contradictions),
        "assumption_algebra_contradiction_messages": contradictions,
        "intent": str(candidate.get("intent") or registry_record.get("intent") or ""),
        **graph,
        **source_program_graph,
        **global_initializer_safety,
        **globalopt_witness,
        **predicate_provenance,
        **globalopt_rewrite,
        **contract_verification,
        **formalization_verification,
        **transaction,
        **analysis_facts,
        **guards,
    }


def count(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(record.get(key) or "unset") for record in records).items()))


def reduction_coverage_gaps(records: list[dict[str, Any]]) -> dict[str, Any]:
    reduction_records = [
        record
        for record in records
        if record.get("transaction_kind") == "slp-vectorize-reduction"
    ]
    gap_records = [
        record
        for record in reduction_records
        if record.get("transaction_lowering") not in {"formal-ir", "relaxed-fp-policy"}
        or record.get("transaction_consistency") == "failed"
        or record.get("transaction_unsupported_reduction_reasons")
    ]
    unsupported_reasons = collections.Counter(
        reason
        for record in gap_records
        for reason in record.get("transaction_unsupported_reduction_reasons", [])
    )
    lane_blockers = collections.Counter(
        error
        for record in gap_records
        for error in record.get("transaction_consistency_errors", [])
        if str(error).startswith("unsupported-lane-count:")
        or str(error).startswith("reduction-lane-count-mismatch:")
    )
    width_status = collections.Counter(
        str(record.get("transaction_reduction_width_status") or "unset")
        for record in gap_records
        if record.get("transaction_reduction_width_status")
        or any("width" in str(error) for error in record.get("transaction_consistency_errors", []))
    )
    recommendations = collections.Counter(
        reduction_gap_recommendation(record)
        for record in gap_records
        if reduction_gap_recommendation(record)
    )
    next_target = ""
    if recommendations:
        next_target = sorted(recommendations.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return {
        "records": len(gap_records),
        "unsupported_reasons": dict(sorted(unsupported_reasons.items())),
        "width_status": dict(sorted(width_status.items())),
        "lane_blockers": dict(sorted(lane_blockers.items())),
        "recommendations": dict(sorted(recommendations.items())),
        "next_modeling_target": next_target,
    }


MASKED_MEMORY_GAP_REASONS = {
    "unsupported-unresolved-memory-mask",
    "unsupported-variable-mask-index",
    "unsupported-missing-masked-load-passthru",
    "unsupported-scalable-masked-memory",
    "unsupported-volatile-or-atomic-memory",
    "unsupported-volatile-or-atomic-store",
    "unsupported-unresolved-memory-alias",
}


MASKED_MEMORY_RECOMMENDATIONS = {
    "unsupported-unresolved-memory-mask": "expand mask provenance mining",
    "unsupported-variable-mask-index": "support remaining variable mask index provenance",
    "unsupported-missing-masked-load-passthru": "model remaining implicit masked load passthrough provenance",
    "unsupported-scalable-masked-memory": "classify unresolved scalable mask provenance or unsupported mask syntax",
    "unsupported-unresolved-memory-alias": "improve masked memory alias evidence",
    "unsupported-volatile-or-atomic-memory": "keep volatile/atomic masked memory blocked",
    "unsupported-volatile-or-atomic-store": "keep volatile/atomic masked memory blocked",
}


MASKED_MEMORY_RECOMMENDATION_PRIORITY = {
    "expand mask provenance mining": 0,
    "model remaining implicit masked load passthrough provenance": 1,
    "support remaining variable mask index provenance": 2,
    "classify unresolved scalable mask provenance or unsupported mask syntax": 3,
    "improve masked memory alias evidence": 4,
    "improve helper body resolution": 5,
    "keep volatile/atomic masked memory blocked": 6,
}


MASKED_MEMORY_BLOCKER_KIND_BY_REASON = {
    "unsupported-unresolved-memory-mask": "unresolved-mask",
    "unsupported-variable-mask-index": "unsafe-mask-index",
    "unsupported-missing-masked-load-passthru": "missing-passthru",
    "unsupported-scalable-masked-memory": "scalable-mask-syntax",
    "unsupported-unresolved-memory-alias": "alias",
    "unsupported-volatile-or-atomic-memory": "volatile-atomic",
    "unsupported-volatile-or-atomic-store": "volatile-atomic",
}


MASKED_MEMORY_BLOCKER_RECOMMENDATIONS = {
    "unresolved-mask": "expand mask provenance mining",
    "unsafe-mask-index": "support remaining variable mask index provenance",
    "missing-passthru": "model remaining implicit masked load passthrough provenance",
    "scalable-mask-syntax": "classify unresolved scalable mask provenance or unsupported mask syntax",
    "alias": "improve masked memory alias evidence",
    "helper-slice": "improve helper body resolution",
    "volatile-atomic": "keep volatile/atomic masked memory blocked",
}


MASKED_MEMORY_BLOCKER_PRIORITY = {
    "unresolved-mask": 0,
    "missing-passthru": 1,
    "unsafe-mask-index": 2,
    "scalable-mask-syntax": 3,
    "alias": 4,
    "helper-slice": 5,
    "volatile-atomic": 6,
}


def masked_memory_gap_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("transaction_graph_absent_reasons", "transaction_consistency_errors"):
        for reason in record.get(key, []):
            text = str(reason)
            if text in MASKED_MEMORY_GAP_REASONS and text not in reasons:
                reasons.append(text)
    return reasons


def masked_memory_blocker_kinds(record: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for reason in masked_memory_gap_reasons(record):
        kind = MASKED_MEMORY_BLOCKER_KIND_BY_REASON.get(reason)
        if kind and kind not in kinds:
            kinds.append(kind)
    diagnostics = record.get("transaction_graph_absent_diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            reason = str(diagnostic.get("reason") or "")
            role = str(diagnostic.get("role") or "")
            if reason in HELPER_SLICE_GAP_REASONS and role in {"memory-pack", "memory-store", "masked-memory"}:
                if "helper-slice" not in kinds:
                    kinds.append("helper-slice")
    return kinds


def masked_memory_blocker_details(record: dict[str, Any]) -> list[str]:
    details: list[str] = []
    diagnostics = record.get("transaction_graph_absent_diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            detail = str(diagnostic.get("detail") or "")
            if detail and detail not in details:
                details.append(detail)
            reason = str(diagnostic.get("reason") or "")
            role = str(diagnostic.get("role") or "")
            if reason in HELPER_SLICE_GAP_REASONS and role in {"memory-pack", "memory-store", "masked-memory"}:
                helper_detail = "helper-slice:" + reason
                if helper_detail not in details:
                    details.append(helper_detail)
    if not details:
        for kind in masked_memory_blocker_kinds(record):
            fallback = kind
            if fallback not in details:
                details.append(fallback)
    return details


def primary_masked_memory_blocker_kind(record: dict[str, Any]) -> str:
    kinds = masked_memory_blocker_kinds(record)
    if not kinds:
        return ""
    return sorted(kinds, key=lambda kind: (MASKED_MEMORY_BLOCKER_PRIORITY.get(kind, 100), kind))[0]


def primary_masked_memory_blocker_detail(record: dict[str, Any]) -> str:
    details = masked_memory_blocker_details(record)
    return details[0] if details else ""


MEMORY_ADDRESS_GAP_REASONS = {
    "unsupported-variable-gather-index",
    "unsupported-variable-store-index",
    "unsupported-duplicate-gather-lane",
    "unsupported-duplicate-scatter-lane",
    "unsupported-ambiguous-memory-base",
    "unsupported-ambiguous-store-base",
    "unresolved-gather-lane-address",
    "unresolved-memory-lane-address",
    "unresolved-store-lane-address",
}


MEMORY_ADDRESS_BLOCKER_KIND_BY_REASON = {
    "unsupported-variable-gather-index": "unsafe-gather-index",
    "unsupported-variable-store-index": "unsafe-store-index",
    "unsupported-duplicate-gather-lane": "duplicate-gather-lane",
    "unsupported-duplicate-scatter-lane": "duplicate-scatter-lane",
    "unsupported-ambiguous-memory-base": "ambiguous-memory-base",
    "unsupported-ambiguous-store-base": "ambiguous-store-base",
    "unresolved-gather-lane-address": "unresolved-gather-address",
    "unresolved-memory-lane-address": "unresolved-memory-address",
    "unresolved-store-lane-address": "unresolved-store-address",
}


MEMORY_ADDRESS_BLOCKER_RECOMMENDATIONS = {
    "unsafe-gather-index": "model safe symbolic gather indexes",
    "unsafe-store-index": "model safe symbolic store indexes",
    "duplicate-gather-lane": "classify duplicate gather lane semantics",
    "duplicate-scatter-lane": "classify duplicate scatter lane semantics",
    "ambiguous-memory-base": "improve memory base provenance",
    "ambiguous-store-base": "improve store base provenance",
    "unresolved-gather-address": "expand gather address mining",
    "unresolved-memory-address": "expand memory address mining",
    "unresolved-store-address": "expand store address mining",
}


MEMORY_ADDRESS_BLOCKER_PRIORITY = {
    "unsafe-gather-index": 0,
    "unsafe-store-index": 1,
    "duplicate-gather-lane": 2,
    "duplicate-scatter-lane": 3,
    "ambiguous-memory-base": 4,
    "ambiguous-store-base": 5,
    "unresolved-gather-address": 6,
    "unresolved-store-address": 7,
    "unresolved-memory-address": 8,
}


def memory_address_gap_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("transaction_graph_absent_reasons", "transaction_consistency_errors"):
        for reason in record.get(key, []):
            text = str(reason)
            if text in MEMORY_ADDRESS_GAP_REASONS and text not in reasons:
                reasons.append(text)
    return reasons


def memory_address_blocker_kinds(record: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for reason in memory_address_gap_reasons(record):
        kind = MEMORY_ADDRESS_BLOCKER_KIND_BY_REASON.get(reason)
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


def memory_address_blocker_details(record: dict[str, Any]) -> list[str]:
    details: list[str] = []
    diagnostics = record.get("transaction_graph_absent_diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            reason = str(diagnostic.get("reason") or "")
            if reason not in MEMORY_ADDRESS_GAP_REASONS:
                continue
            detail = str(diagnostic.get("detail") or "")
            if detail and detail not in details:
                details.append(detail)
    if not details:
        for kind in memory_address_blocker_kinds(record):
            if kind not in details:
                details.append(kind)
    return details


def primary_memory_address_blocker_kind(record: dict[str, Any]) -> str:
    kinds = memory_address_blocker_kinds(record)
    if not kinds:
        return ""
    return sorted(kinds, key=lambda kind: (MEMORY_ADDRESS_BLOCKER_PRIORITY.get(kind, 100), kind))[0]


def primary_memory_address_blocker_detail(record: dict[str, Any]) -> str:
    details = memory_address_blocker_details(record)
    return details[0] if details else ""


def memory_address_coverage_gaps(records: list[dict[str, Any]]) -> dict[str, Any]:
    transaction_records = [record for record in records if record.get("transaction_present")]
    gap_records = [
        record
        for record in transaction_records
        if memory_address_gap_reasons(record) or memory_address_blocker_kinds(record)
    ]
    unsupported_reasons = collections.Counter(
        reason for record in gap_records for reason in memory_address_gap_reasons(record)
    )
    blocker_kinds = collections.Counter(
        kind for record in gap_records for kind in memory_address_blocker_kinds(record)
    )
    blocker_details = collections.Counter(
        detail for record in gap_records for detail in memory_address_blocker_details(record)
    )
    recommendations = collections.Counter(
        MEMORY_ADDRESS_BLOCKER_RECOMMENDATIONS[kind]
        for kind, count in blocker_kinds.items()
        for _ in range(count)
        if kind in MEMORY_ADDRESS_BLOCKER_RECOMMENDATIONS
    )
    next_target = ""
    if blocker_kinds:
        next_kind = sorted(
            blocker_kinds.items(),
            key=lambda item: (-item[1], MEMORY_ADDRESS_BLOCKER_PRIORITY.get(item[0], 100), item[0]),
        )[0][0]
        next_target = MEMORY_ADDRESS_BLOCKER_RECOMMENDATIONS.get(next_kind, "")
    return {
        "records": len(gap_records),
        "unsupported_reasons": dict(sorted(unsupported_reasons.items())),
        "blocker_kinds": dict(sorted(blocker_kinds.items())),
        "blocker_details": dict(sorted(blocker_details.items())),
        "recommendations": dict(sorted(recommendations.items())),
        "next_modeling_target": next_target,
    }


def masked_memory_coverage_gaps(records: list[dict[str, Any]]) -> dict[str, Any]:
    transaction_records = [record for record in records if record.get("transaction_present")]
    masked_records = [
        record
        for record in transaction_records
        if record.get("transaction_masked_memory") or masked_memory_gap_reasons(record) or masked_memory_blocker_kinds(record)
    ]
    gap_records = [record for record in masked_records if masked_memory_gap_reasons(record) or masked_memory_blocker_kinds(record)]
    unsupported_reasons = collections.Counter(
        reason for record in gap_records for reason in masked_memory_gap_reasons(record)
    )
    blocker_kinds = collections.Counter(
        kind for record in gap_records for kind in masked_memory_blocker_kinds(record)
    )
    blocker_details = collections.Counter(
        detail for record in gap_records for detail in masked_memory_blocker_details(record)
    )
    recommendations = collections.Counter(
        MASKED_MEMORY_RECOMMENDATIONS[reason]
        for reason in unsupported_reasons
        for _ in range(unsupported_reasons[reason])
        if reason in MASKED_MEMORY_RECOMMENDATIONS
    )
    recommendations.update(
        MASKED_MEMORY_BLOCKER_RECOMMENDATIONS[kind]
        for kind, count in blocker_kinds.items()
        for _ in range(count)
        if kind == "helper-slice"
    )
    next_target = ""
    if blocker_kinds:
        next_kind = sorted(
            blocker_kinds.items(),
            key=lambda item: (-item[1], MASKED_MEMORY_BLOCKER_PRIORITY.get(item[0], 100), item[0]),
        )[0][0]
        next_target = MASKED_MEMORY_BLOCKER_RECOMMENDATIONS.get(next_kind, "")
    elif recommendations:
        next_target = sorted(
            recommendations.items(),
            key=lambda item: (-item[1], MASKED_MEMORY_RECOMMENDATION_PRIORITY.get(item[0], 100), item[0]),
        )[0][0]
    return {
        "records": len(gap_records),
        "masked_records": len(masked_records),
        "covered_records": sum(
            1
            for record in masked_records
            if record.get("transaction_masked_memory")
            and record.get("transaction_lowering") in {"formal-ir", "relaxed-fp-policy"}
            and not masked_memory_gap_reasons(record)
        ),
        "unsupported_reasons": dict(sorted(unsupported_reasons.items())),
        "blocker_kinds": dict(sorted(blocker_kinds.items())),
        "blocker_details": dict(sorted(blocker_details.items())),
        "recommendations": dict(sorted(recommendations.items())),
        "next_modeling_target": next_target,
    }


def helper_slice_coverage_gaps(records: list[dict[str, Any]]) -> dict[str, Any]:
    transaction_records = [record for record in records if record.get("transaction_present")]
    gap_records = [
        record for record in transaction_records if helper_slice_gap_reasons(record)
    ]
    diagnostics = [
        diagnostic
        for record in gap_records
        for diagnostic in record.get("transaction_graph_absent_diagnostics", [])
        if isinstance(diagnostic, dict)
    ]
    unsupported_reasons = collections.Counter(
        reason for record in gap_records for reason in helper_slice_gap_reasons(record)
    )
    diagnostic_reasons = collections.Counter(
        str(diagnostic.get("reason") or "")
        for diagnostic in diagnostics
        if str(diagnostic.get("reason") or "")
    )
    helpers = collections.Counter(
        str(diagnostic.get("helper") or "")
        for diagnostic in diagnostics
        if str(diagnostic.get("helper") or "")
    )
    roles = collections.Counter(
        str(diagnostic.get("role") or "")
        for diagnostic in diagnostics
        if str(diagnostic.get("role") or "")
    )
    diagnostic_records = []
    for record in gap_records:
        for diagnostic in record.get("transaction_graph_absent_diagnostics", []):
            if not isinstance(diagnostic, dict):
                continue
            reason = str(diagnostic.get("reason") or "")
            if not reason:
                continue
            diagnostic_records.append({
                "file": str(record.get("file") or ""),
                "line": int(record.get("line") or 0),
                "marker": str(record.get("marker") or ""),
                "reason": reason,
                "helper": str(diagnostic.get("helper") or ""),
                "role": str(diagnostic.get("role") or ""),
                "source": str(diagnostic.get("source") or ""),
                "expansion_stack": [
                    str(frame) for frame in diagnostic.get("expansion_stack", [])
                ] if isinstance(diagnostic.get("expansion_stack"), list) else [],
                "depth": int(diagnostic.get("depth") or 0),
                "recommendation": HELPER_SLICE_RECOMMENDATIONS.get(reason, ""),
            })
    diagnostic_records.sort(
        key=lambda item: (
            item["reason"],
            item["helper"],
            item["role"],
            item["file"],
            int(item["line"]),
        )
    )
    recommendations = collections.Counter(
        HELPER_SLICE_RECOMMENDATIONS[reason]
        for reason in unsupported_reasons
        for _ in range(unsupported_reasons[reason])
        if reason in HELPER_SLICE_RECOMMENDATIONS
    )
    next_target = ""
    if recommendations:
        next_target = sorted(
            recommendations.items(),
            key=lambda item: (
                -item[1],
                HELPER_SLICE_RECOMMENDATION_PRIORITY.get(item[0], 100),
                item[0],
            ),
        )[0][0]
    return {
        "records": len(gap_records),
        "unsupported_reasons": dict(sorted(unsupported_reasons.items())),
        "diagnostic_reasons": dict(sorted(diagnostic_reasons.items())),
        "helpers": dict(sorted(helpers.items())),
        "roles": dict(sorted(roles.items())),
        "diagnostics": diagnostic_records[:25],
        "recommendations": dict(sorted(recommendations.items())),
        "next_modeling_target": next_target,
    }


def source_program_graph_contract_gaps(records: list[dict[str, Any]]) -> dict[str, Any]:
    gap_records = [
        record
        for record in records
        if str(record.get("source_program_graph_contract_status") or "") == "failed"
    ]
    failed_checks = collections.Counter(
        str(check)
        for record in gap_records
        for check in record.get("source_program_graph_contract_failed_checks", [])
        if str(check)
    )
    failure_reasons = collections.Counter(
        str(reason)
        for record in gap_records
        for reason in record.get("source_program_graph_contract_failure_reasons", {})
        if str(reason)
    )
    recommendations = collections.Counter(
        source_program_graph_contract_recommendation(record)
        for record in gap_records
        if source_program_graph_contract_recommendation(record)
    )
    samples = [
        {
            "file": str(record.get("file") or ""),
            "line": int(record.get("line") or 0),
            "marker": str(record.get("marker") or ""),
            "failed_checks": [
                str(check)
                for check in record.get("source_program_graph_contract_failed_checks", [])
                if str(check)
            ],
            "failure_reasons": dict(record.get("source_program_graph_contract_failure_reasons", {}))
            if isinstance(record.get("source_program_graph_contract_failure_reasons"), dict)
            else {},
            "recommendation": source_program_graph_contract_recommendation(record),
        }
        for record in gap_records[:25]
    ]
    next_target = ""
    if recommendations:
        next_target = sorted(
            recommendations.items(),
            key=lambda item: (
                -int(item[1]),
                SOURCE_PROGRAM_GRAPH_CONTRACT_RECOMMENDATION_PRIORITY.get(item[0], 100),
                item[0],
            ),
        )[0][0]
    return {
        "records": len(gap_records),
        "failed_checks": dict(sorted(failed_checks.items())),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "recommendations": dict(sorted(recommendations.items())),
        "samples": samples,
        "next_modeling_target": next_target,
    }


def summarize(records: list[dict[str, Any]], missing_markers: list[str]) -> dict[str, Any]:
    modeled_guards = collections.Counter(kind for record in records for kind in record.get("modeled_guards", []))
    structural_guards = collections.Counter(kind for record in records for kind in record.get("structural_guards", []))
    profitability_guards = collections.Counter(kind for record in records for kind in record.get("profitability_guards", []))
    unsupported_guards = collections.Counter(kind for record in records for kind in record.get("unsupported_guards", []))
    unsupported_records = unsupported_guard_records(records)
    derived_count = sum(int(record.get("assumption_algebra_derived") or 0) for record in records)
    contradiction_records = [
        {
            "file": record.get("file", ""),
            "line": record.get("line", 0),
            "marker": record.get("marker", ""),
            "messages": record.get("assumption_algebra_contradiction_messages", []),
        }
        for record in records
        if int(record.get("assumption_algebra_contradictions") or 0)
    ]
    graph_status = collections.Counter(str(record.get("source_intent_graph_status") or "absent") for record in records)
    graph_lowering = collections.Counter(str(record.get("source_intent_graph_lowering") or "unset") for record in records)
    graph_reasons = collections.Counter(
        reason
        for record in records
        for reason in record.get("source_intent_graph_unsupported_reasons", [])
    )
    graph_consistency = collections.Counter(str(record.get("source_intent_graph_consistency") or "absent") for record in records)
    graph_consistency_errors = collections.Counter(
        error
        for record in records
        for error in record.get("source_intent_graph_consistency_errors", [])
    )
    source_program_graph_status = collections.Counter(
        str(record.get("source_program_graph_contract_status") or "absent")
        for record in records
    )
    source_program_graph_failed_checks = collections.Counter(
        check
        for record in records
        for check in record.get("source_program_graph_contract_failed_checks", [])
    )
    source_program_graph_failure_reasons = collections.Counter(
        reason
        for record in records
        for reason in record.get("source_program_graph_contract_failure_reasons", {})
    )
    analysis_fact_kinds = collections.Counter(
        kind
        for record in records
        for kind, count in (record.get("analysis_fact_kinds") or {}).items()
        for _ in range(int(count))
    )
    analysis_fact_status = collections.Counter(
        status
        for record in records
        for status, count in (record.get("analysis_fact_status") or {}).items()
        for _ in range(int(count))
    )
    analysis_fact_roles = collections.Counter(
        role
        for record in records
        for role, count in (record.get("analysis_fact_roles") or {}).items()
        for _ in range(int(count))
    )
    dse_analysis_recommendations = collections.Counter(
        missing_dse_analysis_fact_recommendation(str(record.get("marker") or ""), analysis_facts_for_record(record))
        for record in records
        if missing_dse_analysis_fact_recommendation(str(record.get("marker") or ""), analysis_facts_for_record(record))
    )
    global_initializer_records = [
        record for record in records if record.get("marker") == "probe.globalopt.dead-initializer"
    ]
    global_initializer_safety_status = collections.Counter(
        str(record.get("global_initializer_safety_status") or "unset")
        for record in global_initializer_records
    )
    global_initializer_required_safety_facts = collections.Counter(
        fact
        for record in global_initializer_records
        for fact in record.get("global_initializer_required_safety_facts", [])
    )
    global_initializer_observed_safety_facts = collections.Counter(
        fact
        for record in global_initializer_records
        for fact in record.get("global_initializer_observed_safety_facts", [])
    )
    global_initializer_missing_safety_facts = collections.Counter(
        fact
        for record in global_initializer_records
        for fact in record.get("global_initializer_missing_safety_facts", [])
    )
    globalopt_witness_status = collections.Counter(
        str(record.get("globalopt_witness_status") or "absent")
        for record in global_initializer_records
    )
    globalopt_witness_failure_reasons = collections.Counter(
        str(reason).split(":", 1)[0]
        for record in global_initializer_records
        for reason in record.get("globalopt_witness_failure_reasons", [])
        if str(reason)
    )
    globalopt_witness_case_status = collections.Counter(
        (str(case.get("name") or "unknown"), str(case.get("status") or "unset"))
        for record in global_initializer_records
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_witness_structural_status = collections.Counter(
        str(record.get("globalopt_witness_structural_status") or "absent")
        for record in global_initializer_records
    )
    globalopt_witness_case_structural_status = collections.Counter(
        (str(case.get("name") or "unknown"), str(case.get("structural_checks") or "unset"))
        for record in global_initializer_records
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_witness_case_changed_lines = collections.Counter(
        (
            str(case.get("name") or "unknown"),
            str((case.get("structural_details") or {}).get("changed_line_count") if isinstance(case.get("structural_details"), dict) else "unset"),
        )
        for record in global_initializer_records
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_contract_verification_status = collections.Counter(
        str(record.get("globalopt_witness_contract_verification_status") or "absent")
        for record in global_initializer_records
    )
    globalopt_safety_provenance_status = collections.Counter(
        str(record.get("globalopt_safety_provenance_status") or "absent")
        for record in global_initializer_records
    )
    globalopt_safety_provenance_failed_checks = collections.Counter(
        str(check)
        for record in global_initializer_records
        for check in record.get("globalopt_safety_provenance_failed_checks", [])
        if str(check)
    )
    predicate_provenance_verification_status = collections.Counter(
        str(record.get("predicate_provenance_verification_status") or "absent")
        for record in records
    )
    predicate_provenance_failed_checks = collections.Counter(
        str(check)
        for record in records
        for check in record.get("predicate_provenance_failed_checks", [])
        if str(check)
    )
    predicate_provenance_checked = sum(
        1
        for record in records
        if str(record.get("predicate_provenance_verification_status") or "absent") != "absent"
    )
    globalopt_contract_formal_status = collections.Counter(
        str(status)
        for record in global_initializer_records
        for status, count in (record.get("globalopt_witness_contract_formal_status") or {}).items()
        for _ in range(int(count))
    )
    globalopt_contract_semantic_status = collections.Counter(
        str(status)
        for record in global_initializer_records
        for status, count in (record.get("globalopt_witness_contract_semantic_status") or {}).items()
        for _ in range(int(count))
    )
    globalopt_contract_failed_checks = collections.Counter(
        str(check)
        for record in global_initializer_records
        for check in record.get("globalopt_witness_contract_failed_checks", [])
        if str(check)
    )
    globalopt_contract_semantic_failed_checks = collections.Counter(
        str(check)
        for record in global_initializer_records
        for check in record.get("globalopt_witness_contract_semantic_failed_checks", [])
        if str(check)
    )
    globalopt_required_witness_cases = collections.Counter(
        str(case)
        for record in global_initializer_records
        for case in record.get("globalopt_required_witness_cases", [])
        if str(case)
    )
    globalopt_missing_required_witness_cases = collections.Counter(
        str(case)
        for record in global_initializer_records
        for case in record.get("globalopt_missing_required_witness_cases", [])
        if str(case)
    )
    globalopt_rewrite_provenance_status = collections.Counter(
        str(record.get("globalopt_rewrite_provenance_status") or "absent")
        for record in global_initializer_records
    )
    globalopt_rewrite_callee = collections.Counter(
        str(record.get("globalopt_rewrite_callee") or "absent")
        for record in global_initializer_records
    )
    globalopt_replacement_expr = collections.Counter(
        str(record.get("globalopt_replacement_expr") or "absent")
        for record in global_initializer_records
    )
    globalopt_value_type_expr = collections.Counter(
        str(record.get("globalopt_value_type_expr") or "absent")
        for record in global_initializer_records
    )
    contract_verification_status = collections.Counter(
        str(record.get("source_slice_contract_verification_status") or "absent")
        for record in records
    )
    contract_verification_mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for record in records
        for mismatch in record.get("source_slice_contract_verification_mismatches", [])
        if isinstance(mismatch, dict)
    )
    formalization_verification_status = collections.Counter(
        str(record.get("transaction_formalization_verification_status") or "absent")
        for record in records
    )
    formalization_verification_mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for record in records
        for mismatch in record.get("transaction_formalization_verification_mismatches", [])
        if isinstance(mismatch, dict)
    )
    formal_provenance_coverage_status = collections.Counter(
        str(record.get("transaction_formal_provenance_coverage_status") or "absent")
        for record in records
    )
    formal_provenance_roles = collections.Counter(
        str(role)
        for record in records
        for role, count in (record.get("transaction_formal_provenance_roles") or {}).items()
        for _ in range(int(count))
    )
    formal_provenance_missing_paths = collections.Counter(
        str(path)
        for record in records
        for path in record.get("transaction_formal_provenance_missing_paths", [])
    )
    transaction_records = [record for record in records if record.get("transaction_present")]
    transaction_errors = collections.Counter(
        error
        for record in transaction_records
        for error in record.get("transaction_consistency_errors", [])
    )
    unsupported_reduction_reasons = collections.Counter(
        reason
        for record in transaction_records
        for reason in record.get("transaction_unsupported_reduction_reasons", [])
    )
    source_slice_missing = collections.Counter(
        reason
        for record in transaction_records
        for reason in record.get("transaction_source_slice_missing", [])
    )
    predicate_expansion_roles = collections.Counter(
        role
        for record in transaction_records
        for role in record.get("transaction_predicate_expansion_roles", [])
    )
    source_slice_contract_missing_roles = collections.Counter(
        role
        for record in transaction_records
        for role in record.get("transaction_source_slice_contract_missing_roles", [])
    )
    source_slice_contract_failed_checks = collections.Counter(
        str(check.get("id") or check.get("kind") or "unknown")
        for record in transaction_records
        for check in record.get("transaction_source_slice_contract_checks", [])
        if isinstance(check, dict) and str(check.get("status") or "") == "failed"
    )
    source_slice_contract_failed_kinds = collections.Counter(
        str(check.get("kind") or "unknown")
        for record in transaction_records
        for check in record.get("transaction_source_slice_contract_checks", [])
        if isinstance(check, dict) and str(check.get("status") or "") == "failed"
    )
    reduction_records = [
        record
        for record in transaction_records
        if record.get("transaction_reduction_opcode") or int(record.get("transaction_reduction_lanes") or 0)
    ]
    reduction_gaps = reduction_coverage_gaps(records)
    masked_memory_gaps = masked_memory_coverage_gaps(records)
    memory_address_gaps = memory_address_coverage_gaps(records)
    helper_slice_gaps = helper_slice_coverage_gaps(records)
    graph_contract_gaps = source_program_graph_contract_gaps(records)
    return {
        "records": len(records),
        "missing_registry_markers": len(missing_markers),
        "proof_status": count(records, "proof_status"),
        "proof_result": count(records, "proof_result"),
        "promotion_status": count(records, "promotion_status"),
        "semantic_lowering": count(records, "semantic_lowering"),
        "formal_inference": count(records, "formal_inference"),
        "formal_domain": count(records, "formal_domain"),
        "unsupported_reason": count(records, "unsupported_reason"),
        "recommendation": count(records, "recommendation"),
        "guard_handling": {
            "modeled": dict(sorted(modeled_guards.items())),
            "structural": dict(sorted(structural_guards.items())),
            "profitability": dict(sorted(profitability_guards.items())),
            "unsupported": dict(sorted(unsupported_guards.items())),
        },
        "unsupported_guard_records": unsupported_records,
        "assumption_algebra": {
            "derived": derived_count,
            "contradictions": len(contradiction_records),
            "contradiction_records": contradiction_records,
        },
        "source_intent_graph": {
            "status": dict(sorted(graph_status.items())),
            "lowering": dict(sorted(graph_lowering.items())),
            "unsupported_reasons": dict(sorted(graph_reasons.items())),
            "complete": int(graph_status.get("complete", 0)),
            "with_bindings": sum(1 for record in records if int(record.get("source_intent_graph_bindings") or 0) > 0),
            "missing_rewrite": int(graph_reasons.get("missing-rewrite", 0)),
            "unbound_symbols": int(graph_reasons.get("unbound-symbols", 0)),
            "consistency": dict(sorted(graph_consistency.items())),
            "consistency_failures": int(graph_consistency.get("failed", 0)),
            "consistency_errors": dict(sorted(graph_consistency_errors.items())),
            "lowered_despite_inconsistency": sum(
                1
                for record in records
                if record.get("source_intent_graph_lowering") == "formal-ir"
                and record.get("source_intent_graph_consistency") == "failed"
            ),
        },
        "source_program_graph_contract": {
            "status": dict(sorted(source_program_graph_status.items())),
            "failed_checks": dict(sorted(source_program_graph_failed_checks.items())),
            "failure_reasons": dict(sorted(source_program_graph_failure_reasons.items())),
            "gaps": graph_contract_gaps,
            "passed": int(source_program_graph_status.get("passed", 0)),
            "failed": int(source_program_graph_status.get("failed", 0)),
            "with_cfg": sum(1 for record in records if int(record.get("source_program_graph_cfg_blocks") or 0) > 0),
            "with_dfg": sum(1 for record in records if int(record.get("source_program_graph_dfg_edges") or 0) > 0),
            "with_interprocedural_dfg": sum(
                1 for record in records if record.get("source_program_graph_interprocedural_dfg")
            ),
            "with_access_paths": sum(
                1 for record in records if int(record.get("source_program_graph_access_path_facts") or 0) > 0
            ),
        },
        "analysis_facts": {
            "records": sum(1 for record in records if int(record.get("analysis_fact_count") or 0) > 0),
            "total": sum(int(record.get("analysis_fact_count") or 0) for record in records),
            "kinds": dict(sorted(analysis_fact_kinds.items())),
            "status": dict(sorted(analysis_fact_status.items())),
            "roles": dict(sorted(analysis_fact_roles.items())),
            "blockers": sum(len(record.get("analysis_fact_blockers") or []) for record in records),
            "dse_recommendations": dict(sorted(dse_analysis_recommendations.items())),
        },
        "global_initializer_safety": {
            "records": len(global_initializer_records),
            "status": dict(sorted(global_initializer_safety_status.items())),
            "required_facts": dict(sorted(global_initializer_required_safety_facts.items())),
            "observed_facts": dict(sorted(global_initializer_observed_safety_facts.items())),
            "missing_facts": dict(sorted(global_initializer_missing_safety_facts.items())),
            "complete": int(global_initializer_safety_status.get("complete", 0)),
            "incomplete": int(global_initializer_safety_status.get("incomplete", 0)),
        },
        "predicate_provenance": {
            "records": len(records),
            "checked": predicate_provenance_checked,
            "passed": int(predicate_provenance_verification_status.get("passed", 0)),
            "failed": int(predicate_provenance_verification_status.get("failed", 0)),
            "absent": int(predicate_provenance_verification_status.get("absent", 0)),
            "verification_status": dict(sorted(predicate_provenance_verification_status.items())),
            "failed_checks": dict(sorted(predicate_provenance_failed_checks.items())),
        },
        "globalopt_witnesses": {
            "status": dict(sorted(globalopt_witness_status.items())),
            "failures": dict(sorted(globalopt_witness_failure_reasons.items())),
            "required_cases": dict(sorted(globalopt_required_witness_cases.items())),
            "missing_required_cases": dict(sorted(globalopt_missing_required_witness_cases.items())),
            "structural_status": dict(sorted(globalopt_witness_structural_status.items())),
            "structural_cases": {
                name: dict(sorted(statuses.items()))
                for name, statuses in sorted(
                    {
                        case_name: collections.Counter({
                            status: globalopt_witness_case_structural_status[(case_name, status)]
                            for candidate_name, status in globalopt_witness_case_structural_status
                            if candidate_name == case_name
                        })
                        for case_name, _ in globalopt_witness_case_structural_status
                    }.items()
                )
            },
            "changed_line_counts": {
                f"{name}:{changed_lines}": count
                for (name, changed_lines), count in sorted(globalopt_witness_case_changed_lines.items())
            },
            "contract_verification_status": dict(sorted(globalopt_contract_verification_status.items())),
            "safety_provenance_status": dict(sorted(globalopt_safety_provenance_status.items())),
            "safety_provenance_failed_checks": dict(sorted(globalopt_safety_provenance_failed_checks.items())),
            "contract_formal_status": dict(sorted(globalopt_contract_formal_status.items())),
            "contract_semantic_status": dict(sorted(globalopt_contract_semantic_status.items())),
            "contract_failed_checks": dict(sorted(globalopt_contract_failed_checks.items())),
            "contract_semantic_failed_checks": dict(sorted(globalopt_contract_semantic_failed_checks.items())),
            "cases": {
                name: dict(sorted(statuses.items()))
                for name, statuses in sorted(
                    {
                        case_name: collections.Counter({
                            status: globalopt_witness_case_status[(case_name, status)]
                            for candidate_name, status in globalopt_witness_case_status
                            if candidate_name == case_name
                        })
                        for case_name, _ in globalopt_witness_case_status
                    }.items()
                )
            },
            "passed": int(globalopt_witness_status.get("passed", 0)),
            "failed": int(globalopt_witness_status.get("failed", 0)),
            "absent": int(globalopt_witness_status.get("absent", 0)),
        },
        "globalopt_rewrite_provenance": {
            "status": dict(sorted(globalopt_rewrite_provenance_status.items())),
            "callee": dict(sorted(globalopt_rewrite_callee.items())),
            "replacement_expr": dict(sorted(globalopt_replacement_expr.items())),
            "value_type_expr": dict(sorted(globalopt_value_type_expr.items())),
        },
        "source_slice_contract_verification": {
            "status": dict(sorted(contract_verification_status.items())),
            "mismatch_kinds": dict(sorted(contract_verification_mismatch_kinds.items())),
            "failures": int(contract_verification_status.get("failed", 0)),
        },
        "transaction_formalization_verification": {
            "status": dict(sorted(formalization_verification_status.items())),
            "mismatch_kinds": dict(sorted(formalization_verification_mismatch_kinds.items())),
            "failures": int(formalization_verification_status.get("failed", 0)),
        },
        "transaction_formal_provenance_coverage": {
            "status": dict(sorted(formal_provenance_coverage_status.items())),
            "roles": dict(sorted(formal_provenance_roles.items())),
            "missing_paths": dict(sorted(formal_provenance_missing_paths.items())),
            "incomplete": int(formal_provenance_coverage_status.get("incomplete", 0)),
        },
        "optimization_transactions": {
            "records": len(transaction_records),
            "lowering": count(transaction_records, "transaction_lowering"),
            "formal_ir": sum(1 for record in transaction_records if record.get("transaction_lowering") == "formal-ir"),
            "relaxed_fp_policy": sum(1 for record in transaction_records if record.get("transaction_lowering") == "relaxed-fp-policy"),
            "fallback": sum(1 for record in transaction_records if record.get("transaction_lowering") == "fallback"),
            "kind": count(transaction_records, "transaction_kind"),
            "opcode": count(transaction_records, "transaction_opcode"),
            "lanes": count(transaction_records, "transaction_lanes"),
            "consistency": count(transaction_records, "transaction_consistency"),
            "consistency_errors": dict(sorted(transaction_errors.items())),
            "with_source_slice": sum(1 for record in transaction_records if record.get("transaction_has_source_slice")),
            "complete_source_slice": sum(1 for record in transaction_records if record.get("transaction_source_slice_complete")),
            "incomplete_source_slice": sum(
                1
                for record in transaction_records
                if record.get("transaction_has_source_slice") and not record.get("transaction_source_slice_complete")
            ),
            "source_slice_missing": dict(sorted(source_slice_missing.items())),
            "predicate_expansion_roles": dict(sorted(predicate_expansion_roles.items())),
            "with_source_slice_contract": sum(
                1 for record in transaction_records if record.get("transaction_has_source_slice_contract")
            ),
            "complete_source_slice_contract": sum(
                1 for record in transaction_records if record.get("transaction_source_slice_contract_complete")
            ),
            "incomplete_source_slice_contract": sum(
                1
                for record in transaction_records
                if record.get("transaction_has_source_slice_contract")
                and not record.get("transaction_source_slice_contract_complete")
            ),
            "source_slice_contract_missing_roles": dict(sorted(source_slice_contract_missing_roles.items())),
            "source_slice_contract_failed_checks": dict(sorted(source_slice_contract_failed_checks.items())),
            "source_slice_contract_failed_kinds": dict(sorted(source_slice_contract_failed_kinds.items())),
            "with_lane_mapping": sum(1 for record in transaction_records if record.get("transaction_has_lane_mapping")),
            "with_result_lane_mapping": sum(1 for record in transaction_records if record.get("transaction_has_result_lane_mapping")),
            "scalar_lane_pairs": sum(int(record.get("transaction_scalar_lane_pairs") or 0) for record in transaction_records),
            "with_graph": sum(1 for record in transaction_records if record.get("transaction_has_graph")),
            "graph_kind": count(transaction_records, "transaction_graph_kind"),
            "graph_consistency": count(transaction_records, "transaction_graph_consistency"),
            "graph_root_opcode": count(transaction_records, "transaction_graph_root_opcode"),
            "graph_nodes": sum(int(record.get("transaction_graph_node_count") or 0) for record in transaction_records),
            "graph_edges": sum(int(record.get("transaction_graph_edge_count") or 0) for record in transaction_records),
            "masked_memory": sum(1 for record in transaction_records if record.get("transaction_masked_memory")),
            "scalable_memory_pack": sum(1 for record in transaction_records if record.get("transaction_scalable_memory_pack")),
            "scalable_mask_tuple": sum(1 for record in transaction_records if record.get("transaction_scalable_mask_tuple")),
            "mask_blocker_kind": dict(sorted(collections.Counter(
                str(record.get("transaction_mask_blocker_kind") or "")
                for record in transaction_records
                if record.get("transaction_mask_blocker_kind")
            ).items())),
            "mask_blocker_detail": dict(sorted(collections.Counter(
                str(record.get("transaction_mask_blocker_detail") or "")
                for record in transaction_records
                if record.get("transaction_mask_blocker_detail")
            ).items())),
            "memory_address_blocker_kind": dict(sorted(collections.Counter(
                str(record.get("transaction_memory_address_blocker_kind") or "")
                for record in transaction_records
                if record.get("transaction_memory_address_blocker_kind")
            ).items())),
            "memory_address_blocker_detail": dict(sorted(collections.Counter(
                str(record.get("transaction_memory_address_blocker_detail") or "")
                for record in transaction_records
                if record.get("transaction_memory_address_blocker_detail")
            ).items())),
            "memory_contract": count(transaction_records, "transaction_memory_contract"),
            "store_contract": count(transaction_records, "transaction_store_contract"),
            "global_initializer_contract": count(
                transaction_records, "transaction_global_initializer_contract"
            ),
            "global_initializer_observability_model": count(
                transaction_records,
                "transaction_global_initializer_observability_model",
            ),
            "global_initializer_rewrite_api": count(
                transaction_records, "transaction_global_initializer_rewrite_api"
            ),
            "global_initializer_replacement_kind": count(
                transaction_records, "transaction_global_initializer_replacement_kind"
            ),
            "reduction_opcode": count(reduction_records, "transaction_reduction_opcode"),
            "reduction_family": count(reduction_records, "transaction_reduction_family"),
            "reduction_lanes": count(reduction_records, "transaction_reduction_lanes"),
            "reduction_input_bits": count(reduction_records, "transaction_reduction_input_bits"),
            "reduction_accumulator_bits": count(reduction_records, "transaction_reduction_accumulator_bits"),
            "reduction_width_status": count(reduction_records, "transaction_reduction_width_status"),
            "base_lanes": count(reduction_records, "transaction_base_lanes"),
            "scalable_reductions": sum(1 for record in reduction_records if record.get("transaction_scalable")),
            "widened_reductions": sum(
                1
                for record in reduction_records
                if int(record.get("transaction_reduction_accumulator_bits") or 0)
                > int(record.get("transaction_reduction_input_bits") or 0)
            ),
            "reduction_sources": sum(
                int(record.get("transaction_reduction_sources") or 0) for record in transaction_records
            ),
            "with_reduction_result": sum(
                1 for record in transaction_records if record.get("transaction_has_reduction_result")
            ),
            "unsupported_reduction_reasons": dict(sorted(unsupported_reduction_reasons.items())),
            "reduction_coverage_gaps": reduction_gaps,
            "masked_memory_coverage_gaps": masked_memory_gaps,
            "memory_address_coverage_gaps": memory_address_gaps,
            "helper_slice_coverage_gaps": helper_slice_gaps,
        },
    }


def unsupported_guard_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        source_record = record.get("_source_record")
        if not isinstance(source_record, dict):
            continue
        side_conditions = source_record.get("side_conditions")
        if not isinstance(side_conditions, list):
            continue
        for side_condition in side_conditions:
            if isinstance(side_condition, dict):
                kind = str(side_condition.get("kind") or "unknown")
                source = str(side_condition.get("source") or "")
                line = int(side_condition.get("line") or record.get("line") or 0)
            else:
                kind = "unknown"
                source = str(side_condition)
                line = int(record.get("line") or 0)
            key = (kind, source)
            entry = grouped.setdefault(
                key,
                {
                    "kind": kind,
                    "source": source,
                    "count": 0,
                    "locations": [],
                },
            )
            entry["count"] += 1
            entry["locations"].append(
                {
                    "file": record.get("file", ""),
                    "line": line,
                    "marker": record.get("marker", ""),
                }
            )
    return sorted(grouped.values(), key=lambda item: (-int(item["count"]), str(item["kind"]), str(item["source"])))


def build_audit(
    validated: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    semantic_facts: list[dict[str, Any]],
    guard_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    intents_by_marker = marker_map(intents)
    semantic_by_marker = marker_map(semantic_facts)
    records = []
    for record in validated:
        audited = audit_record(record, intents_by_marker, semantic_by_marker, guard_catalog)
        audited["_source_record"] = record
        records.append(audited)
    observed = {record["marker"] for record in records if record["marker"]}
    missing_markers = sorted(set(intents_by_marker) - observed)
    public_records = [{key: value for key, value in record.items() if key != "_source_record"} for record in records]
    return {
        "summary": summarize(records, missing_markers),
        "records": public_records,
        "missing_registry_markers": missing_markers,
        "guard_recognizers": recognizer_summary(guard_catalog),
    }


def format_counter(title: str, values: dict[str, int]) -> list[str]:
    lines = [title]
    if not values:
        return [title, "  none"]
    lines.extend(f"  {key}: {value}" for key, value in values.items())
    return lines


def format_report(audit: dict[str, Any]) -> str:
    summary = audit["summary"]
    lines = [
        "O2T Intent Coverage Audit",
        f"records: {summary['records']}",
        f"missing_registry_markers: {summary['missing_registry_markers']}",
        "",
    ]
    for key, title in [
        ("proof_status", "Proof status"),
        ("semantic_lowering", "Semantic lowering"),
        ("formal_inference", "Formal inference"),
        ("formal_domain", "Formal domain"),
        ("unsupported_reason", "Unsupported reason"),
        ("recommendation", "Recommendations"),
    ]:
        lines.extend(format_counter(title, summary[key]))
        lines.append("")
    lines.append("Guard handling")
    for key, title in [
        ("modeled", "modeled"),
        ("structural", "structural"),
        ("profitability", "profitability"),
        ("unsupported", "unsupported"),
    ]:
        values = summary["guard_handling"][key]
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{kind}={count}" for kind, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append("")
    lines.append("Assumption algebra")
    algebra = summary.get("assumption_algebra", {})
    lines.append(f"  derived: {int(algebra.get('derived') or 0)}")
    lines.append(f"  contradictions: {int(algebra.get('contradictions') or 0)}")
    contradiction_records = algebra.get("contradiction_records", [])
    if isinstance(contradiction_records, list) and contradiction_records:
        for record in contradiction_records[:10]:
            messages = record.get("messages", []) if isinstance(record, dict) else []
            message = "; ".join(str(item) for item in messages) if isinstance(messages, list) else ""
            lines.append(
                "  "
                + " ".join(
                    [
                        f"{record.get('marker', '')}",
                        f"{record.get('file', '')}:{record.get('line', 0)}",
                        f"message={message}",
                    ]
                )
            )
    lines.append("")
    lines.append("Source intent graph")
    graph = summary.get("source_intent_graph", {})
    status = graph.get("status", {})
    lowering = graph.get("lowering", {})
    reasons = graph.get("unsupported_reasons", {})
    lines.append(
        "  status: " + (", ".join(f"{key}={value}" for key, value in status.items()) if status else "none")
    )
    lines.append(
        "  lowering: " + (", ".join(f"{key}={value}" for key, value in lowering.items()) if lowering else "none")
    )
    lines.append(f"  complete: {int(graph.get('complete') or 0)}")
    lines.append(f"  with_bindings: {int(graph.get('with_bindings') or 0)}")
    lines.append(f"  missing_rewrite: {int(graph.get('missing_rewrite') or 0)}")
    if reasons:
        lines.append("  unsupported: " + ", ".join(f"{key}={value}" for key, value in reasons.items()))
    else:
        lines.append("  unsupported: none")
    consistency = graph.get("consistency", {})
    consistency_errors = graph.get("consistency_errors", {})
    lines.append(
        "  consistency: "
        + (", ".join(f"{key}={value}" for key, value in consistency.items()) if consistency else "none")
    )
    lines.append(f"  consistency_failures: {int(graph.get('consistency_failures') or 0)}")
    lines.append(f"  lowered_despite_inconsistency: {int(graph.get('lowered_despite_inconsistency') or 0)}")
    if consistency_errors:
        lines.append(
            "  consistency_errors: "
            + ", ".join(f"{key}={value}" for key, value in consistency_errors.items())
        )
    else:
        lines.append("  consistency_errors: none")
    lines.append("")
    lines.append("Source program graph contract")
    source_program_graph = summary.get("source_program_graph_contract", {})
    source_program_status = source_program_graph.get("status", {})
    failed_checks = source_program_graph.get("failed_checks", {})
    failure_reasons = source_program_graph.get("failure_reasons", {})
    lines.append(
        "  status: "
        + (
            ", ".join(f"{key}={value}" for key, value in source_program_status.items())
            if source_program_status
            else "none"
        )
    )
    lines.append(f"  passed: {int(source_program_graph.get('passed') or 0)}")
    lines.append(f"  failed: {int(source_program_graph.get('failed') or 0)}")
    lines.append(f"  with_cfg: {int(source_program_graph.get('with_cfg') or 0)}")
    lines.append(f"  with_dfg: {int(source_program_graph.get('with_dfg') or 0)}")
    lines.append(
        f"  with_interprocedural_dfg: {int(source_program_graph.get('with_interprocedural_dfg') or 0)}"
    )
    lines.append(f"  with_access_paths: {int(source_program_graph.get('with_access_paths') or 0)}")
    if failed_checks:
        lines.append("  failed_checks: " + ", ".join(f"{key}={value}" for key, value in failed_checks.items()))
    else:
        lines.append("  failed_checks: none")
    if failure_reasons:
        lines.append(
            "  failure_reasons: "
            + ", ".join(f"{key}={value}" for key, value in failure_reasons.items())
        )
    else:
        lines.append("  failure_reasons: none")
    graph_gaps = source_program_graph.get("gaps", {})
    lines.append(f"  gap_records: {int(graph_gaps.get('records') or 0)}")
    for key, title in [
        ("failed_checks", "gap_failed_checks"),
        ("failure_reasons", "gap_failure_reasons"),
        ("recommendations", "gap_recommendations"),
    ]:
        values = graph_gaps.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  next_modeling_target: {graph_gaps.get('next_modeling_target') or 'none'}")
    lines.append("")
    lines.append("Global initializer safety")
    global_safety = summary.get("global_initializer_safety", {})
    for key, title in [
        ("status", "status"),
        ("required_facts", "required_facts"),
        ("observed_facts", "observed_facts"),
        ("missing_facts", "missing_facts"),
    ]:
        values = global_safety.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  complete: {int(global_safety.get('complete') or 0)}")
    lines.append(f"  incomplete: {int(global_safety.get('incomplete') or 0)}")
    lines.append("")
    lines.append("Predicate provenance")
    predicate_provenance = summary.get("predicate_provenance", {})
    predicate_provenance_status = predicate_provenance.get("verification_status", {})
    predicate_provenance_failed_checks = predicate_provenance.get("failed_checks", {})
    lines.append(f"  records: {int(predicate_provenance.get('records') or 0)}")
    lines.append(f"  checked: {int(predicate_provenance.get('checked') or 0)}")
    lines.append(f"  passed: {int(predicate_provenance.get('passed') or 0)}")
    lines.append(f"  failed: {int(predicate_provenance.get('failed') or 0)}")
    lines.append(f"  absent: {int(predicate_provenance.get('absent') or 0)}")
    lines.append(
        "  verification_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(predicate_provenance_status.items()))
            if predicate_provenance_status
            else "none"
        )
    )
    if predicate_provenance_failed_checks:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(predicate_provenance_failed_checks.items()))
        lines.append(f"  failed_checks: {rendered}")
    else:
        lines.append("  failed_checks: none")
    lines.append("")
    lines.append("GlobalOpt witnesses")
    globalopt_witnesses = summary.get("globalopt_witnesses", {})
    witness_status = globalopt_witnesses.get("status", {})
    witness_failures = globalopt_witnesses.get("failures", {})
    witness_required = globalopt_witnesses.get("required_cases", {})
    witness_missing_required = globalopt_witnesses.get("missing_required_cases", {})
    witness_structural = globalopt_witnesses.get("structural_status", {})
    witness_structural_cases = globalopt_witnesses.get("structural_cases", {})
    witness_changed_line_counts = globalopt_witnesses.get("changed_line_counts", {})
    witness_contract_verification = globalopt_witnesses.get("contract_verification_status", {})
    witness_contract_formal = globalopt_witnesses.get("contract_formal_status", {})
    witness_contract_semantic = globalopt_witnesses.get("contract_semantic_status", {})
    witness_contract_failed_checks = globalopt_witnesses.get("contract_failed_checks", {})
    witness_contract_semantic_failed_checks = globalopt_witnesses.get("contract_semantic_failed_checks", {})
    witness_safety_provenance = globalopt_witnesses.get("safety_provenance_status", {})
    witness_safety_provenance_failed_checks = globalopt_witnesses.get("safety_provenance_failed_checks", {})
    witness_cases = globalopt_witnesses.get("cases", {})
    lines.append(
        "  status: "
        + (", ".join(f"{key}={value}" for key, value in witness_status.items()) if witness_status else "none")
    )
    lines.append(f"  passed: {int(globalopt_witnesses.get('passed') or 0)}")
    lines.append(f"  failed: {int(globalopt_witnesses.get('failed') or 0)}")
    lines.append(f"  absent: {int(globalopt_witnesses.get('absent') or 0)}")
    if witness_failures:
        lines.append("  failures: " + ", ".join(f"{key}={value}" for key, value in witness_failures.items()))
    else:
        lines.append("  failures: none")
    lines.append(
        "  required_cases: "
        + (
            ", ".join(f"{key}={value}" for key, value in witness_required.items())
            if witness_required
            else "none"
        )
    )
    lines.append(
        "  missing_required_cases: "
        + (
            ", ".join(f"{key}={value}" for key, value in witness_missing_required.items())
            if witness_missing_required
            else "none"
        )
    )
    lines.append(
        "  structural_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in witness_structural.items())
            if witness_structural
            else "none"
        )
    )
    if witness_structural_cases:
        for name, statuses in sorted(witness_structural_cases.items()):
            if isinstance(statuses, dict):
                rendered = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
                lines.append(f"  structural {name}: {rendered}")
    if witness_changed_line_counts:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(witness_changed_line_counts.items()))
        lines.append(f"  changed_line_counts: {rendered}")
    lines.append(
        "  contract_verification_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(witness_contract_verification.items()))
            if witness_contract_verification
            else "none"
        )
    )
    lines.append(
        "  contract_formal_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(witness_contract_formal.items()))
            if witness_contract_formal
            else "none"
        )
    )
    lines.append(
        "  contract_semantic_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(witness_contract_semantic.items()))
            if witness_contract_semantic
            else "none"
        )
    )
    if witness_contract_failed_checks:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(witness_contract_failed_checks.items()))
        lines.append(f"  contract_failed_checks: {rendered}")
    else:
        lines.append("  contract_failed_checks: none")
    if witness_contract_semantic_failed_checks:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(witness_contract_semantic_failed_checks.items()))
        lines.append(f"  contract_semantic_failed_checks: {rendered}")
    else:
        lines.append("  contract_semantic_failed_checks: none")
    lines.append(
        "  safety_provenance_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(witness_safety_provenance.items()))
            if witness_safety_provenance
            else "none"
        )
    )
    if witness_safety_provenance_failed_checks:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(witness_safety_provenance_failed_checks.items()))
        lines.append(f"  safety_provenance_failed_checks: {rendered}")
    else:
        lines.append("  safety_provenance_failed_checks: none")
    if witness_cases:
        for name, statuses in sorted(witness_cases.items()):
            if isinstance(statuses, dict):
                rendered = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
                lines.append(f"  {name}: {rendered}")
    else:
        lines.append("  cases: none")
    lines.append("")
    lines.append("GlobalOpt rewrite provenance")
    rewrite_provenance = summary.get("globalopt_rewrite_provenance", {})
    if isinstance(rewrite_provenance, dict) and rewrite_provenance:
        for title in ("status", "callee", "replacement_expr", "value_type_expr"):
            values = rewrite_provenance.get(title, {})
            if isinstance(values, dict) and values:
                lines.append("  " + title + ": " + ", ".join(f"{key}={value}" for key, value in values.items()))
            else:
                lines.append(f"  {title}: none")
    else:
        lines.append("  none")
    lines.append("")
    lines.append("Source-slice contract verification")
    contract_verification = summary.get("source_slice_contract_verification", {})
    verification_status = contract_verification.get("status", {})
    mismatch_kinds = contract_verification.get("mismatch_kinds", {})
    lines.append(
        "  status: "
        + (", ".join(f"{key}={value}" for key, value in verification_status.items()) if verification_status else "none")
    )
    lines.append(f"  failures: {int(contract_verification.get('failures') or 0)}")
    if mismatch_kinds:
        lines.append("  mismatches: " + ", ".join(f"{key}={value}" for key, value in mismatch_kinds.items()))
    else:
        lines.append("  mismatches: none")
    lines.append("")
    lines.append("Transaction formalization verification")
    formalization_verification = summary.get("transaction_formalization_verification", {})
    formalization_status = formalization_verification.get("status", {})
    formalization_mismatches = formalization_verification.get("mismatch_kinds", {})
    lines.append(
        "  status: "
        + (", ".join(f"{key}={value}" for key, value in formalization_status.items()) if formalization_status else "none")
    )
    lines.append(f"  failures: {int(formalization_verification.get('failures') or 0)}")
    if formalization_mismatches:
        lines.append("  mismatches: " + ", ".join(f"{key}={value}" for key, value in formalization_mismatches.items()))
    else:
        lines.append("  mismatches: none")
    provenance_coverage = summary.get("transaction_formal_provenance_coverage", {})
    provenance_status = provenance_coverage.get("status", {})
    provenance_roles = provenance_coverage.get("roles", {})
    provenance_missing = provenance_coverage.get("missing_paths", {})
    lines.append("Transaction formal provenance coverage")
    lines.append(
        "  status: "
        + (", ".join(f"{key}={value}" for key, value in provenance_status.items()) if provenance_status else "none")
    )
    lines.append(f"  incomplete: {int(provenance_coverage.get('incomplete') or 0)}")
    if provenance_roles:
        lines.append("  roles: " + ", ".join(f"{key}={value}" for key, value in provenance_roles.items()))
    else:
        lines.append("  roles: none")
    if provenance_missing:
        lines.append(
            "  top_missing_paths: "
            + ", ".join(f"{key}={value}" for key, value in list(provenance_missing.items())[:10])
        )
    else:
        lines.append("  top_missing_paths: none")
    lines.append("")
    lines.append("Optimization transactions")
    transactions = summary.get("optimization_transactions", {})
    lines.append(f"  records: {int(transactions.get('records') or 0)}")
    for key, title in [
        ("lowering", "lowering"),
        ("kind", "kind"),
        ("opcode", "opcode"),
        ("lanes", "lanes"),
        ("consistency", "consistency"),
    ]:
        values = transactions.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  formal_ir: {int(transactions.get('formal_ir') or 0)}")
    lines.append(f"  relaxed_fp_policy: {int(transactions.get('relaxed_fp_policy') or 0)}")
    lines.append(f"  fallback: {int(transactions.get('fallback') or 0)}")
    lines.append(f"  with_source_slice: {int(transactions.get('with_source_slice') or 0)}")
    lines.append(f"  complete_source_slice: {int(transactions.get('complete_source_slice') or 0)}")
    lines.append(f"  incomplete_source_slice: {int(transactions.get('incomplete_source_slice') or 0)}")
    source_slice_missing = transactions.get("source_slice_missing", {})
    if source_slice_missing:
        lines.append(
            "  source_slice_missing: "
            + ", ".join(f"{key}={value}" for key, value in source_slice_missing.items())
        )
    else:
        lines.append("  source_slice_missing: none")
    predicate_expansion_roles = transactions.get("predicate_expansion_roles", {})
    if predicate_expansion_roles:
        lines.append(
            "  predicate_expansion_roles: "
            + ", ".join(f"{key}={value}" for key, value in predicate_expansion_roles.items())
        )
    else:
        lines.append("  predicate_expansion_roles: none")
    lines.append(f"  with_source_slice_contract: {int(transactions.get('with_source_slice_contract') or 0)}")
    lines.append(f"  complete_source_slice_contract: {int(transactions.get('complete_source_slice_contract') or 0)}")
    lines.append(f"  incomplete_source_slice_contract: {int(transactions.get('incomplete_source_slice_contract') or 0)}")
    contract_missing_roles = transactions.get("source_slice_contract_missing_roles", {})
    if contract_missing_roles:
        lines.append(
            "  source_slice_contract_missing_roles: "
            + ", ".join(f"{key}={value}" for key, value in contract_missing_roles.items())
        )
    else:
        lines.append("  source_slice_contract_missing_roles: none")
    contract_failed_checks = transactions.get("source_slice_contract_failed_checks", {})
    if contract_failed_checks:
        lines.append(
            "  source_slice_contract_failed_checks: "
            + ", ".join(f"{key}={value}" for key, value in contract_failed_checks.items())
        )
    else:
        lines.append("  source_slice_contract_failed_checks: none")
    contract_failed_kinds = transactions.get("source_slice_contract_failed_kinds", {})
    if contract_failed_kinds:
        lines.append(
            "  source_slice_contract_failed_kinds: "
            + ", ".join(f"{key}={value}" for key, value in contract_failed_kinds.items())
        )
    else:
        lines.append("  source_slice_contract_failed_kinds: none")
    lines.append(f"  with_lane_mapping: {int(transactions.get('with_lane_mapping') or 0)}")
    lines.append(f"  with_result_lane_mapping: {int(transactions.get('with_result_lane_mapping') or 0)}")
    lines.append(f"  scalar_lane_pairs: {int(transactions.get('scalar_lane_pairs') or 0)}")
    lines.append(f"  masked_memory: {int(transactions.get('masked_memory') or 0)}")
    lines.append(f"  scalable_memory_pack: {int(transactions.get('scalable_memory_pack') or 0)}")
    lines.append(f"  scalable_mask_tuple: {int(transactions.get('scalable_mask_tuple') or 0)}")
    mask_blocker_kind = transactions.get("mask_blocker_kind", {})
    if mask_blocker_kind:
        lines.append(
            "  mask_blocker_kind: "
            + ", ".join(f"{key}={value}" for key, value in mask_blocker_kind.items())
        )
    else:
        lines.append("  mask_blocker_kind: none")
    mask_blocker_detail = transactions.get("mask_blocker_detail", {})
    if mask_blocker_detail:
        lines.append(
            "  mask_blocker_detail: "
            + ", ".join(f"{key}={value}" for key, value in mask_blocker_detail.items())
        )
    else:
        lines.append("  mask_blocker_detail: none")
    address_blocker_kind = transactions.get("memory_address_blocker_kind", {})
    if address_blocker_kind:
        lines.append(
            "  memory_address_blocker_kind: "
            + ", ".join(f"{key}={value}" for key, value in address_blocker_kind.items())
        )
    else:
        lines.append("  memory_address_blocker_kind: none")
    address_blocker_detail = transactions.get("memory_address_blocker_detail", {})
    if address_blocker_detail:
        lines.append(
            "  memory_address_blocker_detail: "
            + ", ".join(f"{key}={value}" for key, value in address_blocker_detail.items())
        )
    else:
        lines.append("  memory_address_blocker_detail: none")
    for key, title in [
        ("memory_contract", "memory_contract"),
        ("store_contract", "store_contract"),
        ("global_initializer_contract", "global_initializer_contract"),
        (
            "global_initializer_observability_model",
            "global_initializer_observability_model",
        ),
        ("global_initializer_rewrite_api", "global_initializer_rewrite_api"),
        ("global_initializer_replacement_kind", "global_initializer_replacement_kind"),
    ]:
        values = transactions.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    for key, title in [
        ("reduction_opcode", "reduction_opcode"),
        ("reduction_family", "reduction_family"),
        ("reduction_lanes", "reduction_lanes"),
        ("reduction_input_bits", "reduction_input_bits"),
        ("reduction_accumulator_bits", "reduction_accumulator_bits"),
        ("reduction_width_status", "reduction_width_status"),
        ("base_lanes", "base_lanes"),
    ]:
        values = transactions.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  reduction_sources: {int(transactions.get('reduction_sources') or 0)}")
    lines.append(f"  with_reduction_result: {int(transactions.get('with_reduction_result') or 0)}")
    lines.append(f"  widened_reductions: {int(transactions.get('widened_reductions') or 0)}")
    lines.append(f"  scalable_reductions: {int(transactions.get('scalable_reductions') or 0)}")
    unsupported_reduction_reasons = transactions.get("unsupported_reduction_reasons", {})
    if unsupported_reduction_reasons:
        lines.append(
            "  unsupported_reduction_reasons: "
            + ", ".join(f"{key}={value}" for key, value in unsupported_reduction_reasons.items())
        )
    else:
        lines.append("  unsupported_reduction_reasons: none")
    transaction_errors = transactions.get("consistency_errors", {})
    if transaction_errors:
        lines.append(
            "  consistency_errors: "
            + ", ".join(f"{key}={value}" for key, value in transaction_errors.items())
        )
    else:
        lines.append("  consistency_errors: none")
    gaps = transactions.get("reduction_coverage_gaps", {})
    lines.append("")
    lines.append("Reduction coverage gaps")
    lines.append(f"  records: {int(gaps.get('records') or 0)}")
    for key, title in [
        ("unsupported_reasons", "unsupported"),
        ("width_status", "width_status"),
        ("lane_blockers", "lane_blockers"),
        ("recommendations", "recommendations"),
    ]:
        values = gaps.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  next_modeling_target: {gaps.get('next_modeling_target') or 'none'}")
    masked_gaps = transactions.get("masked_memory_coverage_gaps", {})
    lines.append("")
    lines.append("Masked memory coverage gaps")
    lines.append(f"  records: {int(masked_gaps.get('records') or 0)}")
    lines.append(f"  masked_records: {int(masked_gaps.get('masked_records') or 0)}")
    lines.append(f"  covered_records: {int(masked_gaps.get('covered_records') or 0)}")
    for key, title in [
        ("unsupported_reasons", "unsupported"),
        ("blocker_kinds", "blocker_kinds"),
        ("blocker_details", "blocker_details"),
        ("recommendations", "recommendations"),
    ]:
        values = masked_gaps.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  next_modeling_target: {masked_gaps.get('next_modeling_target') or 'none'}")
    address_gaps = transactions.get("memory_address_coverage_gaps", {})
    lines.append("")
    lines.append("Memory address coverage gaps")
    lines.append(f"  records: {int(address_gaps.get('records') or 0)}")
    for key, title in [
        ("unsupported_reasons", "unsupported"),
        ("blocker_kinds", "blocker_kinds"),
        ("blocker_details", "blocker_details"),
        ("recommendations", "recommendations"),
    ]:
        values = address_gaps.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  next_modeling_target: {address_gaps.get('next_modeling_target') or 'none'}")
    helper_gaps = transactions.get("helper_slice_coverage_gaps", {})
    lines.append("")
    lines.append("Helper slice coverage gaps")
    lines.append(f"  records: {int(helper_gaps.get('records') or 0)}")
    for key, title in [
        ("unsupported_reasons", "unsupported"),
        ("diagnostic_reasons", "diagnostic_reasons"),
        ("helpers", "helpers"),
        ("roles", "roles"),
        ("recommendations", "recommendations"),
    ]:
        values = helper_gaps.get(key, {})
        if values:
            lines.append(f"  {title}: " + ", ".join(f"{name}={count}" for name, count in values.items()))
        else:
            lines.append(f"  {title}: none")
    lines.append(f"  next_modeling_target: {helper_gaps.get('next_modeling_target') or 'none'}")
    lines.append("Top helper slice diagnostics")
    diagnostics = helper_gaps.get("diagnostics", [])
    if isinstance(diagnostics, list) and diagnostics:
        for diagnostic in diagnostics[:10]:
            if not isinstance(diagnostic, dict):
                continue
            lines.append(
                "  "
                + " ".join(
                    [
                        str(diagnostic.get("marker") or ""),
                        f"{diagnostic.get('file', '')}:{int(diagnostic.get('line') or 0)}",
                        f"helper={diagnostic.get('helper', '')}",
                        f"role={diagnostic.get('role', '')}",
                        f"reason={diagnostic.get('reason', '')}",
                    ]
                )
            )
    else:
        lines.append("  none")
    lines.append("")
    lines.append("Guard recognizers")
    recognizers = audit.get("guard_recognizers", {})
    for key, title in [("text", "text"), ("ast", "ast"), ("semantic_only", "semantic-only")]:
        values = recognizers.get(key, [])
        lines.append(f"  {title}: {', '.join(values) if values else 'none'}")
    lines.append("")
    lines.append("Top unsupported guards")
    unsupported = summary.get("unsupported_guard_records", [])
    if unsupported:
        for record in unsupported[:10]:
            locations = record.get("locations", [])
            first = locations[0] if locations else {}
            lines.append(
                "  "
                + " ".join(
                    [
                        f"{record['kind']}",
                        f"count={record['count']}",
                        f"first={first.get('file', '')}:{first.get('line', 0)}",
                        f"marker={first.get('marker', '')}",
                        f"source={record.get('source', '')}",
                    ]
                )
            )
    else:
        lines.append("  none")
    lines.append("")
    lines.append("Markers")
    for record in audit["records"]:
        lines.append(
            "  "
            + " ".join(
                [
                    f"{record['marker']}",
                    f"proof={record['proof_status']}",
                    f"semantic={record['semantic_lowering']}",
                    f"inference={record['formal_inference']}",
                    f"domain={record['formal_domain']}",
                    f"guards=modeled:{len(record['modeled_guards'])}/structural:{len(record['structural_guards'])}/profitability:{len(record['profitability_guards'])}/unsupported:{len(record['unsupported_guards'])}",
                    f"recommendation={record['recommendation']}",
                ]
            )
        )
    if not audit["records"]:
        lines.append("  none")
    lines.append("")
    lines.append("Missing registry markers")
    missing = audit["missing_registry_markers"]
    if missing:
        lines.extend(f"  {marker}" for marker in missing)
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    audit = build_audit(
        load_records(args.validated),
        load_records(args.intent_registry),
        load_records(args.semantic_facts),
        load_guard_semantics(args.guard_semantics),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = format_report(audit)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {
                "records": audit["summary"]["records"],
                "missing_registry_markers": audit["summary"]["missing_registry_markers"],
                "recommendation": audit["summary"]["recommendation"],
                "guard_handling": audit["summary"]["guard_handling"],
                "optimization_transactions": audit["summary"]["optimization_transactions"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
