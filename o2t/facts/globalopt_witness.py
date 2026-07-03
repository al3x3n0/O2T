"""Canonical GlobalOpt dead-initializer witness contract helpers."""

from __future__ import annotations

import re
from typing import Any


MARKER = "probe.globalopt.dead-initializer"
WITNESS_MODEL = "global-initializer-default-null-family-v1"
WITNESS_CONTRACT_MODEL = "globalopt-dead-initializer-witness-contract-v1"
DEFAULT_REQUIRED_WITNESS_CASES = ["i32", "ptr", "array"]

GLOBAL_LINE_RE = re.compile(r"^(@[A-Za-z_.$][A-Za-z0-9_.$-]*)\s*=\s+(\w+)\s+global\s+(.+)$")


def split_global_type_initializer(rest: str) -> tuple[str, str]:
    rest = rest.strip()
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            return rest, ""
        return rest[: end + 1], rest[end + 1 :].strip()
    parts = rest.split(None, 1)
    if len(parts) != 2:
        return rest, ""
    return parts[0], parts[1].strip()


def parse_global_initializer_line(line: str) -> dict[str, str]:
    match = GLOBAL_LINE_RE.match(line.strip())
    if not match:
        return {}
    ty, initializer = split_global_type_initializer(match.group(3))
    return {
        "name": match.group(1),
        "linkage": match.group(2),
        "type": ty,
        "initializer": initializer,
    }


def is_default_initializer(initializer: str) -> bool:
    return initializer.strip() in {"0", "null", "zeroinitializer"}


def only_initializer_changed(before_line: str, after_line: str) -> bool:
    before = parse_global_initializer_line(before_line)
    after = parse_global_initializer_line(after_line)
    return bool(before and after) and all(
        before.get(key) == after.get(key)
        for key in ("name", "linkage", "type")
    )


def global_initializer_contract_details(before: str, after: str, case: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    before_global = str(case.get("before_global") or "")
    after_global = str(case.get("after_global") or "")
    before_function = str(case.get("function") or "")
    case_name = str(case.get("name") or "unknown")
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    before_global_lines = [line for line in before_lines if line.startswith("@cv_dead_init =")]
    after_global_lines = [line for line in after_lines if line.startswith("@cv_dead_init =")]
    changed_lines = [
        index
        for index, (before_line, after_line) in enumerate(zip(before_lines, after_lines), start=1)
        if before_line != after_line
    ]
    before_info: dict[str, str] = {}
    after_info: dict[str, str] = {}

    if len(before_lines) != len(after_lines):
        errors.append(f"{case_name}-witness-line-count-changed")
    if len(changed_lines) != 1:
        errors.append(f"{case_name}-witness-changed-line-count:{len(changed_lines)}")
    if before_global not in before:
        errors.append(f"{case_name}-before-global-not-nondefault")
    if after_global not in after:
        errors.append(f"{case_name}-after-global-not-default-null")
    if before_function not in before or before_function not in after:
        errors.append(f"{case_name}-observable-function-body-missing")
    if len(before_global_lines) != 1 or len(after_global_lines) != 1:
        errors.append(f"{case_name}-global-line-count-mismatch")
    else:
        before_info = parse_global_initializer_line(before_global_lines[0])
        after_info = parse_global_initializer_line(after_global_lines[0])
        if not before_info or not after_info:
            errors.append(f"{case_name}-global-line-parse-failed")
        else:
            if before_info.get("linkage") != "internal" or after_info.get("linkage") != "internal":
                errors.append(f"{case_name}-global-linkage-changed")
            for key in ("name", "type"):
                if before_info.get(key) != after_info.get(key):
                    errors.append(f"{case_name}-global-{key}-changed")
            before_initializer = str(before_info.get("initializer") or "")
            after_initializer = str(after_info.get("initializer") or "")
            if is_default_initializer(before_initializer):
                errors.append(f"{case_name}-before-initializer-already-default")
            if not is_default_initializer(after_initializer):
                errors.append(f"{case_name}-after-initializer-not-default")
            if changed_lines and not only_initializer_changed(before_global_lines[0], after_global_lines[0]):
                errors.append(f"{case_name}-global-change-not-initializer-only")
    if before.replace(before_global, after_global) != after:
        errors.append(f"{case_name}-witness-changes-more-than-initializer")
    details = {
        "global_name": before_info.get("name", ""),
        "linkage": before_info.get("linkage", ""),
        "initializer_type": before_info.get("type", ""),
        "before_initializer": before_info.get("initializer", ""),
        "after_initializer": after_info.get("initializer", ""),
        "changed_line_count": len(changed_lines),
        "changed_lines": changed_lines,
    }
    return errors, details


def validate_witness_text(before: str, after: str, case: dict[str, Any]) -> list[str]:
    errors, _ = global_initializer_contract_details(before, after, case)
    return errors


def compact_witness_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(case.get("name") or ""),
        "status": str(case.get("status") or "unset"),
        "before": str(case.get("before") or ""),
        "after": str(case.get("after") or ""),
        "structural_checks": str(case.get("structural_checks") or ""),
        "structural_details": dict(case.get("structural_details"))
        if isinstance(case.get("structural_details"), dict)
        else {},
        "failure_reasons": [
            str(reason) for reason in case.get("failure_reasons", []) if str(reason)
        ] if isinstance(case.get("failure_reasons"), list) else [],
    }


