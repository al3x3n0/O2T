#!/usr/bin/env python3
"""Create a reviewable proposed optimization intent file from validated candidates."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from cv_source_graph_contract import source_graph_contract_parameters


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURRENT = ROOT / "constraints" / "optimization_intents.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validated", type=Path, required=True)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--require-verified-evidence", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def evidence_by_marker(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        marker = str(record.get("marker") or "")
        if marker:
            result[marker] = record
    return result


def evidence_summary(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {}
    out: dict[str, Any] = {
        "evidence_status": str(record.get("evidence_status") or ""),
        "replay_cases": int(record.get("replay_cases") or 0),
        "replay_status": dict(record.get("replay_status") or {}),
        "semantic_status": dict(record.get("semantic_status") or {}),
        "oracle_status": dict(record.get("oracle_status") or {}),
        "alive2_status": dict(record.get("alive2_status") or {}),
    }
    if isinstance(record.get("source_intent_graph"), dict):
        out["source_intent_graph"] = dict(record["source_intent_graph"])
    for key in (
        "source_intent_graph_status",
        "source_intent_graph_lowering",
        "source_intent_graph_consistency",
        "source_intent_graph_consistency_errors",
        "source_intent_graph_predicate_nodes",
        "source_intent_graph_rewrite_nodes",
        "source_intent_graph_bindings",
        "source_intent_graph_formal_symbols",
    ):
        if key in record:
            out[key] = record[key]
    if isinstance(record.get("formal_parameters"), dict):
        out["formal_parameters"] = dict(record["formal_parameters"])
    if isinstance(record.get("optimization_transaction"), dict):
        out["optimization_transaction"] = dict(record["optimization_transaction"])
    for key in (
        "transaction_lowering",
        "transaction_kind",
        "transaction_opcode",
        "transaction_lanes",
        "transaction_consistency",
        "transaction_consistency_errors",
        "transaction_has_lane_mapping",
        "transaction_has_result_lane_mapping",
        "transaction_scalar_lane_pairs",
        "transaction_reduction_opcode",
        "transaction_reduction_lanes",
        "transaction_reduction_sources",
        "transaction_has_reduction_result",
        "transaction_graph_absent_reasons",
        "transaction_graph_absent_diagnostics",
        "source_slice_contract_status",
        "source_slice_contract_missing_roles",
        "source_slice_contract_role_paths",
        "source_slice_contract_checks",
        "source_slice_contract_verification_status",
        "source_slice_contract_verification_mismatches",
        "transaction_formalization_verification_status",
        "transaction_formalization_verification_mismatches",
        "transaction_formal_provenance_coverage_status",
        "transaction_formal_provenance_missing_paths",
        "transaction_formal_provenance_roles",
        "globalopt_witness_status",
        "globalopt_witness_before",
        "globalopt_witness_after",
        "globalopt_witness_manifest",
        "globalopt_witness_failure_reasons",
        "globalopt_witness_model",
        "globalopt_witness_contract",
        "globalopt_witness_contract_verification_status",
        "globalopt_witness_contract_formal_status",
        "globalopt_witness_contract_semantic_status",
        "globalopt_witness_contract_failed_checks",
        "globalopt_witness_contract_semantic_failed_checks",
        "globalopt_witness_contract_formal_obligations",
        "globalopt_witness_contract_semantic_obligations",
        "globalopt_safety_provenance_status",
        "globalopt_safety_provenance_failed_checks",
        "globalopt_safety_provenance",
        "predicate_provenance_verification_status",
        "predicate_provenance_failed_checks",
        "predicate_provenance_verification",
        "globalopt_witness_structural_status",
        "globalopt_required_witness_cases",
        "globalopt_missing_required_witness_cases",
        "globalopt_witness_cases",
        "globalopt_rewrite_provenance_status",
        "globalopt_rewrite_callee",
        "globalopt_replacement_expr",
        "globalopt_value_type_expr",
        "globalopt_rewrite_subject",
    ):
        if key in record:
            out[key] = record[key]
    if isinstance(record.get("globalopt_witness"), dict):
        out["globalopt_witness"] = dict(record["globalopt_witness"])
    if isinstance(record.get("source_slice_contract_verification"), dict):
        out["source_slice_contract_verification"] = dict(record["source_slice_contract_verification"])
    if isinstance(record.get("transaction_formalization_verification"), dict):
        out["transaction_formalization_verification"] = dict(record["transaction_formalization_verification"])
    return out


def compact_graph_provenance(candidate_evidence: dict[str, Any]) -> dict[str, Any]:
    params = candidate_evidence.get("formal_parameters")
    if not isinstance(params, dict):
        params = {}
    graph = candidate_evidence.get("source_intent_graph")
    if not isinstance(graph, dict):
        graph = {}

    out: dict[str, Any] = {}
    status = params.get("source_intent_graph.status", graph.get("status"))
    if status:
        out["status"] = str(status)
    lowering = candidate_evidence.get("source_intent_graph_lowering")
    if lowering:
        out["lowering"] = str(lowering)
    consistency = params.get("source_intent_graph.consistency")
    if consistency:
        out["consistency"] = str(consistency)
    for source_key, output_key in (
        ("source_intent_graph.consistency_errors", "consistency_errors"),
        ("source_intent_graph.unsupported_reasons", "unsupported_reasons"),
    ):
        value = params.get(source_key)
        if isinstance(value, list) and value:
            out[output_key] = list(value)
    for source_key, output_key in (
        ("source_intent_graph.predicate_nodes", "predicate_nodes"),
        ("source_intent_graph.rewrite_nodes", "rewrite_nodes"),
        ("source_intent_graph.bindings", "bindings"),
    ):
        value = params.get(source_key)
        if isinstance(value, int):
            out[output_key] = value
        elif isinstance(graph.get(output_key), list):
            out[output_key] = len(graph.get(output_key) or [])
    formal_symbols = params.get("source_intent_graph.formal_symbols")
    if isinstance(formal_symbols, dict) and formal_symbols:
        out["formal_symbols"] = dict(formal_symbols)
    return out


def compact_source_program_graph_contract(candidate_evidence: dict[str, Any]) -> dict[str, Any]:
    params = candidate_evidence.get("formal_parameters")
    if not isinstance(params, dict):
        params = {}
    transaction = candidate_evidence.get("optimization_transaction")
    if not isinstance(transaction, dict):
        transaction = {}
    graph = transaction.get("source_program_graph")
    graph_params = source_graph_contract_parameters(graph) if isinstance(graph, dict) else {}

    def value_for(name: str) -> Any:
        return params.get(name, graph_params.get(name))

    status = value_for("source_program_graph_contract.status")
    if not status:
        return {}
    out: dict[str, Any] = {"status": str(status)}
    failed_checks = value_for("source_program_graph_contract.failed_checks")
    if isinstance(failed_checks, list):
        out["failed_checks"] = [str(item) for item in failed_checks if str(item)]
    failure_reasons = value_for("source_program_graph_contract.failure_reasons")
    if isinstance(failure_reasons, dict):
        out["failure_reasons"] = dict(failure_reasons)
    for key in ("cfg_blocks", "dfg_edges", "access_path_facts"):
        value = value_for(f"source_program_graph_contract.{key}")
        if isinstance(value, int):
            out[key] = value
    interprocedural = value_for("source_program_graph_contract.interprocedural_dfg")
    if isinstance(interprocedural, bool):
        out["interprocedural_dfg"] = interprocedural
    return out


def _transaction_value(
    params: dict[str, Any],
    transaction: dict[str, Any],
    dotted_key: str,
    nested_key: str,
) -> Any:
    if dotted_key in params:
        return params[dotted_key]
    return transaction.get(nested_key)


def _transaction_lane_mapping(
    params: dict[str, Any],
    transaction: dict[str, Any],
    dotted_key: str,
    nested_key: str,
) -> dict[str, Any]:
    value = _transaction_value(params, transaction, dotted_key, nested_key)
    if isinstance(value, dict):
        return value
    map_value = params.get(f"{dotted_key}.map")
    inverse_value = params.get(f"{dotted_key}.inverse_map")
    out: dict[str, Any] = {}
    if isinstance(map_value, list):
        out["map"] = list(map_value)
    if isinstance(inverse_value, list):
        out["inverse_map"] = list(inverse_value)
    return out


def _transaction_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if str(value or "").isdigit():
        return int(str(value))
    return None


def _transaction_graph_absent_diagnostics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    diagnostics: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        diagnostic = {
            "reason": str(item.get("reason") or ""),
            "helper": str(item.get("helper") or ""),
            "role": str(item.get("role") or ""),
            "source": str(item.get("source") or ""),
            "expansion_stack": [
                str(frame) for frame in item.get("expansion_stack", [])
            ] if isinstance(item.get("expansion_stack"), list) else [],
            "depth": int(item.get("depth") or 0),
        }
        if diagnostic["reason"]:
            diagnostics.append(diagnostic)
    return diagnostics


def compact_transaction_provenance(candidate_evidence: dict[str, Any]) -> dict[str, Any]:
    params = candidate_evidence.get("formal_parameters")
    if not isinstance(params, dict):
        params = {}
    transaction = candidate_evidence.get("optimization_transaction")
    if not isinstance(transaction, dict):
        transaction = {}

    has_transaction = bool(transaction) or any(str(key).startswith("transaction.") for key in params)
    if not has_transaction:
        return {}

    out: dict[str, Any] = {}
    for dotted_key, nested_key, output_key in (
        ("transaction.kind", "kind", "kind"),
        ("transaction.opcode", "opcode", "opcode"),
        ("transaction.consistency", "consistency", "consistency"),
    ):
        value = _transaction_value(params, transaction, dotted_key, nested_key)
        if value:
            out[output_key] = str(value)

    lanes = _transaction_int(_transaction_value(params, transaction, "transaction.lanes", "lanes"))
    if lanes is not None:
        out["lanes"] = lanes

    kind = out.get("kind", "")
    reduction_opcode = _transaction_value(params, transaction, "transaction.reduction_opcode", "reduction_opcode")
    if reduction_opcode:
        out["reduction_opcode"] = str(reduction_opcode)
    reduction_lanes = _transaction_int(
        _transaction_value(params, transaction, "transaction.reduction_lanes", "reduction_lanes")
    )
    if reduction_lanes is not None:
        out["reduction_lanes"] = reduction_lanes
    has_reduction = kind == "slp-vectorize-reduction" or bool(reduction_opcode) or reduction_lanes is not None

    lowering = candidate_evidence.get("transaction_lowering")
    if lowering:
        out["lowering"] = str(lowering)

    errors = _transaction_value(params, transaction, "transaction.consistency_errors", "consistency_errors")
    if isinstance(errors, list):
        out["consistency_errors"] = list(errors)
    absent_reasons = _transaction_value(
        params,
        transaction,
        "transaction.graph.absent_reasons",
        "transaction_graph_absent_reasons",
    )
    if isinstance(absent_reasons, list):
        out["transaction_graph_absent_reasons"] = [
            str(reason) for reason in absent_reasons if str(reason)
        ]
    absent_diagnostics = _transaction_graph_absent_diagnostics(
        transaction.get("transaction_graph_absent_diagnostics", [])
    )
    if absent_diagnostics:
        out["transaction_graph_absent_diagnostics"] = absent_diagnostics

    lane_mapping = _transaction_lane_mapping(params, transaction, "transaction.lane_mapping", "lane_mapping")
    result_lane_mapping = _transaction_lane_mapping(
        params,
        transaction,
        "transaction.result_lane_mapping",
        "result_lane_mapping",
    )
    if lane_mapping:
        out["lane_mapping"] = lane_mapping
        out["has_lane_mapping"] = isinstance(lane_mapping.get("map"), list)
    if result_lane_mapping:
        out["result_lane_mapping"] = result_lane_mapping
        out["has_result_lane_mapping"] = isinstance(result_lane_mapping.get("map"), list)

    scalar_pairs = _transaction_value(params, transaction, "transaction.scalar_lane_pairs", "scalar_lane_pairs")
    if isinstance(scalar_pairs, list):
        out["scalar_lane_pairs"] = len(scalar_pairs)
    reduction_sources = _transaction_value(params, transaction, "transaction.reduction_sources", "reduction_sources")
    if isinstance(reduction_sources, list):
        out["reduction_sources"] = len(reduction_sources)
    reduction_result = _transaction_value(params, transaction, "transaction.reduction_result", "reduction_result")
    if has_reduction:
        out.setdefault("reduction_sources", 0)
        out["has_reduction_result"] = isinstance(reduction_result, dict)
    fp_policy = _transaction_value(params, transaction, "transaction.fp_policy", "fp_policy")
    if isinstance(fp_policy, dict):
        out["fp_policy"] = dict(fp_policy)
    source_slice = transaction.get("source_slice")
    contract = source_slice.get("contract") if isinstance(source_slice, dict) else {}
    if not isinstance(contract, dict):
        contract = {}
    contract_status = params.get("transaction.source_slice.contract.status", contract.get("status"))
    if contract_status:
        out["source_slice_contract_status"] = str(contract_status)
    contract_missing_roles = params.get(
        "transaction.source_slice.contract.missing_roles",
        contract.get("missing_roles", []),
    )
    if isinstance(contract_missing_roles, list):
        out["source_slice_contract_missing_roles"] = [
            str(role)
            for role in contract_missing_roles
            if isinstance(role, (str, int, float)) and str(role)
        ]
    contract_role_paths = params.get(
        "transaction.source_slice.contract.role_paths",
        contract.get("role_paths", []),
    )
    if isinstance(contract_role_paths, list):
        out["source_slice_contract_role_paths"] = [
            dict(path) for path in contract_role_paths if isinstance(path, dict)
        ]
    contract_checks = params.get(
        "transaction.source_slice.contract.checks",
        contract.get("checks", []),
    )
    if isinstance(contract_checks, list):
        out["source_slice_contract_checks"] = [
            dict(check) for check in contract_checks if isinstance(check, dict)
        ]
    return out


def graph_consistency_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    graph = record.get("source_intent_graph")
    if isinstance(graph, dict) and graph.get("consistency"):
        return str(graph.get("consistency") or "")
    return str(record.get("source_intent_graph_consistency") or "")


def transaction_consistency_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    transaction = record.get("optimization_transaction")
    if isinstance(transaction, dict) and transaction.get("consistency"):
        return str(transaction.get("consistency") or "")
    return str(record.get("transaction_consistency") or "")


def transaction_graph_absent_reasons_from_evidence(record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return []
    transaction = record.get("optimization_transaction")
    if isinstance(transaction, dict) and isinstance(transaction.get("transaction_graph_absent_reasons"), list):
        return [str(reason) for reason in transaction["transaction_graph_absent_reasons"] if str(reason)]
    reasons = record.get("transaction_graph_absent_reasons")
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons if str(reason)]
    return []


def transaction_graph_absent_diagnostics_from_evidence(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if record is None:
        return []
    transaction = record.get("optimization_transaction")
    if isinstance(transaction, dict):
        diagnostics = _transaction_graph_absent_diagnostics(
            transaction.get("transaction_graph_absent_diagnostics", [])
        )
        if diagnostics:
            return diagnostics
    return _transaction_graph_absent_diagnostics(record.get("transaction_graph_absent_diagnostics", []))


def source_slice_contract_status_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    transaction = record.get("optimization_transaction")
    if isinstance(transaction, dict) and transaction.get("source_slice_contract_status"):
        return str(transaction.get("source_slice_contract_status") or "")
    return str(record.get("source_slice_contract_status") or "")


def source_slice_contract_checks_from_evidence(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if record is None:
        return []
    transaction = record.get("optimization_transaction")
    if isinstance(transaction, dict) and isinstance(transaction.get("source_slice_contract_checks"), list):
        return [dict(check) for check in transaction["source_slice_contract_checks"] if isinstance(check, dict)]
    checks = record.get("source_slice_contract_checks")
    if isinstance(checks, list):
        return [dict(check) for check in checks if isinstance(check, dict)]
    return []


def source_slice_contract_verification_status_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    return str(record.get("source_slice_contract_verification_status") or "")


def source_slice_contract_verification_mismatches_from_evidence(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if record is None:
        return []
    mismatches = record.get("source_slice_contract_verification_mismatches")
    if isinstance(mismatches, list):
        return [dict(item) for item in mismatches if isinstance(item, dict)]
    verification = record.get("source_slice_contract_verification")
    if isinstance(verification, dict) and isinstance(verification.get("mismatches"), list):
        return [dict(item) for item in verification["mismatches"] if isinstance(item, dict)]
    return []


def transaction_formalization_verification_status_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    return str(record.get("transaction_formalization_verification_status") or "")


def transaction_formalization_verification_mismatches_from_evidence(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if record is None:
        return []
    mismatches = record.get("transaction_formalization_verification_mismatches")
    if isinstance(mismatches, list):
        return [dict(item) for item in mismatches if isinstance(item, dict)]
    verification = record.get("transaction_formalization_verification")
    if isinstance(verification, dict) and isinstance(verification.get("mismatches"), list):
        return [dict(item) for item in verification["mismatches"] if isinstance(item, dict)]
    return []


def transaction_formal_provenance_coverage_status_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    if record.get("transaction_formal_provenance_coverage_status"):
        return str(record.get("transaction_formal_provenance_coverage_status") or "")
    verification = record.get("transaction_formalization_verification")
    if isinstance(verification, dict):
        coverage = verification.get("provenance_coverage")
        if isinstance(coverage, dict):
            return str(coverage.get("status") or "")
    return ""


def globalopt_witness_status_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    if record.get("globalopt_witness_status"):
        return str(record.get("globalopt_witness_status") or "")
    witness = record.get("globalopt_witness")
    if isinstance(witness, dict):
        return str(witness.get("status") or "")
    return ""


def globalopt_witness_failure_reasons_from_evidence(record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return []
    reasons = record.get("globalopt_witness_failure_reasons")
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons if str(reason)]
    witness = record.get("globalopt_witness")
    if isinstance(witness, dict) and isinstance(witness.get("failure_reasons"), list):
        return [str(reason) for reason in witness["failure_reasons"] if str(reason)]
    return []


def globalopt_witness_cases_from_evidence(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if record is None:
        return []
    cases = record.get("globalopt_witness_cases")
    if not isinstance(cases, list):
        witness = record.get("globalopt_witness")
        cases = witness.get("cases") if isinstance(witness, dict) else []
    if not isinstance(cases, list):
        return []
    return [dict(case) for case in cases if isinstance(case, dict)]


def globalopt_witness_structural_status_from_evidence(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    if record.get("globalopt_witness_structural_status"):
        return str(record.get("globalopt_witness_structural_status") or "")
    cases = globalopt_witness_cases_from_evidence(record)
    if not cases:
        return ""
    statuses = [str(case.get("structural_checks") or "unset") for case in cases]
    if any(status == "failed" for status in statuses):
        return "failed"
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    return "incomplete"


def globalopt_required_witness_cases_from_evidence(record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return []
    cases = record.get("globalopt_required_witness_cases")
    if isinstance(cases, list):
        return [str(case) for case in cases if str(case)]
    witness = record.get("globalopt_witness")
    if isinstance(witness, dict) and isinstance(witness.get("required_cases"), list):
        return [str(case) for case in witness.get("required_cases", []) if str(case)]
    return []


def globalopt_missing_required_witness_cases_from_evidence(record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return []
    cases = record.get("globalopt_missing_required_witness_cases")
    if isinstance(cases, list):
        return [str(case) for case in cases if str(case)]
    witness = record.get("globalopt_witness")
    if isinstance(witness, dict) and isinstance(witness.get("missing_required_cases"), list):
        return [str(case) for case in witness.get("missing_required_cases", []) if str(case)]
    return []


def globalopt_rewrite_provenance_from_evidence(record: dict[str, Any] | None) -> dict[str, str]:
    if record is None:
        return {}
    return {
        "globalopt_rewrite_provenance_status": str(record.get("globalopt_rewrite_provenance_status") or ""),
        "globalopt_rewrite_callee": str(record.get("globalopt_rewrite_callee") or ""),
        "globalopt_replacement_expr": str(record.get("globalopt_replacement_expr") or ""),
        "globalopt_value_type_expr": str(record.get("globalopt_value_type_expr") or ""),
        "globalopt_rewrite_subject": str(record.get("globalopt_rewrite_subject") or ""),
    }


def decision_common(candidate: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "evidence_status": str(evidence.get("evidence_status") if evidence else ""),
        "graph_consistency": graph_consistency_from_evidence(evidence),
        "transaction_consistency": transaction_consistency_from_evidence(evidence),
        "transaction_graph_absent_reasons": transaction_graph_absent_reasons_from_evidence(evidence),
        "transaction_graph_absent_diagnostics": transaction_graph_absent_diagnostics_from_evidence(evidence),
        "source_slice_contract_status": source_slice_contract_status_from_evidence(evidence),
        "source_slice_contract_checks": source_slice_contract_checks_from_evidence(evidence),
        "source_slice_contract_verification_status": source_slice_contract_verification_status_from_evidence(evidence),
        "source_slice_contract_verification_mismatches": source_slice_contract_verification_mismatches_from_evidence(evidence),
        "transaction_formalization_verification_status": transaction_formalization_verification_status_from_evidence(evidence),
        "transaction_formalization_verification_mismatches": transaction_formalization_verification_mismatches_from_evidence(evidence),
        "transaction_formal_provenance_coverage_status": transaction_formal_provenance_coverage_status_from_evidence(evidence),
        "globalopt_witness_status": globalopt_witness_status_from_evidence(evidence),
        "globalopt_witness_failure_reasons": globalopt_witness_failure_reasons_from_evidence(evidence),
        "globalopt_witness_contract": dict(evidence.get("globalopt_witness_contract") or {})
        if evidence and isinstance(evidence.get("globalopt_witness_contract"), dict)
        else {},
        "globalopt_witness_contract_verification_status": str(
            evidence.get("globalopt_witness_contract_verification_status") if evidence else ""
        ),
        "globalopt_witness_contract_formal_status": dict(
            evidence.get("globalopt_witness_contract_formal_status") or {}
        ) if evidence and isinstance(evidence.get("globalopt_witness_contract_formal_status"), dict) else {},
        "globalopt_witness_contract_semantic_status": dict(
            evidence.get("globalopt_witness_contract_semantic_status") or {}
        ) if evidence and isinstance(evidence.get("globalopt_witness_contract_semantic_status"), dict) else {},
        "globalopt_witness_contract_failed_checks": list(
            evidence.get("globalopt_witness_contract_failed_checks") or []
        ) if evidence and isinstance(evidence.get("globalopt_witness_contract_failed_checks"), list) else [],
        "globalopt_witness_contract_semantic_failed_checks": list(
            evidence.get("globalopt_witness_contract_semantic_failed_checks") or []
        ) if evidence and isinstance(evidence.get("globalopt_witness_contract_semantic_failed_checks"), list) else [],
        "globalopt_witness_contract_formal_obligations": [
            dict(item) for item in evidence.get("globalopt_witness_contract_formal_obligations", [])
            if isinstance(item, dict)
        ] if evidence and isinstance(evidence.get("globalopt_witness_contract_formal_obligations"), list) else [],
        "globalopt_witness_contract_semantic_obligations": [
            dict(item) for item in evidence.get("globalopt_witness_contract_semantic_obligations", [])
            if isinstance(item, dict)
        ] if evidence and isinstance(evidence.get("globalopt_witness_contract_semantic_obligations"), list) else [],
        "globalopt_safety_provenance_status": str(
            evidence.get("globalopt_safety_provenance_status") if evidence else ""
        ),
        "globalopt_safety_provenance_failed_checks": list(
            evidence.get("globalopt_safety_provenance_failed_checks") or []
        ) if evidence and isinstance(evidence.get("globalopt_safety_provenance_failed_checks"), list) else [],
        "globalopt_safety_provenance": [
            dict(item) for item in evidence.get("globalopt_safety_provenance", [])
            if isinstance(item, dict)
        ] if evidence and isinstance(evidence.get("globalopt_safety_provenance"), list) else [],
        "predicate_provenance_verification_status": str(
            (evidence.get("predicate_provenance_verification_status") if evidence else "") or ""
        ),
        "predicate_provenance_failed_checks": list(
            evidence.get("predicate_provenance_failed_checks") or []
        ) if evidence and isinstance(evidence.get("predicate_provenance_failed_checks"), list) else [],
        "predicate_provenance_verification": dict(evidence.get("predicate_provenance_verification") or {})
        if evidence and isinstance(evidence.get("predicate_provenance_verification"), dict)
        else {},
        "globalopt_witness_structural_status": globalopt_witness_structural_status_from_evidence(evidence),
        "globalopt_required_witness_cases": globalopt_required_witness_cases_from_evidence(evidence),
        "globalopt_missing_required_witness_cases": globalopt_missing_required_witness_cases_from_evidence(evidence),
        "globalopt_witness_cases": globalopt_witness_cases_from_evidence(evidence),
        **globalopt_rewrite_provenance_from_evidence(evidence),
        "file": candidate.get("file", ""),
        "line": candidate.get("line", ""),
    }


def candidate_to_intent(record: dict[str, Any], replay_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    intent = record.get("intent_candidate", {})
    if not isinstance(intent, dict):
        intent = {}
    candidate_evidence = record.get("evidence", {})
    intent_evidence = {
        "file": str(record.get("file") or ""),
        "line": int(record.get("line") or 0),
        "predicate_source": str(record.get("predicate_source") or ""),
        "rewrite_source": str(record.get("rewrite_source") or ""),
        "proof_status": str(record.get("proof_status") or ""),
        "proof_result": str(record.get("proof_result") or ""),
        "confidence": str(record.get("confidence") or ""),
        "matched_pattern": str(candidate_evidence.get("matched_pattern") or "") if isinstance(candidate_evidence, dict) else "",
    }
    if isinstance(candidate_evidence, dict):
        if isinstance(candidate_evidence.get("semantic_facts"), dict):
            intent_evidence["semantic_facts"] = candidate_evidence["semantic_facts"]
        if candidate_evidence.get("semantic_lowering"):
            intent_evidence["semantic_lowering"] = str(candidate_evidence.get("semantic_lowering") or "")
        if candidate_evidence.get("side_condition_lowering"):
            intent_evidence["side_condition_lowering"] = str(candidate_evidence.get("side_condition_lowering") or "")
        if isinstance(record.get("modeled_side_conditions"), list):
            intent_evidence["modeled_side_conditions"] = list(record.get("modeled_side_conditions") or [])
        if isinstance(candidate_evidence.get("formal_parameters"), dict):
            intent_evidence["formal_parameters"] = dict(candidate_evidence["formal_parameters"])
            semantic_parameters = {
                key: value
                for key, value in candidate_evidence["formal_parameters"].items()
                if str(key).startswith("semantic.")
            }
            if semantic_parameters:
                intent_evidence["semantic_parameters"] = semantic_parameters
            side_condition_parameters = {
                key: value
                for key, value in candidate_evidence["formal_parameters"].items()
                if str(key).startswith("side_conditions.")
            }
            if side_condition_parameters:
                intent_evidence["side_condition_parameters"] = side_condition_parameters
            graph = compact_graph_provenance(candidate_evidence)
            if graph:
                intent_evidence["source_intent_graph"] = graph
                if graph.get("status"):
                    intent_evidence["source_intent_graph_status"] = graph["status"]
                if graph.get("lowering"):
                    intent_evidence["source_intent_graph_lowering"] = graph["lowering"]
                if graph.get("consistency"):
                    intent_evidence["source_intent_graph_consistency"] = graph["consistency"]
                if graph.get("consistency_errors"):
                    intent_evidence["source_intent_graph_consistency_errors"] = graph["consistency_errors"]
                for key in ("predicate_nodes", "rewrite_nodes", "bindings", "formal_symbols"):
                    if key in graph:
                        intent_evidence[f"source_intent_graph_{key}"] = graph[key]
            source_program_graph = compact_source_program_graph_contract(candidate_evidence)
            if source_program_graph:
                intent_evidence["source_program_graph_contract"] = source_program_graph
                intent_evidence["source_program_graph_contract_status"] = source_program_graph["status"]
                if source_program_graph.get("failed_checks"):
                    intent_evidence["source_program_graph_contract_failed_checks"] = source_program_graph["failed_checks"]
                if source_program_graph.get("failure_reasons"):
                    intent_evidence["source_program_graph_contract_failure_reasons"] = source_program_graph["failure_reasons"]
                for key in ("cfg_blocks", "dfg_edges", "interprocedural_dfg", "access_path_facts"):
                    if key in source_program_graph:
                        intent_evidence[f"source_program_graph_{key}"] = source_program_graph[key]
        transaction = compact_transaction_provenance(candidate_evidence)
        if transaction:
            intent_evidence["optimization_transaction"] = transaction
            for source_key, output_key in (
                ("lowering", "transaction_lowering"),
                ("kind", "transaction_kind"),
                ("opcode", "transaction_opcode"),
                ("lanes", "transaction_lanes"),
                ("consistency", "transaction_consistency"),
                ("consistency_errors", "transaction_consistency_errors"),
                ("has_lane_mapping", "transaction_has_lane_mapping"),
                ("has_result_lane_mapping", "transaction_has_result_lane_mapping"),
                ("scalar_lane_pairs", "transaction_scalar_lane_pairs"),
                ("reduction_opcode", "transaction_reduction_opcode"),
                ("reduction_lanes", "transaction_reduction_lanes"),
                ("reduction_sources", "transaction_reduction_sources"),
                ("has_reduction_result", "transaction_has_reduction_result"),
                ("transaction_graph_absent_reasons", "transaction_graph_absent_reasons"),
                ("transaction_graph_absent_diagnostics", "transaction_graph_absent_diagnostics"),
                ("source_slice_contract_status", "source_slice_contract_status"),
                ("source_slice_contract_missing_roles", "source_slice_contract_missing_roles"),
                ("source_slice_contract_role_paths", "source_slice_contract_role_paths"),
                ("source_slice_contract_checks", "source_slice_contract_checks"),
                ("source_slice_contract_verification_status", "source_slice_contract_verification_status"),
                ("source_slice_contract_verification_mismatches", "source_slice_contract_verification_mismatches"),
            ):
                if source_key in transaction:
                    intent_evidence[output_key] = transaction[source_key]
        if isinstance(candidate_evidence.get("source_slice_contract_verification"), dict):
            intent_evidence["source_slice_contract_verification"] = dict(
                candidate_evidence["source_slice_contract_verification"]
            )
        for key in (
            "source_slice_contract_verification_status",
            "source_slice_contract_verification_mismatches",
            "transaction_formalization_verification_status",
            "transaction_formalization_verification_mismatches",
            "transaction_formal_provenance_coverage_status",
            "transaction_formal_provenance_missing_paths",
            "transaction_formal_provenance_roles",
        ):
            if key in candidate_evidence:
                intent_evidence[key] = candidate_evidence[key]
        if isinstance(candidate_evidence.get("transaction_formalization_verification"), dict):
            intent_evidence["transaction_formalization_verification"] = dict(
                candidate_evidence["transaction_formalization_verification"]
            )
    intent_evidence.update(evidence_summary(replay_evidence))
    promoted = {
        "marker": str(record.get("marker") or intent.get("marker") or ""),
        "category": str(record.get("pass") or "inferred"),
        "precondition": str(intent.get("precondition") or ""),
        "rewrite": str(intent.get("rewrite") or ""),
        "intent": str(intent.get("intent") or "semantic-equivalence"),
        "evidence": intent_evidence,
    }
    if isinstance(intent.get("formal"), dict):
        promoted["formal"] = intent["formal"]
    if isinstance(intent.get("relaxed_fp_policy"), dict):
        promoted["relaxed_fp_policy"] = intent["relaxed_fp_policy"]
    return promoted


def ready_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("promotion_status") == "ready"]


def promote(
    current: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    replace_existing: bool,
    evidence_records: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposed = [dict(record) for record in current]
    index_by_marker = {
        str(record.get("marker", "")): index
        for index, record in enumerate(proposed)
        if str(record.get("marker", ""))
    }
    decisions: list[dict[str, Any]] = []
    seen_ready: set[str] = set()

    for candidate in candidates:
        marker = str(candidate.get("marker", ""))
        if not marker or marker in seen_ready:
            decisions.append({"marker": marker, "status": "skipped-duplicate", "file": candidate.get("file", ""), "line": candidate.get("line", "")})
            continue
        seen_ready.add(marker)
        evidence = evidence_records.get(marker) if evidence_records is not None else None
        if evidence_records is not None and (evidence is None or evidence.get("evidence_status") != "verified"):
            decisions.append({
                "marker": marker,
                "status": "evidence-blocked",
                **decision_common(candidate, evidence),
            })
            if evidence is None:
                decisions[-1]["evidence_status"] = "missing"
            continue
        intent = candidate_to_intent(candidate, evidence)
        if marker in index_by_marker and not replace_existing:
            decisions.append({"marker": marker, "status": "already-present", **decision_common(candidate, evidence)})
            continue
        if marker in index_by_marker:
            proposed[index_by_marker[marker]] = intent
            decisions.append({"marker": marker, "status": "replaced", **decision_common(candidate, evidence)})
            continue
        index_by_marker[marker] = len(proposed)
        proposed.append(intent)
        decisions.append({"marker": marker, "status": "added", **decision_common(candidate, evidence)})

    return proposed, decisions


def predicate_provenance_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    verification_status = collections.Counter(
        str(record.get("predicate_provenance_verification_status") or "absent")
        for record in records
    )
    failed_checks = collections.Counter(
        str(check)
        for record in records
        for check in record.get("predicate_provenance_failed_checks", [])
        if str(check)
    )
    checked = sum(
        1
        for record in records
        if str(record.get("predicate_provenance_verification_status") or "absent") != "absent"
    )
    return {
        "records": len(records),
        "checked": checked,
        "passed": int(verification_status.get("passed", 0)),
        "failed": int(verification_status.get("failed", 0)),
        "absent": int(verification_status.get("absent", 0)),
        "verification_status": dict(sorted(verification_status.items())),
        "failed_checks": dict(sorted(failed_checks.items())),
    }


def report_text(records: list[dict[str, Any]], ready: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> str:
    decision_counts = collections.Counter(str(record["status"]) for record in decisions)
    graph_counts = collections.Counter(
        str(record.get("graph_consistency") or "absent") for record in decisions if record.get("evidence_status")
    )
    graph_blocked = sum(
        1
        for record in decisions
        if record.get("status") == "evidence-blocked" and record.get("graph_consistency") == "failed"
    )
    transaction_counts = collections.Counter(
        str(record.get("transaction_consistency") or "absent")
        for record in decisions
        if record.get("evidence_status")
    )
    transaction_blocked = sum(
        1
        for record in decisions
        if record.get("status") == "evidence-blocked" and record.get("transaction_consistency") == "failed"
    )
    contract_counts = collections.Counter(
        str(record.get("source_slice_contract_status") or "absent")
        for record in decisions
        if record.get("evidence_status")
    )
    contract_blocked = sum(
        1
        for record in decisions
        if record.get("status") == "evidence-blocked"
        and record.get("source_slice_contract_status") == "failed"
    )
    contract_failed_checks = collections.Counter(
        str(check.get("id") or check.get("kind") or "unknown")
        for record in decisions
        for check in record.get("source_slice_contract_checks", [])
        if isinstance(check, dict) and str(check.get("status") or "") == "failed"
    )
    contract_verification_counts = collections.Counter(
        str(record.get("source_slice_contract_verification_status") or "absent")
        for record in decisions
        if record.get("evidence_status")
    )
    contract_verifier_blocked = sum(
        1
        for record in decisions
        if record.get("status") == "evidence-blocked"
        and record.get("source_slice_contract_verification_status") == "failed"
    )
    contract_verifier_mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for record in decisions
        for mismatch in record.get("source_slice_contract_verification_mismatches", [])
        if isinstance(mismatch, dict)
    )
    formalization_counts = collections.Counter(
        str(record.get("transaction_formalization_verification_status") or "absent")
        for record in decisions
        if record.get("evidence_status")
    )
    formalization_blocked = sum(
        1
        for record in decisions
        if record.get("status") == "evidence-blocked"
        and record.get("transaction_formalization_verification_status") == "failed"
    )
    formalization_mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for record in decisions
        for mismatch in record.get("transaction_formalization_verification_mismatches", [])
        if isinstance(mismatch, dict)
    )
    provenance_coverage_counts = collections.Counter(
        str(record.get("transaction_formal_provenance_coverage_status") or "absent")
        for record in decisions
        if record.get("evidence_status")
    )
    globalopt_witness_counts = collections.Counter(
        str(record.get("globalopt_witness_status") or "absent")
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer" and record.get("evidence_status")
    )
    globalopt_witness_failures = collections.Counter(
        str(reason).split(":", 1)[0]
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for reason in record.get("globalopt_witness_failure_reasons", [])
        if str(reason)
    )
    globalopt_witness_cases = collections.Counter(
        (str(case.get("name") or "unknown"), str(case.get("status") or "unset"))
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_witness_structural_counts = collections.Counter(
        str(record.get("globalopt_witness_structural_status") or "absent")
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer" and record.get("evidence_status")
    )
    globalopt_witness_case_structural = collections.Counter(
        (str(case.get("name") or "unknown"), str(case.get("structural_checks") or "unset"))
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_contract_verification_counts = collections.Counter(
        str(record.get("globalopt_witness_contract_verification_status") or "absent")
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer" and record.get("evidence_status")
    )
    globalopt_safety_provenance_status = collections.Counter(
        str(record.get("globalopt_safety_provenance_status") or "absent")
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
    )
    globalopt_safety_provenance_failed_checks = collections.Counter(
        str(check)
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for check in record.get("globalopt_safety_provenance_failed_checks", [])
        if str(check)
    )
    predicate_provenance = predicate_provenance_summary(decisions)
    globalopt_contract_formal_status = collections.Counter(
        str(status)
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for status, count in (record.get("globalopt_witness_contract_formal_status") or {}).items()
        for _ in range(int(count))
    )
    globalopt_contract_semantic_status = collections.Counter(
        str(status)
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for status, count in (record.get("globalopt_witness_contract_semantic_status") or {}).items()
        for _ in range(int(count))
    )
    globalopt_contract_failed_checks = collections.Counter(
        str(check)
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for check in record.get("globalopt_witness_contract_failed_checks", [])
        if str(check)
    )
    globalopt_contract_semantic_failed_checks = collections.Counter(
        str(check)
        for record in decisions
        if record.get("marker") == "probe.globalopt.dead-initializer"
        for check in record.get("globalopt_witness_contract_semantic_failed_checks", [])
        if str(check)
    )
    globalopt_rewrite_provenance = {
        "status": collections.Counter(
            str(record.get("globalopt_rewrite_provenance_status") or "absent")
            for record in decisions
            if record.get("marker") == "probe.globalopt.dead-initializer"
        ),
        "callee": collections.Counter(
            str(record.get("globalopt_rewrite_callee") or "absent")
            for record in decisions
            if record.get("marker") == "probe.globalopt.dead-initializer"
        ),
        "replacement_expr": collections.Counter(
            str(record.get("globalopt_replacement_expr") or "absent")
            for record in decisions
            if record.get("marker") == "probe.globalopt.dead-initializer"
        ),
        "value_type_expr": collections.Counter(
            str(record.get("globalopt_value_type_expr") or "absent")
            for record in decisions
            if record.get("marker") == "probe.globalopt.dead-initializer"
        ),
    }
    helper_diagnostics = [
        diagnostic
        for record in decisions
        for diagnostic in record.get("transaction_graph_absent_diagnostics", [])
        if isinstance(diagnostic, dict)
    ]
    helper_diagnostic_reasons = collections.Counter(
        str(diagnostic.get("reason") or "")
        for diagnostic in helper_diagnostics
        if str(diagnostic.get("reason") or "")
    )
    helper_diagnostic_helpers = collections.Counter(
        str(diagnostic.get("helper") or "")
        for diagnostic in helper_diagnostics
        if str(diagnostic.get("helper") or "")
    )
    helper_diagnostic_roles = collections.Counter(
        str(diagnostic.get("role") or "")
        for diagnostic in helper_diagnostics
        if str(diagnostic.get("role") or "")
    )
    lines = [
        "O2T Intent Promotion Report",
        f"validated_candidates: {len(records)}",
        f"promotion_ready: {len(ready)}",
        "Decisions",
    ]
    if decision_counts:
        for status, count in sorted(decision_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append("Graph consistency")
    if graph_counts:
        for status, count in sorted(graph_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append(f"Graph-blocked decisions: {graph_blocked}")
    lines.append("Transaction consistency")
    if transaction_counts:
        for status, count in sorted(transaction_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append(f"Transaction-blocked decisions: {transaction_blocked}")
    lines.append("Source-slice contract status")
    if contract_counts:
        for status, count in sorted(contract_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append(f"Contract-blocked decisions: {contract_blocked}")
    lines.append("Source-slice contract failed checks")
    if contract_failed_checks:
        for key, value in sorted(contract_failed_checks.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Source-slice contract verification")
    if contract_verification_counts:
        for status, count in sorted(contract_verification_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append(f"Contract-verifier-blocked decisions: {contract_verifier_blocked}")
    lines.append("Source-slice contract verifier mismatches")
    if contract_verifier_mismatch_kinds:
        for key, value in sorted(contract_verifier_mismatch_kinds.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction formalization verification")
    if formalization_counts:
        for status, count in sorted(formalization_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append(f"Transaction-formalization-blocked decisions: {formalization_blocked}")
    lines.append("Transaction formalization mismatches")
    if formalization_mismatch_kinds:
        for key, value in sorted(formalization_mismatch_kinds.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction formal provenance coverage")
    if provenance_coverage_counts:
        for status, count in sorted(provenance_coverage_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witness status")
    if globalopt_witness_counts:
        for status, count in sorted(globalopt_witness_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witness failures")
    if globalopt_witness_failures:
        for key, value in sorted(globalopt_witness_failures.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witness cases")
    if globalopt_witness_cases:
        by_case: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        for (name, status), count in globalopt_witness_cases.items():
            by_case[name][status] += count
        for name, counts in sorted(by_case.items()):
            rendered = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            lines.append(f"  {name}: {rendered}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witness structural checks")
    if globalopt_witness_structural_counts:
        for status, count in sorted(globalopt_witness_structural_counts.items()):
            lines.append(f"  {status}: {count}")
        by_case: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        for (name, status), count in globalopt_witness_case_structural.items():
            by_case[name][status] += count
        for name, counts in sorted(by_case.items()):
            rendered = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            lines.append(f"  {name}: {rendered}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witness contract verification")
    if globalopt_contract_verification_counts:
        for status, count in sorted(globalopt_contract_verification_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    if globalopt_contract_formal_status:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(globalopt_contract_formal_status.items()))
        lines.append(f"  formal_status: {rendered}")
    else:
        lines.append("  formal_status: none")
    if globalopt_contract_semantic_status:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(globalopt_contract_semantic_status.items()))
        lines.append(f"  semantic_status: {rendered}")
    else:
        lines.append("  semantic_status: none")
    if globalopt_contract_failed_checks:
        for key, value in sorted(globalopt_contract_failed_checks.items())[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  failed_checks: none")
    if globalopt_contract_semantic_failed_checks:
        for key, value in sorted(globalopt_contract_semantic_failed_checks.items())[:10]:
            lines.append(f"  semantic_failed {key}: {value}")
    lines.append("GlobalOpt safety provenance")
    if globalopt_safety_provenance_status:
        for status, count in sorted(globalopt_safety_provenance_status.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    if globalopt_safety_provenance_failed_checks:
        for key, value in sorted(globalopt_safety_provenance_failed_checks.items())[:10]:
            lines.append(f"  {key}: {value}")
    lines.append("Predicate provenance")
    lines.append(f"  records: {int(predicate_provenance.get('records') or 0)}")
    lines.append(f"  checked: {int(predicate_provenance.get('checked') or 0)}")
    lines.append(f"  passed: {int(predicate_provenance.get('passed') or 0)}")
    lines.append(f"  failed: {int(predicate_provenance.get('failed') or 0)}")
    lines.append(f"  absent: {int(predicate_provenance.get('absent') or 0)}")
    verification_status = predicate_provenance.get("verification_status", {})
    lines.append(
        "  verification_status: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(verification_status.items()))
            if verification_status
            else "none"
        )
    )
    failed_checks = predicate_provenance.get("failed_checks", {})
    lines.append(
        "  failed_checks: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(failed_checks.items()))
            if failed_checks
            else "none"
        )
    )
    lines.append("GlobalOpt rewrite provenance")
    if any(globalopt_rewrite_provenance.values()):
        for title, counts in globalopt_rewrite_provenance.items():
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
            lines.append(f"  {title}: {rendered}")
    else:
        lines.append("  none")
    lines.append("Helper slice diagnostics")
    if helper_diagnostic_reasons:
        lines.append(
            "  reasons: "
            + ", ".join(f"{key}={value}" for key, value in sorted(helper_diagnostic_reasons.items()))
        )
    else:
        lines.append("  reasons: none")
    if helper_diagnostic_helpers:
        lines.append(
            "  helpers: "
            + ", ".join(f"{key}={value}" for key, value in sorted(helper_diagnostic_helpers.items()))
        )
    else:
        lines.append("  helpers: none")
    if helper_diagnostic_roles:
        lines.append(
            "  roles: "
            + ", ".join(f"{key}={value}" for key, value in sorted(helper_diagnostic_roles.items()))
        )
    else:
        lines.append("  roles: none")
    lines.append("Ready candidates")
    if ready:
        for record in ready:
            lines.append(f"  {record.get('marker', '')} {record.get('file', '')}:{record.get('line', '')}")
    else:
        lines.append("  none")
    lines.append("Promotion decisions")
    if decisions:
        for record in decisions:
            evidence = f" evidence={record.get('evidence_status')}" if record.get("evidence_status") else ""
            graph = f" graph={record.get('graph_consistency')}" if record.get("graph_consistency") else ""
            transaction = (
                f" transaction={record.get('transaction_consistency')}"
                if record.get("transaction_consistency")
                else ""
            )
            contract = (
                f" contract={record.get('source_slice_contract_status')}"
                if record.get("source_slice_contract_status")
                else ""
            )
            contract_verification = (
                f" contract_verification={record.get('source_slice_contract_verification_status')}"
                if record.get("source_slice_contract_verification_status")
                else ""
            )
            formalization = (
                f" formalization={record.get('transaction_formalization_verification_status')}"
                if record.get("transaction_formalization_verification_status")
                else ""
            )
            provenance = (
                f" formal_provenance={record.get('transaction_formal_provenance_coverage_status')}"
                if record.get("transaction_formal_provenance_coverage_status")
                else ""
            )
            globalopt_witness = (
                f" globalopt_witness={record.get('globalopt_witness_status')}"
                if record.get("globalopt_witness_status")
                else ""
            )
            globalopt_contract = (
                f" globalopt_contract_verification={record.get('globalopt_witness_contract_verification_status')}"
                if record.get("globalopt_witness_contract_verification_status")
                else ""
            )
            helper = ""
            diagnostics = record.get("transaction_graph_absent_diagnostics", [])
            if isinstance(diagnostics, list) and diagnostics:
                diagnostic = next((item for item in diagnostics if isinstance(item, dict)), {})
                if diagnostic:
                    helper = (
                        f" helper={diagnostic.get('helper', '')}"
                        f" role={diagnostic.get('role', '')}"
                        f" reason={diagnostic.get('reason', '')}"
                    )
            lines.append(f"  {record['status']} {record.get('marker', '')} {record.get('file', '')}:{record.get('line', '')}{evidence}{graph}{transaction}{contract}{contract_verification}{formalization}{provenance}{globalopt_witness}{globalopt_contract}{helper}")
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        current = load_json(args.current)
        validated = load_jsonl(args.validated)
        evidence_records = evidence_by_marker(load_jsonl(args.evidence)) if args.evidence else None
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    ready = ready_candidates(validated)
    if args.require_ready and not ready:
        print("no promotion-ready intent candidates", file=sys.stderr)
        return 1
    if args.require_verified_evidence:
        if evidence_records is None:
            print("--require-verified-evidence requires --evidence", file=sys.stderr)
            return 1
        verified_ready = [
            record
            for record in ready
            if evidence_records.get(str(record.get("marker") or ""), {}).get("evidence_status") == "verified"
        ]
        if not verified_ready:
            print("no promotion-ready intent candidates with verified evidence", file=sys.stderr)
            return 1

    proposed, decisions = promote(current, ready, args.replace_existing, evidence_records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(proposed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text(validated, ready, decisions), encoding="utf-8")

    print(json.dumps({"validated": len(validated), "ready": len(ready), "decisions": dict(collections.Counter(str(record["status"]) for record in decisions))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
