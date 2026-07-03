#!/usr/bin/env python3
"""Shared accessors for optimization transaction formalization."""

from __future__ import annotations

from typing import Any

from o2t.formal_ir import FormalIrError  # noqa: F401  (re-exported for importers)

TEMPLATE_DOMAIN = "transaction-template-v1"
TEMPLATE_MODEL = "optimization-transaction-template-v1"


def _load_infer_module() -> Any:
    # Lazy import avoids an import cycle (infer does not import this module) and
    # reaches the real implementation in the package -- not the tools/ CLI shim.
    from o2t.intent import infer
    return infer


def transaction_formal_for(finding: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    return _load_infer_module().transaction_formal_for(finding)


def _template_transactions(formal: dict[str, Any]) -> list[dict[str, Any]]:
    has_single = "transaction" in formal
    has_many = "transactions" in formal
    if has_single and has_many:
        raise FormalIrError("formal transaction template must use transaction or transactions, not both")
    if has_many:
        transactions = formal.get("transactions")
        if not isinstance(transactions, list) or not transactions:
            raise FormalIrError("formal transaction template transactions must be a non-empty array")
        if not all(isinstance(item, dict) for item in transactions):
            raise FormalIrError("formal transaction template transactions entries must be objects")
        return [dict(item) for item in transactions]
    transaction = formal.get("transaction")
    if not isinstance(transaction, dict):
        raise FormalIrError("formal transaction template requires transaction object")
    return [dict(transaction)]


def _template_label(index: int, parameters: dict[str, Any]) -> str:
    kind = str(parameters.get("transaction.kind") or "transaction")
    opcode = str(parameters.get("transaction.opcode") or parameters.get("transaction.reduction_opcode") or "op")
    lanes = str(parameters.get("transaction.lanes") or parameters.get("transaction.base_lanes") or "lanes")
    return f"template-{index}-{kind}-{opcode}-{lanes}lane"


def registry_transaction_template_formal_for(formal: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    lowered = registry_transaction_template_formals_for(formal)
    if len(lowered) != 1:
        raise FormalIrError("formal transaction template produced multiple lowered forms")
    item = lowered[0]
    return item["formal"], item["parameters"]


def registry_transaction_template_formals_for(formal: dict[str, Any]) -> list[dict[str, Any]]:
    if formal.get("domain") != TEMPLATE_DOMAIN:
        raise FormalIrError("formal transaction template domain is unsupported")
    if formal.get("model") != TEMPLATE_MODEL:
        raise FormalIrError(f"formal transaction template model must be {TEMPLATE_MODEL}")
    lowered: list[dict[str, Any]] = []
    for index, transaction in enumerate(_template_transactions(formal)):
        result = transaction_formal_for({"optimization_transaction": transaction})
        if result is None:
            raise FormalIrError(f"formal transaction template {index} could not be lowered")
        lowered_formal, parameters = result
        lowered.append(
            {
                "label": _template_label(index, parameters),
                "formal": lowered_formal,
                "parameters": parameters,
            }
        )
    return lowered
