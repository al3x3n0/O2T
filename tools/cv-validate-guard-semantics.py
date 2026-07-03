#!/usr/bin/env python3
"""Validate the guard semantics registry contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GUARDS = ROOT / "constraints" / "guard_semantics.json"

ROLES = {"semantic", "modeled-side-condition", "unmodeled-side-condition", "profitability"}
REL_PREDICATES = {"sgt", "sge", "slt", "sle", "ugt", "uge", "ult", "ule", "eq", "ne"}
FORMAL_EFFECTS = {
    "cmp-assumption",
    "known-bits-assumption",
    "not-poison-assumption",
    "not-eq-zero-assumption",
    "power-of-two-assumption",
    "relation-assumption",
    "structural-only",
    "semantic-side-condition",
    "none",
}
AUDIT_CATEGORIES = {"modeled", "structural", "profitability", "unsupported"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guard-semantics", type=Path, default=DEFAULT_GUARDS)
    return parser.parse_args()


def load_array(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("guard semantics registry must be a JSON array")
    return [record for record in data if isinstance(record, dict)]


def validate_record(record: dict[str, Any], seen: set[str]) -> list[str]:
    errors: list[str] = []
    kind = str(record.get("kind") or "")
    if not kind:
        errors.append("entry missing kind")
        return errors
    if kind in seen:
        errors.append(f"{kind}: duplicate kind")
    seen.add(kind)

    required = ["role", "proof_effect", "formal_effect", "audit_category"]
    missing = [field for field in required if not record.get(field)]
    if missing:
        errors.append(f"{kind}: missing fields: {', '.join(missing)}")

    role = str(record.get("role") or "")
    formal_effect = str(record.get("formal_effect") or "")
    audit_category = str(record.get("audit_category") or "")
    if role and role not in ROLES:
        errors.append(f"{kind}: invalid role: {role}")
    if formal_effect and formal_effect not in FORMAL_EFFECTS:
        errors.append(f"{kind}: invalid formal_effect: {formal_effect}")
    if audit_category and audit_category not in AUDIT_CATEGORIES:
        errors.append(f"{kind}: invalid audit_category: {audit_category}")

    if role == "profitability" and audit_category != "profitability":
        errors.append(f"{kind}: profitability role requires profitability audit_category")
    if role == "unmodeled-side-condition" and audit_category != "unsupported":
        errors.append(f"{kind}: unmodeled side condition requires unsupported audit_category")
    if formal_effect == "not-poison-assumption" and role != "modeled-side-condition":
        errors.append(f"{kind}: not-poison assumptions must be modeled side conditions")
    if formal_effect == "not-eq-zero-assumption" and role != "modeled-side-condition":
        errors.append(f"{kind}: not-eq-zero assumptions must be modeled side conditions")
    if formal_effect == "cmp-assumption" and role != "modeled-side-condition":
        errors.append(f"{kind}: cmp assumptions must be modeled side conditions")
    if formal_effect == "known-bits-assumption" and role != "modeled-side-condition":
        errors.append(f"{kind}: known-bits assumptions must be modeled side conditions")
    if formal_effect == "power-of-two-assumption" and role != "modeled-side-condition":
        errors.append(f"{kind}: power-of-two assumptions must be modeled side conditions")
    if formal_effect == "relation-assumption" and role != "modeled-side-condition":
        errors.append(f"{kind}: relation assumptions must be modeled side conditions")
    if formal_effect == "structural-only" and audit_category != "structural":
        errors.append(f"{kind}: structural-only guards require structural audit_category")
    if formal_effect == "none" and audit_category in {"modeled", "structural"}:
        errors.append(f"{kind}: modeled/structural guards must declare a non-none formal_effect")
    errors.extend(validate_formal_effect_args(kind, record))
    errors.extend(validate_recognizers(kind, record))
    return errors


def validate_formal_effect_args(kind: str, record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    formal_effect = str(record.get("formal_effect") or "")
    args = record.get("formal_effect_args")
    if formal_effect in {"none", "semantic-side-condition"}:
        if args is not None:
            errors.append(f"{kind}: {formal_effect} must not declare formal_effect_args")
        return errors
    if formal_effect in {
        "cmp-assumption",
        "known-bits-assumption",
        "not-poison-assumption",
        "not-eq-zero-assumption",
        "power-of-two-assumption",
        "relation-assumption",
        "structural-only",
    } and not isinstance(args, dict):
        errors.append(f"{kind}: {formal_effect} requires formal_effect_args object")
        return errors
    if formal_effect == "relation-assumption":
        assumption = args.get("assumption")
        if not isinstance(assumption, dict):
            errors.append(f"{kind}: formal_effect_args.assumption must be an object")
            return errors
        if assumption.get("op") != "rel":
            errors.append(f"{kind}: relation-assumption requires assumption op rel")
        if assumption.get("predicate") not in REL_PREDICATES:
            errors.append(f"{kind}: relation-assumption requires supported predicate")
        return errors
    if formal_effect in {
        "cmp-assumption",
        "known-bits-assumption",
        "not-poison-assumption",
        "not-eq-zero-assumption",
        "power-of-two-assumption",
    }:
        assumption = args.get("assumption")
        if not isinstance(assumption, dict):
            errors.append(f"{kind}: formal_effect_args.assumption must be an object")
            return errors
        op = assumption.get("op")
        if formal_effect == "cmp-assumption":
            if op != "cmp":
                errors.append(f"{kind}: cmp-assumption requires assumption op cmp")
            if assumption.get("predicate") not in {"sgt", "sge", "slt", "sle", "ugt", "uge", "ult", "ule", "eq", "ne"}:
                errors.append(f"{kind}: cmp-assumption requires supported predicate")
            if not isinstance(assumption.get("value"), int):
                errors.append(f"{kind}: cmp-assumption requires integer value")
        if formal_effect == "known-bits-assumption":
            if op != "known-bits":
                errors.append(f"{kind}: known-bits-assumption requires assumption op known-bits")
            zero_mask = assumption.get("zero_mask")
            one_mask = assumption.get("one_mask")
            if zero_mask is not None and not isinstance(zero_mask, int):
                errors.append(f"{kind}: known-bits-assumption zero_mask must be integer")
            if one_mask is not None and not isinstance(one_mask, int):
                errors.append(f"{kind}: known-bits-assumption one_mask must be integer")
            if zero_mask is None and one_mask is None:
                errors.append(f"{kind}: known-bits-assumption requires zero_mask or one_mask")
        if formal_effect == "power-of-two-assumption":
            if op != "power-of-two":
                errors.append(f"{kind}: power-of-two-assumption requires assumption op power-of-two")
            if assumption.get("nonzero") is not True:
                errors.append(f"{kind}: power-of-two-assumption requires nonzero true")
        if formal_effect == "not-poison-assumption":
            if op != "not-poison":
                errors.append(f"{kind}: not-poison-assumption requires assumption op not-poison")
            if assumption.get("requires_poison_variable") is not True:
                errors.append(f"{kind}: not-poison-assumption requires requires_poison_variable true")
        if formal_effect == "not-eq-zero-assumption":
            if op != "not-eq":
                errors.append(f"{kind}: not-eq-zero-assumption requires assumption op not-eq")
            if assumption.get("value") != 0:
                errors.append(f"{kind}: not-eq-zero-assumption requires value 0")
    if formal_effect == "structural-only" and args.get("structural") is not True:
        errors.append(f"{kind}: structural-only requires structural true")
    return errors


def validate_recognizers(kind: str, record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    recognizers = record.get("recognizers")
    if recognizers is None:
        return errors
    if not isinstance(recognizers, dict):
        return [f"{kind}: recognizers must be an object"]
    patterns = recognizers.get("text_patterns")
    subject_group = recognizers.get("subject_group")
    ast_callee = recognizers.get("ast_callee")
    if patterns is not None:
        if not isinstance(patterns, list) or not all(isinstance(pattern, str) and pattern for pattern in patterns):
            errors.append(f"{kind}: recognizers.text_patterns must be a non-empty string array")
        else:
            for pattern in patterns:
                try:
                    compiled = re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{kind}: invalid text pattern {pattern!r}: {exc}")
                    continue
                if subject_group is not None:
                    try:
                        compiled.groupindex.get(str(subject_group)) if isinstance(subject_group, str) else None
                    except re.error:
                        pass
                    if isinstance(subject_group, str) and subject_group not in compiled.groupindex:
                        errors.append(f"{kind}: subject_group {subject_group!r} is not a named capture in {pattern!r}")
                    if isinstance(subject_group, int) and (subject_group < 1 or subject_group > compiled.groups):
                        errors.append(f"{kind}: subject_group {subject_group} is out of range for {pattern!r}")
    if subject_group is not None and not isinstance(subject_group, (str, int)):
        errors.append(f"{kind}: recognizers.subject_group must be a string or integer")
    if ast_callee is not None and (not isinstance(ast_callee, str) or not ast_callee):
        errors.append(f"{kind}: recognizers.ast_callee must be a non-empty string")
    return errors


def main() -> int:
    args = parse_args()
    records = load_array(args.guard_semantics)
    seen: set[str] = set()
    errors: list[str] = []
    for record in records:
        errors.extend(validate_record(record, seen))
    if "unknown" not in seen:
        errors.append("registry must define unknown fallback guard")
    out = {"guard_semantics": len(records), "errors": len(errors), "messages": errors}
    print(json.dumps(out, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
