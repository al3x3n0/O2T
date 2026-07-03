#!/usr/bin/env python3
"""Join intent proof records with replay evidence by optimization marker."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from cv_globalopt_witness import (
    DEFAULT_REQUIRED_WITNESS_CASES,
    compact_witness as compact_globalopt_witness_contract,
    missing_required_cases as globalopt_contract_missing_required_cases,
    structural_status_for_cases,
    witness_contract,
)
from cv_source_graph_contract import source_graph_contract_parameters

BAD_REPLAY_STATUS = {"failed", "error"}
BAD_SEMANTIC_STATUS = {"mismatch", "error"}
BAD_ORACLE_STATUS = {"mismatch", "not-instrumented", "error"}
BAD_ALIVE2_STATUS = {"failed", "error"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validated", type=Path, required=True)
    parser.add_argument("--opt-manifest", type=Path, required=True)
    parser.add_argument("--intents", type=Path)
    parser.add_argument("--source-slice-contract-verification", type=Path)
    parser.add_argument("--transaction-formalization-verification", type=Path)
    parser.add_argument("--globalopt-coverage", type=Path)
    parser.add_argument("--globalopt-witness-contract-verification", type=Path)
    parser.add_argument("--predicate-provenance-verification", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-globalopt-witnesses", action="store_true")
    parser.add_argument("--max-globalopt-witness-failures", type=int)
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


def load_contract_verification_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [record for record in data["records"] if isinstance(record, dict)]
    if isinstance(data, list):
        return [record for record in data if isinstance(record, dict)]
    return []


def split_markers(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def markers_for_case(record: dict[str, Any]) -> set[str]:
    markers = set(split_markers(record.get("expected_markers")))
    markers.update(split_markers(record.get("observed_markers")))
    return markers


def count_field(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(record.get(key) or "unset") for record in records).items()))


def stable_key(record: dict[str, Any]) -> str:
    return "|".join(
        [
            str(record.get("file") or ""),
            str(int(record.get("line") or 0)),
            str(record.get("marker") or ""),
        ]
    )


def intent_by_marker(intents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("marker") or ""): record for record in intents if str(record.get("marker") or "")}


def contract_verification_by_marker(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_marker: dict[str, dict[str, Any]] = {}
    for record in records:
        marker = str(record.get("marker") or "")
        verification = record.get("contract_verification")
        if marker and isinstance(verification, dict):
            by_marker[marker] = verification
    return by_marker


def transaction_formalization_verification_by_marker(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_marker: dict[str, dict[str, Any]] = {}
    for record in records:
        marker = str(record.get("marker") or "")
        verification = record.get("transaction_formalization_verification")
        if marker and isinstance(verification, dict):
            by_marker[marker] = verification
    return by_marker


def globalopt_witness_contract_verification_records(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records") if isinstance(data, dict) else []
    if not isinstance(records, list):
        return {}
    return {
        str(record.get("key") or ""): record
        for record in records
        if isinstance(record, dict) and str(record.get("key") or "")
    }


def predicate_provenance_verification_records(paths: list[Path] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in paths or []:
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records") if isinstance(data, dict) else []
        if not isinstance(records, list):
            continue
        for record in records:
            key = str(record.get("key") or "") if isinstance(record, dict) else ""
            if key:
                out[key] = record
    return out


def globalopt_contract_verification_status(verification: dict[str, Any] | None) -> str:
    if not isinstance(verification, dict) or not verification:
        return "absent"
    return str(verification.get("status") or "absent")


def globalopt_contract_verification_formal_status(verification: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(verification, dict):
        return {}
    counts: collections.Counter[str] = collections.Counter(
        str(obligation.get("formal_status") or "unset")
        for obligation in verification.get("formal_obligations", [])
        if isinstance(obligation, dict)
    )
    return dict(sorted(counts.items()))


def globalopt_contract_verification_semantic_status(verification: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(verification, dict):
        return {}
    counts: collections.Counter[str] = collections.Counter(
        str(obligation.get("semantic_status") or "unset")
        for obligation in verification.get("semantic_obligations", [])
        if isinstance(obligation, dict)
    )
    return dict(sorted(counts.items()))


def load_globalopt_witnesses(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    witnesses = data.get("witnesses")
    if not isinstance(witnesses, dict):
        return {}
    records = witnesses.get("records")
    if not isinstance(records, list):
        return {}
    return {
        str(record.get("key") or ""): record
        for record in records
        if isinstance(record, dict) and str(record.get("key") or "")
    }


def compact_globalopt_witness(record: dict[str, Any], witnesses_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if record.get("marker") != "probe.globalopt.dead-initializer":
        return {}
    witness = witnesses_by_key.get(stable_key(record))
    if not isinstance(witness, dict):
        contract = witness_contract({"status": "absent"}, globalopt_required_witness_cases(record, {}))
        return {"status": "absent", "failure_reasons": [], "witness_contract": contract}
    out = compact_globalopt_witness_contract(witness, globalopt_required_witness_cases(record, witness))
    out.update({
        "before": str(witness.get("before") or ""),
        "after": str(witness.get("after") or ""),
        "witness": str(Path(str(witness.get("before") or "")).parent / "witness.json") if witness.get("before") else "",
    })
    if isinstance(witness.get("source_provenance"), dict):
        out["source_provenance"] = {
            str(key): str(value)
            for key, value in witness["source_provenance"].items()
            if str(key)
        }
    return out


def globalopt_required_witness_cases(record: dict[str, Any], globalopt_witness: dict[str, Any]) -> list[str]:
    witness_cases = globalopt_witness.get("required_cases")
    if isinstance(witness_cases, list) and witness_cases:
        return [str(case) for case in witness_cases if str(case)]
    candidate = record.get("intent_candidate")
    formal = candidate.get("formal") if isinstance(candidate, dict) else {}
    formal_cases = formal.get("required_witness_cases") if isinstance(formal, dict) else []
    if isinstance(formal_cases, list) and formal_cases:
        return [str(case) for case in formal_cases if str(case)]
    evidence = record.get("evidence")
    params = evidence.get("formal_parameters") if isinstance(evidence, dict) else {}
    param_cases = params.get("global.initializer.required_witness_cases") if isinstance(params, dict) else []
    if isinstance(param_cases, list) and param_cases:
        return [str(case) for case in param_cases if str(case)]
    return list(DEFAULT_REQUIRED_WITNESS_CASES) if record.get("marker") == "probe.globalopt.dead-initializer" else []


def globalopt_missing_required_witness_cases(
    record: dict[str, Any],
    globalopt_witness: dict[str, Any],
) -> list[str]:
    required = globalopt_required_witness_cases(record, globalopt_witness)
    if not required:
        return []
    return globalopt_contract_missing_required_cases(globalopt_witness, required)


def globalopt_structural_witness_status(globalopt_witness: dict[str, Any]) -> str:
    contract = globalopt_witness.get("witness_contract")
    if isinstance(contract, dict) and contract.get("structural_status"):
        return str(contract.get("structural_status") or "")
    cases = [case for case in globalopt_witness.get("cases", []) if isinstance(case, dict)] if isinstance(globalopt_witness.get("cases"), list) else []
    return structural_status_for_cases(cases)


def globalopt_rewrite_provenance(
    candidate_evidence: dict[str, Any],
    globalopt_witness: dict[str, Any],
) -> dict[str, str]:
    params = candidate_evidence.get("formal_parameters")
    params = params if isinstance(params, dict) else {}
    witness_source = globalopt_witness.get("source_provenance")
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


GLOBALOPT_REQUIRED_SAFETY_FACTS = ["initializer-dead", "local-linkage", "no-uses"]
GLOBALOPT_FACT_PREDICATES = {
    "initializer-dead": "isGlobalInitializerDead",
    "local-linkage": "hasLocalLinkage",
    "no-uses": "use_empty",
}


def globalopt_safety_provenance_checks(record: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    params = formal_parameters(record)
    raw = params.get("global.initializer.safety_provenance")
    if not isinstance(raw, list):
        return "absent", [], []
    facts = [dict(item) for item in raw if isinstance(item, dict)]
    checks: list[str] = []
    by_fact = {str(item.get("fact") or ""): item for item in facts}
    for fact in GLOBALOPT_REQUIRED_SAFETY_FACTS:
        item = by_fact.get(fact)
        if not item or str(item.get("status") or "") != "observed":
            checks.append(f"{fact}-provenance-missing")
            continue
        expected = GLOBALOPT_FACT_PREDICATES[fact]
        if str(item.get("predicate_family") or "") != expected:
            checks.append(f"{fact}-predicate-family-mismatch")
        if expected not in str(item.get("source") or ""):
            checks.append(f"{fact}-source-mismatch")
        source_range = item.get("source_range")
        if not isinstance(source_range, dict) or not int(source_range.get("begin_line") or 0):
            checks.append(f"{fact}-source-range-missing")
    return ("failed" if checks else "passed"), checks, facts


def compact_case(record: dict[str, Any]) -> dict[str, str]:
    return {
        "case": str(record.get("case") or ""),
        "status": str(record.get("status") or "unset"),
        "semantic_status": str(record.get("semantic_status") or "unset"),
        "oracle_status": str(record.get("oracle_status") or "unset"),
        "alive2_status": str(record.get("alive2_status") or "unset"),
        "before": str(record.get("before") or ""),
        "after": str(record.get("after") or ""),
        "probe_log": str(record.get("probe_log") or ""),
        "alive2_output": str(record.get("alive2_output") or ""),
        "message": str(record.get("message") or ""),
    }


def has_bad_replay(cases: list[dict[str, Any]]) -> bool:
    for record in cases:
        if str(record.get("status") or "unset") in BAD_REPLAY_STATUS:
            return True
        if str(record.get("semantic_status") or "unset") in BAD_SEMANTIC_STATUS:
            return True
        if str(record.get("oracle_status") or "unset") in BAD_ORACLE_STATUS:
            return True
        if str(record.get("alive2_status") or "unset") in BAD_ALIVE2_STATUS:
            return True
    return False


def alive2_only_unsupported(cases: list[dict[str, Any]]) -> bool:
    statuses = {str(record.get("alive2_status") or "unset") for record in cases}
    statuses.discard("unset")
    statuses.discard("not-run")
    return bool(statuses) and statuses <= {"unsupported"}


def formal_parameters(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        return {}
    params = evidence.get("formal_parameters")
    return params if isinstance(params, dict) else {}


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
    for key in ("failed_checks",):
        value = value_for(f"source_program_graph_contract.{key}")
        if isinstance(value, list):
            out[key] = [str(item) for item in value if str(item)]
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
    graph = transaction.get("transaction_graph")
    if isinstance(graph, dict):
        out["graph_kind"] = str(
            params.get("transaction.graph.kind") or graph.get("kind") or ""
        )
        out["graph_consistency"] = str(graph.get("consistency") or "unchecked")
        try:
            out["graph_node_count"] = int(
                params.get("transaction.graph.node_count")
                or len(graph.get("nodes") or [])
            )
        except (TypeError, ValueError):
            out["graph_node_count"] = 0
        try:
            out["graph_edge_count"] = int(
                params.get("transaction.graph.edge_count")
                or len(graph.get("edges") or [])
            )
        except (TypeError, ValueError):
            out["graph_edge_count"] = 0
        root_opcode = params.get("transaction.graph.root_opcode")
        if root_opcode:
            out["graph_root_opcode"] = str(root_opcode)
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


def graph_consistency(record: dict[str, Any]) -> str:
    params = formal_parameters(record)
    if params.get("source_intent_graph.consistency"):
        return str(params.get("source_intent_graph.consistency") or "")
    evidence = record.get("evidence")
    if isinstance(evidence, dict):
        graph = compact_graph_provenance(evidence)
        if graph.get("consistency"):
            return str(graph.get("consistency") or "")
    return "absent"


def source_slice_contract_status(record: dict[str, Any]) -> str:
    params = formal_parameters(record)
    if params.get("transaction.source_slice.contract.status"):
        return str(params.get("transaction.source_slice.contract.status") or "")
    evidence = record.get("evidence")
    if isinstance(evidence, dict):
        transaction = compact_transaction_provenance(evidence)
        if transaction.get("source_slice_contract_status"):
            return str(transaction.get("source_slice_contract_status") or "")
    return "absent"


def source_slice_contract_verification_status(record: dict[str, Any], verification: dict[str, Any] | None) -> str:
    if isinstance(verification, dict) and verification.get("status"):
        return str(verification.get("status") or "")
    evidence = record.get("evidence")
    if isinstance(evidence, dict):
        status = evidence.get("source_slice_contract_verification_status")
        if status:
            return str(status)
    return "absent"


def transaction_formalization_verification_status(record: dict[str, Any], verification: dict[str, Any] | None) -> str:
    if isinstance(verification, dict) and verification.get("status"):
        return str(verification.get("status") or "")
    evidence = record.get("evidence")
    if isinstance(evidence, dict):
        status = evidence.get("transaction_formalization_verification_status")
        if status:
            return str(status)
    return "absent"


def evidence_status(
    record: dict[str, Any],
    covering_cases: list[dict[str, Any]],
    verification: dict[str, Any] | None = None,
    formalization_verification: dict[str, Any] | None = None,
    globalopt_witness: dict[str, Any] | None = None,
    globalopt_contract_verification: dict[str, Any] | None = None,
    predicate_provenance_verification: dict[str, Any] | None = None,
    require_globalopt_witnesses: bool = False,
) -> str:
    proof_status = str(record.get("proof_status") or "unset")
    if proof_status in {"failed", "error"}:
        return "blocked"
    if proof_status == "unsupported":
        return "unsupported"
    if proof_status != "proved":
        return "unsupported"
    verifier_status = str((predicate_provenance_verification or {}).get("status") or "")
    if verifier_status == "failed":
        return "blocked"
    if record.get("marker") == "probe.globalopt.dead-initializer":
        provenance_status, _, _ = globalopt_safety_provenance_checks(record)
        effective_provenance_status = verifier_status or provenance_status
        if effective_provenance_status == "failed":
            return "blocked"
        if require_globalopt_witnesses and effective_provenance_status in {"absent", "failed"}:
            return "blocked"
        contract_verification_status = globalopt_contract_verification_status(globalopt_contract_verification)
        formal_status = globalopt_contract_verification_formal_status(globalopt_contract_verification)
        semantic_status = globalopt_contract_verification_semantic_status(globalopt_contract_verification)
        if contract_verification_status == "failed":
            return "blocked"
        if formal_status.get("error") or formal_status.get("failed"):
            return "blocked"
        if semantic_status.get("error") or semantic_status.get("failed"):
            return "blocked"
        witness_status = str((globalopt_witness or {}).get("status") or "absent")
        missing_required_cases = globalopt_missing_required_witness_cases(record, globalopt_witness or {})
        structural_status = globalopt_structural_witness_status(globalopt_witness or {})
        if witness_status == "failed":
            return "blocked"
        if structural_status == "failed":
            return "blocked"
        if witness_status == "passed":
            if missing_required_cases:
                return "blocked"
            if require_globalopt_witnesses and (
                contract_verification_status == "absent" or formal_status.get("not-run")
            ):
                return "blocked"
            return "verified"
        if require_globalopt_witnesses:
            if contract_verification_status == "absent" or formal_status.get("not-run"):
                return "blocked"
            return "blocked"
    if not covering_cases:
        return "uncovered"
    if has_bad_replay(covering_cases):
        return "blocked"
    if alive2_only_unsupported(covering_cases):
        return "unsupported"
    if graph_consistency(record) == "failed":
        return "blocked"
    if source_slice_contract_status(record) == "failed":
        return "blocked"
    if source_slice_contract_verification_status(record, verification) == "failed":
        return "blocked"
    if transaction_formalization_verification_status(record, formalization_verification) == "failed":
        return "blocked"
    return "verified"


def evidence_record(
    record: dict[str, Any],
    cases_by_marker: dict[str, list[dict[str, Any]]],
    current_intents: dict[str, dict[str, Any]],
    contract_verifications: dict[str, dict[str, Any]] | None = None,
    transaction_formalization_verifications: dict[str, dict[str, Any]] | None = None,
    globalopt_witnesses: dict[str, dict[str, Any]] | None = None,
    globalopt_contract_verifications: dict[str, dict[str, Any]] | None = None,
    predicate_provenance_verifications: dict[str, dict[str, Any]] | None = None,
    require_globalopt_witnesses: bool = False,
) -> dict[str, Any]:
    marker = str(record.get("marker") or "")
    candidate = record.get("intent_candidate", {})
    if not isinstance(candidate, dict):
        candidate = {}
    candidate_evidence = record.get("evidence", {})
    if not isinstance(candidate_evidence, dict):
        candidate_evidence = {}
    current = current_intents.get(marker, {})
    covering_cases = cases_by_marker.get(marker, [])
    verification = contract_verifications.get(marker, {}) if contract_verifications is not None else {}
    formalization_verification = (
        transaction_formalization_verifications.get(marker, {})
        if transaction_formalization_verifications is not None
        else {}
    )
    globalopt_witness = compact_globalopt_witness(record, globalopt_witnesses or {})
    globalopt_contract_verification = (globalopt_contract_verifications or {}).get(stable_key(record), {})
    predicate_provenance_verification = (predicate_provenance_verifications or {}).get(stable_key(record), {})
    status = evidence_status(
        record,
        covering_cases,
        verification,
        formalization_verification,
        globalopt_witness,
        globalopt_contract_verification,
        predicate_provenance_verification,
        require_globalopt_witnesses,
    )
    out = {
        "marker": marker,
        "intent": str(candidate.get("intent") or current.get("intent") or ""),
        "precondition": str(candidate.get("precondition") or current.get("precondition") or ""),
        "rewrite": str(candidate.get("rewrite") or current.get("rewrite") or ""),
        "proof_status": str(record.get("proof_status") or "unset"),
        "proof_result": str(record.get("proof_result") or ""),
        "promotion_status": str(record.get("promotion_status") or ""),
        "confidence": str(record.get("confidence") or ""),
        "evidence_status": status,
        "replay_cases": len(covering_cases),
        "replay_status": count_field(covering_cases, "status"),
        "semantic_status": count_field(covering_cases, "semantic_status"),
        "oracle_status": count_field(covering_cases, "oracle_status"),
        "alive2_status": count_field(covering_cases, "alive2_status"),
        "cases": [compact_case(case) for case in covering_cases],
    }
    if globalopt_witness:
        out["globalopt_witness"] = globalopt_witness
        out["globalopt_witness_status"] = str(globalopt_witness.get("status") or "absent")
        out["globalopt_witness_before"] = str(globalopt_witness.get("before") or "")
        out["globalopt_witness_after"] = str(globalopt_witness.get("after") or "")
        out["globalopt_witness_manifest"] = str(globalopt_witness.get("witness") or "")
        out["globalopt_witness_failure_reasons"] = list(globalopt_witness.get("failure_reasons") or [])
        if isinstance(globalopt_witness.get("witness_contract"), dict):
            out["globalopt_witness_contract"] = dict(globalopt_witness["witness_contract"])
        if globalopt_witness.get("witness_model"):
            out["globalopt_witness_model"] = str(globalopt_witness.get("witness_model") or "")
        structural_status = globalopt_structural_witness_status(globalopt_witness)
        if structural_status:
            out["globalopt_witness_structural_status"] = structural_status
        required_cases = globalopt_required_witness_cases(record, globalopt_witness)
        if required_cases:
            out["globalopt_required_witness_cases"] = required_cases
        missing_required_cases = globalopt_missing_required_witness_cases(record, globalopt_witness)
        if missing_required_cases:
            out["globalopt_missing_required_witness_cases"] = missing_required_cases
        if isinstance(globalopt_witness.get("cases"), list):
            out["globalopt_witness_cases"] = list(globalopt_witness.get("cases") or [])
    if isinstance(predicate_provenance_verification, dict) and predicate_provenance_verification:
        out["predicate_provenance_verification"] = dict(predicate_provenance_verification)
        out["predicate_provenance_verification_status"] = str(
            predicate_provenance_verification.get("status") or "absent"
        )
        out["predicate_provenance_failed_checks"] = [
            str(check)
            for check in predicate_provenance_verification.get("failed_checks", [])
            if str(check)
        ] if isinstance(predicate_provenance_verification.get("failed_checks"), list) else []
    if marker == "probe.globalopt.dead-initializer":
        provenance_status, provenance_checks, provenance_facts = globalopt_safety_provenance_checks(record)
        out["globalopt_safety_provenance_status"] = provenance_status
        out["globalopt_safety_provenance_failed_checks"] = provenance_checks
        if provenance_facts:
            out["globalopt_safety_provenance"] = provenance_facts
        if isinstance(globalopt_contract_verification, dict) and globalopt_contract_verification:
            out["globalopt_witness_contract_verification"] = dict(globalopt_contract_verification)
            out["globalopt_witness_contract_verification_status"] = globalopt_contract_verification_status(
                globalopt_contract_verification
            )
            out["globalopt_witness_contract_formal_status"] = globalopt_contract_verification_formal_status(
                globalopt_contract_verification
            )
            out["globalopt_witness_contract_semantic_status"] = globalopt_contract_verification_semantic_status(
                globalopt_contract_verification
            )
            failed_checks = globalopt_contract_verification.get("failed_checks")
            if isinstance(failed_checks, list):
                out["globalopt_witness_contract_failed_checks"] = [
                    str(check) for check in failed_checks if str(check)
                ]
            obligations = globalopt_contract_verification.get("formal_obligations")
            if isinstance(obligations, list):
                out["globalopt_witness_contract_formal_obligations"] = [
                    dict(item) for item in obligations if isinstance(item, dict)
                ]
            semantic_obligations = globalopt_contract_verification.get("semantic_obligations")
            if isinstance(semantic_obligations, list):
                out["globalopt_witness_contract_semantic_obligations"] = [
                    dict(item) for item in semantic_obligations if isinstance(item, dict)
                ]
            semantic_failed_checks = globalopt_contract_verification.get("semantic_failed_checks")
            if isinstance(semantic_failed_checks, list):
                out["globalopt_witness_contract_semantic_failed_checks"] = [
                    str(check) for check in semantic_failed_checks if str(check)
                ]
        for key, value in globalopt_rewrite_provenance(candidate_evidence, globalopt_witness).items():
            if value:
                out[key] = value
    if isinstance(candidate_evidence.get("semantic_facts"), dict):
        out["semantic_facts"] = candidate_evidence["semantic_facts"]
    if candidate_evidence.get("semantic_lowering"):
        out["semantic_lowering"] = str(candidate_evidence.get("semantic_lowering") or "")
    if isinstance(candidate_evidence.get("formal_parameters"), dict):
        out["formal_parameters"] = dict(candidate_evidence["formal_parameters"])
        semantic_parameters = {
            key: value
            for key, value in candidate_evidence["formal_parameters"].items()
            if str(key).startswith("semantic.")
        }
        if semantic_parameters:
            out["semantic_parameters"] = semantic_parameters
    graph = compact_graph_provenance(candidate_evidence)
    if graph:
        out["source_intent_graph"] = graph
        if graph.get("status"):
            out["source_intent_graph_status"] = graph["status"]
        if graph.get("lowering"):
            out["source_intent_graph_lowering"] = graph["lowering"]
        if graph.get("consistency"):
            out["source_intent_graph_consistency"] = graph["consistency"]
        if graph.get("consistency_errors"):
            out["source_intent_graph_consistency_errors"] = graph["consistency_errors"]
        for key in ("predicate_nodes", "rewrite_nodes", "bindings", "formal_symbols"):
            if key in graph:
                out[f"source_intent_graph_{key}"] = graph[key]
    source_program_graph = compact_source_program_graph_contract(candidate_evidence)
    if source_program_graph:
        out["source_program_graph_contract"] = source_program_graph
        out["source_program_graph_contract_status"] = source_program_graph["status"]
        if source_program_graph.get("failed_checks"):
            out["source_program_graph_contract_failed_checks"] = source_program_graph["failed_checks"]
        if source_program_graph.get("failure_reasons"):
            out["source_program_graph_contract_failure_reasons"] = source_program_graph["failure_reasons"]
        for key in ("cfg_blocks", "dfg_edges", "interprocedural_dfg", "access_path_facts"):
            if key in source_program_graph:
                out[f"source_program_graph_{key}"] = source_program_graph[key]
    transaction = compact_transaction_provenance(candidate_evidence)
    if transaction:
        out["optimization_transaction"] = transaction
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
        ):
            if source_key in transaction:
                out[output_key] = transaction[source_key]
    if isinstance(verification, dict) and verification:
        out["source_slice_contract_verification"] = dict(verification)
        if verification.get("status"):
            out["source_slice_contract_verification_status"] = str(verification.get("status") or "")
        mismatches = verification.get("mismatches")
        if isinstance(mismatches, list):
            out["source_slice_contract_verification_mismatches"] = [
                dict(item) for item in mismatches if isinstance(item, dict)
            ]
    if isinstance(formalization_verification, dict) and formalization_verification:
        out["transaction_formalization_verification"] = dict(formalization_verification)
        if formalization_verification.get("status"):
            out["transaction_formalization_verification_status"] = str(
                formalization_verification.get("status") or ""
            )
        coverage = formalization_verification.get("provenance_coverage")
        if isinstance(coverage, dict):
            if coverage.get("status"):
                out["transaction_formal_provenance_coverage_status"] = str(coverage.get("status") or "")
            missing_paths = coverage.get("missing_paths")
            if isinstance(missing_paths, list):
                out["transaction_formal_provenance_missing_paths"] = [
                    str(path) for path in missing_paths if str(path)
                ]
            roles = coverage.get("roles")
            if isinstance(roles, dict):
                out["transaction_formal_provenance_roles"] = {
                    str(role): int(count)
                    for role, count in roles.items()
                    if str(role)
                }
        mismatches = formalization_verification.get("mismatches")
        if isinstance(mismatches, list):
            out["transaction_formalization_verification_mismatches"] = [
                dict(item) for item in mismatches if isinstance(item, dict)
            ]
    return out


def build_evidence(
    validated: list[dict[str, Any]],
    opt_records: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    contract_verifications: dict[str, dict[str, Any]] | None = None,
    transaction_formalization_verifications: dict[str, dict[str, Any]] | None = None,
    globalopt_witnesses: dict[str, dict[str, Any]] | None = None,
    globalopt_contract_verifications: dict[str, dict[str, Any]] | None = None,
    predicate_provenance_verifications: dict[str, dict[str, Any]] | None = None,
    require_globalopt_witnesses: bool = False,
) -> list[dict[str, Any]]:
    cases_by_marker: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for record in opt_records:
        for marker in markers_for_case(record):
            cases_by_marker[marker].append(record)
    current_intents = intent_by_marker(intents)
    return [
        evidence_record(
            record,
            cases_by_marker,
            current_intents,
            contract_verifications,
            transaction_formalization_verifications,
            globalopt_witnesses,
            globalopt_contract_verifications,
            predicate_provenance_verifications,
            require_globalopt_witnesses,
        )
        for record in validated
    ]


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


def report_text(records: list[dict[str, Any]]) -> str:
    counts = collections.Counter(str(record.get("evidence_status") or "unset") for record in records)
    semantic_counts = collections.Counter(str(record.get("semantic_lowering") or "unset") for record in records)
    graph_consistency_counts = collections.Counter(
        str(record.get("source_intent_graph_consistency") or "absent") for record in records
    )
    graph_lowering_counts = collections.Counter(
        str(record.get("source_intent_graph_lowering") or "unset") for record in records
    )
    graph_blocked = sum(
        1
        for record in records
        if record.get("evidence_status") == "blocked"
        and record.get("source_intent_graph_consistency") == "failed"
    )
    contract_status_counts = collections.Counter(
        str(record.get("source_slice_contract_status") or "absent") for record in records
    )
    contract_blocked = sum(
        1
        for record in records
        if record.get("evidence_status") == "blocked"
        and record.get("source_slice_contract_status") == "failed"
    )
    contract_failed_checks = collections.Counter(
        str(check.get("id") or check.get("kind") or "unknown")
        for record in records
        for check in record.get("source_slice_contract_checks", [])
        if isinstance(check, dict) and str(check.get("status") or "") == "failed"
    )
    contract_verification_counts = collections.Counter(
        str(record.get("source_slice_contract_verification_status") or "absent") for record in records
    )
    contract_verification_blocked = sum(
        1
        for record in records
        if record.get("evidence_status") == "blocked"
        and record.get("source_slice_contract_verification_status") == "failed"
    )
    contract_verification_mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for record in records
        for mismatch in record.get("source_slice_contract_verification_mismatches", [])
        if isinstance(mismatch, dict)
    )
    formalization_verification_counts = collections.Counter(
        str(record.get("transaction_formalization_verification_status") or "absent") for record in records
    )
    formalization_verification_blocked = sum(
        1
        for record in records
        if record.get("evidence_status") == "blocked"
        and record.get("transaction_formalization_verification_status") == "failed"
    )
    formalization_verification_mismatch_kinds = collections.Counter(
        str(mismatch.get("kind") or "unknown")
        for record in records
        for mismatch in record.get("transaction_formalization_verification_mismatches", [])
        if isinstance(mismatch, dict)
    )
    formal_provenance_coverage_counts = collections.Counter(
        str(record.get("transaction_formal_provenance_coverage_status") or "absent") for record in records
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
    globalopt_records = [record for record in records if record.get("marker") == "probe.globalopt.dead-initializer"]
    globalopt_witness_counts = collections.Counter(
        str(record.get("globalopt_witness_status") or "absent") for record in globalopt_records
    )
    globalopt_witness_failures = collections.Counter(
        str(reason).split(":", 1)[0]
        for record in globalopt_records
        for reason in record.get("globalopt_witness_failure_reasons", [])
        if str(reason)
    )
    globalopt_witness_cases = collections.Counter(
        (str(case.get("name") or "unknown"), str(case.get("status") or "unset"))
        for record in globalopt_records
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_witness_structural = collections.Counter(
        str(record.get("globalopt_witness_structural_status") or "absent")
        for record in globalopt_records
    )
    globalopt_witness_case_structural = collections.Counter(
        (str(case.get("name") or "unknown"), str(case.get("structural_checks") or "unset"))
        for record in globalopt_records
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_witness_changed_lines = collections.Counter(
        (
            str(case.get("name") or "unknown"),
            str((case.get("structural_details") or {}).get("changed_line_count") if isinstance(case.get("structural_details"), dict) else "unset"),
        )
        for record in globalopt_records
        for case in record.get("globalopt_witness_cases", [])
        if isinstance(case, dict)
    )
    globalopt_contract_verification_counts = collections.Counter(
        str(record.get("globalopt_witness_contract_verification_status") or "absent")
        for record in globalopt_records
    )
    globalopt_safety_provenance_status = collections.Counter(
        str(record.get("globalopt_safety_provenance_status") or "absent")
        for record in globalopt_records
    )
    globalopt_safety_provenance_failed_checks = collections.Counter(
        str(check)
        for record in globalopt_records
        for check in record.get("globalopt_safety_provenance_failed_checks", [])
        if str(check)
    )
    predicate_provenance = predicate_provenance_summary(records)
    globalopt_contract_formal_status = collections.Counter(
        str(status)
        for record in globalopt_records
        for status, count in (record.get("globalopt_witness_contract_formal_status") or {}).items()
        for _ in range(int(count))
    )
    globalopt_contract_semantic_status = collections.Counter(
        str(status)
        for record in globalopt_records
        for status, count in (record.get("globalopt_witness_contract_semantic_status") or {}).items()
        for _ in range(int(count))
    )
    globalopt_contract_failed_checks = collections.Counter(
        str(check)
        for record in globalopt_records
        for check in record.get("globalopt_witness_contract_failed_checks", [])
        if str(check)
    )
    globalopt_contract_semantic_failed_checks = collections.Counter(
        str(check)
        for record in globalopt_records
        for check in record.get("globalopt_witness_contract_semantic_failed_checks", [])
        if str(check)
    )
    globalopt_rewrite_provenance = {
        "status": collections.Counter(
            str(record.get("globalopt_rewrite_provenance_status") or "absent")
            for record in globalopt_records
        ),
        "callee": collections.Counter(
            str(record.get("globalopt_rewrite_callee") or "absent")
            for record in globalopt_records
        ),
        "replacement_expr": collections.Counter(
            str(record.get("globalopt_replacement_expr") or "absent")
            for record in globalopt_records
        ),
        "value_type_expr": collections.Counter(
            str(record.get("globalopt_value_type_expr") or "absent")
            for record in globalopt_records
        ),
    }
    globalopt_missing_witness = sum(
        1
        for record in globalopt_records
        if record.get("proof_status") == "proved"
        and str(record.get("globalopt_witness_status") or "absent") == "absent"
    )
    transaction_records = [
        record for record in records if isinstance(record.get("optimization_transaction"), dict)
    ]
    transaction_lowering_counts = collections.Counter(
        str(record.get("transaction_lowering") or "unset") for record in transaction_records
    )
    transaction_consistency_counts = collections.Counter(
        str(record.get("transaction_consistency") or "absent") for record in transaction_records
    )
    transaction_error_counts = collections.Counter(
        str(error)
        for record in transaction_records
        for error in record.get("transaction_consistency_errors", [])
    )
    helper_diagnostics = [
        diagnostic
        for record in transaction_records
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
        "O2T Intent Evidence Summary",
        f"records: {len(records)}",
        "Evidence status",
    ]
    if counts:
        for key, value in sorted(counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Semantic lowering")
    if semantic_counts:
        for key, value in sorted(semantic_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Source intent graph consistency")
    if graph_consistency_counts:
        for key, value in sorted(graph_consistency_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Source intent graph lowering")
    if graph_lowering_counts:
        for key, value in sorted(graph_lowering_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append(f"Graph-blocked evidence: {graph_blocked}")
    lines.append("Source-slice contract status")
    if contract_status_counts:
        for key, value in sorted(contract_status_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append(f"Contract-blocked evidence: {contract_blocked}")
    lines.append("Source-slice contract failed checks")
    if contract_failed_checks:
        for key, value in sorted(contract_failed_checks.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Source-slice contract verification")
    if contract_verification_counts:
        for key, value in sorted(contract_verification_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append(f"Contract-verifier-blocked evidence: {contract_verification_blocked}")
    lines.append("Source-slice contract verifier mismatches")
    if contract_verification_mismatch_kinds:
        for key, value in sorted(contract_verification_mismatch_kinds.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction formalization verification")
    if formalization_verification_counts:
        for key, value in sorted(formalization_verification_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append(f"Transaction-formalization-blocked evidence: {formalization_verification_blocked}")
    lines.append("Transaction formalization mismatches")
    if formalization_verification_mismatch_kinds:
        for key, value in sorted(formalization_verification_mismatch_kinds.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction formal provenance coverage")
    if formal_provenance_coverage_counts:
        for key, value in sorted(formal_provenance_coverage_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction formal provenance roles")
    if formal_provenance_roles:
        for key, value in sorted(formal_provenance_roles.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction formal provenance missing paths")
    if formal_provenance_missing_paths:
        for key, value in sorted(formal_provenance_missing_paths.items())[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witnesses")
    if globalopt_witness_counts:
        for key, value in sorted(globalopt_witness_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append(f"GlobalOpt proved records missing witnesses: {globalopt_missing_witness}")
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
    if globalopt_witness_structural:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(globalopt_witness_structural.items()))
        lines.append(f"  status: {rendered}")
        by_case_structural: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        for (name, status), count in globalopt_witness_case_structural.items():
            by_case_structural[name][status] += count
        for name, counts in sorted(by_case_structural.items()):
            rendered = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            lines.append(f"  {name}: {rendered}")
        for (name, changed_lines), count in sorted(globalopt_witness_changed_lines.items()):
            lines.append(f"  {name}.changed_lines={changed_lines}: {count}")
    else:
        lines.append("  none")
    lines.append("GlobalOpt witness contract verification")
    if globalopt_contract_verification_counts:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(globalopt_contract_verification_counts.items()))
        lines.append(f"  status: {rendered}")
    else:
        lines.append("  status: none")
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
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(globalopt_safety_provenance_status.items()))
        lines.append(f"  status: {rendered}")
    else:
        lines.append("  status: none")
    if globalopt_safety_provenance_failed_checks:
        for key, value in sorted(globalopt_safety_provenance_failed_checks.items())[:10]:
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  failed_checks: none")
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
    if globalopt_records:
        for title, counts in globalopt_rewrite_provenance.items():
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
            lines.append(f"  {title}: {rendered}")
    else:
        lines.append("  none")
    lines.append("Optimization transactions")
    lines.append(f"  records: {len(transaction_records)}")
    lines.append("Transaction lowering")
    if transaction_lowering_counts:
        for key, value in sorted(transaction_lowering_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction consistency")
    if transaction_consistency_counts:
        for key, value in sorted(transaction_consistency_counts.items()):
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  none")
    lines.append("Transaction consistency errors")
    if transaction_error_counts:
        for key, value in sorted(transaction_error_counts.items()):
            lines.append(f"  {key}: {value}")
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
    lines.append("Markers")
    if records:
        for record in records:
            lines.append(
                f"  {record.get('evidence_status', '')} {record.get('marker', '')} "
                f"proof={record.get('proof_status', '')} replay_cases={record.get('replay_cases', 0)}"
            )
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    try:
        validated = load_records(args.validated)
        opt_records = load_records(args.opt_manifest)
        intents = load_records(args.intents) if args.intents else []
        contract_verifications = (
            contract_verification_by_marker(load_contract_verification_records(args.source_slice_contract_verification))
            if args.source_slice_contract_verification
            else None
        )
        transaction_formalization_verifications = (
            transaction_formalization_verification_by_marker(
                load_contract_verification_records(args.transaction_formalization_verification)
            )
            if args.transaction_formalization_verification
            else None
        )
        globalopt_witnesses = load_globalopt_witnesses(args.globalopt_coverage)
        globalopt_contract_verifications = globalopt_witness_contract_verification_records(
            args.globalopt_witness_contract_verification
        )
        predicate_provenance_verifications = predicate_provenance_verification_records(
            args.predicate_provenance_verification
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    records = build_evidence(
        validated,
        opt_records,
        intents,
        contract_verifications,
        transaction_formalization_verifications,
        globalopt_witnesses,
        globalopt_contract_verifications,
        predicate_provenance_verifications,
        args.require_globalopt_witnesses,
    )
    write_jsonl(args.out, records)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text(records), encoding="utf-8")

    counts = collections.Counter(str(record.get("evidence_status") or "unset") for record in records)
    print(json.dumps({"records": len(records), "evidence_status": dict(sorted(counts.items()))}, sort_keys=True))
    if args.require_clean and any(record.get("evidence_status") in {"blocked", "uncovered"} for record in records):
        print(
            "intent evidence issues: "
            + str(sum(1 for record in records if record.get("evidence_status") in {"blocked", "uncovered"})),
            file=sys.stderr,
        )
        return 1
    if args.max_globalopt_witness_failures is not None:
        failures = sum(
            1
            for record in records
            if record.get("marker") == "probe.globalopt.dead-initializer"
            and record.get("globalopt_witness_status") == "failed"
        )
        if failures > args.max_globalopt_witness_failures:
            print(
                f"globalopt witness failures: {failures} limit={args.max_globalopt_witness_failures}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
