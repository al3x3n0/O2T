"""Shared declarative intent->formal lift engine (M2).

Importable core used by both cv-lift-rules.py (the prove-all tool) and
cv-infer-optimization-intent.py (production lifting). Instantiates before/after
DSL templates with typed holes through the unified vocabulary (rides on ①):

    binop -> BV_OP_FOR_OPERATION   const -> CONSTANT_FOR_IDENTITY
    unop  -> bvneg / bvxor-allones ite   -> select
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from o2t.registry.optimization_registry import BV_OP_FOR_OPERATION, CONSTANT_FOR_IDENTITY

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES = ROOT / "constraints" / "lift_rules.json"
MASK = (1 << 32) - 1


class RuleError(ValueError):
    pass


def instantiate(node: Any) -> dict[str, Any]:
    """Instantiate a template node into a cv_formal_ir DSL node."""
    if not isinstance(node, dict):
        raise RuleError(f"template node must be an object: {node!r}")
    if "var" in node:
        return {"op": "var", "name": str(node["var"])}
    if "const" in node:
        value = node["const"]
        if isinstance(value, bool):
            raise RuleError("const must be an int or identity name")
        if isinstance(value, int):
            resolved = value
        else:
            if value not in CONSTANT_FOR_IDENTITY or CONSTANT_FOR_IDENTITY[value] is None:
                raise RuleError(f"unknown const identity {value!r}")
            resolved = CONSTANT_FOR_IDENTITY[value]
        return {"op": "bvconst", "bits": 32, "value": resolved & MASK}
    if "binop" in node:
        op = BV_OP_FOR_OPERATION.get(node["binop"])
        if not op:
            raise RuleError(f"unknown binop {node['binop']!r}")
        args = node.get("args")
        if not isinstance(args, list) or len(args) != 2:
            raise RuleError(f"binop {node['binop']} needs 2 args")
        return {"op": op, "args": [instantiate(a) for a in args]}
    if "unop" in node:
        args = node.get("args")
        if not isinstance(args, list) or len(args) != 1:
            raise RuleError("unop needs 1 arg")
        inner = instantiate(args[0])
        if node["unop"] == "neg":
            return {"op": "bvneg", "args": [inner]}
        if node["unop"] == "not":
            allones = CONSTANT_FOR_IDENTITY.get("allones")
            return {"op": "bvxor", "args": [inner, {"op": "bvconst", "bits": 32, "value": allones & MASK}]}
        raise RuleError(f"unknown unop {node['unop']!r}")
    if "ite" in node:
        parts = node["ite"]
        if not isinstance(parts, list) or len(parts) != 3:
            raise RuleError("ite needs [cond, then, else]")
        return {"op": "ite", "args": [instantiate(p) for p in parts]}
    raise RuleError(f"unrecognized template node: {node!r}")


def load_rules(path: Path = DEFAULT_RULES) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    return [r for r in rules if isinstance(r, dict)]


def rule_before_after(rule: dict[str, Any]) -> tuple[dict, dict, list[str]]:
    variables = rule.get("variables")
    if not isinstance(variables, list) or not variables or not all(isinstance(v, str) for v in variables):
        raise RuleError(f"rule {rule.get('name')!r} needs a non-empty string variables list")
    return instantiate(rule["before"]), instantiate(rule["after"]), list(variables)


def match_rule(rules: list[dict[str, Any]], operation: str, identity: str, rewrite: str):
    """First rule whose match block equals the (operation, identity, rewrite) facts."""
    for rule in rules:
        m = rule.get("match")
        if not isinstance(m, dict):
            continue
        if (str(m.get("operation") or "") == operation
                and str(m.get("identity") or "") == identity
                and str(m.get("rewrite") or "") == rewrite):
            return rule
    return None
