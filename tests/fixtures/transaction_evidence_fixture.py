#!/usr/bin/env python3
"""Regression fixture for transaction-aware evidence and promotion."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MARKER = "probe.slp.vectorize-binop"
REDUCTION_MARKER = "probe.slp.vectorize-reduction"
GLOBAL_INITIALIZER_MARKER = "probe.globalopt.dead-initializer"


def structural_case(name: str) -> dict[str, Any]:
    details = {
        "i32": {
            "initializer_type": "i32",
            "before_initializer": "42",
            "after_initializer": "0",
            "changed_lines": [3],
        },
        "ptr": {
            "initializer_type": "ptr",
            "before_initializer": "@cv_target",
            "after_initializer": "null",
            "changed_lines": [4],
        },
        "array": {
            "initializer_type": "[2 x i32]",
            "before_initializer": "[i32 1, i32 2]",
            "after_initializer": "zeroinitializer",
            "changed_lines": [3],
        },
    }[name]
    return {
        "global_name": "@cv_dead_init",
        "linkage": "internal",
        "changed_line_count": 1,
        **details,
    }


def witness_contract(status: str = "passed", structural_status: str = "passed") -> dict[str, Any]:
    cases = [
        {"name": "i32", "status": status, "structural_checks": structural_status, "structural_details": structural_case("i32"), "failure_reasons": []},
        {"name": "ptr", "status": status, "structural_checks": structural_status, "structural_details": structural_case("ptr"), "failure_reasons": []},
        {"name": "array", "status": status, "structural_checks": structural_status, "structural_details": structural_case("array"), "failure_reasons": []},
    ]
    missing = [] if status == "passed" and structural_status == "passed" else ["i32"]
    contract_status = "passed" if not missing else "failed"
    return {
        "model": "globalopt-dead-initializer-witness-contract-v1",
        "witness_model": "global-initializer-default-null-family-v1",
        "status": contract_status,
        "witness_status": status,
        "structural_status": structural_status,
        "required_cases": ["i32", "ptr", "array"],
        "missing_required_cases": missing,
        "cases": cases,
    }


def witness_contract_verification(status: str = "passed", formal_status: str = "proved") -> dict[str, Any]:
    obligations = [
        {"case": "i32", "formal_status": formal_status, "reason": "", "smt_file": "i32.smt2", "counterexample": ""},
        {"case": "ptr", "formal_status": formal_status, "reason": "", "smt_file": "ptr.smt2", "counterexample": ""},
        {"case": "array", "formal_status": formal_status, "reason": "", "smt_file": "array.smt2", "counterexample": ""},
    ]
    semantic_obligations = [
        {"case": "i32", "semantic_status": "proved", "reason": "", "before": "i32-before.ll", "after": "i32-after.ll", "result_file": "i32.json", "log_file": "i32.log", "message": ""},
        {"case": "ptr", "semantic_status": "proved", "reason": "", "before": "ptr-before.ll", "after": "ptr-after.ll", "result_file": "ptr.json", "log_file": "ptr.log", "message": ""},
        {"case": "array", "semantic_status": "proved", "reason": "", "before": "array-before.ll", "after": "array-after.ll", "result_file": "array.json", "log_file": "array.log", "message": ""},
    ]
    failed_checks = [] if status == "passed" and formal_status in {"proved", "not-run"} else ["i32-formal-error"]
    return {
        "key": "GlobalOpt.cpp|321|probe.globalopt.dead-initializer",
        "marker": GLOBAL_INITIALIZER_MARKER,
        "status": status,
        "contract_status": "passed" if status == "passed" else "failed",
        "structural_status": "passed",
        "failed_checks": failed_checks,
        "semantic_failed_checks": [],
        "diagnostics": [],
        "formal_obligations": obligations,
        "semantic_status": "proved",
        "semantic_obligations": semantic_obligations,
        "required_cases": ["i32", "ptr", "array"],
        "missing_required_cases": [],
    }


def witness_contract_verification_file(path: Path, records: list[dict[str, Any]]) -> None:
    write_json(path, {
        "model": "o2t-globalopt-witness-contract-verification-v1",
        "summary": {},
        "records": records,
    })


def predicate_provenance_verification(
    status: str = "passed",
    marker: str = GLOBAL_INITIALIZER_MARKER,
    key: str = "GlobalOpt.cpp|321|probe.globalopt.dead-initializer",
    failed_check: str = "local-linkage-provenance-missing",
) -> dict[str, Any]:
    failed_checks = [] if status == "passed" else [failed_check]
    return {
        "key": key,
        "marker": marker,
        "status": status,
        "predicate_provenance_status": status,
        "required_facts": ["initializer-dead", "local-linkage", "no-uses"],
        "observed_facts": ["initializer-dead", "local-linkage", "no-uses"] if status == "passed" else ["initializer-dead", "no-uses"],
        "missing_facts": [] if status == "passed" else ["local-linkage"],
        "failed_checks": failed_checks,
        "facts": global_initializer_validated_record()["evidence"]["formal_parameters"]["global.initializer.safety_provenance"],
    }


def predicate_provenance_verification_file(path: Path, records: list[dict[str, Any]]) -> None:
    write_json(path, {
        "model": "o2t-predicate-provenance-verification-v1",
        "summary": {},
        "records": records,
    })


COMPLETE_CONTRACT_CHECKS = [
    {
        "id": "role-reachability:legality",
        "kind": "role-reachability",
        "role": "legality",
        "status": "passed",
        "witness": {"function": "isTreeLegal", "path": ["vectorizeTree", "isTreeLegal"]},
    },
    {
        "id": "predicate-expands-legality",
        "kind": "predicate-expansion",
        "role": "legality",
        "status": "passed",
        "witness": {"control_root_function": "vectorizeTree"},
    },
]
FAILED_CONTRACT_CHECKS = [
    {
        "id": "role-reachability:legality",
        "kind": "role-reachability",
        "role": "legality",
        "status": "failed",
        "counterexample": {"reason": "missing-role-evidence", "control_root_function": "vectorizeTree"},
    },
    {
        "id": "predicate-expands-legality",
        "kind": "predicate-expansion",
        "role": "legality",
        "status": "failed",
        "counterexample": {"reason": "missing-expanded-legality", "control_root_function": "vectorizeTree"},
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("evidence", "promotion", "audit"), required=True)
    return parser.parse_args()


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def contract_verification(marker: str, status: str = "passed") -> dict[str, Any]:
    mismatches = [] if status == "passed" else [{"id": "contract-status", "kind": "status-mismatch"}]
    return {
        "summary": {"records": 1, "status": {status: 1}},
        "records": [
            {
                "marker": marker,
                "contract_verification": {
                    "status": status,
                    "contract_status": "complete",
                    "recomputed_contract_status": "complete" if status == "passed" else "failed",
                    "mismatches": mismatches,
                    "failed_checks": [],
                },
            }
        ],
    }


def formalization_verification(marker: str, status: str = "passed") -> dict[str, Any]:
    mismatches = [] if status == "passed" else [{"kind": "after-mismatch"}]
    coverage = {
        "status": "passed",
        "covered_paths": ["domain", "after.op"],
        "missing_paths": [],
        "roles": {"domain": 1, "opcode": 1},
    } if status == "passed" else {
        "status": "incomplete",
        "covered_paths": ["domain"],
        "missing_paths": ["after.op"],
        "roles": {"domain": 1},
    }
    return {
        "summary": {"records": 1, "status": {status: 1}},
        "records": [
            {
                "marker": marker,
                "transaction_formalization_verification": {
                    "status": status,
                    "reason": "" if status == "passed" else "formal-mismatch",
                    "mismatches": mismatches,
                    "provenance_coverage": coverage,
                },
            }
        ],
    }


def globalopt_coverage(work_dir: Path, status: str = "passed") -> dict[str, Any]:
    failure_reasons = [] if status == "passed" else ["i32-before-llvm-as-failed: failed", "ptr-after-llvm-as-failed: failed"]
    case_status = "passed" if status == "passed" else "failed"
    cases = [
        {
            "name": "i32",
            "status": case_status,
            "before": str(work_dir / "i32" / "before.ll"),
            "after": str(work_dir / "i32" / "after.ll"),
            "structural_checks": "passed",
            "structural_details": structural_case("i32"),
            "failure_reasons": [] if status == "passed" else ["i32-before-llvm-as-failed: failed"],
        },
        {
            "name": "ptr",
            "status": case_status,
            "before": str(work_dir / "ptr" / "before.ll"),
            "after": str(work_dir / "ptr" / "after.ll"),
            "structural_checks": "passed",
            "structural_details": structural_case("ptr"),
            "failure_reasons": [] if status == "passed" else ["ptr-after-llvm-as-failed: failed"],
        },
        {
            "name": "array",
            "status": "passed",
            "before": str(work_dir / "array" / "before.ll"),
            "after": str(work_dir / "array" / "after.ll"),
            "structural_checks": "passed",
            "structural_details": structural_case("array"),
            "failure_reasons": [],
        },
    ]
    return {
        "model": "o2t-globalopt-coverage-v1",
        "witnesses": {
            "enabled": True,
            "total": 1,
            "passed": 1 if status == "passed" else 0,
            "failed": 1 if status == "failed" else 0,
            "skipped": 0,
            "required_cases": ["i32", "ptr", "array"],
            "failure_reasons": {} if status == "passed" else {
                "i32-before-llvm-as-failed": 1,
                "ptr-after-llvm-as-failed": 1,
            },
            "records": [
                {
                    "key": "GlobalOpt.cpp|321|probe.globalopt.dead-initializer",
                    "marker": GLOBAL_INITIALIZER_MARKER,
                    "file": "GlobalOpt.cpp",
                    "line": 321,
                    "status": status,
                    "before": str(work_dir / "before.ll"),
                    "after": str(work_dir / "after.ll"),
                    "witness_model": "global-initializer-default-null-family-v1",
                    "required_cases": ["i32", "ptr", "array"],
                    "missing_required_cases": [] if status == "passed" else ["i32", "ptr"],
                    "source_provenance": {
                        "rewrite_callee": "setInitializer",
                        "replacement_expr": "Constant::getNullValue(GV->getValueType())",
                        "value_type_expr": "GV->getValueType()",
                        "subject": "GV",
                        "rewrite_provenance_status": "complete",
                    },
                    "cases": cases,
                    "failure_reasons": failure_reasons,
                }
            ],
        },
    }


def run(cmd: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{cmd} returned {result.returncode}, expected {expect}")
    return result


def transaction_evidence(consistency: str = "ok", contract_status: str = "") -> dict[str, Any]:
    lane_map = [2, 0, 3, 1]
    scalar_pairs = [
        {"result": "r2", "lhs": "a2", "rhs": "b2"},
        {"result": "r0", "lhs": "a0", "rhs": "b0"},
        {"result": "r3", "lhs": "a3", "rhs": "b3"},
        {"result": "r1", "lhs": "a1", "rhs": "b1"},
    ]
    params: dict[str, Any] = {
        "transaction.kind": "slp-vectorize-binop",
        "transaction.opcode": "add",
        "transaction.lanes": 4,
        "transaction.consistency": consistency,
        "transaction.consistency_errors": [] if consistency == "ok" else ["lane pairing mismatch"],
        "transaction.lane_mapping.map": lane_map,
        "transaction.lane_mapping.inverse_map": [1, 3, 0, 2],
        "transaction.result_lane_mapping.map": lane_map,
        "transaction.result_lane_mapping.inverse_map": [1, 3, 0, 2],
        "transaction.scalar_lane_pairs": scalar_pairs,
    }
    source_slice: dict[str, Any] = {}
    if contract_status:
        missing_roles = [] if contract_status == "complete" else ["legality"]
        role_paths = [{"role": "legality", "function": "isTreeLegal", "path": ["vectorizeTree", "isTreeLegal"]}]
        checks = COMPLETE_CONTRACT_CHECKS if contract_status == "complete" else FAILED_CONTRACT_CHECKS
        params["transaction.source_slice.contract.status"] = contract_status
        params["transaction.source_slice.contract.missing_roles"] = missing_roles
        params["transaction.source_slice.contract.role_paths"] = role_paths
        params["transaction.source_slice.contract.checks"] = checks
        source_slice["contract"] = {
            "status": contract_status,
            "missing_roles": missing_roles,
            "role_paths": role_paths,
            "checks": checks,
        }
        if contract_status == "failed":
            params["transaction.consistency_errors"] = ["missing-contract-role:legality"]
    return {
        "formal_inference": "source-derived-transaction",
        "transaction_lowering": "formal-ir",
        "optimization_transaction": {
            "model": "optimization-transaction-v1",
            "kind": "slp-vectorize-binop",
            "opcode": "add",
            "lanes": 4,
            "consistency": consistency,
            "consistency_errors": params["transaction.consistency_errors"],
            "scalar_lane_pairs": scalar_pairs,
            **({"source_slice": source_slice} if source_slice else {}),
        },
        "formal_parameters": params,
    }


def reduction_transaction_evidence(
    opcode: str = "add",
    consistency_errors: list[str] | None = None,
    lanes: int = 4,
    width_status: str = "",
) -> dict[str, Any]:
    unsupported_reasons: list[str] = []
    if consistency_errors is not None:
        unsupported_reasons = [error for error in consistency_errors if error.startswith("unsupported-reduction-")]
    errors = consistency_errors if consistency_errors is not None else unsupported_reasons
    consistency = "failed" if errors else "ok"
    params: dict[str, Any] = {
        "transaction.kind": "slp-vectorize-reduction",
        "transaction.opcode": opcode,
        "transaction.reduction_opcode": opcode,
        "transaction.lanes": lanes,
        "transaction.reduction_lanes": lanes,
        "transaction.consistency": consistency,
        "transaction.consistency_errors": errors,
        "transaction.unsupported_reduction_reasons": unsupported_reasons,
        "transaction.lane_mapping.map": [2, 0, 3, 1],
        "transaction.lane_mapping.inverse_map": [1, 3, 0, 2],
        "transaction.reduction_sources": [{"line": 42, "source": f"Create{opcode.title()}Reduce(LHS)"}],
        "transaction.reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
        "transaction.scalar_lane_pairs": [],
    }
    if width_status:
        params["transaction.reduction_width_status"] = width_status
    if opcode in {"fadd", "fmul"} and not errors:
        params["transaction.fp_semantics"] = "ordered-fp32"
        params["transaction.fp_rounding"] = "rne"
    return {
        "formal_inference": "source-derived-transaction",
        "transaction_lowering": "fallback" if errors else "formal-ir",
        "optimization_transaction": {
            "model": "optimization-transaction-v1",
            "kind": "slp-vectorize-reduction",
            "opcode": opcode,
            "reduction_opcode": opcode,
            "lanes": lanes,
            "reduction_lanes": lanes,
            "consistency": consistency,
            "consistency_errors": errors,
            "unsupported_reduction_reasons": unsupported_reasons,
            "lane_mapping": {"map": [2, 0, 3, 1], "inverse_map": [1, 3, 0, 2]},
            "reduction_sources": [{"line": 42, "source": f"Create{opcode.title()}Reduce(LHS)"}],
            "reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
            "scalar_lane_pairs": [],
            **({"reduction_width_status": width_status} if width_status else {}),
        },
        "formal_parameters": params,
    }


def validated_record(consistency: str = "ok") -> dict[str, Any]:
    return {
        "marker": MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 1234,
        "proof_status": "proved" if consistency == "ok" else "unsupported",
        "proof_result": "unsat" if consistency == "ok" else "unsupported-marker",
        "promotion_status": "ready" if consistency == "ok" else "blocked",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "vector-result-equivalence",
            "precondition": "same opcode scalar lane pack",
            "rewrite": "vector add with lane mapping",
        },
        "evidence": transaction_evidence(consistency),
    }


def source_graph_contract_validated_record(
    failed_check: str = "",
    reason: str = "",
) -> dict[str, Any]:
    evidence = transaction_evidence("ok")
    status = "failed" if failed_check else "passed"
    evidence["transaction_lowering"] = "fallback" if failed_check else "formal-ir"
    evidence["formal_inference"] = "" if failed_check else "source-derived-transaction"
    evidence["formal_parameters"]["source_program_graph_contract.status"] = status
    evidence["formal_parameters"]["source_program_graph_contract.failed_checks"] = (
        [failed_check] if failed_check else []
    )
    evidence["formal_parameters"]["source_program_graph_contract.failure_reasons"] = (
        {reason: 1} if reason else {}
    )
    evidence["formal_parameters"]["source_program_graph_contract.cfg_blocks"] = 4
    evidence["formal_parameters"]["source_program_graph_contract.dfg_edges"] = 7 if not failed_check else 5
    evidence["formal_parameters"]["source_program_graph_contract.interprocedural_dfg"] = not failed_check
    evidence["formal_parameters"]["source_program_graph_contract.access_path_facts"] = 2
    return {
        "marker": MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 1300 if not failed_check else 1301,
        "proof_status": "proved" if not failed_check else "unsupported",
        "proof_result": "unsat" if not failed_check else "unsupported-formal-ir",
        "promotion_status": "ready" if not failed_check else "blocked",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "vector-result-equivalence",
            "precondition": "same opcode scalar lane pack",
            "rewrite": "vector add with lane mapping",
        },
        "evidence": evidence,
    }


def global_initializer_validated_record(complete: bool = True) -> dict[str, Any]:
    observed = ["initializer-dead", "local-linkage", "no-uses"] if complete else ["initializer-dead"]
    missing = [] if complete else ["local-linkage", "no-uses"]
    safety_provenance = [
        {
            "fact": fact,
            "status": "observed" if fact in observed else "missing",
            "predicate_family": {
                "initializer-dead": "isGlobalInitializerDead",
                "local-linkage": "hasLocalLinkage",
                "no-uses": "use_empty",
            }[fact] if fact in observed else "",
            "source": {
                "initializer-dead": "isGlobalInitializerDead(GV)",
                "local-linkage": "GV->hasLocalLinkage()",
                "no-uses": "GV->use_empty()",
            }[fact] if fact in observed else "",
            "source_range": {"begin_line": 321, "begin_column": 7, "end_line": 321, "end_column": 10}
            if fact in observed else {},
        }
        for fact in ["initializer-dead", "local-linkage", "no-uses"]
    ]
    formal_parameters: dict[str, Any] = {
        "global.initializer.observability_model": "local-unobservable-initializer-v1",
        "global.initializer.required_safety_facts": [
            "initializer-dead",
            "local-linkage",
            "no-uses",
        ],
        "global.initializer.observed_safety_facts": observed,
        "global.initializer.missing_safety_facts": missing,
        "global.initializer.safety_status": "complete" if complete else "incomplete",
        "global.initializer.safety_provenance": safety_provenance,
        "global.initializer.safety_provenance_status": "complete" if complete else "incomplete",
        "global.initializer.rewrite_api": "setInitializer",
        "global.initializer.rewrite_callee": "setInitializer",
        "global.initializer.replacement_kind": "default-null-initializer",
        "global.initializer.required_witness_cases": ["i32", "ptr", "array"],
        "global.initializer.replacement_expr": "Constant::getNullValue(GV->getValueType())",
        "global.initializer.value_type_expr": "GV->getValueType()",
        "global.initializer.subject": "GV",
        "global.initializer.rewrite_provenance_status": "complete",
    }
    if complete:
        formal_parameters.update({
            "global.initializer.contract": "remove-global-initializer-if-dead-v1",
            "global.initializer.safety_facts": [
                "initializer-dead",
                "local-linkage",
                "no-uses",
            ],
        })
    else:
        formal_parameters.update({
            "semantic.unsupported": True,
            "semantic.unsupported_reason": "missing-global-initializer-safety-facts",
        })
    return {
        "marker": GLOBAL_INITIALIZER_MARKER,
        "file": "GlobalOpt.cpp",
        "line": 321,
        "proof_status": "proved" if complete else "unsupported",
        "proof_result": "unsat" if complete else "unsupported-rewrite",
        "promotion_status": "ready" if complete else "blocked",
        "confidence": "high",
        "intent_candidate": {
            "marker": GLOBAL_INITIALIZER_MARKER,
            "intent": "global-initializer-observable-equivalence",
            "precondition": "local global initializer is dead and has no uses",
            "rewrite": "replace the initializer with a default null initializer",
        },
        "evidence": {
            "formal_inference": "source-derived-intent-graph" if complete else "unset",
            "transaction_lowering": "formal-ir",
            "optimization_transaction": {
                "model": "optimization-transaction-v1",
                "kind": "global-dead-initializer",
                "opcode": "setInitializer",
                "lanes": 0,
                "consistency": "ok",
                "consistency_errors": [],
            },
            "formal_parameters": formal_parameters,
        },
    }


def reduction_validated_record(
    opcode: str = "add",
    consistency_errors: list[str] | None = None,
    lanes: int = 4,
    width_status: str = "",
) -> dict[str, Any]:
    evidence = reduction_transaction_evidence(opcode, consistency_errors, lanes, width_status)
    is_ok = evidence["optimization_transaction"]["consistency"] == "ok"
    return {
        "marker": REDUCTION_MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 5678,
        "proof_status": "proved" if is_ok else "unsupported",
        "proof_result": "unsat" if is_ok else "unsupported-formal-ir",
        "promotion_status": "ready" if is_ok else "blocked",
        "confidence": "high",
        "intent_candidate": {
            "marker": REDUCTION_MARKER,
            "intent": "result-equivalence",
            "precondition": "legal scalar reduction lanes",
            "rewrite": "vector reduction with lane mapping",
        },
        "evidence": evidence,
    }


def masked_memory_validated_record(
    reason: str = "",
    scalable_mask_tuple: bool = False,
    detail: str = "",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "transaction.kind": "slp-vectorize-binop",
        "transaction.opcode": "add",
        "transaction.lanes": 4,
        "transaction.consistency": "failed" if reason else "ok",
        "transaction.consistency_errors": [reason] if reason else [],
        "transaction.lane_mapping.map": [0, 1, 2, 3],
        "transaction.result_lane_mapping.map": [0, 1, 2, 3],
        "transaction.scalar_lane_pairs": [],
    }
    graph: dict[str, Any] | None = None
    absent_reasons: list[str] = []
    if reason:
        absent_reasons = [reason]
    else:
        params["transaction.graph.masked_memory"] = True
        params["transaction.graph.memory_contract"] = "masked-contiguous-load-pack-v1"
        params["transaction.graph.store_contract"] = "masked-contiguous-store-pack-v1"
        if scalable_mask_tuple:
            params["transaction.graph.scalable_mask_tuple"] = True
        graph = {
            "model": "optimization-transaction-graph-v1",
            "kind": "slp-binop-chain",
            "consistency": "ok",
            "operands": [
                {
                    "kind": "memory-pack",
                    "name": "a",
                    "masked": True,
                    "memory_contract": "masked-contiguous-load-pack-v1",
                }
            ],
            "store_sinks": [
                {
                    "masked": True,
                    "store_contract": "masked-contiguous-store-pack-v1",
                }
            ],
            "nodes": [],
            "edges": [],
        }
    transaction = {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "consistency": "failed" if reason else "ok",
        "consistency_errors": [reason] if reason else [],
        "scalar_lane_pairs": [],
    }
    if graph is not None:
        transaction["transaction_graph"] = graph
    if absent_reasons:
        transaction["transaction_graph_absent_reasons"] = absent_reasons
        if detail:
            transaction["transaction_graph_absent_diagnostics"] = [
                {
                    "reason": reason,
                    "role": "memory-pack",
                    "source": "masked memory fixture",
                    "detail": detail,
                }
            ]
    return {
        "marker": MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 6400,
        "proof_status": "unsupported" if reason else "proved",
        "proof_result": "unsupported-formal-ir" if reason else "unsat",
        "promotion_status": "blocked" if reason else "ready",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "vector-result-equivalence",
            "precondition": "masked memory transaction",
            "rewrite": "masked vector memory",
        },
        "evidence": {
            "formal_inference": "source-derived-transaction",
            "transaction_lowering": "fallback" if reason else "formal-ir",
            "optimization_transaction": transaction,
            "formal_parameters": params,
        },
    }


def memory_address_validated_record(reason: str, detail: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {
        "transaction.kind": "slp-vectorize-binop",
        "transaction.opcode": "add",
        "transaction.lanes": 4,
        "transaction.consistency": "failed",
        "transaction.consistency_errors": [reason],
        "transaction.lane_mapping.map": [0, 1, 2, 3],
        "transaction.result_lane_mapping.map": [0, 1, 2, 3],
        "transaction.scalar_lane_pairs": [],
    }
    diagnostic = {
        "reason": reason,
        "role": "memory-store" if "store" in reason or "scatter" in reason else "memory-pack",
        "source": "memory address fixture",
    }
    if detail:
        diagnostic["detail"] = detail
    transaction = {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "consistency": "failed",
        "consistency_errors": [reason],
        "scalar_lane_pairs": [],
        "transaction_graph_absent_reasons": [reason],
        "transaction_graph_absent_diagnostics": [diagnostic],
    }
    return {
        "marker": MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 6410,
        "proof_status": "unsupported",
        "proof_result": "unsupported-formal-ir",
        "promotion_status": "blocked",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "vector-result-equivalence",
            "precondition": "memory address transaction",
            "rewrite": "vector operation with memory address provenance",
        },
        "evidence": {
            "formal_inference": "source-derived-transaction",
            "transaction_lowering": "fallback",
            "optimization_transaction": transaction,
            "formal_parameters": params,
        },
    }


def scalable_memory_pack_validated_record(contract: str = "static-gather-pack-v1") -> dict[str, Any]:
    address_order = [0, 2, 4, 6] if contract == "static-gather-pack-v1" else [0, 1, 2, 3]
    params: dict[str, Any] = {
        "transaction.kind": "slp-vectorize-binop",
        "transaction.opcode": "add",
        "transaction.lanes": 4,
        "transaction.consistency": "ok",
        "transaction.consistency_errors": [],
        "transaction.scalable": True,
        "transaction.base_lanes": 4,
        "transaction.graph.scalable_memory_pack": True,
        "transaction.graph.memory_contract": contract,
        "transaction.lane_mapping.map": [0, 1, 2, 3],
        "transaction.result_lane_mapping.map": [0, 1, 2, 3],
        "transaction.scalar_lane_pairs": [],
    }
    graph = {
        "model": "optimization-transaction-graph-v1",
        "kind": "slp-binop-chain",
        "consistency": "ok",
        "operands": [
            {
                "kind": "memory-pack",
                "name": "a",
                "memory_contract": contract,
                "address_order": address_order,
                "load_order": address_order,
                "memory_safety_status": "complete",
            }
        ],
        "nodes": [{"id": "n0", "kind": "binop", "opcode": "add"}],
        "edges": [],
    }
    transaction = {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "scalable": True,
        "base_lanes": 4,
        "consistency": "ok",
        "consistency_errors": [],
        "transaction_graph": graph,
        "scalar_lane_pairs": [],
    }
    return {
        "marker": MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 6420,
        "proof_status": "proved",
        "proof_result": "unsat",
        "promotion_status": "ready",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "vector-result-equivalence",
            "precondition": "scalable memory-pack transaction",
            "rewrite": "scalable vector operation with memory-pack provenance",
        },
        "evidence": {
            "formal_inference": "source-derived-transaction",
            "transaction_lowering": "formal-ir",
            "optimization_transaction": transaction,
            "formal_parameters": params,
        },
    }


def helper_slice_diagnostic(reason: str) -> dict[str, Any]:
    helper_by_reason = {
        "unsupported-recursive-helper-slice": "recursiveMask",
        "unsupported-unresolved-helper-slice": "missingMaskBody",
        "unsupported-multiple-return-helper-slice": "multiReturnMask",
        "unsupported-incomplete-helper-arguments": "defaultedMask",
        "unsupported-helper-expansion-depth": "depthMask5",
    }
    helper = helper_by_reason[reason]
    return {
        "reason": reason,
        "helper": helper,
        "role": "memory-pack",
        "source": f"Value *M0 = {helper}(...)",
        "expansion_stack": ["loadHelperMemory", helper],
        "depth": 1,
    }


def helper_slice_validated_record(reason: str) -> dict[str, Any]:
    diagnostic = helper_slice_diagnostic(reason)
    params: dict[str, Any] = {
        "transaction.kind": "slp-vectorize-binop",
        "transaction.opcode": "add",
        "transaction.lanes": 4,
        "transaction.consistency": "failed",
        "transaction.consistency_errors": [],
        "transaction.graph.absent_reasons": [reason],
        "transaction.lane_mapping.map": [0, 1, 2, 3],
        "transaction.result_lane_mapping.map": [0, 1, 2, 3],
        "transaction.scalar_lane_pairs": [],
    }
    transaction = {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "consistency": "failed",
        "consistency_errors": [],
        "transaction_graph_absent_reasons": [reason],
        "transaction_graph_absent_diagnostics": [diagnostic],
        "scalar_lane_pairs": [],
    }
    return {
        "marker": MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 6450,
        "proof_status": "unsupported",
        "proof_result": "unsupported-formal-ir",
        "promotion_status": "blocked",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "vector-result-equivalence",
            "precondition": "helper slice transaction",
            "rewrite": "vector helper slice",
        },
        "evidence": {
            "formal_inference": "source-derived-transaction",
            "transaction_lowering": "fallback",
            "optimization_transaction": transaction,
            "formal_parameters": params,
        },
    }


def relaxed_fp_policy_validated_record(scalable: bool = False) -> dict[str, Any]:
    lane_mapping = {"map": [2, 0, 3, 1], "inverse_map": [1, 3, 0, 2], "kind": "permutation"}
    policy = {
        "kind": "fp-reduction-reassociation",
        "semantics": "relaxed-reassoc",
        "operation": "fadd",
        "element_type": "fp32",
        "lanes": 4,
        "lane_mapping": lane_mapping,
        "evidence": [{"line": 41, "source": "FMF.setAllowReassoc();"}],
    }
    if scalable:
        policy["scalable"] = True
        policy["base_lanes"] = 4
        policy["vscale_values"] = [1, 2, 4]
    params = {
        "transaction.kind": "slp-vectorize-reduction",
        "transaction.opcode": "fadd",
        "transaction.reduction_opcode": "fadd",
        "transaction.lanes": 4,
        "transaction.reduction_lanes": 4,
        "transaction.consistency": "ok",
        "transaction.consistency_errors": [],
        "transaction.unsupported_reduction_reasons": [],
        "transaction.lane_mapping": lane_mapping,
        "transaction.lane_mapping.map": lane_mapping["map"],
        "transaction.fp_policy": policy,
        "transaction.reduction_sources": [{"line": 42, "source": "CreateFAddReduce(LHS)"}],
        "transaction.reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
    }
    if scalable:
        params["transaction.scalable"] = True
        params["transaction.base_lanes"] = 4
    transaction = {
        "model": "optimization-transaction-v1",
        "kind": "slp-vectorize-reduction",
        "opcode": "fadd",
        "reduction_opcode": "fadd",
        "lanes": 4,
        "reduction_lanes": 4,
        "consistency": "ok",
        "consistency_errors": [],
        "unsupported_reduction_reasons": [],
        "lane_mapping": lane_mapping,
        "fp_policy": policy,
        "reduction_sources": [{"line": 42, "source": "CreateFAddReduce(LHS)"}],
        "reduction_result": {"kind": "scalar-reduction-result", "source": "Reduced"},
    }
    if scalable:
        transaction["scalable"] = True
        transaction["base_lanes"] = 4
    return {
        "marker": REDUCTION_MARKER,
        "file": "SLPVectorizer.cpp",
        "line": 5800 if scalable else 5700,
        "proof_status": "proved",
        "proof_result": "policy-contract",
        "promotion_status": "ready",
        "confidence": "high",
        "intent_candidate": {
            "marker": REDUCTION_MARKER,
            "intent": "result-equivalence",
            "precondition": "legal reassociated scalar reduction lanes",
            "rewrite": "relaxed FP vector reduction with lane mapping",
            "relaxed_fp_policy": policy,
        },
        "evidence": {
            "formal_inference": "source-derived-transaction-policy",
            "transaction_lowering": "relaxed-fp-policy",
            "optimization_transaction": transaction,
            "formal_parameters": params,
        },
    }


def minmax_validated_record() -> dict[str, Any]:
    record = validated_record()
    record["evidence"] = transaction_evidence()
    record["evidence"]["optimization_transaction"]["kind"] = "slp-vectorize-minmax"
    record["evidence"]["optimization_transaction"]["opcode"] = "smin"
    record["evidence"]["optimization_transaction"]["predicate"] = "slt"
    params = record["evidence"]["formal_parameters"]
    params["transaction.kind"] = "slp-vectorize-minmax"
    params["transaction.opcode"] = "smin"
    params["transaction.predicate"] = "slt"
    params["transaction.select_order"] = "canonical"
    return record


def replay_case() -> dict[str, Any]:
    return {
        "case": "slp_vectorize_binop",
        "status": "passed",
        "semantic_status": "matched",
        "oracle_status": "matched",
        "alive2_status": "proved",
        "expected_markers": MARKER,
        "observed_markers": MARKER,
    }


def reduction_replay_case() -> dict[str, Any]:
    return {
        "case": "slp_vectorize_reduction",
        "status": "passed",
        "semantic_status": "matched",
        "oracle_status": "matched",
        "alive2_status": "proved",
        "expected_markers": REDUCTION_MARKER,
        "observed_markers": REDUCTION_MARKER,
    }


def check_transaction_fields(record: dict[str, Any], status: str) -> None:
    assert record["evidence_status"] == status
    assert record["transaction_lowering"] == "formal-ir"
    assert record["transaction_kind"] == "slp-vectorize-binop"
    assert record["transaction_opcode"] == "add"
    assert record["transaction_lanes"] == 4
    assert record["transaction_consistency"] == "ok"
    assert record["transaction_has_lane_mapping"] is True
    assert record["transaction_has_result_lane_mapping"] is True
    assert record["transaction_scalar_lane_pairs"] == 4
    assert record["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]


def check_contract_fields(record: dict[str, Any], status: str, contract_status: str) -> None:
    assert record["evidence_status"] == status
    assert record["source_slice_contract_status"] == contract_status
    assert record["optimization_transaction"]["source_slice_contract_status"] == contract_status
    if contract_status == "complete":
        assert record["source_slice_contract_missing_roles"] == []
    else:
        assert record["source_slice_contract_missing_roles"] == ["legality"]
        assert record["optimization_transaction"]["source_slice_contract_missing_roles"] == ["legality"]
    checks = record["source_slice_contract_checks"]
    assert record["optimization_transaction"]["source_slice_contract_checks"] == checks
    assert any(check["id"] == "predicate-expands-legality" for check in checks)
    if contract_status == "failed":
        assert any(check["status"] == "failed" for check in checks)


def check_reduction_fields(record: dict[str, Any], status: str) -> None:
    assert record["evidence_status"] == status
    assert record["transaction_lowering"] == "formal-ir"
    assert record["transaction_kind"] == "slp-vectorize-reduction"
    assert record["transaction_opcode"] == "add"
    assert record["transaction_reduction_opcode"] == "add"
    assert record["transaction_lanes"] == 4
    assert record["transaction_reduction_lanes"] == 4
    assert record["transaction_consistency"] == "ok"
    assert record["transaction_has_lane_mapping"] is True
    assert record["transaction_reduction_sources"] == 1
    assert record["transaction_has_reduction_result"] is True
    assert record["optimization_transaction"]["reduction_opcode"] == "add"
    assert record["optimization_transaction"]["reduction_sources"] == 1
    assert record["optimization_transaction"]["has_reduction_result"] is True


def evidence_mode(repo: Path, work_dir: Path) -> None:
    validated = work_dir / "validated.jsonl"
    manifest = work_dir / "manifest.jsonl"
    out = work_dir / "evidence.jsonl"
    report = work_dir / "report.txt"
    write_jsonl(validated, [validated_record(), reduction_validated_record()])
    write_jsonl(manifest, [replay_case(), reduction_replay_case()])
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(validated),
        "--opt-manifest",
        str(manifest),
        "--out",
        str(out),
        "--report",
        str(report),
        "--require-clean",
    ])
    evidence_records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    by_marker = {record["marker"]: record for record in evidence_records}
    check_transaction_fields(by_marker[MARKER], "verified")
    check_reduction_fields(by_marker[REDUCTION_MARKER], "verified")
    report_text = report.read_text(encoding="utf-8")
    assert "Optimization transactions" in report_text
    assert "formal-ir: 2" in report_text
    assert "ok: 2" in report_text

    helper = work_dir / "helper-diagnostics"
    helper_reason = "unsupported-unresolved-helper-slice"
    write_jsonl(helper / "validated.jsonl", [helper_slice_validated_record(helper_reason)])
    write_jsonl(helper / "manifest.jsonl", [])
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(helper / "validated.jsonl"),
        "--opt-manifest",
        str(helper / "manifest.jsonl"),
        "--out",
        str(helper / "evidence.jsonl"),
        "--report",
        str(helper / "report.txt"),
    ])
    helper_evidence = json.loads((helper / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert helper_evidence["evidence_status"] == "unsupported"
    assert helper_evidence["transaction_graph_absent_reasons"] == [helper_reason]
    assert helper_evidence["transaction_graph_absent_diagnostics"][0]["helper"] == "missingMaskBody"
    assert helper_evidence["optimization_transaction"]["transaction_graph_absent_diagnostics"][0]["role"] == "memory-pack"
    helper_report = (helper / "report.txt").read_text(encoding="utf-8")
    assert "Helper slice diagnostics" in helper_report
    assert "reasons: unsupported-unresolved-helper-slice=1" in helper_report
    assert "helpers: missingMaskBody=1" in helper_report
    assert "roles: memory-pack=1" in helper_report

    uncovered = work_dir / "uncovered"
    write_jsonl(uncovered / "validated.jsonl", [validated_record(), reduction_validated_record()])
    write_jsonl(uncovered / "manifest.jsonl", [])
    result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(uncovered / "validated.jsonl"),
        "--opt-manifest",
        str(uncovered / "manifest.jsonl"),
        "--out",
        str(uncovered / "evidence.jsonl"),
        "--report",
        str(uncovered / "report.txt"),
        "--require-clean",
    ], expect=1)
    assert "intent evidence issues: 2" in result.stderr
    uncovered_records = [
        json.loads(line) for line in (uncovered / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    uncovered_by_marker = {record["marker"]: record for record in uncovered_records}
    check_transaction_fields(
        uncovered_by_marker[MARKER],
        "uncovered",
    )
    check_reduction_fields(uncovered_by_marker[REDUCTION_MARKER], "uncovered")

    minmax = work_dir / "minmax"
    write_jsonl(minmax / "validated.jsonl", [minmax_validated_record()])
    write_jsonl(minmax / "manifest.jsonl", [replay_case()])
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(minmax / "validated.jsonl"),
        "--opt-manifest",
        str(minmax / "manifest.jsonl"),
        "--out",
        str(minmax / "evidence.jsonl"),
        "--report",
        str(minmax / "report.txt"),
        "--require-clean",
    ])
    minmax_record = json.loads((minmax / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert minmax_record["evidence_status"] == "verified"
    assert minmax_record["transaction_kind"] == "slp-vectorize-minmax"
    assert minmax_record["transaction_opcode"] == "smin"

    complete_contract = work_dir / "complete-contract"
    complete_record = validated_record()
    complete_record["evidence"] = transaction_evidence(contract_status="complete")
    write_jsonl(complete_contract / "validated.jsonl", [complete_record])
    write_jsonl(complete_contract / "manifest.jsonl", [replay_case()])
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(complete_contract / "validated.jsonl"),
        "--opt-manifest",
        str(complete_contract / "manifest.jsonl"),
        "--out",
        str(complete_contract / "evidence.jsonl"),
        "--report",
        str(complete_contract / "report.txt"),
        "--require-clean",
    ])
    complete_evidence = json.loads((complete_contract / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    check_contract_fields(complete_evidence, "verified", "complete")

    failed_contract = work_dir / "failed-contract"
    failed_record = validated_record()
    failed_record["evidence"] = transaction_evidence(contract_status="failed")
    write_jsonl(failed_contract / "validated.jsonl", [failed_record])
    write_jsonl(failed_contract / "manifest.jsonl", [replay_case()])
    failed_result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(failed_contract / "validated.jsonl"),
        "--opt-manifest",
        str(failed_contract / "manifest.jsonl"),
        "--out",
        str(failed_contract / "evidence.jsonl"),
        "--report",
        str(failed_contract / "report.txt"),
        "--require-clean",
    ], expect=1)
    assert "intent evidence issues: 1" in failed_result.stderr
    failed_evidence = json.loads((failed_contract / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    check_contract_fields(failed_evidence, "blocked", "failed")
    failed_report = (failed_contract / "report.txt").read_text(encoding="utf-8")
    assert "Source-slice contract status" in failed_report
    assert "failed: 1" in failed_report
    assert "Contract-blocked evidence: 1" in failed_report
    assert "Source-slice contract failed checks" in failed_report
    assert "predicate-expands-legality: 1" in failed_report

    verifier_passed = work_dir / "verifier-passed"
    verifier_passed_record = validated_record()
    verifier_passed_record["evidence"] = transaction_evidence(contract_status="complete")
    write_jsonl(verifier_passed / "validated.jsonl", [verifier_passed_record])
    write_jsonl(verifier_passed / "manifest.jsonl", [replay_case()])
    write_json(verifier_passed / "contract-verification.json", contract_verification(MARKER, "passed"))
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(verifier_passed / "validated.jsonl"),
        "--opt-manifest",
        str(verifier_passed / "manifest.jsonl"),
        "--source-slice-contract-verification",
        str(verifier_passed / "contract-verification.json"),
        "--out",
        str(verifier_passed / "evidence.jsonl"),
        "--report",
        str(verifier_passed / "report.txt"),
        "--require-clean",
    ])
    verifier_passed_evidence = json.loads((verifier_passed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert verifier_passed_evidence["evidence_status"] == "verified"
    assert verifier_passed_evidence["source_slice_contract_verification_status"] == "passed"

    verifier_failed = work_dir / "verifier-failed"
    verifier_failed_record = validated_record()
    verifier_failed_record["evidence"] = transaction_evidence(contract_status="complete")
    write_jsonl(verifier_failed / "validated.jsonl", [verifier_failed_record])
    write_jsonl(verifier_failed / "manifest.jsonl", [replay_case()])
    write_json(verifier_failed / "contract-verification.json", contract_verification(MARKER, "failed"))
    verifier_failed_result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(verifier_failed / "validated.jsonl"),
        "--opt-manifest",
        str(verifier_failed / "manifest.jsonl"),
        "--source-slice-contract-verification",
        str(verifier_failed / "contract-verification.json"),
        "--out",
        str(verifier_failed / "evidence.jsonl"),
        "--report",
        str(verifier_failed / "report.txt"),
        "--require-clean",
    ], expect=1)
    assert "intent evidence issues: 1" in verifier_failed_result.stderr
    verifier_failed_evidence = json.loads((verifier_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert verifier_failed_evidence["evidence_status"] == "blocked"
    assert verifier_failed_evidence["source_slice_contract_verification_status"] == "failed"
    verifier_failed_report = (verifier_failed / "report.txt").read_text(encoding="utf-8")
    assert "Source-slice contract verification" in verifier_failed_report
    assert "failed: 1" in verifier_failed_report
    assert "Contract-verifier-blocked evidence: 1" in verifier_failed_report
    assert "status-mismatch: 1" in verifier_failed_report

    formalization_passed = work_dir / "formalization-passed"
    formalization_passed_record = validated_record()
    formalization_passed_record["evidence"] = transaction_evidence(contract_status="complete")
    write_jsonl(formalization_passed / "validated.jsonl", [formalization_passed_record])
    write_jsonl(formalization_passed / "manifest.jsonl", [replay_case()])
    write_json(formalization_passed / "formalization-verification.json", formalization_verification(MARKER, "passed"))
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(formalization_passed / "validated.jsonl"),
        "--opt-manifest",
        str(formalization_passed / "manifest.jsonl"),
        "--transaction-formalization-verification",
        str(formalization_passed / "formalization-verification.json"),
        "--out",
        str(formalization_passed / "evidence.jsonl"),
        "--report",
        str(formalization_passed / "report.txt"),
        "--require-clean",
    ])
    formalization_passed_evidence = json.loads((formalization_passed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert formalization_passed_evidence["evidence_status"] == "verified"
    assert formalization_passed_evidence["transaction_formalization_verification_status"] == "passed"
    assert formalization_passed_evidence["transaction_formal_provenance_coverage_status"] == "passed"
    assert formalization_passed_evidence["transaction_formal_provenance_roles"] == {"domain": 1, "opcode": 1}

    formalization_failed = work_dir / "formalization-failed"
    formalization_failed_record = validated_record()
    formalization_failed_record["evidence"] = transaction_evidence(contract_status="complete")
    write_jsonl(formalization_failed / "validated.jsonl", [formalization_failed_record])
    write_jsonl(formalization_failed / "manifest.jsonl", [replay_case()])
    write_json(formalization_failed / "formalization-verification.json", formalization_verification(MARKER, "failed"))
    formalization_failed_result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(formalization_failed / "validated.jsonl"),
        "--opt-manifest",
        str(formalization_failed / "manifest.jsonl"),
        "--transaction-formalization-verification",
        str(formalization_failed / "formalization-verification.json"),
        "--out",
        str(formalization_failed / "evidence.jsonl"),
        "--report",
        str(formalization_failed / "report.txt"),
        "--require-clean",
    ], expect=1)
    assert "intent evidence issues: 1" in formalization_failed_result.stderr
    formalization_failed_evidence = json.loads((formalization_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert formalization_failed_evidence["evidence_status"] == "blocked"
    assert formalization_failed_evidence["transaction_formalization_verification_status"] == "failed"
    assert formalization_failed_evidence["transaction_formal_provenance_coverage_status"] == "incomplete"
    assert formalization_failed_evidence["transaction_formal_provenance_missing_paths"] == ["after.op"]
    formalization_failed_report = (formalization_failed / "report.txt").read_text(encoding="utf-8")
    assert "Transaction formalization verification" in formalization_failed_report
    assert "failed: 1" in formalization_failed_report
    assert "Transaction-formalization-blocked evidence: 1" in formalization_failed_report
    assert "after-mismatch: 1" in formalization_failed_report
    assert "Transaction formal provenance coverage" in formalization_failed_report
    assert "incomplete: 1" in formalization_failed_report
    assert "after.op: 1" in formalization_failed_report

    slp_provenance_failed = work_dir / "slp-predicate-provenance-failed"
    write_jsonl(slp_provenance_failed / "validated.jsonl", [validated_record()])
    write_jsonl(slp_provenance_failed / "manifest.jsonl", [replay_case()])
    predicate_provenance_verification_file(
        slp_provenance_failed / "predicate-provenance-verification-passed.json",
        [
            predicate_provenance_verification(
                "passed",
                MARKER,
                "SLPVectorizer.cpp|1234|probe.slp.vectorize-binop",
                "legality-provenance-missing",
            )
        ],
    )
    predicate_provenance_verification_file(
        slp_provenance_failed / "predicate-provenance-verification-failed.json",
        [
            predicate_provenance_verification(
                "failed",
                MARKER,
                "SLPVectorizer.cpp|1234|probe.slp.vectorize-binop",
                "legality-provenance-missing",
            )
        ],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(slp_provenance_failed / "validated.jsonl"),
        "--opt-manifest",
        str(slp_provenance_failed / "manifest.jsonl"),
        "--predicate-provenance-verification",
        str(slp_provenance_failed / "predicate-provenance-verification-passed.json"),
        "--predicate-provenance-verification",
        str(slp_provenance_failed / "predicate-provenance-verification-failed.json"),
        "--out",
        str(slp_provenance_failed / "evidence.jsonl"),
        "--report",
        str(slp_provenance_failed / "report.txt"),
    ])
    slp_provenance_failed_evidence = json.loads(
        (slp_provenance_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert slp_provenance_failed_evidence["evidence_status"] == "blocked"
    assert slp_provenance_failed_evidence["predicate_provenance_verification_status"] == "failed"
    assert slp_provenance_failed_evidence["predicate_provenance_failed_checks"] == ["legality-provenance-missing"]
    slp_provenance_failed_report = (slp_provenance_failed / "report.txt").read_text(encoding="utf-8")
    assert "Predicate provenance" in slp_provenance_failed_report
    assert "  records: 1" in slp_provenance_failed_report
    assert "  checked: 1" in slp_provenance_failed_report
    assert "  passed: 0" in slp_provenance_failed_report
    assert "  failed: 1" in slp_provenance_failed_report
    assert "  absent: 0" in slp_provenance_failed_report
    assert "verification_status: failed=1" in slp_provenance_failed_report
    assert "failed_checks: legality-provenance-missing=1" in slp_provenance_failed_report

    globalopt_passed = work_dir / "globalopt-witness-passed"
    write_jsonl(globalopt_passed / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_passed / "manifest.jsonl", [])
    write_json(globalopt_passed / "globalopt-coverage.json", globalopt_coverage(globalopt_passed, "passed"))
    witness_contract_verification_file(
        globalopt_passed / "globalopt-witness-contract-verification.json",
        [witness_contract_verification()],
    )
    predicate_provenance_verification_file(
        globalopt_passed / "predicate-provenance-verification.json",
        [predicate_provenance_verification()],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_passed / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_passed / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_passed / "globalopt-coverage.json"),
        "--globalopt-witness-contract-verification",
        str(globalopt_passed / "globalopt-witness-contract-verification.json"),
        "--predicate-provenance-verification",
        str(globalopt_passed / "predicate-provenance-verification.json"),
        "--out",
        str(globalopt_passed / "evidence.jsonl"),
        "--report",
        str(globalopt_passed / "report.txt"),
        "--require-clean",
    ])
    globalopt_passed_evidence = json.loads(
        (globalopt_passed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert globalopt_passed_evidence["evidence_status"] == "verified"
    assert globalopt_passed_evidence["globalopt_witness_status"] == "passed"
    assert globalopt_passed_evidence["globalopt_witness_before"].endswith("before.ll")
    assert globalopt_passed_evidence["globalopt_witness_model"] == "global-initializer-default-null-family-v1"
    assert globalopt_passed_evidence["globalopt_required_witness_cases"] == ["i32", "ptr", "array"]
    assert "globalopt_missing_required_witness_cases" not in globalopt_passed_evidence
    assert globalopt_passed_evidence["globalopt_witness_structural_status"] == "passed"
    assert globalopt_passed_evidence["globalopt_witness_contract"]["model"] == "globalopt-dead-initializer-witness-contract-v1"
    assert globalopt_passed_evidence["globalopt_witness_contract"]["status"] == "passed"
    assert globalopt_passed_evidence["globalopt_witness_contract"]["structural_status"] == "passed"
    assert globalopt_passed_evidence["globalopt_witness_contract"]["required_cases"] == ["i32", "ptr", "array"]
    assert globalopt_passed_evidence["globalopt_safety_provenance_status"] == "passed"
    assert len(globalopt_passed_evidence["globalopt_safety_provenance"]) == 3
    assert globalopt_passed_evidence["predicate_provenance_verification_status"] == "passed"
    assert globalopt_passed_evidence["globalopt_witness_contract_verification_status"] == "passed"
    assert globalopt_passed_evidence["globalopt_witness_contract_formal_status"] == {"proved": 3}
    assert globalopt_passed_evidence["globalopt_witness_contract_semantic_status"] == {"proved": 3}
    assert len(globalopt_passed_evidence["globalopt_witness_contract_formal_obligations"]) == 3
    assert len(globalopt_passed_evidence["globalopt_witness_contract_semantic_obligations"]) == 3
    assert globalopt_passed_evidence["globalopt_rewrite_provenance_status"] == "complete"
    assert globalopt_passed_evidence["globalopt_rewrite_callee"] == "setInitializer"
    assert globalopt_passed_evidence["globalopt_replacement_expr"] == "Constant::getNullValue(GV->getValueType())"
    assert globalopt_passed_evidence["globalopt_value_type_expr"] == "GV->getValueType()"
    assert globalopt_passed_evidence["globalopt_rewrite_subject"] == "GV"
    assert [case["name"] for case in globalopt_passed_evidence["globalopt_witness_cases"]] == ["i32", "ptr", "array"]
    assert {case["status"] for case in globalopt_passed_evidence["globalopt_witness_cases"]} == {"passed"}
    assert globalopt_passed_evidence["globalopt_witness_cases"][0]["structural_details"]["initializer_type"] == "i32"
    assert globalopt_passed_evidence["globalopt_witness"]["witness_model"] == "global-initializer-default-null-family-v1"
    globalopt_passed_report = (globalopt_passed / "report.txt").read_text(encoding="utf-8")
    assert "GlobalOpt witnesses" in globalopt_passed_report
    assert "passed: 1" in globalopt_passed_report
    assert "GlobalOpt witness cases" in globalopt_passed_report
    assert "i32: passed=1" in globalopt_passed_report
    assert "ptr: passed=1" in globalopt_passed_report
    assert "array: passed=1" in globalopt_passed_report
    assert "GlobalOpt witness structural checks" in globalopt_passed_report
    assert "status: passed=1" in globalopt_passed_report
    assert "i32.changed_lines=1: 1" in globalopt_passed_report
    assert "GlobalOpt witness contract verification" in globalopt_passed_report
    assert "formal_status: proved=3" in globalopt_passed_report
    assert "semantic_status: proved=3" in globalopt_passed_report
    assert "GlobalOpt safety provenance" in globalopt_passed_report
    assert "status: passed=1" in globalopt_passed_report
    assert "Predicate provenance" in globalopt_passed_report
    assert "  records: 1" in globalopt_passed_report
    assert "  checked: 1" in globalopt_passed_report
    assert "  passed: 1" in globalopt_passed_report
    assert "  failed: 0" in globalopt_passed_report
    assert "  absent: 0" in globalopt_passed_report
    assert "verification_status: passed=1" in globalopt_passed_report
    assert "failed_checks: none" in globalopt_passed_report
    assert "GlobalOpt rewrite provenance" in globalopt_passed_report
    assert "status: complete=1" in globalopt_passed_report
    assert "callee: setInitializer=1" in globalopt_passed_report
    assert "replacement_expr: Constant::getNullValue(GV->getValueType())=1" in globalopt_passed_report
    assert "value_type_expr: GV->getValueType()=1" in globalopt_passed_report

    globalopt_missing_case = work_dir / "globalopt-witness-missing-required-case"
    write_jsonl(globalopt_missing_case / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_missing_case / "manifest.jsonl", [])
    missing_case_coverage = globalopt_coverage(globalopt_missing_case, "passed")
    missing_case_record = missing_case_coverage["witnesses"]["records"][0]
    missing_case_record["cases"] = [
        case for case in missing_case_record["cases"] if case["name"] != "ptr"
    ]
    missing_case_record["missing_required_cases"] = ["ptr"]
    write_json(globalopt_missing_case / "globalopt-coverage.json", missing_case_coverage)
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_missing_case / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_missing_case / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_missing_case / "globalopt-coverage.json"),
        "--out",
        str(globalopt_missing_case / "evidence.jsonl"),
        "--report",
        str(globalopt_missing_case / "report.txt"),
    ])
    missing_case_evidence = json.loads(
        (globalopt_missing_case / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert missing_case_evidence["evidence_status"] == "blocked"
    assert missing_case_evidence["globalopt_witness_status"] == "passed"
    assert missing_case_evidence["globalopt_missing_required_witness_cases"] == ["ptr"]

    globalopt_structural_failed = work_dir / "globalopt-witness-structural-failed"
    write_jsonl(globalopt_structural_failed / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_structural_failed / "manifest.jsonl", [])
    structural_failed_coverage = globalopt_coverage(globalopt_structural_failed, "passed")
    structural_failed_record = structural_failed_coverage["witnesses"]["records"][0]
    structural_failed_record["cases"][0]["structural_checks"] = "failed"
    structural_failed_record["cases"][0]["failure_reasons"] = ["i32-global-linkage-changed"]
    structural_failed_record["failure_reasons"] = []
    structural_failed_record["status"] = "passed"
    write_json(globalopt_structural_failed / "globalopt-coverage.json", structural_failed_coverage)
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_structural_failed / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_structural_failed / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_structural_failed / "globalopt-coverage.json"),
        "--out",
        str(globalopt_structural_failed / "evidence.jsonl"),
        "--report",
        str(globalopt_structural_failed / "report.txt"),
    ])
    structural_failed_evidence = json.loads(
        (globalopt_structural_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert structural_failed_evidence["evidence_status"] == "blocked"
    assert structural_failed_evidence["globalopt_witness_status"] == "passed"
    assert structural_failed_evidence["globalopt_witness_structural_status"] == "failed"
    assert structural_failed_evidence["globalopt_witness_contract"]["status"] == "failed"

    globalopt_contract_failed = work_dir / "globalopt-witness-contract-failed"
    write_jsonl(globalopt_contract_failed / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_contract_failed / "manifest.jsonl", [])
    write_json(globalopt_contract_failed / "globalopt-coverage.json", globalopt_coverage(globalopt_contract_failed, "passed"))
    witness_contract_verification_file(
        globalopt_contract_failed / "globalopt-witness-contract-verification.json",
        [witness_contract_verification("failed", "proved")],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_contract_failed / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_contract_failed / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_contract_failed / "globalopt-coverage.json"),
        "--globalopt-witness-contract-verification",
        str(globalopt_contract_failed / "globalopt-witness-contract-verification.json"),
        "--out",
        str(globalopt_contract_failed / "evidence.jsonl"),
        "--report",
        str(globalopt_contract_failed / "report.txt"),
    ])
    contract_failed_evidence = json.loads(
        (globalopt_contract_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert contract_failed_evidence["evidence_status"] == "blocked"
    assert contract_failed_evidence["globalopt_witness_contract_verification_status"] == "failed"

    globalopt_contract_error = work_dir / "globalopt-witness-contract-error"
    write_jsonl(globalopt_contract_error / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_contract_error / "manifest.jsonl", [])
    write_json(globalopt_contract_error / "globalopt-coverage.json", globalopt_coverage(globalopt_contract_error, "passed"))
    witness_contract_verification_file(
        globalopt_contract_error / "globalopt-witness-contract-verification.json",
        [witness_contract_verification("passed", "error")],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_contract_error / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_contract_error / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_contract_error / "globalopt-coverage.json"),
        "--globalopt-witness-contract-verification",
        str(globalopt_contract_error / "globalopt-witness-contract-verification.json"),
        "--out",
        str(globalopt_contract_error / "evidence.jsonl"),
        "--report",
        str(globalopt_contract_error / "report.txt"),
    ])
    contract_error_evidence = json.loads(
        (globalopt_contract_error / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert contract_error_evidence["evidence_status"] == "blocked"
    assert contract_error_evidence["globalopt_witness_contract_formal_status"] == {"error": 3}

    globalopt_contract_semantic_error = work_dir / "globalopt-witness-contract-semantic-error"
    write_jsonl(globalopt_contract_semantic_error / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_contract_semantic_error / "manifest.jsonl", [])
    write_json(
        globalopt_contract_semantic_error / "globalopt-coverage.json",
        globalopt_coverage(globalopt_contract_semantic_error, "passed"),
    )
    semantic_error_verification = witness_contract_verification()
    for obligation in semantic_error_verification["semantic_obligations"]:
        obligation["semantic_status"] = "error"
        obligation["reason"] = "alive2 error"
    semantic_error_verification["semantic_status"] = "error"
    witness_contract_verification_file(
        globalopt_contract_semantic_error / "globalopt-witness-contract-verification.json",
        [semantic_error_verification],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_contract_semantic_error / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_contract_semantic_error / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_contract_semantic_error / "globalopt-coverage.json"),
        "--globalopt-witness-contract-verification",
        str(globalopt_contract_semantic_error / "globalopt-witness-contract-verification.json"),
        "--out",
        str(globalopt_contract_semantic_error / "evidence.jsonl"),
        "--report",
        str(globalopt_contract_semantic_error / "report.txt"),
    ])
    contract_semantic_error_evidence = json.loads(
        (globalopt_contract_semantic_error / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert contract_semantic_error_evidence["evidence_status"] == "blocked"
    assert contract_semantic_error_evidence["globalopt_witness_contract_semantic_status"] == {"error": 3}

    globalopt_provenance_failed = work_dir / "globalopt-predicate-provenance-failed"
    write_jsonl(globalopt_provenance_failed / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_provenance_failed / "manifest.jsonl", [])
    write_json(
        globalopt_provenance_failed / "globalopt-coverage.json",
        globalopt_coverage(globalopt_provenance_failed, "passed"),
    )
    predicate_provenance_verification_file(
        globalopt_provenance_failed / "predicate-provenance-verification.json",
        [predicate_provenance_verification("failed")],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_provenance_failed / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_provenance_failed / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_provenance_failed / "globalopt-coverage.json"),
        "--predicate-provenance-verification",
        str(globalopt_provenance_failed / "predicate-provenance-verification.json"),
        "--out",
        str(globalopt_provenance_failed / "evidence.jsonl"),
        "--report",
        str(globalopt_provenance_failed / "report.txt"),
    ])
    provenance_failed_evidence = json.loads(
        (globalopt_provenance_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert provenance_failed_evidence["evidence_status"] == "blocked"
    assert provenance_failed_evidence["predicate_provenance_verification_status"] == "failed"
    assert provenance_failed_evidence["predicate_provenance_failed_checks"] == ["local-linkage-provenance-missing"]

    globalopt_contract_not_run = work_dir / "globalopt-witness-contract-not-run"
    write_jsonl(globalopt_contract_not_run / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_contract_not_run / "manifest.jsonl", [])
    write_json(globalopt_contract_not_run / "globalopt-coverage.json", globalopt_coverage(globalopt_contract_not_run, "passed"))
    witness_contract_verification_file(
        globalopt_contract_not_run / "globalopt-witness-contract-verification.json",
        [witness_contract_verification("passed", "not-run")],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_contract_not_run / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_contract_not_run / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_contract_not_run / "globalopt-coverage.json"),
        "--globalopt-witness-contract-verification",
        str(globalopt_contract_not_run / "globalopt-witness-contract-verification.json"),
        "--out",
        str(globalopt_contract_not_run / "evidence.jsonl"),
        "--report",
        str(globalopt_contract_not_run / "report.txt"),
    ])
    contract_not_run_evidence = json.loads(
        (globalopt_contract_not_run / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert contract_not_run_evidence["evidence_status"] == "verified"
    assert contract_not_run_evidence["globalopt_witness_contract_formal_status"] == {"not-run": 3}
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_contract_not_run / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_contract_not_run / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_contract_not_run / "globalopt-coverage.json"),
        "--globalopt-witness-contract-verification",
        str(globalopt_contract_not_run / "globalopt-witness-contract-verification.json"),
        "--out",
        str(globalopt_contract_not_run / "strict-evidence.jsonl"),
        "--report",
        str(globalopt_contract_not_run / "strict-report.txt"),
        "--require-globalopt-witnesses",
    ])
    strict_not_run_evidence = json.loads(
        (globalopt_contract_not_run / "strict-evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert strict_not_run_evidence["evidence_status"] == "blocked"

    globalopt_failed = work_dir / "globalopt-witness-failed"
    write_jsonl(globalopt_failed / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_failed / "manifest.jsonl", [])
    write_json(globalopt_failed / "globalopt-coverage.json", globalopt_coverage(globalopt_failed, "failed"))
    globalopt_failed_result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_failed / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_failed / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_failed / "globalopt-coverage.json"),
        "--out",
        str(globalopt_failed / "evidence.jsonl"),
        "--report",
        str(globalopt_failed / "report.txt"),
        "--require-clean",
        "--max-globalopt-witness-failures",
        "0",
    ], expect=1)
    assert "intent evidence issues: 1" in globalopt_failed_result.stderr
    globalopt_failed_evidence = json.loads(
        (globalopt_failed / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert globalopt_failed_evidence["evidence_status"] == "blocked"
    assert globalopt_failed_evidence["globalopt_witness_status"] == "failed"
    assert globalopt_failed_evidence["globalopt_missing_required_witness_cases"] == ["i32", "ptr"]
    assert globalopt_failed_evidence["globalopt_witness_failure_reasons"] == [
        "i32-before-llvm-as-failed: failed",
        "ptr-after-llvm-as-failed: failed",
    ]
    assert [case["status"] for case in globalopt_failed_evidence["globalopt_witness_cases"]] == [
        "failed",
        "failed",
        "passed",
    ]
    globalopt_failed_report = (globalopt_failed / "report.txt").read_text(encoding="utf-8")
    assert "failed: 1" in globalopt_failed_report
    assert "i32-before-llvm-as-failed: 1" in globalopt_failed_report
    assert "ptr-after-llvm-as-failed: 1" in globalopt_failed_report
    assert "i32: failed=1" in globalopt_failed_report
    assert "ptr: failed=1" in globalopt_failed_report
    assert "array: passed=1" in globalopt_failed_report

    globalopt_failed_budget = work_dir / "globalopt-witness-failed-budget"
    write_jsonl(globalopt_failed_budget / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_failed_budget / "manifest.jsonl", [])
    write_json(globalopt_failed_budget / "globalopt-coverage.json", globalopt_coverage(globalopt_failed_budget, "failed"))
    globalopt_failed_budget_result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_failed_budget / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_failed_budget / "manifest.jsonl"),
        "--globalopt-coverage",
        str(globalopt_failed_budget / "globalopt-coverage.json"),
        "--out",
        str(globalopt_failed_budget / "evidence.jsonl"),
        "--max-globalopt-witness-failures",
        "0",
    ], expect=1)
    assert "globalopt witness failures: 1 limit=0" in globalopt_failed_budget_result.stderr

    globalopt_absent = work_dir / "globalopt-witness-absent"
    write_jsonl(globalopt_absent / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_absent / "manifest.jsonl", [])
    run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_absent / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_absent / "manifest.jsonl"),
        "--out",
        str(globalopt_absent / "evidence.jsonl"),
        "--report",
        str(globalopt_absent / "report.txt"),
    ])
    globalopt_absent_evidence = json.loads(
        (globalopt_absent / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert globalopt_absent_evidence["evidence_status"] == "uncovered"
    assert globalopt_absent_evidence["globalopt_witness_status"] == "absent"

    globalopt_required = work_dir / "globalopt-witness-required"
    write_jsonl(globalopt_required / "validated.jsonl", [global_initializer_validated_record()])
    write_jsonl(globalopt_required / "manifest.jsonl", [])
    globalopt_required_result = run([
        sys.executable,
        str(repo / "tools" / "cv-build-intent-evidence.py"),
        "--validated",
        str(globalopt_required / "validated.jsonl"),
        "--opt-manifest",
        str(globalopt_required / "manifest.jsonl"),
        "--out",
        str(globalopt_required / "evidence.jsonl"),
        "--report",
        str(globalopt_required / "report.txt"),
        "--require-globalopt-witnesses",
        "--require-clean",
    ], expect=1)
    assert "intent evidence issues: 1" in globalopt_required_result.stderr
    globalopt_required_evidence = json.loads(
        (globalopt_required / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert globalopt_required_evidence["evidence_status"] == "blocked"
    assert globalopt_required_evidence["globalopt_witness_status"] == "absent"


def promotion_mode(repo: Path, work_dir: Path) -> None:
    validated = work_dir / "validated.jsonl"
    evidence = work_dir / "evidence.jsonl"
    current = work_dir / "current.json"
    proposed = work_dir / "proposed.json"
    report = work_dir / "report.txt"
    write_jsonl(validated, [validated_record(), reduction_validated_record(), global_initializer_validated_record()])
    evidence_record = {
        "marker": MARKER,
        "evidence_status": "verified",
        "replay_cases": 1,
        "replay_status": {"passed": 1},
        "semantic_status": {"matched": 1},
        "oracle_status": {"matched": 1},
        "alive2_status": {"proved": 1},
    }
    evidence_record.update(transaction_evidence(contract_status="complete"))
    evidence_record["optimization_transaction"] = {
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "lowering": "formal-ir",
        "consistency": "ok",
        "lane_mapping": {"map": [2, 0, 3, 1], "inverse_map": [1, 3, 0, 2]},
        "result_lane_mapping": {"map": [2, 0, 3, 1], "inverse_map": [1, 3, 0, 2]},
        "has_lane_mapping": True,
        "has_result_lane_mapping": True,
        "scalar_lane_pairs": 4,
        "source_slice_contract_status": "complete",
        "source_slice_contract_missing_roles": [],
        "source_slice_contract_role_paths": [{"role": "legality", "function": "isTreeLegal", "path": ["vectorizeTree", "isTreeLegal"]}],
        "source_slice_contract_checks": COMPLETE_CONTRACT_CHECKS,
    }
    evidence_record.update({
        "transaction_lowering": "formal-ir",
        "transaction_kind": "slp-vectorize-binop",
        "transaction_opcode": "add",
        "transaction_lanes": 4,
        "transaction_consistency": "ok",
        "transaction_has_lane_mapping": True,
        "transaction_has_result_lane_mapping": True,
        "transaction_scalar_lane_pairs": 4,
        "source_slice_contract_status": "complete",
        "source_slice_contract_missing_roles": [],
        "source_slice_contract_role_paths": [{"role": "legality", "function": "isTreeLegal", "path": ["vectorizeTree", "isTreeLegal"]}],
        "source_slice_contract_checks": COMPLETE_CONTRACT_CHECKS,
        "source_slice_contract_verification_status": "passed",
        "source_slice_contract_verification_mismatches": [],
        "transaction_formalization_verification_status": "passed",
        "transaction_formalization_verification_mismatches": [],
        "transaction_formal_provenance_coverage_status": "passed",
        "transaction_formal_provenance_missing_paths": [],
        "transaction_formal_provenance_roles": {"domain": 1, "opcode": 1},
        "predicate_provenance_verification_status": "passed",
        "predicate_provenance_failed_checks": [],
        "predicate_provenance_verification": predicate_provenance_verification(
            "passed",
            MARKER,
            "SLPVectorizer.cpp|1234|probe.slp.vectorize-binop",
            "legality-provenance-missing",
        ),
    })
    reduction_evidence_record = {
        "marker": REDUCTION_MARKER,
        "evidence_status": "verified",
        "replay_cases": 1,
        "replay_status": {"passed": 1},
        "semantic_status": {"matched": 1},
        "oracle_status": {"matched": 1},
        "alive2_status": {"proved": 1},
    }
    reduction_evidence_record.update(reduction_transaction_evidence())
    reduction_evidence_record["optimization_transaction"] = {
        "kind": "slp-vectorize-reduction",
        "opcode": "add",
        "reduction_opcode": "add",
        "lanes": 4,
        "reduction_lanes": 4,
        "lowering": "formal-ir",
        "consistency": "ok",
        "lane_mapping": {"map": [2, 0, 3, 1], "inverse_map": [1, 3, 0, 2]},
        "has_lane_mapping": True,
        "reduction_sources": 1,
        "has_reduction_result": True,
        "scalar_lane_pairs": 0,
    }
    reduction_evidence_record.update({
        "transaction_lowering": "formal-ir",
        "transaction_kind": "slp-vectorize-reduction",
        "transaction_opcode": "add",
        "transaction_reduction_opcode": "add",
        "transaction_lanes": 4,
        "transaction_reduction_lanes": 4,
        "transaction_consistency": "ok",
        "transaction_has_lane_mapping": True,
        "transaction_reduction_sources": 1,
        "transaction_has_reduction_result": True,
        "transaction_scalar_lane_pairs": 0,
    })
    globalopt_evidence_record = {
        "marker": GLOBAL_INITIALIZER_MARKER,
        "evidence_status": "verified",
        "replay_cases": 0,
        "replay_status": {},
        "semantic_status": {},
        "oracle_status": {},
        "alive2_status": {},
        "globalopt_witness": {
            "status": "passed",
            "before": str(work_dir / "globalopt-before.ll"),
            "after": str(work_dir / "globalopt-after.ll"),
            "witness": str(work_dir / "globalopt-witness.json"),
            "witness_model": "global-initializer-default-null-family-v1",
            "required_cases": ["i32", "ptr", "array"],
            "missing_required_cases": [],
            "source_provenance": {
                "rewrite_callee": "setInitializer",
                "replacement_expr": "Constant::getNullValue(GV->getValueType())",
                "value_type_expr": "GV->getValueType()",
                "subject": "GV",
                "rewrite_provenance_status": "complete",
            },
            "cases": [
                {"name": "i32", "status": "passed", "failure_reasons": []},
                {"name": "ptr", "status": "passed", "failure_reasons": []},
                {"name": "array", "status": "passed", "failure_reasons": []},
            ],
            "failure_reasons": [],
        },
        "globalopt_witness_status": "passed",
        "globalopt_witness_before": str(work_dir / "globalopt-before.ll"),
        "globalopt_witness_after": str(work_dir / "globalopt-after.ll"),
        "globalopt_witness_manifest": str(work_dir / "globalopt-witness.json"),
        "globalopt_witness_model": "global-initializer-default-null-family-v1",
        "globalopt_witness_contract": witness_contract(),
        "globalopt_witness_contract_verification_status": "passed",
        "globalopt_witness_contract_formal_status": {"proved": 3},
        "globalopt_witness_contract_semantic_status": {"proved": 3},
        "globalopt_witness_contract_failed_checks": [],
        "globalopt_witness_contract_semantic_failed_checks": [],
        "globalopt_witness_contract_formal_obligations": witness_contract_verification()["formal_obligations"],
        "globalopt_witness_contract_semantic_obligations": witness_contract_verification()["semantic_obligations"],
        "globalopt_safety_provenance_status": "passed",
        "globalopt_safety_provenance_failed_checks": [],
        "globalopt_safety_provenance": global_initializer_validated_record()["evidence"]["formal_parameters"]["global.initializer.safety_provenance"],
        "predicate_provenance_verification_status": "passed",
        "predicate_provenance_failed_checks": [],
        "predicate_provenance_verification": predicate_provenance_verification(),
        "globalopt_witness_structural_status": "passed",
        "globalopt_required_witness_cases": ["i32", "ptr", "array"],
        "globalopt_missing_required_witness_cases": [],
        "globalopt_rewrite_provenance_status": "complete",
        "globalopt_rewrite_callee": "setInitializer",
        "globalopt_replacement_expr": "Constant::getNullValue(GV->getValueType())",
        "globalopt_value_type_expr": "GV->getValueType()",
        "globalopt_rewrite_subject": "GV",
        "globalopt_witness_cases": [
            {"name": "i32", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("i32"), "failure_reasons": []},
            {"name": "ptr", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("ptr"), "failure_reasons": []},
            {"name": "array", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("array"), "failure_reasons": []},
        ],
        "globalopt_witness_failure_reasons": [],
    }
    globalopt_evidence_record.update(global_initializer_validated_record()["evidence"])
    write_jsonl(evidence, [evidence_record, reduction_evidence_record, globalopt_evidence_record])
    write_json(current, [])
    run([
        sys.executable,
        str(repo / "tools" / "cv-promote-intent-candidates.py"),
        "--validated",
        str(validated),
        "--evidence",
        str(evidence),
        "--current",
        str(current),
        "--out",
        str(proposed),
        "--report",
        str(report),
        "--require-ready",
        "--require-verified-evidence",
    ])
    promoted = json.loads(proposed.read_text(encoding="utf-8"))
    promoted_by_marker = {record["marker"]: record["evidence"] for record in promoted}
    promoted_evidence = promoted_by_marker[MARKER]
    assert promoted_evidence["evidence_status"] == "verified"
    assert promoted_evidence["transaction_consistency"] == "ok"
    assert promoted_evidence["transaction_scalar_lane_pairs"] == 4
    assert promoted_evidence["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert promoted_evidence["source_slice_contract_status"] == "complete"
    assert promoted_evidence["optimization_transaction"]["source_slice_contract_status"] == "complete"
    assert promoted_evidence["source_slice_contract_checks"] == COMPLETE_CONTRACT_CHECKS
    assert promoted_evidence["optimization_transaction"]["source_slice_contract_checks"] == COMPLETE_CONTRACT_CHECKS
    assert promoted_evidence["source_slice_contract_verification_status"] == "passed"
    assert promoted_evidence["transaction_formalization_verification_status"] == "passed"
    assert promoted_evidence["transaction_formal_provenance_coverage_status"] == "passed"
    assert promoted_evidence["predicate_provenance_verification_status"] == "passed"
    promoted_reduction = promoted_by_marker[REDUCTION_MARKER]
    assert promoted_reduction["transaction_reduction_opcode"] == "add"
    assert promoted_reduction["transaction_reduction_lanes"] == 4
    assert promoted_reduction["transaction_reduction_sources"] == 1
    assert promoted_reduction["transaction_has_reduction_result"] is True
    assert promoted_reduction["optimization_transaction"]["reduction_sources"] == 1
    promoted_global = promoted_by_marker[GLOBAL_INITIALIZER_MARKER]
    assert promoted_global["evidence_status"] == "verified"
    assert promoted_global["globalopt_witness_status"] == "passed"
    assert promoted_global["globalopt_witness_before"].endswith("globalopt-before.ll")
    assert promoted_global["globalopt_witness_after"].endswith("globalopt-after.ll")
    assert promoted_global["globalopt_witness_manifest"].endswith("globalopt-witness.json")
    assert promoted_global["globalopt_witness"]["status"] == "passed"
    assert promoted_global["globalopt_witness_model"] == "global-initializer-default-null-family-v1"
    assert promoted_global["globalopt_witness_contract"]["model"] == "globalopt-dead-initializer-witness-contract-v1"
    assert promoted_global["globalopt_witness_contract"]["status"] == "passed"
    assert promoted_global["globalopt_witness_contract_verification_status"] == "passed"
    assert promoted_global["globalopt_witness_contract_formal_status"] == {"proved": 3}
    assert promoted_global["globalopt_witness_contract_semantic_status"] == {"proved": 3}
    assert promoted_global["globalopt_safety_provenance_status"] == "passed"
    assert promoted_global["predicate_provenance_verification_status"] == "passed"
    assert len(promoted_global["globalopt_witness_contract_formal_obligations"]) == 3
    assert len(promoted_global["globalopt_witness_contract_semantic_obligations"]) == 3
    assert promoted_global["globalopt_witness_structural_status"] == "passed"
    assert promoted_global["globalopt_required_witness_cases"] == ["i32", "ptr", "array"]
    assert promoted_global["globalopt_missing_required_witness_cases"] == []
    assert promoted_global["globalopt_rewrite_provenance_status"] == "complete"
    assert promoted_global["globalopt_rewrite_callee"] == "setInitializer"
    assert promoted_global["globalopt_replacement_expr"] == "Constant::getNullValue(GV->getValueType())"
    assert promoted_global["globalopt_value_type_expr"] == "GV->getValueType()"
    assert promoted_global["globalopt_rewrite_subject"] == "GV"
    assert [case["name"] for case in promoted_global["globalopt_witness_cases"]] == ["i32", "ptr", "array"]
    assert promoted_global["globalopt_witness"]["cases"][0]["name"] == "i32"
    assert promoted_global["globalopt_witness_cases"][0]["structural_details"]["initializer_type"] == "i32"
    promotion_report_text = report.read_text(encoding="utf-8")
    assert "Transaction consistency" in promotion_report_text
    assert "GlobalOpt witness status" in promotion_report_text
    assert "passed: 1" in promotion_report_text
    assert "GlobalOpt witness cases" in promotion_report_text
    assert "i32: passed=1" in promotion_report_text
    assert "GlobalOpt witness structural checks" in promotion_report_text
    assert "passed: 1" in promotion_report_text
    assert "i32: passed=1" in promotion_report_text
    assert "GlobalOpt witness contract verification" in promotion_report_text
    assert "formal_status: proved=3" in promotion_report_text
    assert "semantic_status: proved=3" in promotion_report_text
    assert "GlobalOpt safety provenance" in promotion_report_text
    assert "Predicate provenance" in promotion_report_text
    assert "  records: 3" in promotion_report_text
    assert "  checked: 2" in promotion_report_text
    assert "  passed: 2" in promotion_report_text
    assert "  failed: 0" in promotion_report_text
    assert "  absent: 1" in promotion_report_text
    assert "verification_status: absent=1, passed=2" in promotion_report_text
    assert "failed_checks: none" in promotion_report_text
    assert "ptr: passed=1" in promotion_report_text
    assert "array: passed=1" in promotion_report_text
    assert "GlobalOpt rewrite provenance" in promotion_report_text
    assert "status: complete=1" in promotion_report_text
    assert "callee: setInitializer=1" in promotion_report_text
    assert "replacement_expr: Constant::getNullValue(GV->getValueType())=1" in promotion_report_text
    assert "value_type_expr: GV->getValueType()=1" in promotion_report_text
    assert "globalopt_witness=passed" in promotion_report_text

    contract_blocked = work_dir / "contract-blocked"
    write_jsonl(contract_blocked / "validated.jsonl", [validated_record(), reduction_validated_record(), global_initializer_validated_record()])
    blocked_reduction_evidence = dict(reduction_evidence_record)
    blocked_reduction_evidence["evidence_status"] = "blocked"
    blocked_reduction_evidence["source_slice_contract_status"] = "failed"
    blocked_reduction_evidence["source_slice_contract_missing_roles"] = ["legality"]
    blocked_reduction_evidence["source_slice_contract_checks"] = FAILED_CONTRACT_CHECKS
    blocked_reduction_evidence["source_slice_contract_verification_status"] = "failed"
    blocked_reduction_evidence["source_slice_contract_verification_mismatches"] = [
        {"id": "contract-status", "kind": "status-mismatch"}
    ]
    blocked_reduction_evidence["transaction_formalization_verification_status"] = "failed"
    blocked_reduction_evidence["transaction_formalization_verification_mismatches"] = [
        {"kind": "after-mismatch"}
    ]
    blocked_reduction_evidence["transaction_formal_provenance_coverage_status"] = "incomplete"
    blocked_reduction_evidence["optimization_transaction"] = dict(blocked_reduction_evidence["optimization_transaction"])
    blocked_reduction_evidence["optimization_transaction"]["source_slice_contract_status"] = "failed"
    blocked_reduction_evidence["optimization_transaction"]["source_slice_contract_missing_roles"] = ["legality"]
    blocked_reduction_evidence["optimization_transaction"]["source_slice_contract_checks"] = FAILED_CONTRACT_CHECKS
    blocked_globalopt_evidence = dict(globalopt_evidence_record)
    blocked_globalopt_evidence["evidence_status"] = "blocked"
    blocked_globalopt_evidence["globalopt_witness_status"] = "failed"
    blocked_globalopt_evidence["globalopt_witness_failure_reasons"] = ["i32-before-llvm-as-failed: failed"]
    blocked_globalopt_evidence["globalopt_witness_cases"] = [
        {"name": "i32", "status": "failed", "failure_reasons": ["i32-before-llvm-as-failed: failed"]},
        {"name": "ptr", "status": "passed", "failure_reasons": []},
        {"name": "array", "status": "passed", "failure_reasons": []},
    ]
    blocked_globalopt_evidence["globalopt_witness"] = dict(blocked_globalopt_evidence["globalopt_witness"])
    blocked_globalopt_evidence["globalopt_witness"]["status"] = "failed"
    blocked_globalopt_evidence["globalopt_witness"]["failure_reasons"] = ["i32-before-llvm-as-failed: failed"]
    blocked_globalopt_evidence["globalopt_witness"]["cases"] = list(blocked_globalopt_evidence["globalopt_witness_cases"])
    write_jsonl(contract_blocked / "evidence.jsonl", [evidence_record, blocked_reduction_evidence, blocked_globalopt_evidence])
    write_json(contract_blocked / "current.json", [])
    run([
        sys.executable,
        str(repo / "tools" / "cv-promote-intent-candidates.py"),
        "--validated",
        str(contract_blocked / "validated.jsonl"),
        "--evidence",
        str(contract_blocked / "evidence.jsonl"),
        "--current",
        str(contract_blocked / "current.json"),
        "--out",
        str(contract_blocked / "proposed.json"),
        "--report",
        str(contract_blocked / "report.txt"),
        "--require-ready",
        "--require-verified-evidence",
    ])
    contract_report = (contract_blocked / "report.txt").read_text(encoding="utf-8")
    assert "Source-slice contract status" in contract_report
    assert "failed: 1" in contract_report
    assert "Contract-blocked decisions: 1" in contract_report
    assert "Source-slice contract failed checks" in contract_report
    assert "predicate-expands-legality: 1" in contract_report
    assert "Source-slice contract verification" in contract_report
    assert "Contract-verifier-blocked decisions: 1" in contract_report
    assert "status-mismatch: 1" in contract_report
    assert "Transaction formalization verification" in contract_report
    assert "Transaction-formalization-blocked decisions: 1" in contract_report
    assert "after-mismatch: 1" in contract_report
    assert "Transaction formal provenance coverage" in contract_report
    assert "incomplete: 1" in contract_report
    assert "GlobalOpt witness status" in contract_report
    assert "failed: 1" in contract_report
    assert "i32-before-llvm-as-failed: 1" in contract_report
    assert "GlobalOpt witness cases" in contract_report
    assert "i32: failed=1" in contract_report
    assert "evidence-blocked probe.slp.vectorize-reduction" in contract_report
    assert "contract=failed" in contract_report
    assert "contract_verification=failed" in contract_report
    assert "formalization=failed" in contract_report
    assert "formal_provenance=incomplete" in contract_report

    helper_blocked = work_dir / "helper-blocked"
    helper_reason = "unsupported-unresolved-helper-slice"
    helper_diagnostic = helper_slice_diagnostic(helper_reason)
    helper_blocked_evidence = dict(evidence_record)
    helper_blocked_evidence["evidence_status"] = "unsupported"
    helper_blocked_evidence["transaction_lowering"] = "fallback"
    helper_blocked_evidence["transaction_consistency"] = "failed"
    helper_blocked_evidence["transaction_graph_absent_reasons"] = [helper_reason]
    helper_blocked_evidence["transaction_graph_absent_diagnostics"] = [helper_diagnostic]
    helper_blocked_evidence["optimization_transaction"] = {
        "kind": "slp-vectorize-binop",
        "opcode": "add",
        "lanes": 4,
        "lowering": "fallback",
        "consistency": "failed",
        "transaction_graph_absent_reasons": [helper_reason],
        "transaction_graph_absent_diagnostics": [helper_diagnostic],
    }
    write_jsonl(helper_blocked / "validated.jsonl", [validated_record()])
    write_jsonl(helper_blocked / "evidence.jsonl", [helper_blocked_evidence])
    write_json(helper_blocked / "current.json", [])
    run([
        sys.executable,
        str(repo / "tools" / "cv-promote-intent-candidates.py"),
        "--validated",
        str(helper_blocked / "validated.jsonl"),
        "--evidence",
        str(helper_blocked / "evidence.jsonl"),
        "--current",
        str(helper_blocked / "current.json"),
        "--out",
        str(helper_blocked / "proposed.json"),
        "--report",
        str(helper_blocked / "report.txt"),
        "--require-ready",
    ])
    helper_report = (helper_blocked / "report.txt").read_text(encoding="utf-8")
    assert "Helper slice diagnostics" in helper_report
    assert "reasons: unsupported-unresolved-helper-slice=1" in helper_report
    assert "helpers: missingMaskBody=1" in helper_report
    assert "roles: memory-pack=1" in helper_report
    assert "helper=missingMaskBody role=memory-pack reason=unsupported-unresolved-helper-slice" in helper_report

    blocked = work_dir / "blocked"
    write_jsonl(blocked / "validated.jsonl", [validated_record()])
    blocked_evidence = dict(evidence_record)
    blocked_evidence["evidence_status"] = "uncovered"
    write_jsonl(blocked / "evidence.jsonl", [blocked_evidence])
    write_json(blocked / "current.json", [])
    result = run([
        sys.executable,
        str(repo / "tools" / "cv-promote-intent-candidates.py"),
        "--validated",
        str(blocked / "validated.jsonl"),
        "--evidence",
        str(blocked / "evidence.jsonl"),
        "--current",
        str(blocked / "current.json"),
        "--out",
        str(blocked / "proposed.json"),
        "--require-ready",
        "--require-verified-evidence",
    ], expect=1)
    assert "no promotion-ready intent candidates with verified evidence" in result.stderr


def audit_mode(repo: Path, work_dir: Path) -> None:
    validated = work_dir / "validated.jsonl"
    out = work_dir / "audit.json"
    report = work_dir / "audit.txt"
    write_jsonl(
        validated,
        [
            validated_record(),
            reduction_validated_record("add"),
            reduction_validated_record("and"),
            reduction_validated_record("smin"),
            reduction_validated_record("fadd"),
            relaxed_fp_policy_validated_record(),
            relaxed_fp_policy_validated_record(scalable=True),
            scalable_memory_pack_validated_record(),
            reduction_validated_record("add", ["unsupported-reduction-ambiguous-width"], width_status="ambiguous"),
            reduction_validated_record("add", ["unsupported-reduction-conflicting-width"], width_status="conflicting"),
            reduction_validated_record("add", ["unsupported-lane-count:128"], lanes=128),
        ],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(validated),
        "--out",
        str(out),
        "--report",
        str(report),
    ])
    data = json.loads(out.read_text(encoding="utf-8"))
    tx = data["summary"]["optimization_transactions"]
    assert tx["records"] == 11
    assert tx["kind"] == {"slp-vectorize-binop": 2, "slp-vectorize-reduction": 9}
    assert tx["formal_ir"] == 6
    assert tx["relaxed_fp_policy"] == 2
    assert tx["fallback"] == 3
    assert tx["scalable_memory_pack"] == 1
    assert tx["memory_contract"] == {"static-gather-pack-v1": 1, "unset": 10}
    assert "unsupported-scalable-transaction" not in tx["consistency_errors"]
    assert tx["reduction_opcode"] == {"add": 4, "and": 1, "fadd": 3, "smin": 1}
    assert tx["reduction_family"] == {"arithmetic": 4, "bitwise": 1, "floating-point": 3, "minmax": 1}
    assert tx["reduction_lanes"] == {"4": 8, "128": 1}
    assert tx["reduction_sources"] == 9
    assert tx["with_reduction_result"] == 9
    assert tx["scalable_reductions"] == 1
    assert tx["unsupported_reduction_reasons"] == {
        "unsupported-reduction-ambiguous-width": 1,
        "unsupported-reduction-conflicting-width": 1,
    }
    gaps = tx["reduction_coverage_gaps"]
    assert gaps["records"] == 3
    assert gaps["unsupported_reasons"] == {
        "unsupported-reduction-ambiguous-width": 1,
        "unsupported-reduction-conflicting-width": 1,
    }
    assert gaps["lane_blockers"] == {"unsupported-lane-count:128": 1}
    assert gaps["recommendations"] == {
        "add wider reduction lane formal coverage": 1,
        "improve width provenance mining": 1,
        "inspect conflicting width evidence": 1,
    }
    reduction_record = [record for record in data["records"] if record["marker"] == REDUCTION_MARKER][0]
    assert reduction_record["transaction_reduction_opcode"] == "add"
    assert reduction_record["transaction_reduction_sources"] == 1
    recommendations = {record["recommendation"] for record in data["records"]}
    assert "covered by source-derived relaxed FP policy" in recommendations
    assert "model FP reduction semantics and fast-math policy" not in recommendations
    assert "improve width provenance mining" in recommendations
    assert "inspect conflicting width evidence" in recommendations
    assert "add wider reduction lane formal coverage" in recommendations
    assert "model scalable min/max vector ops" not in recommendations
    report_text = report.read_text(encoding="utf-8")
    assert "relaxed_fp_policy: 2" in report_text
    assert "scalable_memory_pack: 1" in report_text
    assert "memory_contract: static-gather-pack-v1=1, unset=10" in report_text
    assert "reduction_opcode: add=4" in report_text
    assert "reduction_family: arithmetic=4, bitwise=1, floating-point=3, minmax=1" in report_text
    assert "reduction_lanes: 128=1, 4=8" in report_text
    assert "reduction_sources: 9" in report_text
    assert "with_reduction_result: 9" in report_text
    assert "unsupported-reduction-floating-point=1" not in report_text
    assert "Reduction coverage gaps" in report_text
    assert "recommendations: add wider reduction lane formal coverage=1" in report_text
    assert "next_modeling_target:" in report_text

    graph_validated = work_dir / "graph-contract-validated.jsonl"
    graph_out = work_dir / "graph-contract-audit.json"
    graph_report = work_dir / "graph-contract-audit.txt"
    write_jsonl(
        graph_validated,
        [
            source_graph_contract_validated_record(),
            source_graph_contract_validated_record(
                "source-graph:interprocedural-dfg",
                "missing-interprocedural-dfg-edges",
            ),
            source_graph_contract_validated_record(
                "source-graph:access-path-provenance",
                "invalid-access-path-provenance",
            ),
        ],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(graph_validated),
        "--out",
        str(graph_out),
        "--report",
        str(graph_report),
    ])
    graph_data = json.loads(graph_out.read_text(encoding="utf-8"))
    graph_summary = graph_data["summary"]["source_program_graph_contract"]
    assert graph_summary["status"] == {"failed": 2, "passed": 1}
    assert graph_summary["failed_checks"] == {
        "source-graph:access-path-provenance": 1,
        "source-graph:interprocedural-dfg": 1,
    }
    graph_gaps = graph_summary["gaps"]
    assert graph_gaps["records"] == 2
    assert graph_gaps["failure_reasons"] == {
        "invalid-access-path-provenance": 1,
        "missing-interprocedural-dfg-edges": 1,
    }
    assert graph_gaps["recommendations"] == {
        "fix source access-path provenance": 1,
        "improve helper return/argument DFG mining": 1,
    }
    assert graph_gaps["next_modeling_target"] == "improve helper return/argument DFG mining"
    graph_recommendations = {record["recommendation"] for record in graph_data["records"]}
    assert "covered by source-derived transaction formal IR" in graph_recommendations
    assert "improve helper return/argument DFG mining" in graph_recommendations
    assert "fix source access-path provenance" in graph_recommendations
    graph_report_text = graph_report.read_text(encoding="utf-8")
    assert "Source program graph contract" in graph_report_text
    assert "gap_records: 2" in graph_report_text
    assert "gap_recommendations: fix source access-path provenance=1" in graph_report_text
    assert "improve helper return/argument DFG mining=1" in graph_report_text
    assert "next_modeling_target: improve helper return/argument DFG mining" in graph_report_text

    masked_validated = work_dir / "masked-validated.jsonl"
    masked_out = work_dir / "masked-audit.json"
    masked_report = work_dir / "masked-audit.txt"
    write_jsonl(
        masked_validated,
        [
            masked_memory_validated_record(),
            masked_memory_validated_record(scalable_mask_tuple=True),
            masked_memory_validated_record(
                "unsupported-unresolved-memory-mask",
                detail="incomplete-branch-assignment",
            ),
            masked_memory_validated_record("unsupported-variable-mask-index"),
            masked_memory_validated_record("unsupported-missing-masked-load-passthru"),
            masked_memory_validated_record("unsupported-scalable-masked-memory"),
            helper_slice_validated_record("unsupported-unresolved-helper-slice"),
        ],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(masked_validated),
        "--out",
        str(masked_out),
        "--report",
        str(masked_report),
    ])
    masked_data = json.loads(masked_out.read_text(encoding="utf-8"))
    masked_tx = masked_data["summary"]["optimization_transactions"]
    assert masked_tx["masked_memory"] == 2
    assert masked_tx["scalable_mask_tuple"] == 1
    assert masked_tx["mask_blocker_kind"] == {
        "helper-slice": 1,
        "missing-passthru": 1,
        "scalable-mask-syntax": 1,
        "unsafe-mask-index": 1,
        "unresolved-mask": 1,
    }
    assert masked_tx["mask_blocker_detail"] == {
        "helper-slice:unsupported-unresolved-helper-slice": 1,
        "incomplete-branch-assignment": 1,
        "missing-passthru": 1,
        "scalable-mask-syntax": 1,
        "unsafe-mask-index": 1,
    }
    assert masked_tx["memory_contract"]["masked-contiguous-load-pack-v1"] == 2
    assert masked_tx["store_contract"]["masked-contiguous-store-pack-v1"] == 2
    masked_gaps = masked_tx["masked_memory_coverage_gaps"]
    assert masked_gaps["records"] == 5
    assert masked_gaps["masked_records"] == 7
    assert masked_gaps["covered_records"] == 2
    assert masked_gaps["unsupported_reasons"] == {
        "unsupported-missing-masked-load-passthru": 1,
        "unsupported-scalable-masked-memory": 1,
        "unsupported-unresolved-memory-mask": 1,
        "unsupported-variable-mask-index": 1,
    }
    assert masked_gaps["blocker_kinds"] == {
        "helper-slice": 1,
        "missing-passthru": 1,
        "scalable-mask-syntax": 1,
        "unsafe-mask-index": 1,
        "unresolved-mask": 1,
    }
    assert masked_gaps["blocker_details"] == {
        "helper-slice:unsupported-unresolved-helper-slice": 1,
        "incomplete-branch-assignment": 1,
        "missing-passthru": 1,
        "scalable-mask-syntax": 1,
        "unsafe-mask-index": 1,
    }
    assert masked_gaps["recommendations"] == {
        "classify unresolved scalable mask provenance or unsupported mask syntax": 1,
        "expand mask provenance mining": 1,
        "improve helper body resolution": 1,
        "model remaining implicit masked load passthrough provenance": 1,
        "support remaining variable mask index provenance": 1,
    }
    assert masked_gaps["next_modeling_target"] == "expand mask provenance mining"
    masked_report_text = masked_report.read_text(encoding="utf-8")
    assert "Masked memory coverage gaps" in masked_report_text
    assert "scalable_mask_tuple: 1" in masked_report_text
    assert "blocker_kinds: helper-slice=1" in masked_report_text
    assert "unresolved-mask=1" in masked_report_text
    assert "blocker_details: helper-slice:unsupported-unresolved-helper-slice=1" in masked_report_text
    assert "incomplete-branch-assignment=1" in masked_report_text
    assert "unsupported: unsupported-missing-masked-load-passthru=1" in masked_report_text
    assert "next_modeling_target: expand mask provenance mining" in masked_report_text

    address_validated = work_dir / "address-validated.jsonl"
    address_out = work_dir / "address-audit.json"
    address_report = work_dir / "address-audit.txt"
    write_jsonl(
        address_validated,
        [
            memory_address_validated_record(
                "unsupported-variable-gather-index",
                detail="unsafe-gather-index",
            ),
            memory_address_validated_record(
                "unsupported-variable-store-index",
                detail="unsafe-store-index",
            ),
            memory_address_validated_record("unsupported-duplicate-gather-lane"),
            memory_address_validated_record("unsupported-duplicate-scatter-lane"),
        ],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(address_validated),
        "--out",
        str(address_out),
        "--report",
        str(address_report),
    ])
    address_data = json.loads(address_out.read_text(encoding="utf-8"))
    address_tx = address_data["summary"]["optimization_transactions"]
    assert address_tx["memory_address_blocker_kind"] == {
        "duplicate-gather-lane": 1,
        "duplicate-scatter-lane": 1,
        "unsafe-gather-index": 1,
        "unsafe-store-index": 1,
    }
    assert address_tx["memory_address_blocker_detail"] == {
        "duplicate-gather-lane": 1,
        "duplicate-scatter-lane": 1,
        "unsafe-gather-index": 1,
        "unsafe-store-index": 1,
    }
    address_gaps = address_tx["memory_address_coverage_gaps"]
    assert address_gaps["records"] == 4
    assert address_gaps["unsupported_reasons"] == {
        "unsupported-duplicate-gather-lane": 1,
        "unsupported-duplicate-scatter-lane": 1,
        "unsupported-variable-gather-index": 1,
        "unsupported-variable-store-index": 1,
    }
    assert address_gaps["blocker_kinds"] == {
        "duplicate-gather-lane": 1,
        "duplicate-scatter-lane": 1,
        "unsafe-gather-index": 1,
        "unsafe-store-index": 1,
    }
    assert address_gaps["blocker_details"] == {
        "duplicate-gather-lane": 1,
        "duplicate-scatter-lane": 1,
        "unsafe-gather-index": 1,
        "unsafe-store-index": 1,
    }
    assert address_gaps["recommendations"] == {
        "classify duplicate gather lane semantics": 1,
        "classify duplicate scatter lane semantics": 1,
        "model safe symbolic gather indexes": 1,
        "model safe symbolic store indexes": 1,
    }
    assert address_gaps["next_modeling_target"] == "model safe symbolic gather indexes"
    address_report_text = address_report.read_text(encoding="utf-8")
    assert "Memory address coverage gaps" in address_report_text
    assert "memory_address_blocker_kind: duplicate-gather-lane=1" in address_report_text
    assert "unsafe-store-index=1" in address_report_text
    assert "blocker_details: duplicate-gather-lane=1" in address_report_text
    assert "next_modeling_target: model safe symbolic gather indexes" in address_report_text

    global_validated = work_dir / "global-initializer-validated.jsonl"
    global_out = work_dir / "global-initializer-audit.json"
    global_report = work_dir / "global-initializer-audit.txt"
    global_complete = global_initializer_validated_record()
    global_complete["globalopt_witness_status"] = "passed"
    global_complete["globalopt_witness_failure_reasons"] = []
    global_complete["globalopt_witness_model"] = "global-initializer-default-null-family-v1"
    global_complete["globalopt_witness_contract"] = witness_contract()
    global_complete["globalopt_witness_contract_verification_status"] = "passed"
    global_complete["globalopt_witness_contract_formal_status"] = {"proved": 3}
    global_complete["globalopt_witness_contract_semantic_status"] = {"proved": 3}
    global_complete["globalopt_witness_contract_failed_checks"] = []
    global_complete["globalopt_witness_contract_semantic_failed_checks"] = []
    global_complete["globalopt_safety_provenance_status"] = "passed"
    global_complete["globalopt_safety_provenance_failed_checks"] = []
    global_complete["globalopt_safety_provenance"] = global_complete["evidence"]["formal_parameters"]["global.initializer.safety_provenance"]
    global_complete["predicate_provenance_verification_status"] = "passed"
    global_complete["predicate_provenance_failed_checks"] = []
    global_complete["globalopt_witness_structural_status"] = "passed"
    global_complete["globalopt_required_witness_cases"] = ["i32", "ptr", "array"]
    global_complete["globalopt_missing_required_witness_cases"] = []
    global_complete["globalopt_witness_cases"] = [
        {"name": "i32", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("i32"), "failure_reasons": []},
        {"name": "ptr", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("ptr"), "failure_reasons": []},
        {"name": "array", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("array"), "failure_reasons": []},
    ]
    global_incomplete = global_initializer_validated_record(complete=False)
    global_incomplete["globalopt_witness_status"] = "failed"
    global_incomplete["globalopt_witness_failure_reasons"] = ["i32-before-llvm-as-failed: failed"]
    global_incomplete["globalopt_witness_model"] = "global-initializer-default-null-family-v1"
    global_incomplete["globalopt_witness_contract"] = witness_contract("failed", "failed")
    global_incomplete["globalopt_witness_contract_verification_status"] = "failed"
    global_incomplete["globalopt_witness_contract_formal_status"] = {"not-run": 1, "proved": 2}
    global_incomplete["globalopt_witness_contract_semantic_status"] = {"failed": 1, "proved": 2}
    global_incomplete["globalopt_witness_contract_failed_checks"] = ["i32-structural-checks-not-passed"]
    global_incomplete["globalopt_witness_contract_semantic_failed_checks"] = ["i32-semantic-failed"]
    global_incomplete["globalopt_safety_provenance_status"] = "failed"
    global_incomplete["globalopt_safety_provenance_failed_checks"] = ["local-linkage-provenance-missing"]
    global_incomplete["globalopt_safety_provenance"] = global_incomplete["evidence"]["formal_parameters"]["global.initializer.safety_provenance"]
    global_incomplete["predicate_provenance_verification_status"] = "failed"
    global_incomplete["predicate_provenance_failed_checks"] = ["local-linkage-provenance-missing"]
    global_incomplete["globalopt_witness_structural_status"] = "failed"
    global_incomplete["globalopt_required_witness_cases"] = ["i32", "ptr", "array"]
    global_incomplete["globalopt_missing_required_witness_cases"] = ["i32", "ptr"]
    global_incomplete["evidence"]["formal_parameters"]["global.initializer.rewrite_provenance_status"] = "unsupported"
    global_incomplete["evidence"]["formal_parameters"]["global.initializer.rewrite_provenance_reason"] = (
        "unsupported-global-initializer-replacement"
    )
    global_incomplete["evidence"]["formal_parameters"]["global.initializer.replacement_expr"] = "SomeOtherValue"
    global_incomplete["evidence"]["formal_parameters"]["global.initializer.value_type_expr"] = ""
    global_incomplete["globalopt_witness_cases"] = [
        {"name": "i32", "status": "failed", "structural_checks": "failed", "structural_details": structural_case("i32"), "failure_reasons": ["i32-before-llvm-as-failed: failed"]},
        {"name": "ptr", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("ptr"), "failure_reasons": []},
        {"name": "array", "status": "passed", "structural_checks": "passed", "structural_details": structural_case("array"), "failure_reasons": []},
    ]
    slp_predicate_provenance = validated_record()
    slp_predicate_provenance["predicate_provenance_verification_status"] = "passed"
    slp_predicate_provenance["predicate_provenance_failed_checks"] = []
    write_jsonl(global_validated, [global_complete, global_incomplete, slp_predicate_provenance])
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(global_validated),
        "--out",
        str(global_out),
        "--report",
        str(global_report),
    ])
    global_data = json.loads(global_out.read_text(encoding="utf-8"))
    assert global_data["summary"]["formal_inference"] == {
        "source-derived-intent-graph": 1,
        "source-derived-transaction": 1,
        "unset": 1,
    }
    global_tx = global_data["summary"]["optimization_transactions"]
    assert global_tx["global_initializer_contract"] == {
        "remove-global-initializer-if-dead-v1": 1,
        "unset": 2,
    }
    assert global_tx["global_initializer_observability_model"] == {
        "local-unobservable-initializer-v1": 2,
        "unset": 1,
    }
    assert global_tx["global_initializer_rewrite_api"] == {"setInitializer": 2, "unset": 1}
    assert global_tx["global_initializer_replacement_kind"] == {
        "default-null-initializer": 2,
        "unset": 1,
    }
    global_safety = global_data["summary"]["global_initializer_safety"]
    assert global_safety["status"] == {"complete": 1, "incomplete": 1}
    assert global_safety["observed_facts"] == {
        "initializer-dead": 2,
        "local-linkage": 1,
        "no-uses": 1,
    }
    assert global_safety["missing_facts"] == {
        "local-linkage": 1,
        "no-uses": 1,
    }
    global_witnesses = global_data["summary"]["globalopt_witnesses"]
    assert global_witnesses["status"] == {"failed": 1, "passed": 1}
    assert global_witnesses["failures"] == {"i32-before-llvm-as-failed": 1}
    assert global_witnesses["required_cases"] == {"array": 2, "i32": 2, "ptr": 2}
    assert global_witnesses["missing_required_cases"] == {"i32": 1, "ptr": 1}
    assert global_witnesses["cases"] == {
        "array": {"passed": 2},
        "i32": {"failed": 1, "passed": 1},
        "ptr": {"passed": 2},
    }
    assert global_witnesses["structural_status"] == {"failed": 1, "passed": 1}
    assert global_witnesses["structural_cases"] == {
        "array": {"passed": 2},
        "i32": {"failed": 1, "passed": 1},
        "ptr": {"passed": 2},
    }
    assert global_witnesses["changed_line_counts"] == {
        "array:1": 2,
        "i32:1": 2,
        "ptr:1": 2,
    }
    assert global_witnesses["contract_verification_status"] == {"failed": 1, "passed": 1}
    assert global_witnesses["contract_formal_status"] == {"not-run": 1, "proved": 5}
    assert global_witnesses["contract_semantic_status"] == {"failed": 1, "proved": 5}
    assert global_witnesses["contract_failed_checks"] == {"i32-structural-checks-not-passed": 1}
    assert global_witnesses["contract_semantic_failed_checks"] == {"i32-semantic-failed": 1}
    assert global_witnesses["safety_provenance_status"] == {"failed": 1, "passed": 1}
    assert global_witnesses["safety_provenance_failed_checks"] == {"local-linkage-provenance-missing": 1}
    assert "predicate_provenance_verification_status" not in global_witnesses
    assert "predicate_provenance_failed_checks" not in global_witnesses
    predicate_provenance = global_data["summary"]["predicate_provenance"]
    assert predicate_provenance["records"] == 3
    assert predicate_provenance["checked"] == 3
    assert predicate_provenance["passed"] == 2
    assert predicate_provenance["failed"] == 1
    assert predicate_provenance["absent"] == 0
    assert predicate_provenance["verification_status"] == {"failed": 1, "passed": 2}
    assert predicate_provenance["failed_checks"] == {"local-linkage-provenance-missing": 1}
    global_rewrite = global_data["summary"]["globalopt_rewrite_provenance"]
    assert global_rewrite["status"] == {"complete": 1, "unsupported": 1}
    assert global_rewrite["callee"] == {"setInitializer": 2}
    assert global_rewrite["replacement_expr"] == {
        "Constant::getNullValue(GV->getValueType())": 1,
        "SomeOtherValue": 1,
    }
    assert global_rewrite["value_type_expr"] == {"GV->getValueType()": 1, "absent": 1}
    global_report_text = global_report.read_text(encoding="utf-8")
    assert "global_initializer_contract: remove-global-initializer-if-dead-v1=1" in global_report_text
    assert "global_initializer_observability_model: local-unobservable-initializer-v1=2" in global_report_text
    assert "global_initializer_rewrite_api: setInitializer=2" in global_report_text
    assert "global_initializer_replacement_kind: default-null-initializer=2" in global_report_text
    assert "Global initializer safety" in global_report_text
    assert "status: complete=1, incomplete=1" in global_report_text
    assert "missing_facts: local-linkage=1, no-uses=1" in global_report_text
    assert "GlobalOpt witnesses" in global_report_text
    assert "status: failed=1, passed=1" in global_report_text
    assert "failures: i32-before-llvm-as-failed=1" in global_report_text
    assert "required_cases: array=2, i32=2, ptr=2" in global_report_text
    assert "missing_required_cases: i32=1, ptr=1" in global_report_text
    assert "i32: failed=1, passed=1" in global_report_text
    assert "ptr: passed=2" in global_report_text
    assert "array: passed=2" in global_report_text
    assert "structural_status: failed=1, passed=1" in global_report_text
    assert "structural i32: failed=1, passed=1" in global_report_text
    assert "changed_line_counts: array:1=2, i32:1=2, ptr:1=2" in global_report_text
    assert "contract_verification_status: failed=1, passed=1" in global_report_text
    assert "contract_formal_status: not-run=1, proved=5" in global_report_text
    assert "contract_semantic_status: failed=1, proved=5" in global_report_text
    assert "contract_failed_checks: i32-structural-checks-not-passed=1" in global_report_text
    assert "contract_semantic_failed_checks: i32-semantic-failed=1" in global_report_text
    assert "safety_provenance_status: failed=1, passed=1" in global_report_text
    assert "safety_provenance_failed_checks: local-linkage-provenance-missing=1" in global_report_text
    assert "Predicate provenance" in global_report_text
    assert "  records: 3" in global_report_text
    assert "  checked: 3" in global_report_text
    assert "  passed: 2" in global_report_text
    assert "  failed: 1" in global_report_text
    assert "  absent: 0" in global_report_text
    assert "verification_status: failed=1, passed=2" in global_report_text
    assert "failed_checks: local-linkage-provenance-missing=1" in global_report_text
    assert "predicate_provenance_verification_status:" not in global_report_text
    assert "predicate_provenance_failed_checks:" not in global_report_text
    assert "GlobalOpt rewrite provenance" in global_report_text
    assert "status: complete=1, unsupported=1" in global_report_text
    assert "callee: setInitializer=2" in global_report_text
    assert "Constant::getNullValue(GV->getValueType())=1" in global_report_text
    assert "SomeOtherValue=1" in global_report_text

    helper_validated = work_dir / "helper-validated.jsonl"
    helper_out = work_dir / "helper-audit.json"
    helper_report = work_dir / "helper-audit.txt"
    write_jsonl(
        helper_validated,
        [
            helper_slice_validated_record("unsupported-recursive-helper-slice"),
            helper_slice_validated_record("unsupported-unresolved-helper-slice"),
            helper_slice_validated_record("unsupported-multiple-return-helper-slice"),
            helper_slice_validated_record("unsupported-incomplete-helper-arguments"),
            helper_slice_validated_record("unsupported-helper-expansion-depth"),
        ],
    )
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(helper_validated),
        "--out",
        str(helper_out),
        "--report",
        str(helper_report),
    ])
    helper_data = json.loads(helper_out.read_text(encoding="utf-8"))
    helper_tx = helper_data["summary"]["optimization_transactions"]
    helper_gaps = helper_tx["helper_slice_coverage_gaps"]
    assert helper_gaps["records"] == 5
    assert helper_gaps["unsupported_reasons"] == {
        "unsupported-helper-expansion-depth": 1,
        "unsupported-incomplete-helper-arguments": 1,
        "unsupported-multiple-return-helper-slice": 1,
        "unsupported-recursive-helper-slice": 1,
        "unsupported-unresolved-helper-slice": 1,
    }
    assert helper_gaps["diagnostic_reasons"] == helper_gaps["unsupported_reasons"]
    assert helper_gaps["helpers"] == {
        "defaultedMask": 1,
        "depthMask5": 1,
        "missingMaskBody": 1,
        "multiReturnMask": 1,
        "recursiveMask": 1,
    }
    assert helper_gaps["roles"] == {"memory-pack": 5}
    incomplete_diag = next(
        diagnostic for diagnostic in helper_gaps["diagnostics"]
        if diagnostic["reason"] == "unsupported-incomplete-helper-arguments"
    )
    assert incomplete_diag["file"] == "SLPVectorizer.cpp"
    assert incomplete_diag["line"] == 6450
    assert incomplete_diag["marker"] == MARKER
    assert incomplete_diag["helper"] == "defaultedMask"
    assert incomplete_diag["role"] == "memory-pack"
    assert incomplete_diag["recommendation"] == "improve helper argument binding"
    assert helper_gaps["recommendations"] == {
        "summarize non-terminal helper expansion depth": 1,
        "improve helper argument binding": 1,
        "improve helper body resolution": 1,
        "model recursive helper slice summaries": 1,
        "normalize non-lane-local multi-return helper slices": 1,
    }
    assert helper_gaps["next_modeling_target"] == "improve helper body resolution"
    helper_recommendations = {record["recommendation"] for record in helper_data["records"]}
    assert "improve helper body resolution" in helper_recommendations
    assert "normalize non-lane-local multi-return helper slices" in helper_recommendations
    assert "summarize non-terminal helper expansion depth" in helper_recommendations
    helper_report_text = helper_report.read_text(encoding="utf-8")
    assert "Helper slice coverage gaps" in helper_report_text
    assert "unsupported-incomplete-helper-arguments=1" in helper_report_text
    assert "unsupported-helper-expansion-depth=1" in helper_report_text
    assert "helpers: defaultedMask=1" in helper_report_text
    assert "roles: memory-pack=5" in helper_report_text
    assert "Top helper slice diagnostics" in helper_report_text
    assert "helper=defaultedMask role=memory-pack reason=unsupported-incomplete-helper-arguments" in helper_report_text
    assert "next_modeling_target: improve helper body resolution" in helper_report_text

    contract_validated = work_dir / "contract-validated.jsonl"
    contract_out = work_dir / "contract-audit.json"
    contract_report = work_dir / "contract-audit.txt"
    complete_record = validated_record()
    complete_record["evidence"] = transaction_evidence(contract_status="complete")
    complete_record["evidence"]["source_slice_contract_verification_status"] = "passed"
    complete_record["evidence"]["source_slice_contract_verification_mismatches"] = []
    complete_record["evidence"]["transaction_formalization_verification_status"] = "passed"
    complete_record["evidence"]["transaction_formalization_verification_mismatches"] = []
    complete_record["evidence"]["transaction_formal_provenance_coverage_status"] = "passed"
    complete_record["evidence"]["transaction_formal_provenance_missing_paths"] = []
    complete_record["evidence"]["transaction_formal_provenance_roles"] = {"domain": 1, "opcode": 1}
    failed_record = validated_record()
    failed_record["line"] = 1235
    failed_record["proof_status"] = "unsupported"
    failed_record["proof_result"] = "unsupported-marker"
    failed_record["promotion_status"] = "blocked"
    failed_record["evidence"] = transaction_evidence(contract_status="failed")
    failed_record["evidence"]["source_slice_contract_verification_status"] = "failed"
    failed_record["evidence"]["source_slice_contract_verification_mismatches"] = [
        {"id": "contract-status", "kind": "status-mismatch"}
    ]
    failed_record["evidence"]["transaction_formalization_verification_status"] = "failed"
    failed_record["evidence"]["transaction_formalization_verification_mismatches"] = [
        {"kind": "after-mismatch"}
    ]
    failed_record["evidence"]["transaction_formal_provenance_coverage_status"] = "incomplete"
    failed_record["evidence"]["transaction_formal_provenance_missing_paths"] = ["after.op"]
    failed_record["evidence"]["transaction_formal_provenance_roles"] = {"domain": 1}
    write_jsonl(contract_validated, [complete_record, failed_record])
    run([
        sys.executable,
        str(repo / "tools" / "cv-audit-intent-coverage.py"),
        "--validated",
        str(contract_validated),
        "--out",
        str(contract_out),
        "--report",
        str(contract_report),
    ])
    contract_data = json.loads(contract_out.read_text(encoding="utf-8"))
    contract_tx = contract_data["summary"]["optimization_transactions"]
    assert contract_tx["with_source_slice_contract"] == 2
    assert contract_tx["complete_source_slice_contract"] == 1
    assert contract_tx["incomplete_source_slice_contract"] == 1
    assert contract_tx["source_slice_contract_missing_roles"] == {"legality": 1}
    assert contract_tx["source_slice_contract_failed_checks"] == {
        "predicate-expands-legality": 1,
        "role-reachability:legality": 1,
    }
    assert contract_tx["source_slice_contract_failed_kinds"] == {
        "predicate-expansion": 1,
        "role-reachability": 1,
    }
    contract_verification = contract_data["summary"]["source_slice_contract_verification"]
    assert contract_verification["status"] == {"failed": 1, "passed": 1}
    assert contract_verification["mismatch_kinds"] == {"status-mismatch": 1}
    formalization = contract_data["summary"]["transaction_formalization_verification"]
    assert formalization["status"] == {"failed": 1, "passed": 1}
    assert formalization["mismatch_kinds"] == {"after-mismatch": 1}
    provenance = contract_data["summary"]["transaction_formal_provenance_coverage"]
    assert provenance["status"] == {"incomplete": 1, "passed": 1}
    assert provenance["roles"] == {"domain": 2, "opcode": 1}
    assert provenance["missing_paths"] == {"after.op": 1}
    assert provenance["incomplete"] == 1
    contract_report_text = contract_report.read_text(encoding="utf-8")
    assert "source_slice_contract_failed_checks: predicate-expands-legality=1" in contract_report_text
    assert "Source-slice contract verification" in contract_report_text
    assert "mismatches: status-mismatch=1" in contract_report_text
    assert "Transaction formalization verification" in contract_report_text
    assert "mismatches: after-mismatch=1" in contract_report_text
    assert "Transaction formal provenance coverage" in contract_report_text
    assert "incomplete: 1" in contract_report_text
    assert "top_missing_paths: after.op=1" in contract_report_text


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "evidence":
        evidence_mode(args.repo, args.work_dir)
    elif args.mode == "promotion":
        promotion_mode(args.repo, args.work_dir)
    else:
        audit_mode(args.repo, args.work_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
