#!/usr/bin/env python3
"""Joined optimization registry helpers used by O2T tools."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PASS_CONSTRAINTS = ROOT / "constraints" / "pass_constraints.json"
DEFAULT_SEMANTIC_FACTS = ROOT / "constraints" / "semantic_facts.json"
DEFAULT_OPTIMIZATION_INTENTS = ROOT / "constraints" / "optimization_intents.json"
DEFAULT_LLVM_IDIOMS = ROOT / "constraints" / "llvm_idioms.json"
DEFAULT_MARKER_CONFIG_MAP = ROOT / "constraints" / "marker_config_map.json"
DEFAULT_FORMAL_TEMPLATES = ROOT / "constraints" / "formal_templates.json"
DEFAULT_VECTOR_INFERENCE_TEMPLATES = ROOT / "constraints" / "vector_inference_templates.json"


@lru_cache(maxsize=4)
def load_llvm_idioms(path: str = str(DEFAULT_LLVM_IDIOMS)) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    for key in ("operations", "constants", "rewrites", "guards"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"{path} must contain a {key} array")
    for key in ("reductions", "vector_builders"):
        if key in data and not isinstance(data.get(key), list):
            raise ValueError(f"{path} {key} entry must be an array")
    return copy.deepcopy(data)


def operation_idioms() -> dict[str, dict[str, Any]]:
    idioms = load_llvm_idioms()
    result: dict[str, dict[str, Any]] = {}
    for entry in idioms.get("operations", []):
        if not isinstance(entry, dict):
            continue
        operation = str(entry.get("operation") or "")
        if operation:
            result[operation] = dict(entry)
    return result


def constant_idioms() -> dict[str, dict[str, Any]]:
    idioms = load_llvm_idioms()
    result: dict[str, dict[str, Any]] = {}
    for entry in idioms.get("constants", []):
        if not isinstance(entry, dict):
            continue
        identity = str(entry.get("identity") or "")
        if identity:
            result[identity] = dict(entry)
    return result


def rewrite_idioms() -> dict[str, dict[str, Any]]:
    idioms = load_llvm_idioms()
    result: dict[str, dict[str, Any]] = {}
    for entry in idioms.get("rewrites", []):
        if not isinstance(entry, dict):
            continue
        rewrite = str(entry.get("rewrite") or "")
        if rewrite:
            result[rewrite] = dict(entry)
    return result


def reduction_idioms() -> dict[str, dict[str, Any]]:
    idioms = load_llvm_idioms()
    result: dict[str, dict[str, Any]] = {}
    for entry in idioms.get("reductions", []):
        if not isinstance(entry, dict):
            continue
        operation = str(entry.get("operation") or "")
        if operation:
            result[operation] = dict(entry)
    return result


def vector_builder_idioms() -> dict[str, dict[str, Any]]:
    idioms = load_llvm_idioms()
    result: dict[str, dict[str, Any]] = {}
    for entry in idioms.get("vector_builders", []):
        if not isinstance(entry, dict):
            continue
        operation = str(entry.get("operation") or "")
        if operation:
            result[operation] = dict(entry)
    return result


def operation_matcher_tokens() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for operation, entry in operation_idioms().items():
        matchers = entry.get("matchers")
        if not isinstance(matchers, list):
            continue
        result[operation] = tuple(f"{matcher}(" for matcher in matchers if isinstance(matcher, str))
    return result


def constant_matcher_tokens() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for identity, entry in constant_idioms().items():
        matchers = entry.get("matchers")
        if not isinstance(matchers, list):
            continue
        result[identity] = tuple(f"{matcher}(" for matcher in matchers if isinstance(matcher, str))
    return result


def builder_tokens_for_registered_operations() -> tuple[str, ...]:
    tokens = {
        builder
        for entry in operation_idioms().values()
        for builder in entry.get("builders", [])
        if isinstance(builder, str) and builder
    }
    return tuple(sorted(tokens))


def _tokens_by_operation(entries: dict[str, dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for operation, entry in entries.items():
        tokens = entry.get("tokens")
        if not isinstance(tokens, list):
            continue
        result[operation] = tuple(token for token in tokens if isinstance(token, str) and token)
    return result


def reduction_tokens_by_operation() -> dict[str, tuple[str, ...]]:
    return _tokens_by_operation(reduction_idioms())


def vector_builder_tokens_by_operation() -> dict[str, tuple[str, ...]]:
    return _tokens_by_operation(vector_builder_idioms())


def reduction_tokens() -> tuple[str, ...]:
    tokens = {
        token
        for values in reduction_tokens_by_operation().values()
        for token in values
        if token
    }
    return tuple(sorted(tokens))


def vector_builder_tokens() -> tuple[str, ...]:
    tokens = {
        token
        for values in vector_builder_tokens_by_operation().values()
        for token in values
        if token
    }
    return tuple(sorted(tokens))


def vector_emission_tokens() -> tuple[str, ...]:
    return tuple(sorted({*reduction_tokens(), *builder_tokens_for_registered_operations(), *vector_builder_tokens()}))


def reduction_operation_for_token(token: str) -> str:
    lowered = token.lower()
    for operation, tokens in reduction_tokens_by_operation().items():
        if any(item.lower() in lowered for item in tokens):
            return operation
    return ""


def vector_operation_for_token(token: str) -> str:
    lowered = token.lower()
    for operation, tokens in vector_builder_tokens_by_operation().items():
        if any(item.lower() in lowered for item in tokens):
            return operation
    for builder, operation in OPERATION_FOR_BUILDER_CALL.items():
        if builder.lower() in lowered:
            return operation
    return reduction_operation_for_token(token)


def rewrite_api_idioms() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in rewrite_idioms().values():
        apis = entry.get("apis")
        if not isinstance(apis, list):
            continue
        for api in apis:
            if isinstance(api, str) and api:
                result[api] = dict(entry)
    return result


def rewrite_tokens() -> tuple[str, ...]:
    return tuple(sorted(rewrite_api_idioms()))


def supported_scalar_operations() -> set[str]:
    return {
        operation
        for operation, entry in operation_idioms().items()
        if isinstance(entry.get("smt"), str) and entry.get("smt")
    }


BUILDER_CALL_FOR_OPERATION = {
    operation: str((entry.get("builders") or [""])[0])
    for operation, entry in operation_idioms().items()
    if isinstance(entry.get("builders"), list) and entry.get("builders")
}
OPERATION_FOR_BUILDER_CALL = {
    builder: operation
    for operation, entry in operation_idioms().items()
    for builder in entry.get("builders", [])
    if isinstance(builder, str) and builder
}
BV_OP_FOR_OPERATION = {
    operation: str(entry.get("smt"))
    for operation, entry in operation_idioms().items()
    if isinstance(entry.get("smt"), str) and entry.get("smt")
}
CONSTANT_FOR_IDENTITY = {
    identity: int(entry["formal_value"])
    for identity, entry in constant_idioms().items()
    if isinstance(entry.get("formal_value"), int)
}


def _load_array(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [record for record in data if isinstance(record, dict) and isinstance(record.get("marker"), str)]


@lru_cache(maxsize=4)
def load_marker_config_map(path: str = str(DEFAULT_MARKER_CONFIG_MAP)) -> list[dict[str, Any]]:
    records = _load_array(Path(path))
    for record in records:
        if not isinstance(record.get("config"), dict):
            raise ValueError(f"marker config entry {record['marker']} must include config")
        if not isinstance(record.get("matches"), list):
            raise ValueError(f"marker config entry {record['marker']} must include matches")
    return copy.deepcopy(records)


def marker_config_entries() -> list[dict[str, Any]]:
    return load_marker_config_map()


def marker_config_patch(marker: str) -> dict[str, int] | None:
    for entry in load_marker_config_map():
        if entry.get("marker") != marker:
            continue
        patch = entry.get("config")
        if not isinstance(patch, dict):
            return None
        return {str(key): int(value) for key, value in patch.items() if isinstance(value, int)}
    return None


def _match_sets_for_entry(entry: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    if mode == "formal" and isinstance(entry.get("formal_matches"), list):
        raw = entry["formal_matches"]
    else:
        raw = entry.get("matches", [])
    return [item for item in raw if isinstance(item, dict)]


def _config_matches(config: dict[str, int], match: dict[str, Any]) -> bool:
    for key, expected in match.items():
        if not isinstance(expected, int):
            return False
        if int(config.get(str(key), 0)) != expected:
            return False
    return True


def _entry_matches_config(entry: dict[str, Any], config: dict[str, int], mode: str) -> bool:
    return any(_config_matches(config, match) for match in _match_sets_for_entry(entry, mode))


def markers_for_config(config: dict[str, int], mode: str = "coverage") -> list[str]:
    entries = load_marker_config_map()
    markers: list[str] = []

    def add(marker: str) -> None:
        if marker not in markers:
            markers.append(marker)

    def add_group(group: str) -> None:
        for entry in entries:
            if entry.get("group") != group:
                continue
            if _entry_matches_config(entry, config, mode):
                add(str(entry["marker"]))

    if int(config.get("global_shape", 0)) != 0:
        add_group("global")
        return markers
    if int(config.get("vector_shape", 0)) != 0:
        add_group("vector")
        return markers

    if mode == "formal":
        if int(config.get("memory_shape", 0)) != 0:
            add_group("memory")
            return markers
        if int(config.get("loop_shape", 0)) != 0:
            add_group("loop")
            return markers

    for group in ("scalar", "cfg", "memory", "loop"):
        add_group(group)
    return markers


def scalar_formal_from_marker_config(marker: str, config: dict[str, int]) -> tuple[str, str] | None:
    facts = semantic_facts_for_marker(marker)
    if facts.get("shape") != "scalar":
        return None
    operation = str(facts.get("operation") or "")
    identity = str(facts.get("identity") or "")
    rewrite = str(facts.get("rewrite") or "")
    bvop = BV_OP_FOR_OPERATION.get(operation)
    if not bvop:
        return None
    if rewrite == "replace-with-lhs":
        if identity == "same-value":
            return f"({bvop} a a)", "a"
        constant = CONSTANT_FOR_IDENTITY.get(identity)
        if constant is None:
            return None
        return f"({bvop} a #x{constant & 0xFFFFFFFF:08x})", "a"
    if rewrite == "replace-with-zero":
        return f"({bvop} a a)", "#x00000000"
    return None


@lru_cache(maxsize=4)
def load_formal_templates(path: str = str(DEFAULT_FORMAL_TEMPLATES)) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if not isinstance(data.get("templates"), list):
        raise ValueError(f"{path} must contain a templates array")
    for entry in data["templates"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("marker"), str):
            raise ValueError(f"{path} formal template entries must include marker")
        if not isinstance(entry.get("smt_before"), str) or not isinstance(entry.get("smt_after"), str):
            raise ValueError(f"{path} formal template {entry.get('marker')} must include SMT expressions")
        if not isinstance(entry.get("result_bits"), int):
            raise ValueError(f"{path} formal template {entry.get('marker')} must include result_bits")
    return copy.deepcopy(data)


def formal_template_entries() -> list[dict[str, Any]]:
    templates = load_formal_templates().get("templates", [])
    return [dict(entry) for entry in templates if isinstance(entry, dict)]


def formal_template_for_marker(marker: str) -> dict[str, Any]:
    for entry in formal_template_entries():
        if entry.get("marker") == marker:
            return dict(entry)
    return {}


def _format_template_smt(expr: str, config: dict[str, int]) -> str:
    def bitvector(value: int) -> str:
        return f"#x{value & 0xFFFFFFFF:08x}"

    return expr.replace("{const_a}", bitvector(int(config.get("const_a", 0)))).replace(
        "{const_b}", bitvector(int(config.get("const_b", 0)))
    )


def vector_formal_from_template(marker: str, config: dict[str, int]) -> tuple[str, str, int] | None:
    template = formal_template_for_marker(marker)
    before = template.get("smt_before")
    after = template.get("smt_after")
    bits = template.get("result_bits")
    if not isinstance(before, str) or not isinstance(after, str) or not isinstance(bits, int):
        return None
    return _format_template_smt(before, config), _format_template_smt(after, config), bits


@lru_cache(maxsize=4)
def load_vector_inference_templates(path: str = str(DEFAULT_VECTOR_INFERENCE_TEMPLATES)) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if not isinstance(data.get("templates"), list):
        raise ValueError(f"{path} must contain a templates array")
    for entry in data["templates"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("marker"), str):
            raise ValueError(f"{path} vector inference template entries must include marker")
        if not isinstance(entry.get("builder"), str):
            raise ValueError(f"{path} vector inference template {entry.get('marker')} must include builder")
        if "text_tokens" in entry and not isinstance(entry.get("text_tokens"), list):
            raise ValueError(f"{path} vector inference template {entry.get('marker')} text_tokens must be an array")
        if "constraints_any" in entry and not isinstance(entry.get("constraints_any"), list):
            raise ValueError(f"{path} vector inference template {entry.get('marker')} constraints_any must be an array")
    return copy.deepcopy(data)


def vector_inference_template_entries() -> list[dict[str, Any]]:
    templates = load_vector_inference_templates().get("templates", [])
    return [dict(entry) for entry in templates if isinstance(entry, dict)]


def vector_inference_template_for_marker(marker: str) -> dict[str, Any]:
    for entry in vector_inference_template_entries():
        if entry.get("marker") == marker:
            return dict(entry)
    return {}


@lru_cache(maxsize=8)
def load_optimization_registry(
    pass_constraints_path: str = str(DEFAULT_PASS_CONSTRAINTS),
    semantic_facts_path: str = str(DEFAULT_SEMANTIC_FACTS),
    optimization_intents_path: str = str(DEFAULT_OPTIMIZATION_INTENTS),
) -> dict[str, dict[str, Any]]:
    joined: dict[str, dict[str, Any]] = {}
    for record in _load_array(Path(pass_constraints_path)):
        marker = str(record["marker"])
        spec = joined.setdefault(marker, {"marker": marker})
        for key in ("pass", "predicate_kind", "source_patterns", "constraints"):
            if key in record:
                spec[key] = copy.deepcopy(record[key])
        spec.setdefault("registry_sources", []).append("pass_constraints")
    for record in _load_array(Path(semantic_facts_path)):
        marker = str(record["marker"])
        spec = joined.setdefault(marker, {"marker": marker})
        if isinstance(record.get("semantic_facts"), dict):
            spec["semantic_facts"] = copy.deepcopy(record["semantic_facts"])
        spec.setdefault("registry_sources", []).append("semantic_facts")
    for record in _load_array(Path(optimization_intents_path)):
        marker = str(record["marker"])
        spec = joined.setdefault(marker, {"marker": marker})
        for key in ("category", "precondition", "rewrite", "intent", "formal"):
            if key in record:
                spec[key] = copy.deepcopy(record[key])
        spec.setdefault("registry_sources", []).append("optimization_intents")
    return {marker: dict(spec) for marker, spec in joined.items()}


def registry_spec_for_marker(marker: str) -> dict[str, Any]:
    return dict(load_optimization_registry().get(marker) or {})


def semantic_facts_for_marker(marker: str) -> dict[str, Any]:
    facts = registry_spec_for_marker(marker).get("semantic_facts")
    return dict(facts) if isinstance(facts, dict) else {}


def scalar_instcombine_spec(marker: str) -> dict[str, Any]:
    spec = registry_spec_for_marker(marker)
    facts = spec.get("semantic_facts")
    if not marker.startswith("probe.instcombine.") or not isinstance(facts, dict):
        return {}
    if facts.get("shape") != "scalar":
        return {}
    operation = str(facts.get("operation") or "")
    rewrite = str(facts.get("rewrite") or "")
    if operation not in BV_OP_FOR_OPERATION:
        return {}
    if rewrite not in {"replace-with-lhs", "replace-with-zero"}:
        return {}
    return spec


def marker_has_legacy_validation_path(marker: str) -> bool:
    facts = semantic_facts_for_marker(marker)
    shape = str(facts.get("shape") or "")
    operation = str(facts.get("operation") or "")
    rewrite = str(facts.get("rewrite") or "")
    if scalar_instcombine_spec(marker):
        return True
    if shape == "scalar" and operation == "erase" and rewrite == "remove-dead-instruction":
        return True
    if shape == "global" and rewrite == "remove-global-initializer-if-dead-v1":
        return True
    return False


def marker_has_supported_formal_path(marker: str) -> bool:
    spec = registry_spec_for_marker(marker)
    if isinstance(spec.get("formal"), dict):
        return True
    if marker_has_legacy_validation_path(marker):
        return True
    return False


def registry_diagnostic(marker: str) -> dict[str, Any]:
    spec = registry_spec_for_marker(marker)
    facts = spec.get("semantic_facts") if isinstance(spec.get("semantic_facts"), dict) else {}
    return {
        "marker": marker,
        "operation": str(facts.get("operation") or ""),
        "rewrite": str(facts.get("rewrite") or spec.get("rewrite") or ""),
        "source": "+".join(str(item) for item in spec.get("registry_sources", []) if item),
    }


def source_patterns_for_marker(marker: str) -> list[str]:
    patterns = registry_spec_for_marker(marker).get("source_patterns")
    return [str(item) for item in patterns if isinstance(item, str)] if isinstance(patterns, list) else []


def builder_operation_for_call(name: str) -> str:
    return OPERATION_FOR_BUILDER_CALL.get(name, "")


def builder_calls_for_registered_operations() -> list[str]:
    operations = {
        str((spec.get("semantic_facts") or {}).get("operation") or "")
        for spec in load_optimization_registry().values()
        if isinstance(spec.get("semantic_facts"), dict)
    }
    return sorted(BUILDER_CALL_FOR_OPERATION[op] for op in operations if op in BUILDER_CALL_FOR_OPERATION)
