#!/usr/bin/env python3
"""Checks for joined optimization registry semantics."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


def main() -> int:
    repo = Path(sys.argv[1])
    sys.path.insert(0, str(repo / "tools"))

    from cv_optimization_registry import (  # pylint: disable=import-outside-toplevel
        BV_OP_FOR_OPERATION,
        CONSTANT_FOR_IDENTITY,
        OPERATION_FOR_BUILDER_CALL,
        builder_tokens_for_registered_operations,
        builder_calls_for_registered_operations,
        constant_matcher_tokens,
        formal_template_for_marker,
        formal_template_entries,
        load_llvm_idioms,
        load_formal_templates,
        marker_config_patch,
        marker_config_entries,
        marker_has_legacy_validation_path,
        marker_has_supported_formal_path,
        markers_for_config,
        operation_matcher_tokens,
        reduction_operation_for_token,
        reduction_tokens,
        registry_diagnostic,
        rewrite_api_idioms,
        rewrite_tokens,
        registry_spec_for_marker,
        scalar_formal_from_marker_config,
        scalar_instcombine_spec,
        source_patterns_for_marker,
        vector_inference_template_entries,
        vector_inference_template_for_marker,
        vector_formal_from_template,
        vector_emission_tokens,
        vector_operation_for_token,
    )

    idioms = load_llvm_idioms()
    assert idioms["model"] == "llvm-idioms-v1"
    assert {"operations", "constants", "rewrites", "guards"} <= set(idioms)
    formal_templates = load_formal_templates()
    assert formal_templates["model"] == "formal-templates-v1"
    assert len(formal_template_entries()) >= 24
    template_markers = {entry["marker"] for entry in formal_template_entries()}
    required_vector_templates = {
        "probe.vector.add-zero",
        "probe.vector.shuffle-splat",
        "probe.vector.extract-insert",
        "probe.vector.reduction-add-zero",
        "probe.vector.smin",
        "probe.vector.abs",
        "probe.vector.scalable.add-zero",
        "probe.vector.scalable.reduction-add-zero",
    }
    assert required_vector_templates <= template_markers
    vector_add_template = formal_template_for_marker("probe.vector.add-zero")
    assert vector_add_template["kind"] == "fixed-vector-algebraic-identity"
    assert vector_add_template["result_bits"] == 128
    assert "bvadd a #x00000000" in vector_add_template["smt_before"]
    assert vector_formal_from_template("probe.vector.add-zero", {}) == (
        vector_add_template["smt_before"],
        vector_add_template["smt_after"],
        128,
    )
    assert formal_template_for_marker("probe.vector.shuffle-splat")["kind"] == "fixed-vector-structural-identity"
    assert vector_formal_from_template("probe.vector.extract-insert", {"const_a": 7}) == (
        "#x00000007",
        "#x00000007",
        32,
    )
    assert vector_formal_from_template("probe.vector.reduction-add-zero", {}) == (
        "#x00000000",
        "#x00000000",
        32,
    )
    assert formal_template_for_marker("probe.vector.smin")["result_bits"] == 128
    assert "bvslt" in formal_template_for_marker("probe.vector.smin")["smt_before"]
    assert "bvsub #x00000000" in formal_template_for_marker("probe.vector.abs")["smt_before"]
    assert formal_template_for_marker("probe.vector.scalable.add-zero")["domain"] == "scalable-vector-bv32"
    assert vector_formal_from_template("probe.vector.scalable.reduction-add-zero", {}) == (
        "#x00000000",
        "#x00000000",
        32,
    )
    vector_inference_templates = vector_inference_template_entries()
    assert len(vector_inference_templates) == 24
    inference_markers = {entry["marker"] for entry in vector_inference_templates}
    assert template_markers == inference_markers
    assert vector_inference_template_for_marker("probe.vector.add-zero")["builder"] == "fixed-binop-identity"
    assert vector_inference_template_for_marker("probe.vector.shuffle-splat")["builder"] == "shuffle-splat"
    assert vector_inference_template_for_marker("probe.vector.reduction-add-single-lane")["builder"] == "reduction-add-single-lane"
    assert vector_inference_template_for_marker("probe.vector.scalable.add-zero")["builder"] == "scalable-binop-identity"
    assert scalar_formal_from_marker_config("probe.vector.add-zero", {"arith_opcode": 0}) is None
    assert marker_has_legacy_validation_path("probe.instcombine.add-zero")
    assert marker_has_legacy_validation_path("probe.dce.dead-instruction")
    assert marker_has_legacy_validation_path("probe.globalopt.dead-initializer")
    assert marker_has_supported_formal_path("probe.vector.add-zero")
    assert marker_has_supported_formal_path("probe.simplifycfg.diamond")
    assert not marker_has_supported_formal_path("probe.unknown")
    pass_constraints = repo / "constraints" / "pass_constraints.json"
    generator_path = repo / "tools" / "cv-generate-probe-marker-map.py"
    spec = importlib.util.spec_from_file_location("cv_generate_probe_marker_map", generator_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generated_header = repo / "include" / "o2t" / "GeneratedProbeMarkerMap.h"
    marker_config = repo / "constraints" / "marker_config_map.json"
    expected_header = generator.generate(generator.load_entries(marker_config))
    assert generated_header.read_text(encoding="utf-8") == expected_header
    generated_markers = re.findall(r'\{"(probe\.[^"]+)",\s*"([^"]+)",\s*"((?:\\.|[^"])*)"', expected_header)
    marker_entries = json.loads(marker_config.read_text(encoding="utf-8"))
    by_marker = {entry["marker"]: entry for entry in marker_entries}
    assert len(generated_markers) == len(by_marker)
    for marker, group, patch_json in generated_markers:
        assert marker in by_marker
        assert group == by_marker[marker]["group"]
        assert json.loads(patch_json.replace('\\"', '"')) == by_marker[marker]["config"]
    ast_bind_generator_path = repo / "tools" / "cv-generate-ast-bind-marker-map.py"
    ast_bind_spec = importlib.util.spec_from_file_location(
        "cv_generate_ast_bind_marker_map", ast_bind_generator_path
    )
    assert ast_bind_spec is not None and ast_bind_spec.loader is not None
    ast_bind_generator = importlib.util.module_from_spec(ast_bind_spec)
    ast_bind_spec.loader.exec_module(ast_bind_generator)
    assert not hasattr(ast_bind_generator, "BIND_MARKERS")
    assert not hasattr(ast_bind_generator, "SCALAR_MATCHER_BIND_BY_OPCODE_VALUE")
    assert not hasattr(ast_bind_generator, "AST_BIND_BY_MARKER")
    ast_metadata_path = repo / "tools" / "cv_ast_mining_metadata.py"
    ast_metadata_spec = importlib.util.spec_from_file_location(
        "cv_ast_mining_metadata", ast_metadata_path
    )
    assert ast_metadata_spec is not None and ast_metadata_spec.loader is not None
    ast_metadata = importlib.util.module_from_spec(ast_metadata_spec)
    ast_metadata_spec.loader.exec_module(ast_metadata)
    assert not hasattr(ast_metadata, "AST_BIND_BY_MARKER")
    assert not hasattr(ast_metadata, "TEXT_FALLBACK_ONLY_BINDS")
    assert not hasattr(ast_metadata, "MEMBER_CALL_BINDS")
    assert not hasattr(ast_metadata, "TYPE_NAME_BINDS")
    assert not hasattr(ast_metadata, "MATCHER_NAMES_BY_BIND")
    pass_entries = json.loads(pass_constraints.read_text(encoding="utf-8"))
    pass_by_marker = {entry["marker"]: entry for entry in pass_entries}
    assert pass_by_marker["probe.globalopt.dead-initializer"]["ast_mining"]["bind"] == "dead-global-init"
    assert pass_by_marker["probe.loop.canonical-header"]["ast_mining"]["matcher_kind"] == "member-call"
    assert pass_by_marker["probe.vector.scalable.add-zero"]["ast_mining"]["registration"] == "text-fallback"
    generated_ast_bind_header = repo / "include" / "o2t" / "GeneratedAstBindMarkerMap.h"
    ast_bind_entries = ast_bind_generator.bind_marker_entries(pass_constraints)
    expected_ast_bind_header = ast_bind_generator.generate(ast_bind_entries)
    assert generated_ast_bind_header.read_text(encoding="utf-8") == expected_ast_bind_header
    ast_bind_pairs = {(entry["bind"], entry["marker"]) for entry in ast_bind_entries}
    ast_bind_registration = {entry["bind"]: entry["registration"] for entry in ast_bind_entries}
    assert ("vector-add-zero", "probe.vector.add-zero") in ast_bind_pairs
    assert ("vector-scalable-add-zero", "probe.vector.scalable.add-zero") in ast_bind_pairs
    assert ast_bind_registration["vector-add-zero"] == "ast"
    assert ast_bind_registration["vector-scalable-add-zero"] == "text-fallback"
    assert ("m-sub", "probe.instcombine.sub-zero") in ast_bind_pairs
    assert ("dead-global-init", "probe.globalopt.dead-initializer") in ast_bind_pairs
    assert ("promotable-alloca", "probe.mem2reg.promotable-alloca") in ast_bind_pairs
    assert ("loop-header", "probe.loop.canonical-header") in ast_bind_pairs
    assert ("diamond", "probe.simplifycfg.diamond") in ast_bind_pairs
    ast_matcher_generator_path = repo / "tools" / "cv-generate-ast-matcher-specs.py"
    ast_matcher_spec = importlib.util.spec_from_file_location(
        "cv_generate_ast_matcher_specs", ast_matcher_generator_path
    )
    assert ast_matcher_spec is not None and ast_matcher_spec.loader is not None
    ast_matcher_generator = importlib.util.module_from_spec(ast_matcher_spec)
    ast_matcher_spec.loader.exec_module(ast_matcher_generator)
    assert not hasattr(ast_matcher_generator, "SCALAR_MATCHER_BIND_BY_OPCODE_VALUE")
    assert not hasattr(ast_matcher_generator, "AST_BIND_BY_MARKER")
    assert not hasattr(ast_matcher_generator, "SCALAR_MATCHER_NAME_BY_BIND")
    assert not hasattr(ast_matcher_generator, "NAME_BY_BIND")
    assert not hasattr(ast_matcher_generator, "MEMBER_CALL_BINDS")
    assert not hasattr(ast_matcher_generator, "TYPE_NAME_BINDS")
    generated_ast_matcher_header = repo / "include" / "o2t" / "GeneratedAstMatcherSpecs.h"
    ast_matcher_entries = ast_matcher_generator.ast_matcher_spec_entries(pass_constraints)
    expected_ast_matcher_header = ast_matcher_generator.generate(ast_matcher_entries)
    assert generated_ast_matcher_header.read_text(encoding="utf-8") == expected_ast_matcher_header
    ast_matcher_pairs = {(entry["bind"], entry["kind"], entry["name"]) for entry in ast_matcher_entries}
    ast_nested_pairs = {
        (entry["bind"], entry["kind"], entry["name"], entry["nested_name"])
        for entry in ast_matcher_entries
    }
    assert ("vector-add-zero", "nested-function-call", "m_SplatOrPoison", "m_Zero") in ast_nested_pairs
    assert ("vector-mul-one", "nested-function-call", "m_SplatOrPoison", "m_One") in ast_nested_pairs
    assert ("vector-xor-self", "function-call", "VectorXorSelf") in ast_matcher_pairs
    assert ("vector-reduction-add-zero", "function-call", "vector_reduce_add") in ast_matcher_pairs
    assert ("m-zero", "function-call", "m_Zero") in ast_matcher_pairs
    assert ("xor-self", "binary-equality", "") in ast_matcher_pairs
    for alloca_guard in ("use_empty", "user_empty"):
        assert ("unused-alloca", "member-call", alloca_guard) in ast_matcher_pairs
    for broad_alloca_guard in ("hasNUses", "empty", "hasNUsesOrMore"):
        assert ("unused-alloca", "member-call", broad_alloca_guard) not in ast_matcher_pairs
    assert ("loop-header", "member-call", "getHeader") in ast_matcher_pairs
    assert ("induction-phi", "type-name", "PHINode") in ast_matcher_pairs
    source_generator_path = repo / "tools" / "cv-generate-source-marker-patterns.py"
    source_spec = importlib.util.spec_from_file_location(
        "cv_generate_source_marker_patterns", source_generator_path
    )
    assert source_spec is not None and source_spec.loader is not None
    source_generator = importlib.util.module_from_spec(source_spec)
    source_spec.loader.exec_module(source_generator)
    assert not hasattr(source_generator, "SCALAR_MARKER_OPS")
    text_miner_source = (repo / "tools" / "cv-mine-pass-source.py").read_text(encoding="utf-8")
    assert "SCALAR_AMBIGUOUS_IDENTITY_DEFAULTS" not in text_miner_source
    assert "scalar_pattern_context_matches" not in text_miner_source
    generated_source_header = repo / "include" / "o2t" / "GeneratedSourceMarkerPatterns.h"
    llvm_idioms = repo / "constraints" / "llvm_idioms.json"
    source_entries = source_generator.source_pattern_entries(pass_constraints, llvm_idioms)
    expected_source_header = source_generator.generate(source_entries)
    assert generated_source_header.read_text(encoding="utf-8") == expected_source_header
    assert source_generator.scalar_identity_default_patterns(
        source_generator.load_json_array(pass_constraints)
    ) == {
        "m_Zero(": "probe.instcombine.add-zero",
        "m_One(": "probe.instcombine.mul-one",
    }
    assert source_entries
    source_pairs = {(entry["marker"], entry["pattern"]) for entry in source_entries}
    source_rules = {
        (entry["marker"], entry["pattern"]): (entry["required"], entry["forbidden"])
        for entry in source_entries
    }
    assert ("probe.vector.add-zero", "m_SplatOrPoison(m_Zero") in source_pairs
    assert ("probe.instcombine.add-zero", "m_Add(") in source_pairs
    assert ("probe.instcombine.add-zero", "m_c_Add(") in source_pairs
    for alloca_pattern in ("use_empty", "user_empty", "hasNUses(0)", "users().empty", "hasNUsesOrMore(1)"):
        assert ("probe.cleanup.unused-alloca", alloca_pattern) in source_pairs
    assert ("probe.cleanup.unused-alloca", "AllocaInst") not in source_pairs
    for alloca_pattern in ("use_empty", "user_empty", "hasNUses(0)", "users().empty"):
        assert source_rules[("probe.cleanup.unused-alloca", alloca_pattern)] == ("if", "")
    assert source_rules[("probe.cleanup.unused-alloca", "hasNUsesOrMore(1)")] == ("!\tif", "")
    by_pair = {(entry["marker"], entry["pattern"]): entry for entry in source_entries}
    assert source_generator.source_rule_matches(
        "if (AI->hasNUses( 0 )) AI->eraseFromParent();",
        by_pair[("probe.cleanup.unused-alloca", "hasNUses(0)")],
    )
    assert source_generator.source_rule_matches(
        "if (!AI->hasNUsesOrMore( 1 )) AI->eraseFromParent();",
        by_pair[("probe.cleanup.unused-alloca", "hasNUsesOrMore(1)")],
    )
    assert not source_generator.source_rule_matches(
        "if (AI->hasNUsesOrMore(1)) AI->eraseFromParent();",
        by_pair[("probe.cleanup.unused-alloca", "hasNUsesOrMore(1)")],
    )
    assert ("probe.instcombine.xor-self", "m_Xor(") not in source_pairs
    assert ("probe.instcombine.and-allones", "m_AllOnes(") in source_pairs
    assert ("probe.instcombine.and-self", "m_And(") in source_pairs
    assert ("probe.instcombine.sub-zero", "m_Zero(") not in source_pairs
    assert ("probe.instcombine.or-zero", "m_Zero(") not in source_pairs
    assert ("probe.instcombine.and-self", "Op0 == Op1") not in source_pairs
    assert ("probe.instcombine.xor-self", "Op0 == Op1") in source_pairs
    assert source_rules[("probe.instcombine.and-allones", "m_And(")] == ("m_AllOnes(", "")
    assert source_rules[("probe.instcombine.and-self", "m_And(")] == ("", "m_AllOnes(")
    assert ("probe.vector.scalable.reduction-add-zero", "ScalableReductionAddZero") in source_pairs
    assert not any(marker.startswith("probe.slp.") for marker, _ in source_pairs)
    assert BV_OP_FOR_OPERATION["and"] == "bvand"
    assert CONSTANT_FOR_IDENTITY["allones"] == 0xFFFFFFFF
    assert OPERATION_FOR_BUILDER_CALL["CreateOr"] == "or"
    assert "m_c_And(" in operation_matcher_tokens()["and"]
    assert "m_AllOnes(" in constant_matcher_tokens()["allones"]
    assert "CreateSub" in builder_tokens_for_registered_operations()
    assert "replaceInstUsesWith" in rewrite_tokens()
    assert rewrite_api_idioms()["setInitializer"]["action"] == "remove-global-initializer-if-dead-v1"
    assert "CreateAddReduce" in reduction_tokens()
    assert reduction_operation_for_token("Builder.CreateSMinReduce(LHS)") == "smin"
    assert "CreateSelect" in vector_emission_tokens()
    assert vector_operation_for_token("Builder.CreateUMax(A, B)") == "umax"
    assert marker_config_entries()
    assert marker_config_patch("probe.instcombine.and-allones") == {
        "arith_opcode": 5,
        "rhs_mode": 3,
        "const_a": -1,
    }
    assert markers_for_config({"arith_opcode": 4, "rhs_mode": 0}, mode="coverage") == [
        "probe.instcombine.or-zero"
    ]
    assert markers_for_config({"vector_shape": 23}, mode="formal") == ["probe.vector.umax"]
    assert markers_for_config({"memory_shape": 2}, mode="formal") == [
        "probe.mem2reg.promotable-alloca",
        "probe.mem2reg.store-load-forward",
        "probe.instcombine.redundant-load",
    ]

    expected = {
        "probe.instcombine.add-zero": ("add", "zero", "replace-with-lhs", "CreateAdd"),
        "probe.instcombine.sub-zero": ("sub", "zero", "replace-with-lhs", "CreateSub"),
        "probe.instcombine.mul-one": ("mul", "one", "replace-with-lhs", "CreateMul"),
        "probe.instcombine.xor-self": ("xor", "same-value", "replace-with-zero", "CreateXor"),
        "probe.instcombine.or-zero": ("or", "zero", "replace-with-lhs", "CreateOr"),
        "probe.instcombine.and-allones": ("and", "allones", "replace-with-lhs", "CreateAnd"),
        "probe.instcombine.and-self": ("and", "same-value", "replace-with-lhs", "CreateAnd"),
    }
    calls = set(builder_calls_for_registered_operations())
    for marker, (operation, identity, rewrite, builder_call) in expected.items():
        spec = scalar_instcombine_spec(marker)
        facts = spec["semantic_facts"]
        assert spec["pass"] == "instcombine"
        assert facts["shape"] == "scalar"
        assert facts["operation"] == operation
        assert facts["identity"] == identity
        assert facts["rewrite"] == rewrite
        assert builder_call in calls
        assert registry_diagnostic(marker)["operation"] == operation
        assert source_patterns_for_marker(marker)

    dce = registry_spec_for_marker("probe.dce.dead-instruction")
    assert dce["semantic_facts"]["operation"] == "erase"
    assert scalar_instcombine_spec("probe.dce.dead-instruction") == {}
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
