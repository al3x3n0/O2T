#!/usr/bin/env python3
"""Fixture checks for registry transaction-template formal records."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def load_tool(repo: Path, path: str, module_name: str) -> Any:
    tool_path = repo / path
    tools_dir = str(repo / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {tool_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    args = parse_args()
    repo = args.repo
    validator = load_tool(repo, "tools/cv-validate-intent-registry.py", "cv_validate_intent_registry")

    records = validator.load_intents(repo / "constraints" / "optimization_intents.json")
    by_marker = {record["marker"]: record for record in records}
    binop = by_marker["probe.slp.vectorize-binop"]
    reduction = by_marker["probe.slp.vectorize-reduction"]

    expected_template_counts = {
        "probe.slp.vectorize-binop": 25,
        "probe.slp.vectorize-reduction": 23,
    }
    expected_instance_counts = {
        "probe.slp.vectorize-binop": 53,
        "probe.slp.vectorize-reduction": 45,
    }
    for record in (binop, reduction):
        instances, lowered = validator.formal_instances_for_record(record["formal"])
        assert len(instances) == expected_instance_counts[record["marker"]]
        assert lowered is not None
        assert len(lowered["templates"]) == expected_template_counts[record["marker"]]
        template_labels = [template["label"] for template in lowered["templates"]]
        assert len(template_labels) == len(set(template_labels))
        instance_labels = [(label, vscale) for label, vscale, _pair in instances]
        assert len(instance_labels) == len(set(instance_labels))
        for template in lowered["templates"]:
            assert template["formal_parameters"]["transaction.model"] == "optimization-transaction-v1"
            assert template["formal_parameters"]["transaction.kind"] in {
                "slp-vectorize-binop",
                "slp-vectorize-reduction",
            }
            assert template["lowered_formal"]["domain"] in {
                "scalar-bv32",
                "scalar-fp32",
                "scalable-scalar-bv32",
                "scalable-scalar-fp32",
                "scalable-vector-bv32",
                "vector-bv32x4",
                "vector-bv32xN",
            }
        scalable_templates = [
            template for template in lowered["templates"]
            if template["formal_parameters"].get("transaction.scalable") is True
        ]
        assert scalable_templates
        for template in scalable_templates:
            assert template["formal_parameters"]["transaction.base_lanes"] == 4
            assert template["formal_parameters"]["transaction.vscale_values"] == [1, 2, 4]
        if record["marker"] == "probe.slp.vectorize-binop":
            opcodes = {
                template["formal_parameters"]["transaction.opcode"]
                for template in lowered["templates"]
            }
            assert opcodes == {"add", "sub", "mul", "xor", "or", "and"}
            assert any(
                template["formal_parameters"].get("transaction.lane_mapping.kind") == "permutation"
                for template in lowered["templates"]
            )
            assert any(template["lowered_formal"]["domain"] == "scalable-vector-bv32" for template in lowered["templates"])
            graph_templates = [
                template for template in lowered["templates"]
                if template["formal_parameters"].get("transaction.graph.kind") == "slp-binop-chain"
            ]
            assert len(graph_templates) == 11
            assert any(template["formal_parameters"].get("transaction.graph.node_count") == 3 for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.shuffle_mask_frame") == "packed-vector-frame" for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.memory_contract") == "contiguous-load-pack-v1" for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.memory_contract") == "static-gather-pack-v1" for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.memory_contract") == "masked-static-gather-pack-v1" for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.masked_memory") is True for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.scalable_memory_pack") is True for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.scalable_masked_memory_pack") is True for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.scalable_store_sink") is True for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.scalable_masked_store_sink") is True for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.memory_model") == "bounded-scalable-lane-memory-v1" for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.store_contract") == "contiguous-store-pack-v1" for template in graph_templates)
            assert any(template["formal_parameters"].get("transaction.graph.store_contract") == "masked-contiguous-store-pack-v1" for template in graph_templates)
            assert any(template["lowered_formal"].get("equivalence") == "observable-result" for template in graph_templates)
        else:
            opcodes = {
                template["formal_parameters"]["transaction.opcode"]
                for template in lowered["templates"]
            }
            assert opcodes == {"add", "mul", "and", "or", "xor", "smin", "smax", "umin", "umax", "fadd", "fmul"}
            assert any(
                template["formal_parameters"].get("transaction.lanes") == 8
                for template in lowered["templates"]
            )
            fp_templates = [
                template for template in lowered["templates"]
                if template["formal_parameters"]["transaction.opcode"] in {"fadd", "fmul"}
            ]
            assert fp_templates
            assert all(template["formal_parameters"]["transaction.fp_semantics"] == "ordered-fp32" for template in fp_templates)
            assert any(template["lowered_formal"]["domain"] == "scalable-scalar-bv32" for template in lowered["templates"])
            assert any(template["lowered_formal"]["domain"] == "scalable-scalar-fp32" for template in lowered["templates"])

    malformed = dict(binop["formal"])
    malformed["transactions"] = [dict(malformed["transactions"][0])]
    malformed["transactions"][0].pop("opcode", None)
    try:
        validator.formal_instances_for_record(malformed)
    except validator.FormalIrError as exc:
        assert "template 0 could not be lowered" in str(exc)
    else:
        raise AssertionError("malformed transaction template unexpectedly lowered")

    for bad_transactions, message in (
        ([], "non-empty array"),
        ([{}], "could not be lowered"),
        ([dict(binop["formal"]["transactions"][0]), "bad"], "entries must be objects"),
    ):
        bad = dict(binop["formal"])
        bad["transactions"] = bad_transactions
        try:
            validator.formal_instances_for_record(bad)
        except validator.FormalIrError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("bad transactions array unexpectedly lowered")

    z3 = args.z3 if Path(args.z3).is_file() else shutil.which(args.z3)
    if z3 is not None:
        for index, record in enumerate((binop, reduction)):
            result = validator.validate_record(record, str(z3), None, index)
            assert result["formal_status"] == "proved", result
            assert result["formal_template_domain"] == "transaction-template-v1"
            assert result["formal_template_count"] == expected_template_counts[record["marker"]]
            assert len(result["formal_instances"]) == expected_instance_counts[record["marker"]]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