def structural_status_for_cases(cases: list[dict[str, Any]]) -> str:
    if not cases:
        return "absent"
    statuses = [str(case.get("structural_checks") or "unset") for case in cases]
    if any(status == "failed" for status in statuses):
        return "failed"
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    return "incomplete"


def required_cases_from_witness(witness: dict[str, Any], fallback: list[str] | None = None) -> list[str]:
    cases = witness.get("required_cases")
    if isinstance(cases, list) and cases:
        return [str(case) for case in cases if str(case)]
    return list(fallback or DEFAULT_REQUIRED_WITNESS_CASES)


def missing_required_cases(witness: dict[str, Any], required_cases: list[str]) -> list[str]:
    explicit_missing = witness.get("missing_required_cases")
    if isinstance(explicit_missing, list) and explicit_missing:
        return [str(case) for case in explicit_missing if str(case)]
    cases = witness.get("cases")
    if not isinstance(cases, list):
        return list(required_cases)
    status_by_name = {
        str(case.get("name") or ""): str(case.get("status") or "unset")
        for case in cases
        if isinstance(case, dict) and str(case.get("name") or "")
    }
    return [case for case in required_cases if status_by_name.get(case) != "passed"]


def witness_contract(
    witness: dict[str, Any],
    required_cases: list[str] | None = None,
) -> dict[str, Any]:
    normalized_required = required_cases_from_witness(witness, required_cases)
    cases = [
        compact_witness_case(case)
        for case in witness.get("cases", [])
        if isinstance(case, dict)
    ] if isinstance(witness.get("cases"), list) else []
    structural_status = str(witness.get("structural_checks") or "")
    if not structural_status:
        structural_status = structural_status_for_cases(cases)
    missing = missing_required_cases({"cases": cases, "missing_required_cases": witness.get("missing_required_cases")}, normalized_required)
    status = str(witness.get("status") or "absent")
    if status not in {"passed", "failed", "absent", "unset"}:
        status = "unset"
    if structural_status == "failed" or missing:
        contract_status = "failed"
    elif status == "passed" and structural_status == "passed":
        contract_status = "passed"
    elif status == "absent":
        contract_status = "absent"
    else:
        contract_status = "incomplete"
    return {
        "model": WITNESS_CONTRACT_MODEL,
        "witness_model": str(witness.get("witness_model") or WITNESS_MODEL),
        "status": contract_status,
        "witness_status": status,
        "structural_status": structural_status,
        "required_cases": normalized_required,
        "missing_required_cases": missing,
        "cases": cases,
    }


def compact_witness(
    witness: dict[str, Any],
    required_cases: list[str] | None = None,
) -> dict[str, Any]:
    contract = witness_contract(witness, required_cases)
    out = {
        "status": str(witness.get("status") or "unset"),
        "before": str(witness.get("before") or ""),
        "after": str(witness.get("after") or ""),
        "failure_reasons": [
            str(reason) for reason in witness.get("failure_reasons", []) if str(reason)
        ] if isinstance(witness.get("failure_reasons"), list) else [],
        "witness_contract": contract,
    }
    if witness.get("witness"):
        out["witness"] = str(witness.get("witness") or "")
    if witness.get("witness_model"):
        out["witness_model"] = str(witness.get("witness_model") or "")
    out["required_cases"] = list(contract["required_cases"])
    out["missing_required_cases"] = list(contract["missing_required_cases"])
    out["structural_checks"] = str(contract["structural_status"])
    if isinstance(witness.get("source_provenance"), dict):
        out["source_provenance"] = {
            str(key): str(value)
            for key, value in witness["source_provenance"].items()
            if str(key)
        }
    out["cases"] = list(contract["cases"])
    return out
