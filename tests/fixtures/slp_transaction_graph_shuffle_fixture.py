#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], stdout: Path | None = None) -> None:
    if stdout is None:
        subprocess.run(cmd, check=True)
        return
    with stdout.open("w") as handle:
        subprocess.run(cmd, check=True, stdout=handle)


def load_first_json(path: Path) -> dict:
    return json.loads(path.read_text())[0]


def load_first_jsonl(path: Path) -> dict:
    return json.loads(path.read_text().splitlines()[0])


def contains_op(value, op: str) -> bool:
    if isinstance(value, dict):
        if value.get("op") == op:
            return True
        return any(contains_op(child, op) for child in value.get("args", []))
    if isinstance(value, list):
        return any(contains_op(child, op) for child in value)
    return False


def mem_load_address_symbols(value) -> list[str]:
    symbols: list[str] = []
    if isinstance(value, dict):
        if value.get("op") == "mem_load":
            args = value.get("args", [])
            if len(args) == 2 and isinstance(args[1], dict) and args[1].get("op") == "var":
                symbols.append(args[1].get("name"))
        for child in value.get("args", []):
            symbols.extend(mem_load_address_symbols(child))
    elif isinstance(value, list):
        for child in value:
            symbols.extend(mem_load_address_symbols(child))
    return symbols


CASE_FIXTURES = {
    "ast-slp-transaction-graph-shuffle": "tests/fixtures/slp_transaction_graph_shuffle_snippet.cpp",
    "ast-slp-transaction-graph-static-pack-index": "tests/fixtures/slp_transaction_graph_static_pack_index_snippet.cpp",
    "ast-slp-transaction-graph-static-helper-pack-index": "tests/fixtures/slp_transaction_graph_static_helper_pack_index_snippet.cpp",
    "ast-slp-transaction-graph-helper-node-chain": "tests/fixtures/slp_transaction_graph_helper_node_chain_snippet.cpp",
    "ast-slp-transaction-graph-shuffle-reorder": "tests/fixtures/slp_transaction_graph_shuffle_reorder_snippet.cpp",
    "ast-slp-transaction-graph-shuffle-two-input": "tests/fixtures/slp_transaction_graph_shuffle_two_input_snippet.cpp",
    "ast-slp-transaction-graph-shuffle-two-input-reorder": "tests/fixtures/slp_transaction_graph_shuffle_two_input_reorder_snippet.cpp",
    "ast-slp-scalable-transaction-graph-shuffle": "tests/fixtures/slp_scalable_transaction_graph_shuffle_reorder_snippet.cpp",
    "ast-slp-scalable-transaction-graph-shuffle-two-input": "tests/fixtures/slp_scalable_transaction_graph_shuffle_two_input_snippet.cpp",
    "ast-slp-transaction-graph-extract-insert": "tests/fixtures/slp_transaction_graph_extract_insert_snippet.cpp",
    "ast-slp-transaction-graph-static-extract-insert-index": "tests/fixtures/slp_transaction_graph_static_extract_insert_index_snippet.cpp",
    "ast-slp-transaction-graph-extract-insert-reorder": "tests/fixtures/slp_transaction_graph_extract_insert_reorder_snippet.cpp",
    "ast-slp-scalable-transaction-graph-extract-insert": "tests/fixtures/slp_scalable_transaction_graph_extract_insert_snippet.cpp",
    "ast-slp-transaction-graph-memory-pack": "tests/fixtures/slp_transaction_graph_memory_pack_snippet.cpp",
    "ast-slp-scalable-transaction-graph-memory-pack": "tests/fixtures/slp_scalable_transaction_graph_memory_pack_snippet.cpp",
    "ast-slp-scalable-transaction-graph-memory-gather": "tests/fixtures/slp_scalable_transaction_graph_memory_gather_snippet.cpp",
    "ast-slp-scalable-transaction-graph-masked-memory": "tests/fixtures/slp_scalable_transaction_graph_masked_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-symbolic-mask-index-memory": "tests/fixtures/slp_scalable_transaction_graph_symbolic_mask_index_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-guarded-symbolic-mask-index-memory": "tests/fixtures/slp_scalable_transaction_graph_guarded_symbolic_mask_index_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-symbolic-undef-passthru-memory": "tests/fixtures/slp_scalable_transaction_graph_symbolic_undef_passthru_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-implicit-undef-passthru-memory": "tests/fixtures/slp_scalable_transaction_graph_implicit_undef_passthru_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-mask-provenance-memory": "tests/fixtures/slp_scalable_transaction_graph_mask_provenance_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-mask-tuple-memory": "tests/fixtures/slp_scalable_transaction_graph_mask_tuple_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-rich-mask-tuple-memory": "tests/fixtures/slp_scalable_transaction_graph_rich_mask_tuple_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-helper-mask-memory": "tests/fixtures/slp_scalable_transaction_graph_helper_mask_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-masked-gather-memory": "tests/fixtures/slp_scalable_transaction_graph_masked_gather_memory_snippet.cpp",
    "ast-slp-scalable-transaction-graph-store-sink": "tests/fixtures/slp_scalable_transaction_graph_store_sink_snippet.cpp",
    "ast-slp-scalable-transaction-graph-masked-store-sink": "tests/fixtures/slp_scalable_transaction_graph_masked_store_sink_snippet.cpp",
    "ast-slp-scalable-transaction-graph-gather-scatter-memory": "tests/fixtures/slp_scalable_transaction_graph_gather_scatter_memory_snippet.cpp",
    "ast-slp-transaction-graph-memory-pack-reorder": "tests/fixtures/slp_transaction_graph_memory_pack_reorder_snippet.cpp",
    "ast-slp-transaction-graph-memory-pack-extract-insert": "tests/fixtures/slp_transaction_graph_memory_pack_extract_insert_snippet.cpp",
    "ast-slp-transaction-graph-memory-gather": "tests/fixtures/slp_transaction_graph_memory_pack_noncontiguous_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-gather-index": "tests/fixtures/slp_transaction_graph_symbolic_gather_index_snippet.cpp",
    "ast-slp-transaction-graph-masked-symbolic-gather-index": "tests/fixtures/slp_transaction_graph_masked_symbolic_gather_index_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-gather-index-constant": "tests/fixtures/slp_transaction_graph_symbolic_gather_index_constant_snippet.cpp",
    "ast-slp-transaction-graph-memory-gather-reorder": "tests/fixtures/slp_transaction_graph_memory_gather_reorder_snippet.cpp",
    "ast-slp-transaction-graph-memory-gather-shuffle": "tests/fixtures/slp_transaction_graph_memory_gather_shuffle_snippet.cpp",
    "ast-slp-transaction-graph-memory-gather-extract-insert": "tests/fixtures/slp_transaction_graph_memory_gather_extract_insert_snippet.cpp",
    "ast-slp-transaction-graph-store-sink": "tests/fixtures/slp_transaction_graph_store_sink_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-store-sink": "tests/fixtures/slp_transaction_graph_symbolic_store_sink_snippet.cpp",
    "ast-slp-transaction-graph-masked-symbolic-store-sink": "tests/fixtures/slp_transaction_graph_masked_symbolic_store_sink_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-store-sink-constant": "tests/fixtures/slp_transaction_graph_symbolic_store_sink_constant_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-store-duplicate-term": "tests/fixtures/slp_transaction_graph_symbolic_store_duplicate_term_snippet.cpp",
    "ast-slp-transaction-graph-store-scatter": "tests/fixtures/slp_transaction_graph_store_scatter_snippet.cpp",
    "ast-slp-transaction-graph-load-store-memory": "tests/fixtures/slp_transaction_graph_load_store_memory_snippet.cpp",
    "ast-slp-transaction-graph-load-store-mayalias-guard": "tests/fixtures/slp_transaction_graph_load_store_mayalias_guard_snippet.cpp",
    "ast-slp-transaction-graph-load-store-aa-guard": "tests/fixtures/slp_transaction_graph_load_store_alias_analysis_guard_snippet.cpp",
    "ast-slp-transaction-graph-gather-scatter-memory": "tests/fixtures/slp_transaction_graph_gather_scatter_memory_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-gather-store-noalias": "tests/fixtures/slp_transaction_graph_symbolic_gather_store_noalias_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-same-base-load-store": "tests/fixtures/slp_transaction_graph_symbolic_same_base_load_store_snippet.cpp",
    "ast-slp-transaction-graph-masked-load-store-memory": "tests/fixtures/slp_transaction_graph_masked_load_store_memory_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-undef-passthru-memory": "tests/fixtures/slp_transaction_graph_symbolic_undef_passthru_memory_snippet.cpp",
    "ast-slp-transaction-graph-implicit-undef-passthru-memory": "tests/fixtures/slp_transaction_graph_implicit_undef_passthru_memory_snippet.cpp",
    "ast-slp-transaction-graph-passthru-alias-memory": "tests/fixtures/slp_transaction_graph_passthru_alias_memory_snippet.cpp",
    "ast-slp-transaction-graph-helper-passthru-memory": "tests/fixtures/slp_transaction_graph_helper_passthru_memory_snippet.cpp",
    "ast-slp-transaction-graph-undef-passthru-alias-memory": "tests/fixtures/slp_transaction_graph_undef_passthru_alias_memory_snippet.cpp",
    "ast-slp-transaction-graph-helper-undef-passthru-memory": "tests/fixtures/slp_transaction_graph_helper_undef_passthru_memory_snippet.cpp",
    "ast-slp-transaction-graph-static-mask-index-memory": "tests/fixtures/slp_transaction_graph_static_mask_index_memory_snippet.cpp",
    "ast-slp-transaction-graph-variable-mask-index-memory": "tests/fixtures/slp_transaction_graph_masked_memory_variable_mask_snippet.cpp",
    "ast-slp-transaction-graph-symbolic-mask-index-memory": "tests/fixtures/slp_transaction_graph_symbolic_mask_index_memory_snippet.cpp",
    "ast-slp-transaction-graph-static-symbolic-mask-index-memory": "tests/fixtures/slp_transaction_graph_static_symbolic_mask_index_memory_snippet.cpp",
    "ast-slp-transaction-graph-guarded-symbolic-mask-index-memory": "tests/fixtures/slp_transaction_graph_guarded_symbolic_mask_index_memory_snippet.cpp",
    "ast-slp-transaction-graph-static-memory-index": "tests/fixtures/slp_transaction_graph_static_memory_index_snippet.cpp",
    "ast-slp-transaction-graph-masked-gather-scatter-memory": "tests/fixtures/slp_transaction_graph_masked_gather_scatter_memory_snippet.cpp",
    "ast-slp-transaction-graph-mask-provenance-memory": "tests/fixtures/slp_transaction_graph_mask_provenance_memory_snippet.cpp",
    "ast-slp-transaction-graph-alias-mask-memory": "tests/fixtures/slp_transaction_graph_alias_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-split-mask-assignment-memory": "tests/fixtures/slp_transaction_graph_split_mask_assignment_memory_snippet.cpp",
    "ast-slp-transaction-graph-branch-mask-memory": "tests/fixtures/slp_transaction_graph_branch_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-branch-alias-mask-memory": "tests/fixtures/slp_transaction_graph_branch_alias_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-nested-branch-mask-memory": "tests/fixtures/slp_transaction_graph_nested_branch_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-branch-store-mask-memory": "tests/fixtures/slp_transaction_graph_branch_store_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-generalized-mask-syntax-memory": "tests/fixtures/slp_transaction_graph_generalized_mask_syntax_memory_snippet.cpp",
    "ast-slp-transaction-graph-helper-mask-memory": "tests/fixtures/slp_transaction_graph_helper_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-helper-opaque-mask-memory": "tests/fixtures/slp_transaction_graph_helper_unresolved_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-helper-unresolved-opaque-slice": "tests/fixtures/slp_transaction_graph_helper_unresolved_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-indexed-slice": "tests/fixtures/slp_transaction_graph_helper_indexed_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-default-args-slice": "tests/fixtures/slp_transaction_graph_helper_default_args_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-simple-multi-return-slice": "tests/fixtures/slp_transaction_graph_helper_simple_multi_return_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-opaque-multi-return-slice": "tests/fixtures/slp_transaction_graph_helper_multi_return_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-deep-chain-slice": "tests/fixtures/slp_transaction_graph_helper_deep_chain_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-depth-limit-slice": "tests/fixtures/slp_transaction_graph_helper_depth_limit_slice_snippet.cpp",
    "ast-slp-transaction-graph-helper-boolean-mask-memory": "tests/fixtures/slp_transaction_graph_helper_boolean_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-nested-helper-memory": "tests/fixtures/slp_transaction_graph_nested_helper_memory_snippet.cpp",
    "ast-slp-transaction-graph-helper-store-sink": "tests/fixtures/slp_transaction_graph_helper_store_sink_snippet.cpp",
    "ast-slp-transaction-graph-boolean-mask-memory": "tests/fixtures/slp_transaction_graph_boolean_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-rich-mask-memory": "tests/fixtures/slp_transaction_graph_rich_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-normalized-mask-memory": "tests/fixtures/slp_transaction_graph_normalized_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-guarded-temp-mask-memory": "tests/fixtures/slp_transaction_graph_guarded_temp_mask_memory_snippet.cpp",
    "ast-slp-transaction-graph-guarded-masked-load-store-memory": "tests/fixtures/slp_transaction_graph_guarded_masked_load_store_memory_snippet.cpp",
    "ast-slp-transaction-graph-guarded-masked-gather-scatter-memory": "tests/fixtures/slp_transaction_graph_guarded_masked_gather_scatter_memory_snippet.cpp",
}

def prove_case(repo: Path, work: Path, miner: Path, z3: Path, stem: str, fixture: str) -> tuple[dict, dict, dict]:
    start = time.monotonic()
    print(f"[shuffle-fixture] start {stem}", flush=True)
    findings = work / f"{stem}-findings.json"
    inferred = work / f"{stem}.jsonl"
    validated = work / f"{stem}-validated.jsonl"
    formalization = work / f"{stem}-formalization.json"
    run([str(miner), "--registry", str(repo / "constraints/pass_constraints.json"), str(repo / fixture), "--", "-std=c++17"], findings)
    run([
        sys.executable,
        str(repo / "tools/cv-infer-optimization-intent.py"),
        "--findings",
        str(findings),
        "--format",
        "jsonl",
        "--min-confidence",
        "high",
        "--out",
        str(inferred),
        "--require-marker",
        "probe.slp.vectorize-binop",
    ])
    run([
        sys.executable,
        str(repo / "tools/cv-validate-intent-candidates.py"),
        "--z3",
        str(z3),
        "--input",
        str(inferred),
        "--out",
        str(validated),
    ])
    run([
        sys.executable,
        str(repo / "tools/cv-verify-transaction-formalization.py"),
        "--input",
        str(validated),
        "--out",
        str(formalization),
        "--require-clean",
        "--require-provenance-complete",
    ])
    elapsed = time.monotonic() - start
    print(f"[shuffle-fixture] done {stem} ({elapsed:.2f}s)", flush=True)
    return load_first_json(findings), load_first_jsonl(validated), json.loads(formalization.read_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--miner", type=Path, required=True)
    parser.add_argument("--z3", type=Path, required=True)
    parser.add_argument(
        "--prove-only",
        action="append",
        default=[],
        help="Comma-separated stem list to run through prove_case without the detailed inline assertions.",
    )
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    prove_only = [
        stem.strip()
        for value in args.prove_only
        for stem in value.split(",")
        if stem.strip()
    ]
    if prove_only:
        unknown = [stem for stem in prove_only if stem not in CASE_FIXTURES]
        if unknown:
            raise SystemExit(f"unknown --prove-only stem(s): {', '.join(unknown)}")
        for stem in prove_only:
            _finding, validated, formalization = prove_case(
                args.repo,
                args.work,
                args.miner,
                args.z3,
                stem,
                CASE_FIXTURES[stem],
            )
            assert validated["proof_status"] == "proved"
            assert formalization["summary"]["provenance_coverage"] == {"passed": 1}
        return

    fixed, fixedv, fixedver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-shuffle",
        "tests/fixtures/slp_transaction_graph_shuffle_snippet.cpp",
    )
    fg = fixed["optimization_transaction"]["transaction_graph"]
    ff = fixedv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in fg["nodes"]] == ["add", "shuffle", "xor"]
    assert fg["nodes"][1]["kind"] == "shuffle"
    assert fg["nodes"][1]["mask"] == [1, 0, 3, 2]
    assert fixedv["proof_status"] == "proved"
    assert ff["after"]["op"] == "vxor"
    assert ff["after"]["args"][0]["op"] == "vshuffle"
    assert fixedv["evidence"]["formal_parameters"]["transaction.graph.shuffle_mask_frame"] == "packed-vector-frame"
    assert fixedver["summary"]["provenance_coverage"] == {"passed": 1}

    static_pack, static_packv, static_packver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-static-pack-index",
        "tests/fixtures/slp_transaction_graph_static_pack_index_snippet.cpp",
    )
    spg = static_pack["optimization_transaction"]["transaction_graph"]
    spf = static_packv["intent_candidate"]["formal"]
    assert [operand["name"] for operand in spg["operands"]] == ["a", "b", "c", "d"]
    assert [node["opcode"] for node in spg["nodes"]] == ["add", "mul", "xor"]
    assert static_packv["proof_status"] == "proved"
    assert spf["after"]["op"] == "vxor"
    assert spf["after"]["args"][0]["op"] == "vmul"
    assert static_packver["summary"]["provenance_coverage"] == {"passed": 1}

    static_helper_pack, static_helper_packv, static_helper_packver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-static-helper-pack-index",
        "tests/fixtures/slp_transaction_graph_static_helper_pack_index_snippet.cpp",
    )
    shpg = static_helper_pack["optimization_transaction"]["transaction_graph"]
    shpf = static_helper_packv["intent_candidate"]["formal"]
    assert [operand["name"] for operand in shpg["operands"]] == ["a", "b", "c"]
    assert [node["opcode"] for node in shpg["nodes"]] == ["add", "mul"]
    assert static_helper_packv["proof_status"] == "proved"
    assert shpf["after"]["op"] == "vmul"
    assert static_helper_packver["summary"]["provenance_coverage"] == {"passed": 1}

    helper_node_chain, helper_node_chainv, helper_node_chainver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-node-chain",
        "tests/fixtures/slp_transaction_graph_helper_node_chain_snippet.cpp",
    )
    hncg = helper_node_chain["optimization_transaction"]["transaction_graph"]
    hncf = helper_node_chainv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in hncg["nodes"]] == ["add", "xor"]
    assert helper_node_chainv["proof_status"] == "proved"
    assert hncf["after"]["op"] == "vxor"
    assert hncf["after"]["args"][0]["op"] == "vadd"
    assert helper_node_chainver["summary"]["provenance_coverage"] == {"passed": 1}

    fixed_reorder, fixed_reorderv, fixed_reorderver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-shuffle-reorder",
        "tests/fixtures/slp_transaction_graph_shuffle_reorder_snippet.cpp",
    )
    frg = fixed_reorder["optimization_transaction"]["transaction_graph"]
    frf = fixed_reorderv["intent_candidate"]["formal"]
    assert fixed_reorder["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert [node["opcode"] for node in frg["nodes"]] == ["add", "shuffle", "xor"]
    assert frg["nodes"][1]["mask"] == [1, 0, 3, 2]
    assert fixed_reorderv["proof_status"] == "proved"
    assert fixed_reorderv["evidence"]["formal_parameters"]["transaction.graph.shuffle_mask_frame"] == "packed-vector-frame"
    assert frf["after"]["op"] == "vshuffle"
    assert contains_op(frf["after"], "vshuffle")
    assert fixed_reorderver["summary"]["provenance_coverage"] == {"passed": 1}

    blend, blendv, blendver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-shuffle-two-input",
        "tests/fixtures/slp_transaction_graph_shuffle_two_input_snippet.cpp",
    )
    bg = blend["optimization_transaction"]["transaction_graph"]
    bf = blendv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in bg["nodes"]] == ["shuffle", "xor"]
    assert bg["nodes"][0]["mask"] == [0, 5, 2, 7]
    assert blendv["proof_status"] == "proved"
    assert bf["after"]["args"][0]["op"] == "vshuffle"
    assert len(bf["after"]["args"][0]["args"]) == 2
    assert blendver["summary"]["provenance_coverage"] == {"passed": 1}

    blend_reorder, blend_reorderv, blend_reorderver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-shuffle-two-input-reorder",
        "tests/fixtures/slp_transaction_graph_shuffle_two_input_reorder_snippet.cpp",
    )
    brg = blend_reorder["optimization_transaction"]["transaction_graph"]
    brf = blend_reorderv["intent_candidate"]["formal"]
    assert blend_reorder["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert [node["opcode"] for node in brg["nodes"]] == ["shuffle", "xor"]
    assert brg["nodes"][0]["mask"] == [0, 5, 2, 7]
    assert blend_reorderv["proof_status"] == "proved"
    assert brf["after"]["op"] == "vshuffle"
    assert contains_op(brf["after"], "vshuffle")
    assert blend_reorderver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable, scalablev, scalablever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-shuffle",
        "tests/fixtures/slp_scalable_transaction_graph_shuffle_reorder_snippet.cpp",
    )
    sg = scalable["optimization_transaction"]["transaction_graph"]
    sf = scalablev["intent_candidate"]["formal"]
    assert scalable["optimization_transaction"]["scalable"] is True
    assert scalable["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert [node["opcode"] for node in sg["nodes"]] == ["add", "shuffle", "xor"]
    assert sg["nodes"][1]["base_mask"] == [1, 0, 3, 2]
    assert scalablev["proof_status"] == "proved"
    assert [item["vscale"] for item in scalablev["proof_instances"]] == [1, 2, 4]
    assert scalablev["evidence"]["formal_parameters"]["transaction.graph.shuffle_mask_frame"] == "packed-vector-frame"
    assert contains_op(sf["after"], "svshuffle")
    assert scalablever["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_two_input, scalable_two_inputv, scalable_two_inputver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-shuffle-two-input",
        "tests/fixtures/slp_scalable_transaction_graph_shuffle_two_input_snippet.cpp",
    )
    stig = scalable_two_input["optimization_transaction"]["transaction_graph"]
    stif = scalable_two_inputv["intent_candidate"]["formal"]
    shuffle_node = next(node for node in stig["nodes"] if node.get("kind") == "shuffle")
    assert scalable_two_input["optimization_transaction"]["scalable"] is True
    assert len(shuffle_node["operands"]) == 2
    assert shuffle_node["base_mask"] == [0, 5, 2, 7]
    assert scalable_two_inputv["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_two_inputv["proof_instances"]] == [1, 2, 4]
    assert contains_op(stif["before"], "svshuffle")
    assert scalable_two_inputv["evidence"]["formal_parameters"]["transaction.graph.shuffle_mask_frame"] == "packed-vector-frame"
    assert scalable_two_inputver["summary"]["provenance_coverage"] == {"passed": 1}

    extract_insert, extract_insertv, extract_insertver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-extract-insert",
        "tests/fixtures/slp_transaction_graph_extract_insert_snippet.cpp",
    )
    eig = extract_insert["optimization_transaction"]["transaction_graph"]
    eif = extract_insertv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in eig["nodes"]] == ["add", "extract", "insert", "xor"]
    assert eig["nodes"][1]["kind"] == "extract"
    assert eig["nodes"][1]["index"] == 1
    assert eig["nodes"][2]["kind"] == "insert"
    assert eig["nodes"][2]["index"] == 2
    assert extract_insertv["proof_status"] == "proved"
    assert eif["after"]["op"] == "vxor"
    assert contains_op(eif["after"], "vextract")
    assert contains_op(eif["after"], "vinsert")
    assert extract_insertv["evidence"]["formal_parameters"]["transaction.graph.lane_index_frame"] == "packed-vector-frame"
    assert extract_insertver["summary"]["provenance_coverage"] == {"passed": 1}

    static_extract_insert, static_extract_insertv, static_extract_insertver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-static-extract-insert-index",
        "tests/fixtures/slp_transaction_graph_static_extract_insert_index_snippet.cpp",
    )
    seig = static_extract_insert["optimization_transaction"]["transaction_graph"]
    seif = static_extract_insertv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in seig["nodes"]] == ["add", "extract", "insert", "xor"]
    assert [seig["nodes"][1]["index"], seig["nodes"][2]["index"]] == [1, 2]
    assert static_extract_insertv["proof_status"] == "proved"
    assert contains_op(seif["after"], "vextract")
    assert contains_op(seif["after"], "vinsert")
    assert static_extract_insertv["evidence"]["formal_parameters"]["transaction.graph.lane_index_frame"] == "packed-vector-frame"
    assert static_extract_insertver["summary"]["provenance_coverage"] == {"passed": 1}

    extract_insert_reorder, extract_insert_reorderv, extract_insert_reorderver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-extract-insert-reorder",
        "tests/fixtures/slp_transaction_graph_extract_insert_reorder_snippet.cpp",
    )
    eirg = extract_insert_reorder["optimization_transaction"]["transaction_graph"]
    eirf = extract_insert_reorderv["intent_candidate"]["formal"]
    assert extract_insert_reorder["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert [node["opcode"] for node in eirg["nodes"]] == ["add", "extract", "insert", "xor"]
    assert [eirg["nodes"][1]["index"], eirg["nodes"][2]["index"]] == [1, 2]
    assert extract_insert_reorderv["proof_status"] == "proved"
    assert contains_op(eirf["after"], "vextract")
    assert contains_op(eirf["after"], "vinsert")
    assert extract_insert_reorderv["evidence"]["formal_parameters"]["transaction.graph.lane_index_frame"] == "packed-vector-frame"
    assert extract_insert_reorderver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_extract_insert, scalable_extract_insertv, scalable_extract_insertver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-extract-insert",
        "tests/fixtures/slp_scalable_transaction_graph_extract_insert_snippet.cpp",
    )
    seig = scalable_extract_insert["optimization_transaction"]["transaction_graph"]
    seif = scalable_extract_insertv["intent_candidate"]["formal"]
    assert scalable_extract_insert["optimization_transaction"]["scalable"] is True
    assert [node["opcode"] for node in seig["nodes"]] == ["add", "extract", "insert", "xor"]
    assert [seig["nodes"][1]["index"], seig["nodes"][2]["index"]] == [1, 2]
    assert scalable_extract_insertv["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_extract_insertv["proof_instances"]] == [1, 2, 4]
    assert contains_op(seif["after"], "svextract")
    assert contains_op(seif["after"], "svinsert")
    assert scalable_extract_insertv["evidence"]["formal_parameters"]["transaction.graph.lane_index_frame"] == "packed-vector-frame"
    assert scalable_extract_insertver["summary"]["provenance_coverage"] == {"passed": 1}

    extract_insert_unresolved_path = args.work / "ast-slp-transaction-graph-extract-insert-unresolved-findings.json"
    run([
        str(args.miner),
        "--registry",
        str(args.repo / "constraints/pass_constraints.json"),
        str(args.repo / "tests/fixtures/slp_transaction_graph_extract_insert_unresolved_snippet.cpp"),
        "--",
        "-std=c++17",
    ], extract_insert_unresolved_path)
    extract_insert_unresolved = load_first_json(extract_insert_unresolved_path)
    eutx = extract_insert_unresolved["optimization_transaction"]
    assert "transaction_graph" not in eutx
    assert eutx["transaction_graph_absent_reasons"] == ["unresolved-extract-insert-index"]

    memory_pack, memory_packv, memory_packver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-pack",
        "tests/fixtures/slp_transaction_graph_memory_pack_snippet.cpp",
    )
    mpg = memory_pack["optimization_transaction"]["transaction_graph"]
    mpf = memory_packv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in mpg["nodes"]] == ["add", "xor"]
    assert any(operand.get("kind") == "memory-pack" for operand in mpg["operands"])
    memory_operand = next(operand for operand in mpg["operands"] if operand.get("kind") == "memory-pack")
    assert memory_operand["address_order"] == [0, 1, 2, 3]
    assert memory_operand["memory_contract"] == "contiguous-load-pack-v1"
    assert memory_operand["memory_safety_status"] == "complete"
    assert memory_operand["no_intervening_store"] is True
    assert memory_packv["proof_status"] == "proved"
    assert mpf["after"]["op"] == "vxor"
    assert memory_packv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "contiguous-load-pack-v1"
    assert memory_packv["evidence"]["formal_parameters"]["transaction.graph.memory_lane_frame"] == "packed-vector-frame"
    assert memory_packv["evidence"]["formal_parameters"]["transaction.graph.memory_safety_status"] == "complete"
    assert memory_packver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_memory_pack, scalable_memory_packv, scalable_memory_packver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-memory-pack",
        "tests/fixtures/slp_scalable_transaction_graph_memory_pack_snippet.cpp",
    )
    smpg = scalable_memory_pack["optimization_transaction"]["transaction_graph"]
    smpf = scalable_memory_packv["intent_candidate"]["formal"]
    assert scalable_memory_pack["optimization_transaction"]["scalable"] is True
    assert any(operand.get("kind") == "memory-pack" for operand in smpg["operands"])
    scalable_memory_operand = next(operand for operand in smpg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_memory_operand["address_order"] == [0, 1, 2, 3]
    assert scalable_memory_operand["load_order"] == [0, 1, 2, 3]
    assert scalable_memory_operand["memory_contract"] == "contiguous-load-pack-v1"
    assert scalable_memory_operand["memory_safety_status"] == "complete"
    assert scalable_memory_packv["proof_status"] == "proved"
    assert smpf["equivalence"] == "vector-result"
    assert contains_op(smpf["after"], "svxor")
    assert scalable_memory_packv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "contiguous-load-pack-v1"
    assert scalable_memory_packv["evidence"]["formal_parameters"]["transaction.graph.scalable_memory_pack"] is True
    assert scalable_memory_packver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_memory_gather, scalable_memory_gatherv, scalable_memory_gatherver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-memory-gather",
        "tests/fixtures/slp_scalable_transaction_graph_memory_gather_snippet.cpp",
    )
    smgg = scalable_memory_gather["optimization_transaction"]["transaction_graph"]
    smgf = scalable_memory_gatherv["intent_candidate"]["formal"]
    assert scalable_memory_gather["optimization_transaction"]["scalable"] is True
    scalable_gather_operand = next(operand for operand in smgg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_gather_operand["address_order"] == [0, 2, 4, 6]
    assert scalable_gather_operand["load_order"] == [0, 2, 4, 6]
    assert scalable_gather_operand["address_stride"] == 2
    assert scalable_gather_operand["memory_contract"] == "static-gather-pack-v1"
    assert scalable_gather_operand["memory_safety_status"] == "complete"
    assert scalable_memory_gatherv["proof_status"] == "proved"
    assert smgf["equivalence"] == "vector-result"
    assert contains_op(smgf["after"], "svxor")
    assert scalable_memory_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert scalable_memory_gatherv["evidence"]["formal_parameters"]["transaction.graph.scalable_memory_pack"] is True
    assert scalable_memory_gatherver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_masked_memory, scalable_masked_memoryv, scalable_masked_memoryver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-masked-memory",
        "tests/fixtures/slp_scalable_transaction_graph_masked_memory_snippet.cpp",
    )
    smmg = scalable_masked_memory["optimization_transaction"]["transaction_graph"]
    smmf = scalable_masked_memoryv["intent_candidate"]["formal"]
    assert scalable_masked_memory["optimization_transaction"]["scalable"] is True
    scalable_masked_operand = next(operand for operand in smmg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_masked_operand["masked"] is True
    assert scalable_masked_operand["mask_operand"] == "Mask"
    assert scalable_masked_operand["mask_order"] == [0, 1, 2, 3]
    assert scalable_masked_operand["passthru_operand"] == "Passthru"
    assert scalable_masked_operand["passthru_order"] == [0, 1, 2, 3]
    assert scalable_masked_operand["memory_contract"] == "masked-contiguous-load-pack-v1"
    assert scalable_masked_operand["memory_safety_status"] == "complete"
    assert scalable_masked_memoryv["proof_status"] == "proved"
    assert contains_op(smmf["after"], "svselect")
    assert contains_op(smmf["after"], "svicmp")
    assert scalable_masked_memoryv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_masked_memoryv["evidence"]["formal_parameters"]["transaction.graph.scalable_memory_pack"] is True
    assert scalable_masked_memoryv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_masked_memoryver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_symbolic_mask_index, scalable_symbolic_mask_indexv, scalable_symbolic_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-symbolic-mask-index-memory",
        "tests/fixtures/slp_scalable_transaction_graph_symbolic_mask_index_memory_snippet.cpp",
    )
    ssmig = scalable_symbolic_mask_index["optimization_transaction"]["transaction_graph"]
    ssmif = scalable_symbolic_mask_indexv["intent_candidate"]["formal"]
    assert scalable_symbolic_mask_index["optimization_transaction"]["scalable"] is True
    scalable_symbolic_mask_operand = next(operand for operand in ssmig["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_symbolic_mask_operand["masked"] is True
    assert scalable_symbolic_mask_operand["mask_order"] == [-1, 1, 2, 3]
    assert scalable_symbolic_mask_operand["mask_conditions"][0]["op"] == "indexed-mask"
    assert scalable_symbolic_mask_operand["mask_conditions"][0]["index"] == "Lane + 1"
    assert scalable_symbolic_mask_operand["mask_conditions"][0]["source"] == "Mask[Lane + 1]"
    assert ssmig["store_sinks"][0]["masked"] is True
    assert ssmig["store_sinks"][0]["mask_conditions"][0]["index"] == "Lane + 1"
    assert scalable_symbolic_mask_indexv["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_symbolic_mask_indexv["proof_instances"]] == [1, 2, 4]
    assert ssmif["equivalence"] == "observable-result"
    assert "Mask_Lane_plus_1" in ssmif["variables"]
    assert contains_op(ssmif["before"], "svindexed_mask")
    assert contains_op(ssmif["after"], "svindexed_mask")
    assert scalable_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_store_sink"] is True
    assert "transaction.graph.scalable_mask_tuple" not in scalable_symbolic_mask_indexv["evidence"]["formal_parameters"]
    assert scalable_symbolic_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_guarded_symbolic_mask_index, scalable_guarded_symbolic_mask_indexv, scalable_guarded_symbolic_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-guarded-symbolic-mask-index-memory",
        "tests/fixtures/slp_scalable_transaction_graph_guarded_symbolic_mask_index_memory_snippet.cpp",
    )
    sgsmig = scalable_guarded_symbolic_mask_index["optimization_transaction"]["transaction_graph"]
    sgsmif = scalable_guarded_symbolic_mask_indexv["intent_candidate"]["formal"]
    assert scalable_guarded_symbolic_mask_index["optimization_transaction"]["scalable"] is True
    scalable_guarded_symbolic_mask_operand = next(operand for operand in sgsmig["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_guarded_symbolic_mask_operand["masked"] is True
    assert scalable_guarded_symbolic_mask_operand["mask_order"] == [-1, 1, 2, 3]
    assert scalable_guarded_symbolic_mask_operand["mask_conditions"][0]["op"] == "indexed-mask"
    assert scalable_guarded_symbolic_mask_operand["mask_conditions"][0]["index"] == "(Lane & 3)"
    assert scalable_guarded_symbolic_mask_operand["mask_conditions"][0]["source"] == "Mask[(Lane & 3)]"
    assert sgsmig["store_sinks"][0]["masked"] is True
    assert sgsmig["store_sinks"][0]["mask_conditions"][0]["index"] == "(Lane & 3)"
    assert scalable_guarded_symbolic_mask_indexv["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_guarded_symbolic_mask_indexv["proof_instances"]] == [1, 2, 4]
    assert sgsmif["equivalence"] == "observable-result"
    assert "Mask_Lane_and_3" in sgsmif["variables"]
    assert contains_op(sgsmif["before"], "svindexed_mask")
    assert contains_op(sgsmif["after"], "svindexed_mask")
    assert scalable_guarded_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_guarded_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_guarded_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_store_sink"] is True
    assert "transaction.graph.scalable_mask_tuple" not in scalable_guarded_symbolic_mask_indexv["evidence"]["formal_parameters"]
    assert scalable_guarded_symbolic_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_symbolic_undef, scalable_symbolic_undefv, scalable_symbolic_undefver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-symbolic-undef-passthru-memory",
        "tests/fixtures/slp_scalable_transaction_graph_symbolic_undef_passthru_memory_snippet.cpp",
    )
    ssug = scalable_symbolic_undef["optimization_transaction"]["transaction_graph"]
    ssuf = scalable_symbolic_undefv["intent_candidate"]["formal"]
    assert scalable_symbolic_undef["optimization_transaction"]["scalable"] is True
    scalable_symbolic_operand = next(operand for operand in ssug["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_symbolic_operand["masked"] is True
    assert scalable_symbolic_operand["mask_operand"] == "Mask"
    assert scalable_symbolic_operand["mask_order"] == [0, 1, 2, 3]
    assert scalable_symbolic_operand["passthru_kind"] == "symbolic-undef"
    assert scalable_symbolic_operand["passthru_symbols"] == ["a_undef0", "a_undef1", "a_undef2", "a_undef3"]
    assert scalable_symbolic_undefv["proof_status"] == "proved"
    assert ssuf["equivalence"] == "vector-result"
    assert "a_undef" in ssuf["variables"]
    assert "a_undef" in ssuf["poison_variables"]
    assert contains_op(ssuf["after"], "svselect")
    assert contains_op(ssuf["after"], "svicmp")
    assert scalable_symbolic_undefv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_symbolic_undefv["evidence"]["formal_parameters"]["transaction.graph.scalable_memory_pack"] is True
    assert scalable_symbolic_undefv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_symbolic_undefver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_implicit_undef, scalable_implicit_undefv, scalable_implicit_undefver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-implicit-undef-passthru-memory",
        "tests/fixtures/slp_scalable_transaction_graph_implicit_undef_passthru_memory_snippet.cpp",
    )
    siug = scalable_implicit_undef["optimization_transaction"]["transaction_graph"]
    siuf = scalable_implicit_undefv["intent_candidate"]["formal"]
    scalable_implicit_operand = next(operand for operand in siug["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_implicit_operand["masked"] is True
    assert scalable_implicit_operand["mask_operand"] == "Mask"
    assert scalable_implicit_operand["mask_order"] == [0, 1, 2, 3]
    assert scalable_implicit_operand["passthru_kind"] == "symbolic-undef"
    assert scalable_implicit_operand["passthru_symbols"] == ["a_undef0", "a_undef1", "a_undef2", "a_undef3"]
    assert scalable_implicit_undefv["proof_status"] == "proved"
    assert siuf["equivalence"] == "vector-result"
    assert "a_undef" in siuf["variables"]
    assert "a_undef" in siuf["poison_variables"]
    assert contains_op(siuf["after"], "svselect")
    assert contains_op(siuf["after"], "svicmp")
    assert scalable_implicit_undefv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_implicit_undefv["evidence"]["formal_parameters"]["transaction.graph.scalable_memory_pack"] is True
    assert scalable_implicit_undefv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_implicit_undefver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_mask_provenance, scalable_mask_provenancev, scalable_mask_provenancever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-mask-provenance-memory",
        "tests/fixtures/slp_scalable_transaction_graph_mask_provenance_memory_snippet.cpp",
    )
    smpmg = scalable_mask_provenance["optimization_transaction"]["transaction_graph"]
    smpmf = scalable_mask_provenancev["intent_candidate"]["formal"]
    assert scalable_mask_provenance["optimization_transaction"]["scalable"] is True
    scalable_mask_provenance_operand = next(operand for operand in smpmg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_mask_provenance_operand["masked"] is True
    assert scalable_mask_provenance_operand["mask_operand"] == ""
    assert len(scalable_mask_provenance_operand["mask_conditions"]) == 4
    assert scalable_mask_provenance_operand["mask_conditions"][0]["predicate"] == "eq"
    assert scalable_mask_provenance_operand["mask_conditions"][0]["lhs"] == "Cmp[0]"
    assert scalable_mask_provenance_operand["mask_conditions"][0]["rhs"] == "Passthru[0]"
    assert len(spmg_store_conditions := smpmg["store_sinks"][0]["mask_conditions"]) == 4
    assert spmg_store_conditions[0]["predicate"] == "eq"
    assert scalable_mask_provenancev["proof_status"] == "proved"
    assert smpmf["equivalence"] == "observable-result"
    assert "Cmp" in smpmf["variables"]
    assert "Passthru" in smpmf["variables"]
    assert contains_op(smpmf["before"], "svselect")
    assert contains_op(smpmf["before"], "svicmp")
    assert contains_op(smpmf["after"], "svselect")
    assert contains_op(smpmf["after"], "svicmp")
    assert scalable_mask_provenancev["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_mask_provenancev["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_mask_provenancev["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_store_sink"] is True
    assert "transaction.graph.scalable_mask_tuple" not in scalable_mask_provenancev["evidence"]["formal_parameters"]
    assert scalable_mask_provenancever["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_mask_tuple, scalable_mask_tuplev, scalable_mask_tuplever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-mask-tuple-memory",
        "tests/fixtures/slp_scalable_transaction_graph_mask_tuple_memory_snippet.cpp",
    )
    smtmg = scalable_mask_tuple["optimization_transaction"]["transaction_graph"]
    smtmf = scalable_mask_tuplev["intent_candidate"]["formal"]
    assert scalable_mask_tuple["optimization_transaction"]["scalable"] is True
    scalable_mask_tuple_operand = next(operand for operand in smtmg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_mask_tuple_operand["masked"] is True
    assert scalable_mask_tuple_operand["mask_operand"] == ""
    assert len(scalable_mask_tuple_operand["mask_conditions"]) == 4
    assert scalable_mask_tuple_operand["mask_conditions"][0]["predicate"] == "eq"
    assert scalable_mask_tuple_operand["mask_conditions"][1]["predicate"] == "ne"
    assert scalable_mask_tuple_operand["mask_conditions"][2]["op"] == "and"
    assert scalable_mask_tuple_operand["mask_conditions"][3]["op"] == "not"
    assert len(smtmg["store_sinks"][0]["mask_conditions"]) == 4
    assert scalable_mask_tuplev["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_mask_tuplev["proof_instances"]] == [1, 2, 4]
    assert smtmf["equivalence"] == "observable-result"
    assert "Cmp" in smtmf["variables"]
    assert "Passthru" in smtmf["variables"]
    assert contains_op(smtmf["before"], "svmask_tuple")
    assert contains_op(smtmf["after"], "svmask_tuple")
    assert scalable_mask_tuplev["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_mask_tuplev["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_mask_tuplev["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_store_sink"] is True
    assert scalable_mask_tuplev["evidence"]["formal_parameters"]["transaction.graph.scalable_mask_tuple"] is True
    assert scalable_mask_tuplever["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_rich_mask_tuple, scalable_rich_mask_tuplev, scalable_rich_mask_tuplever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-rich-mask-tuple-memory",
        "tests/fixtures/slp_scalable_transaction_graph_rich_mask_tuple_memory_snippet.cpp",
    )
    srmtg = scalable_rich_mask_tuple["optimization_transaction"]["transaction_graph"]
    srmtf = scalable_rich_mask_tuplev["intent_candidate"]["formal"]
    scalable_rich_mask_tuple_operand = next(operand for operand in srmtg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_rich_mask_tuple_operand["masked"] is True
    assert len(scalable_rich_mask_tuple_operand["mask_conditions"]) == 4
    assert scalable_rich_mask_tuple_operand["mask_conditions"][0]["op"] == "select"
    assert scalable_rich_mask_tuple_operand["mask_conditions"][1]["op"] == "const"
    assert scalable_rich_mask_tuple_operand["mask_conditions"][2]["predicate"] == "ne"
    assert scalable_rich_mask_tuple_operand["mask_conditions"][3]["rhs"] == "Mask[3]"
    assert scalable_rich_mask_tuplev["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_rich_mask_tuplev["proof_instances"]] == [1, 2, 4]
    assert contains_op(srmtf["before"], "svmask_tuple")
    assert contains_op(srmtf["after"], "svmask_tuple")
    assert scalable_rich_mask_tuplev["evidence"]["formal_parameters"]["transaction.graph.scalable_mask_tuple"] is True
    assert scalable_rich_mask_tuplever["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_helper_mask, scalable_helper_maskv, scalable_helper_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-helper-mask-memory",
        "tests/fixtures/slp_scalable_transaction_graph_helper_mask_memory_snippet.cpp",
    )
    shmg = scalable_helper_mask["optimization_transaction"]["transaction_graph"]
    shmf = scalable_helper_maskv["intent_candidate"]["formal"]
    assert scalable_helper_mask["optimization_transaction"]["scalable"] is True
    scalable_helper_operand = next(operand for operand in shmg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_helper_operand["masked"] is True
    assert scalable_helper_operand["mask_operand"] == ""
    assert len(scalable_helper_operand["mask_conditions"]) == 4
    assert scalable_helper_operand["mask_conditions"][0]["predicate"] == "eq"
    assert "CreateICmp" in scalable_helper_operand["mask_conditions"][0]["source"]
    assert scalable_helper_maskv["proof_status"] == "proved"
    assert shmf["equivalence"] == "vector-result"
    assert "Cmp" in shmf["variables"]
    assert "Passthru" in shmf["variables"]
    assert contains_op(shmf["after"], "svselect")
    assert contains_op(shmf["after"], "svicmp")
    assert scalable_helper_maskv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_helper_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_masked_gather, scalable_masked_gatherv, scalable_masked_gatherver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-masked-gather-memory",
        "tests/fixtures/slp_scalable_transaction_graph_masked_gather_memory_snippet.cpp",
    )
    smgmg = scalable_masked_gather["optimization_transaction"]["transaction_graph"]
    smgmf = scalable_masked_gatherv["intent_candidate"]["formal"]
    scalable_masked_gather_operand = next(operand for operand in smgmg["operands"] if operand.get("kind") == "memory-pack")
    assert scalable_masked_gather["optimization_transaction"]["scalable"] is True
    assert scalable_masked_gather_operand["masked"] is True
    assert scalable_masked_gather_operand["address_order"] == [0, 2, 4, 6]
    assert scalable_masked_gather_operand["address_stride"] == 2
    assert scalable_masked_gather_operand["mask_order"] == [0, 1, 2, 3]
    assert scalable_masked_gather_operand["passthru_order"] == [0, 1, 2, 3]
    assert scalable_masked_gather_operand["memory_contract"] == "masked-static-gather-pack-v1"
    assert scalable_masked_gatherv["proof_status"] == "proved"
    assert contains_op(smgmf["after"], "svselect")
    assert scalable_masked_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-static-gather-pack-v1"
    assert scalable_masked_gatherv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_memory_pack"] is True
    assert scalable_masked_gatherver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_store_sink, scalable_store_sinkv, scalable_store_sinkver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-store-sink",
        "tests/fixtures/slp_scalable_transaction_graph_store_sink_snippet.cpp",
    )
    sssg = scalable_store_sink["optimization_transaction"]["transaction_graph"]
    sssf = scalable_store_sinkv["intent_candidate"]["formal"]
    assert scalable_store_sink["optimization_transaction"]["scalable"] is True
    assert len(sssg["store_sinks"]) == 1
    assert sssg["store_sinks"][0]["store_contract"] == "contiguous-store-pack-v1"
    assert sssg["store_sinks"][0]["store_order"] == [0, 1, 2, 3]
    assert scalable_store_sinkv["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_store_sinkv["proof_instances"]] == [1, 2, 4]
    assert sssf["equivalence"] == "observable-result"
    assert scalable_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.scalable_store_sink"] is True
    assert scalable_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.memory_model"] == "bounded-scalable-lane-memory-v1"
    assert scalable_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "contiguous-store-pack-v1"
    assert scalable_store_sinkver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_masked_store_sink, scalable_masked_store_sinkv, scalable_masked_store_sinkver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-masked-store-sink",
        "tests/fixtures/slp_scalable_transaction_graph_masked_store_sink_snippet.cpp",
    )
    smssg = scalable_masked_store_sink["optimization_transaction"]["transaction_graph"]
    smssf = scalable_masked_store_sinkv["intent_candidate"]["formal"]
    assert scalable_masked_store_sink["optimization_transaction"]["scalable"] is True
    assert len(smssg["store_sinks"]) == 1
    assert smssg["store_sinks"][0]["masked"] is True
    assert smssg["store_sinks"][0]["mask_operand"] == "Mask"
    assert smssg["store_sinks"][0]["mask_order"] == [0, 1, 2, 3]
    assert smssg["store_sinks"][0]["store_contract"] == "masked-contiguous-store-pack-v1"
    assert scalable_masked_store_sinkv["proof_status"] == "proved"
    assert [item["vscale"] for item in scalable_masked_store_sinkv["proof_instances"]] == [1, 2, 4]
    assert smssf["equivalence"] == "observable-result"
    assert contains_op(smssf["before"], "svselect")
    assert contains_op(smssf["before"], "svicmp")
    assert contains_op(smssf["after"], "svselect")
    assert contains_op(smssf["after"], "svicmp")
    assert scalable_masked_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert scalable_masked_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.scalable_store_sink"] is True
    assert scalable_masked_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.scalable_masked_store_sink"] is True
    assert scalable_masked_store_sinkv["evidence"]["formal_parameters"]["transaction.graph.memory_model"] == "bounded-scalable-lane-memory-v1"
    assert scalable_masked_store_sinkver["summary"]["provenance_coverage"] == {"passed": 1}

    scalable_gather_scatter, scalable_gather_scatterv, scalable_gather_scatterver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-scalable-transaction-graph-gather-scatter-memory",
        "tests/fixtures/slp_scalable_transaction_graph_gather_scatter_memory_snippet.cpp",
    )
    sgsg = scalable_gather_scatter["optimization_transaction"]["transaction_graph"]
    sgsf = scalable_gather_scatterv["intent_candidate"]["formal"]
    assert scalable_gather_scatter["optimization_transaction"]["scalable"] is True
    assert next(operand for operand in sgsg["operands"] if operand.get("kind") == "memory-pack")["address_order"] == [0, 2, 4, 6]
    assert sgsg["store_sinks"][0]["store_order"] == [0, 2, 4, 6]
    assert sgsg["store_sinks"][0]["store_contract"] == "static-scatter-store-pack-v1"
    assert scalable_gather_scatterv["proof_status"] == "proved"
    assert sgsf["equivalence"] == "observable-result"
    assert scalable_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert scalable_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "static-scatter-store-pack-v1"
    assert scalable_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.scalable_store_sink"] is True
    assert scalable_gather_scatterver["summary"]["provenance_coverage"] == {"passed": 1}

    memory_pack_reorder, memory_pack_reorderv, memory_pack_reorderver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-pack-reorder",
        "tests/fixtures/slp_transaction_graph_memory_pack_reorder_snippet.cpp",
    )
    mprg = memory_pack_reorder["optimization_transaction"]["transaction_graph"]
    mprf = memory_pack_reorderv["intent_candidate"]["formal"]
    assert memory_pack_reorder["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert any(operand.get("kind") == "memory-pack" for operand in mprg["operands"])
    assert memory_pack_reorderv["proof_status"] == "proved"
    assert mprf["after"]["op"] == "vshuffle"
    assert memory_pack_reorderv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "contiguous-load-pack-v1"
    assert memory_pack_reorderv["evidence"]["formal_parameters"]["transaction.graph.memory_safety_status"] == "complete"
    assert memory_pack_reorderver["summary"]["provenance_coverage"] == {"passed": 1}

    memory_pack_extract, memory_pack_extractv, memory_pack_extractver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-pack-extract-insert",
        "tests/fixtures/slp_transaction_graph_memory_pack_extract_insert_snippet.cpp",
    )
    mpeg = memory_pack_extract["optimization_transaction"]["transaction_graph"]
    mpef = memory_pack_extractv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in mpeg["nodes"]] == ["add", "extract", "insert", "xor"]
    assert any(operand.get("kind") == "memory-pack" for operand in mpeg["operands"])
    assert memory_pack_extractv["proof_status"] == "proved"
    assert contains_op(mpef["after"], "vextract")
    assert contains_op(mpef["after"], "vinsert")
    assert memory_pack_extractv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "contiguous-load-pack-v1"
    assert memory_pack_extractver["summary"]["provenance_coverage"] == {"passed": 1}

    memory_gather, memory_gatherv, memory_gatherver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-gather",
        "tests/fixtures/slp_transaction_graph_memory_pack_noncontiguous_snippet.cpp",
    )
    mgg = memory_gather["optimization_transaction"]["transaction_graph"]
    mgf = memory_gatherv["intent_candidate"]["formal"]
    gather_operand = next(operand for operand in mgg["operands"] if operand.get("kind") == "memory-pack")
    assert gather_operand["address_order"] == [0, 2, 4, 6]
    assert gather_operand["address_stride"] == 2
    assert gather_operand["memory_contract"] == "static-gather-pack-v1"
    assert memory_gatherv["proof_status"] == "proved"
    assert mgf["after"]["op"] == "vxor"
    assert memory_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert memory_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_safety_status"] == "complete"
    assert memory_gatherver["summary"]["provenance_coverage"] == {"passed": 1}

    symbolic_gather, symbolic_gatherv, symbolic_gatherver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-gather-index",
        "tests/fixtures/slp_transaction_graph_symbolic_gather_index_snippet.cpp",
    )
    sgg = symbolic_gather["optimization_transaction"]["transaction_graph"]
    sg_operand = next(operand for operand in sgg["operands"] if operand.get("kind") == "memory-pack")
    assert sg_operand["address_order"] == [-1, -1, -1, -1]
    assert sg_operand["memory_contract"] == "symbolic-gather-pack-v1"
    assert sg_operand["memory_address_model"] == "lane-index-expression-v1"
    assert [term["index"] for term in sg_operand["address_terms"]] == [
        "Lane + 1",
        "Lane + 2",
        "Lane + 3",
        "(Lane & 3)",
    ]
    assert symbolic_gatherv["proof_status"] == "proved"
    assert symbolic_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "symbolic-gather-pack-v1"
    assert symbolic_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_address_model"] == "lane-index-expression-v1"
    assert symbolic_gatherver["summary"]["provenance_coverage"] == {"passed": 1}

    masked_symbolic_gather, masked_symbolic_gatherv, masked_symbolic_gatherver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-masked-symbolic-gather-index",
        "tests/fixtures/slp_transaction_graph_masked_symbolic_gather_index_snippet.cpp",
    )
    msgg = masked_symbolic_gather["optimization_transaction"]["transaction_graph"]
    msg_operand = next(operand for operand in msgg["operands"] if operand.get("kind") == "memory-pack")
    assert msg_operand["memory_contract"] == "masked-symbolic-gather-pack-v1"
    assert msg_operand["memory_address_model"] == "lane-index-expression-v1"
    assert msg_operand["mask_order"] == [0, 1, 2, 3]
    assert masked_symbolic_gatherv["proof_status"] == "proved"
    assert masked_symbolic_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-symbolic-gather-pack-v1"
    assert masked_symbolic_gatherver["summary"]["provenance_coverage"] == {"passed": 1}

    constant_symbolic_gather, constant_symbolic_gatherv, constant_symbolic_gatherver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-gather-index-constant",
        "tests/fixtures/slp_transaction_graph_symbolic_gather_index_constant_snippet.cpp",
    )
    csgg = constant_symbolic_gather["optimization_transaction"]["transaction_graph"]
    csg_operand = next(operand for operand in csgg["operands"] if operand.get("kind") == "memory-pack")
    assert [term["index"] for term in csg_operand["address_terms"]] == [
        "Lane + 1",
        "Lane + 1 + 1",
        "Lane + 1 + 2",
        "(Lane + 1) & 3",
    ]
    assert constant_symbolic_gatherv["proof_status"] == "proved"
    assert constant_symbolic_gatherv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "symbolic-gather-pack-v1"
    assert constant_symbolic_gatherver["summary"]["provenance_coverage"] == {"passed": 1}

    memory_gather_reorder, memory_gather_reorderv, memory_gather_reorderver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-gather-reorder",
        "tests/fixtures/slp_transaction_graph_memory_gather_reorder_snippet.cpp",
    )
    mgrg = memory_gather_reorder["optimization_transaction"]["transaction_graph"]
    mgrf = memory_gather_reorderv["intent_candidate"]["formal"]
    assert memory_gather_reorder["optimization_transaction"]["lane_mapping"]["map"] == [2, 0, 3, 1]
    assert next(operand for operand in mgrg["operands"] if operand.get("kind") == "memory-pack")["address_order"] == [0, 2, 4, 6]
    assert memory_gather_reorderv["proof_status"] == "proved"
    assert mgrf["after"]["op"] == "vshuffle"
    assert memory_gather_reorderv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert memory_gather_reorderver["summary"]["provenance_coverage"] == {"passed": 1}

    memory_gather_shuffle, memory_gather_shufflev, memory_gather_shuffler = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-gather-shuffle",
        "tests/fixtures/slp_transaction_graph_memory_gather_shuffle_snippet.cpp",
    )
    mgsg = memory_gather_shuffle["optimization_transaction"]["transaction_graph"]
    mgsf = memory_gather_shufflev["intent_candidate"]["formal"]
    assert [node["opcode"] for node in mgsg["nodes"]] == ["add", "shuffle", "xor"]
    assert memory_gather_shufflev["proof_status"] == "proved"
    assert contains_op(mgsf["after"], "vshuffle")
    assert memory_gather_shufflev["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert memory_gather_shufflev["evidence"]["formal_parameters"]["transaction.graph.shuffle_mask_frame"] == "packed-vector-frame"
    assert memory_gather_shuffler["summary"]["provenance_coverage"] == {"passed": 1}

    memory_gather_extract, memory_gather_extractv, memory_gather_extractver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-memory-gather-extract-insert",
        "tests/fixtures/slp_transaction_graph_memory_gather_extract_insert_snippet.cpp",
    )
    mgeg = memory_gather_extract["optimization_transaction"]["transaction_graph"]
    mgef = memory_gather_extractv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in mgeg["nodes"]] == ["add", "extract", "insert", "xor"]
    assert memory_gather_extractv["proof_status"] == "proved"
    assert contains_op(mgef["after"], "vextract")
    assert contains_op(mgef["after"], "vinsert")
    assert memory_gather_extractv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert memory_gather_extractv["evidence"]["formal_parameters"]["transaction.graph.lane_index_frame"] == "packed-vector-frame"
    assert memory_gather_extractver["summary"]["provenance_coverage"] == {"passed": 1}

    store_sink, store_sinkv, store_sinkver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-store-sink",
        "tests/fixtures/slp_transaction_graph_store_sink_snippet.cpp",
    )
    ssg = store_sink["optimization_transaction"]["transaction_graph"]
    ssf = store_sinkv["intent_candidate"]["formal"]
    assert [node["opcode"] for node in ssg["nodes"]] == ["add", "xor"]
    assert len(ssg["store_sinks"]) == 1
    assert ssg["store_sinks"][0]["address_order"] == [0, 1, 2, 3]
    assert ssg["store_sinks"][0]["store_contract"] == "contiguous-store-pack-v1"
    assert ssg["store_sinks"][0]["store_safety_status"] == "complete"
    assert store_sinkv["proof_status"] == "proved"
    assert ssf["equivalence"] == "observable-result"
    assert ssf["after"]["op"] == "vec"
    assert contains_op(ssf["after"], "mem_store")
    assert contains_op(ssf["after"], "vxor")
    assert store_sinkv["evidence"]["formal_parameters"]["transaction.graph.memory_model"] == "bounded-lane-memory-v1"
    assert [item["offset"] for item in store_sinkv["evidence"]["formal_parameters"]["transaction.graph.observable_addresses"]] == [0, 1, 2, 3]
    assert store_sinkv["evidence"]["formal_parameters"]["transaction.graph.memory_address_model"] == "base-offset-addresses-v1"
    assert store_sinkv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "contiguous-store-pack-v1"
    assert store_sinkv["evidence"]["formal_parameters"]["transaction.graph.store_lane_frame"] == "packed-vector-frame"
    assert store_sinkv["evidence"]["formal_parameters"]["transaction.graph.store_safety_status"] == "complete"
    assert store_sinkver["summary"]["provenance_coverage"] == {"passed": 1}

    symbolic_store, symbolic_storev, symbolic_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-store-sink",
        "tests/fixtures/slp_transaction_graph_symbolic_store_sink_snippet.cpp",
    )
    sysg = symbolic_store["optimization_transaction"]["transaction_graph"]
    syss = sysg["store_sinks"][0]
    assert syss["address_order"] == [-1, -1, -1, -1]
    assert syss["store_contract"] == "symbolic-store-pack-v1"
    assert syss["store_address_model"] == "lane-index-expression-v1"
    assert [term["index"] for term in syss["store_address_terms"]] == [
        "Lane + 1",
        "Lane + 2",
        "Lane + 3",
        "(Lane & 3)",
    ]
    assert symbolic_storev["proof_status"] == "proved"
    assert symbolic_storev["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "symbolic-store-pack-v1"
    assert symbolic_storev["evidence"]["formal_parameters"]["transaction.graph.store_address_model"] == "lane-index-expression-v1"
    assert symbolic_storev["evidence"]["formal_parameters"]["transaction.graph.memory_address_model"] == "lane-index-expression-v1"
    assert symbolic_storever["summary"]["provenance_coverage"] == {"passed": 1}

    masked_symbolic_store, masked_symbolic_storev, masked_symbolic_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-masked-symbolic-store-sink",
        "tests/fixtures/slp_transaction_graph_masked_symbolic_store_sink_snippet.cpp",
    )
    mssg = masked_symbolic_store["optimization_transaction"]["transaction_graph"]
    msss = mssg["store_sinks"][0]
    assert msss["store_contract"] == "masked-symbolic-store-pack-v1"
    assert msss["store_address_model"] == "lane-index-expression-v1"
    assert msss["mask_order"] == [0, 1, 2, 3]
    assert masked_symbolic_storev["proof_status"] == "proved"
    assert masked_symbolic_storev["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "masked-symbolic-store-pack-v1"
    assert masked_symbolic_storever["summary"]["provenance_coverage"] == {"passed": 1}

    constant_symbolic_store, constant_symbolic_storev, constant_symbolic_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-store-sink-constant",
        "tests/fixtures/slp_transaction_graph_symbolic_store_sink_constant_snippet.cpp",
    )
    cssg = constant_symbolic_store["optimization_transaction"]["transaction_graph"]
    csss = cssg["store_sinks"][0]
    assert [term["index"] for term in csss["store_address_terms"]] == [
        "Lane + 1",
        "Lane + 1 + 1",
        "Lane + 1 + 2",
        "(Lane + 1) & 3",
    ]
    assert constant_symbolic_storev["proof_status"] == "proved"
    assert constant_symbolic_storev["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "symbolic-store-pack-v1"
    assert constant_symbolic_storever["summary"]["provenance_coverage"] == {"passed": 1}

    duplicate_symbolic_store, duplicate_symbolic_storev, duplicate_symbolic_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-store-duplicate-term",
        "tests/fixtures/slp_transaction_graph_symbolic_store_duplicate_term_snippet.cpp",
    )
    dssg = duplicate_symbolic_store["optimization_transaction"]["transaction_graph"]
    dsss = dssg["store_sinks"][0]
    assert [term["index"] for term in dsss["store_address_terms"]] == [
        "Lane + 1",
        "Lane + 1",
        "Lane + 2",
        "Lane + 3",
    ]
    assert duplicate_symbolic_storev["proof_status"] == "proved"
    duplicate_observable = duplicate_symbolic_storev["evidence"]["formal_parameters"]["transaction.graph.observable_addresses"]
    assert len(duplicate_observable) == 4
    assert duplicate_observable[0]["symbol"] == duplicate_observable[1]["symbol"]
    assert duplicate_symbolic_storever["summary"]["provenance_coverage"] == {"passed": 1}

    store_scatter, store_scatterv, store_scatterver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-store-scatter",
        "tests/fixtures/slp_transaction_graph_store_scatter_snippet.cpp",
    )
    sctg = store_scatter["optimization_transaction"]["transaction_graph"]
    assert sctg["store_sinks"][0]["address_order"] == [0, 2, 4, 6]
    assert sctg["store_sinks"][0]["address_stride"] == 2
    assert sctg["store_sinks"][0]["store_contract"] == "static-scatter-store-pack-v1"
    assert store_scatterv["proof_status"] == "proved"
    assert store_scatterv["intent_candidate"]["formal"]["equivalence"] == "observable-result"
    assert store_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_model"] == "bounded-lane-memory-v1"
    assert [item["offset"] for item in store_scatterv["evidence"]["formal_parameters"]["transaction.graph.observable_addresses"]] == [0, 2, 4, 6]
    assert store_scatterv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "static-scatter-store-pack-v1"
    assert store_scatterv["evidence"]["formal_parameters"]["transaction.graph.store_safety_status"] == "complete"
    assert store_scatterver["summary"]["provenance_coverage"] == {"passed": 1}

    load_store, load_storev, load_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-load-store-memory",
        "tests/fixtures/slp_transaction_graph_load_store_memory_snippet.cpp",
    )
    lsg = load_store["optimization_transaction"]["transaction_graph"]
    lsf = load_storev["intent_candidate"]["formal"]
    assert any(operand.get("kind") == "memory-pack" for operand in lsg["operands"])
    assert lsg["store_sinks"][0]["store_contract"] == "contiguous-store-pack-v1"
    assert load_storev["proof_status"] == "proved"
    assert lsf["equivalence"] == "observable-result"
    assert contains_op(lsf["before"], "mem_load")
    assert contains_op(lsf["before"], "mem_store")
    assert contains_op(lsf["after"], "mem_store")
    assert load_storev["evidence"]["formal_parameters"]["transaction.graph.memory_model"] == "bounded-lane-memory-v1"
    assert load_storev["evidence"]["formal_parameters"]["transaction.graph.memory_address_model"] == "base-offset-addresses-v1"
    assert any(item.get("relation") == "noalias" for item in load_storev["evidence"]["formal_parameters"]["transaction.graph.memory_alias_conditions"])
    assert any(assumption.get("op") == "addr-diseq" for assumption in lsf.get("assumptions", []))
    assert load_storev["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "contiguous-load-pack-v1"
    assert load_storev["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "contiguous-store-pack-v1"
    assert load_storever["summary"]["provenance_coverage"] == {"passed": 1}

    mayalias_guard, mayalias_guardv, mayalias_guardver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-load-store-mayalias-guard",
        "tests/fixtures/slp_transaction_graph_load_store_mayalias_guard_snippet.cpp",
    )
    mag = mayalias_guard["optimization_transaction"]["transaction_graph"]
    maf = mayalias_guardv["intent_candidate"]["formal"]
    assert mag["memory_alias_conditions"][0]["relation"] == "noalias"
    assert mag["memory_alias_conditions"][0]["status"] == "complete"
    assert mayalias_guardv["proof_status"] == "proved"
    assert maf["equivalence"] == "observable-result"
    assert any(assumption.get("op") == "addr-diseq" for assumption in maf.get("assumptions", []))
    assert mayalias_guardver["summary"]["provenance_coverage"] == {"passed": 1}

    aa_guard, aa_guardv, aa_guardver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-load-store-aa-guard",
        "tests/fixtures/slp_transaction_graph_load_store_alias_analysis_guard_snippet.cpp",
    )
    aag = aa_guard["optimization_transaction"]["transaction_graph"]
    aaf = aa_guardv["intent_candidate"]["formal"]
    assert aag["memory_alias_conditions"][0]["relation"] == "noalias"
    assert aag["memory_alias_conditions"][0]["status"] == "complete"
    assert aa_guardv["proof_status"] == "proved"
    assert aaf["equivalence"] == "observable-result"
    assert any(assumption.get("op") == "addr-diseq" for assumption in aaf.get("assumptions", []))
    assert aa_guardver["summary"]["provenance_coverage"] == {"passed": 1}

    gather_scatter, gather_scatterv, gather_scatterver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-gather-scatter-memory",
        "tests/fixtures/slp_transaction_graph_gather_scatter_memory_snippet.cpp",
    )
    gsg = gather_scatter["optimization_transaction"]["transaction_graph"]
    assert next(operand for operand in gsg["operands"] if operand.get("kind") == "memory-pack")["memory_contract"] == "static-gather-pack-v1"
    assert gsg["store_sinks"][0]["store_contract"] == "static-scatter-store-pack-v1"
    assert gather_scatterv["proof_status"] == "proved"
    assert gather_scatterv["intent_candidate"]["formal"]["equivalence"] == "observable-result"
    assert gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_model"] == "bounded-lane-memory-v1"
    assert gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_address_model"] == "base-offset-addresses-v1"
    assert any(item.get("relation") == "noalias" for item in gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_alias_conditions"])
    assert gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "static-gather-pack-v1"
    assert gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "static-scatter-store-pack-v1"
    assert gather_scatterver["summary"]["provenance_coverage"] == {"passed": 1}

    symbolic_gather_store, symbolic_gather_storev, symbolic_gather_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-gather-store-noalias",
        "tests/fixtures/slp_transaction_graph_symbolic_gather_store_noalias_snippet.cpp",
    )
    sgsg = symbolic_gather_store["optimization_transaction"]["transaction_graph"]
    sgsv = symbolic_gather_storev["evidence"]["formal_parameters"]
    sgs_operand = next(operand for operand in sgsg["operands"] if operand.get("kind") == "memory-pack")
    sgs_sink = sgsg["store_sinks"][0]
    assert sgs_operand["memory_contract"] == "symbolic-gather-pack-v1"
    assert sgs_sink["store_contract"] == "symbolic-store-pack-v1"
    assert sgs_operand["memory_address_model"] == "lane-index-expression-v1"
    assert sgs_sink["store_address_model"] == "lane-index-expression-v1"
    assert symbolic_gather_storev["proof_status"] == "proved"
    assert sgsv["transaction.graph.memory_address_model"] == "lane-index-expression-v1"
    assert sgsv["transaction.graph.memory_contract"] == "symbolic-gather-pack-v1"
    assert sgsv["transaction.graph.store_contract"] == "symbolic-store-pack-v1"
    assert any(item.get("relation") == "noalias" for item in sgsv["transaction.graph.memory_alias_conditions"])
    sgs_assumptions = symbolic_gather_storev["intent_candidate"]["formal"].get("assumptions", [])
    assert any(assumption.get("op") == "addr-diseq" for assumption in sgs_assumptions)
    assert symbolic_gather_storever["summary"]["provenance_coverage"] == {"passed": 1}

    symbolic_same_base, symbolic_same_basev, symbolic_same_basever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-same-base-load-store",
        "tests/fixtures/slp_transaction_graph_symbolic_same_base_load_store_snippet.cpp",
    )
    ssbg = symbolic_same_base["optimization_transaction"]["transaction_graph"]
    ssbv = symbolic_same_basev["evidence"]["formal_parameters"]
    ssb_operand = next(operand for operand in ssbg["operands"] if operand.get("kind") == "memory-pack")
    ssb_sink = ssbg["store_sinks"][0]
    assert ssb_operand["base"] == "Memory"
    assert ssb_sink["base"] == "Memory"
    assert [term["index"] for term in ssb_operand["address_terms"]] == [
        term["index"] for term in ssb_sink["store_address_terms"]
    ]
    assert symbolic_same_basev["proof_status"] == "proved"
    assert ssbv["transaction.graph.memory_address_model"] == "lane-index-expression-v1"
    observable_symbols = [
        item["symbol"] for item in ssbv["transaction.graph.observable_addresses"]
    ]
    load_symbols = set(mem_load_address_symbols(symbolic_same_basev["intent_candidate"]["formal"]["before"]))
    assert all(symbol in load_symbols for symbol in observable_symbols)
    assert not symbolic_same_basev["intent_candidate"]["formal"].get("assumptions")
    assert symbolic_same_basever["summary"]["provenance_coverage"] == {"passed": 1}

    masked_load_store, masked_load_storev, masked_load_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-masked-load-store-memory",
        "tests/fixtures/slp_transaction_graph_masked_load_store_memory_snippet.cpp",
    )
    mlsg = masked_load_store["optimization_transaction"]["transaction_graph"]
    mlsf = masked_load_storev["intent_candidate"]["formal"]
    masked_operand = next(operand for operand in mlsg["operands"] if operand.get("kind") == "memory-pack")
    assert masked_operand["masked"] is True
    assert masked_operand["mask_operand"] == "Mask"
    assert masked_operand["mask_order"] == [0, 1, 2, 3]
    assert masked_operand["passthru_operand"] == "Passthru"
    assert masked_operand["passthru_order"] == [0, 1, 2, 3]
    assert masked_operand["memory_contract"] == "masked-contiguous-load-pack-v1"
    assert mlsg["store_sinks"][0]["masked"] is True
    assert mlsg["store_sinks"][0]["store_contract"] == "masked-contiguous-store-pack-v1"
    assert masked_load_storev["proof_status"] == "proved"
    assert mlsf["equivalence"] == "observable-result"
    assert contains_op(mlsf["before"], "ite")
    assert contains_op(mlsf["after"], "ite")
    assert masked_load_storev["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert masked_load_storev["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-contiguous-load-pack-v1"
    assert masked_load_storev["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "masked-contiguous-store-pack-v1"
    assert masked_load_storever["summary"]["provenance_coverage"] == {"passed": 1}

    symbolic_undef_passthru, symbolic_undef_passthruv, symbolic_undef_passthruver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-undef-passthru-memory",
        "tests/fixtures/slp_transaction_graph_symbolic_undef_passthru_memory_snippet.cpp",
    )
    supg = symbolic_undef_passthru["optimization_transaction"]["transaction_graph"]
    supf = symbolic_undef_passthruv["intent_candidate"]["formal"]
    symbolic_undef_operand = next(operand for operand in supg["operands"] if operand.get("kind") == "memory-pack")
    assert symbolic_undef_operand["masked"] is True
    assert symbolic_undef_operand["mask_operand"] == "Mask"
    assert symbolic_undef_operand["mask_order"] == [0, 1, 2, 3]
    assert symbolic_undef_operand["passthru_kind"] == "symbolic-undef"
    assert symbolic_undef_operand["passthru_symbols"] == ["a_undef0", "a_undef1", "a_undef2", "a_undef3"]
    assert supg["store_sinks"][0]["masked"] is True
    assert supg["store_sinks"][0]["mask_order"] == [0, 1, 2, 3]
    assert symbolic_undef_passthruv["proof_status"] == "proved"
    assert supf["equivalence"] == "observable-result"
    assert all(name in supf["variables"] for name in symbolic_undef_operand["passthru_symbols"])
    assert all(name in supf["poison_variables"] for name in symbolic_undef_operand["passthru_symbols"])
    assert contains_op(supf["before"], "ite")
    assert contains_op(supf["after"], "ite")
    assert symbolic_undef_passthruv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert symbolic_undef_passthruver["summary"]["provenance_coverage"] == {"passed": 1}

    implicit_undef_passthru, implicit_undef_passthruv, implicit_undef_passthruver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-implicit-undef-passthru-memory",
        "tests/fixtures/slp_transaction_graph_implicit_undef_passthru_memory_snippet.cpp",
    )
    iupg = implicit_undef_passthru["optimization_transaction"]["transaction_graph"]
    iupf = implicit_undef_passthruv["intent_candidate"]["formal"]
    implicit_undef_operand = next(operand for operand in iupg["operands"] if operand.get("kind") == "memory-pack")
    assert implicit_undef_operand["masked"] is True
    assert implicit_undef_operand["mask_operand"] == "Mask"
    assert implicit_undef_operand["mask_order"] == [0, 1, 2, 3]
    assert implicit_undef_operand["passthru_kind"] == "symbolic-undef"
    assert implicit_undef_operand["passthru_symbols"] == ["a_undef0", "a_undef1", "a_undef2", "a_undef3"]
    assert iupg["store_sinks"][0]["masked"] is True
    assert iupg["store_sinks"][0]["mask_order"] == [0, 1, 2, 3]
    assert implicit_undef_passthruv["proof_status"] == "proved"
    assert iupf["equivalence"] == "observable-result"
    assert all(name in iupf["variables"] for name in implicit_undef_operand["passthru_symbols"])
    assert all(name in iupf["poison_variables"] for name in implicit_undef_operand["passthru_symbols"])
    assert contains_op(iupf["before"], "ite")
    assert contains_op(iupf["after"], "ite")
    assert implicit_undef_passthruv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert implicit_undef_passthruver["summary"]["provenance_coverage"] == {"passed": 1}

    passthru_alias, passthru_aliasv, passthru_aliasver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-passthru-alias-memory",
        "tests/fixtures/slp_transaction_graph_passthru_alias_memory_snippet.cpp",
    )
    pag = passthru_alias["optimization_transaction"]["transaction_graph"]
    paf = passthru_aliasv["intent_candidate"]["formal"]
    passthru_alias_operand = next(operand for operand in pag["operands"] if operand.get("kind") == "memory-pack")
    assert passthru_alias_operand["masked"] is True
    assert passthru_alias_operand["passthru_operand"] == "Passthru"
    assert passthru_alias_operand["passthru_order"] == [0, 1, 2, 3]
    assert passthru_alias_operand["memory_contract"] == "masked-contiguous-load-pack-v1"
    assert passthru_aliasv["proof_status"] == "proved"
    assert paf["equivalence"] == "observable-result"
    assert contains_op(paf["before"], "ite")
    assert contains_op(paf["after"], "ite")
    assert passthru_aliasv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert passthru_aliasver["summary"]["provenance_coverage"] == {"passed": 1}

    helper_passthru, helper_passthruv, helper_passthruver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-passthru-memory",
        "tests/fixtures/slp_transaction_graph_helper_passthru_memory_snippet.cpp",
    )
    hpg = helper_passthru["optimization_transaction"]["transaction_graph"]
    hpf = helper_passthruv["intent_candidate"]["formal"]
    helper_passthru_operand = next(operand for operand in hpg["operands"] if operand.get("kind") == "memory-pack")
    assert helper_passthru_operand["masked"] is True
    assert helper_passthru_operand["passthru_operand"] == "Passthru"
    assert helper_passthru_operand["passthru_order"] == [0, 1, 2, 3]
    assert helper_passthruv["proof_status"] == "proved"
    assert hpf["equivalence"] == "observable-result"
    assert contains_op(hpf["before"], "ite")
    assert contains_op(hpf["after"], "ite")
    assert helper_passthruv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert helper_passthruver["summary"]["provenance_coverage"] == {"passed": 1}

    undef_passthru_alias, undef_passthru_aliasv, undef_passthru_aliasver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-undef-passthru-alias-memory",
        "tests/fixtures/slp_transaction_graph_undef_passthru_alias_memory_snippet.cpp",
    )
    upag = undef_passthru_alias["optimization_transaction"]["transaction_graph"]
    upaf = undef_passthru_aliasv["intent_candidate"]["formal"]
    undef_passthru_alias_operand = next(operand for operand in upag["operands"] if operand.get("kind") == "memory-pack")
    assert undef_passthru_alias_operand["masked"] is True
    assert undef_passthru_alias_operand["passthru_kind"] == "symbolic-undef"
    assert undef_passthru_alias_operand["passthru_symbols"] == ["a_undef0", "a_undef1", "a_undef2", "a_undef3"]
    assert undef_passthru_aliasv["proof_status"] == "proved"
    assert upaf["equivalence"] == "observable-result"
    assert all(name in upaf["variables"] for name in undef_passthru_alias_operand["passthru_symbols"])
    assert all(name in upaf["poison_variables"] for name in undef_passthru_alias_operand["passthru_symbols"])
    assert undef_passthru_aliasv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert undef_passthru_aliasver["summary"]["provenance_coverage"] == {"passed": 1}

    helper_undef_passthru, helper_undef_passthruv, helper_undef_passthruver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-undef-passthru-memory",
        "tests/fixtures/slp_transaction_graph_helper_undef_passthru_memory_snippet.cpp",
    )
    hupg = helper_undef_passthru["optimization_transaction"]["transaction_graph"]
    hupf = helper_undef_passthruv["intent_candidate"]["formal"]
    helper_undef_passthru_operand = next(operand for operand in hupg["operands"] if operand.get("kind") == "memory-pack")
    assert helper_undef_passthru_operand["masked"] is True
    assert helper_undef_passthru_operand["passthru_kind"] == "symbolic-undef"
    assert helper_undef_passthru_operand["passthru_symbols"] == ["a_undef0", "a_undef1", "a_undef2", "a_undef3"]
    assert helper_undef_passthruv["proof_status"] == "proved"
    assert hupf["equivalence"] == "observable-result"
    assert all(name in hupf["variables"] for name in helper_undef_passthru_operand["passthru_symbols"])
    assert all(name in hupf["poison_variables"] for name in helper_undef_passthru_operand["passthru_symbols"])
    assert helper_undef_passthruv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert helper_undef_passthruver["summary"]["provenance_coverage"] == {"passed": 1}

    static_mask_index, static_mask_indexv, static_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-static-mask-index-memory",
        "tests/fixtures/slp_transaction_graph_static_mask_index_memory_snippet.cpp",
    )
    smig = static_mask_index["optimization_transaction"]["transaction_graph"]
    smif = static_mask_indexv["intent_candidate"]["formal"]
    static_mask_operand = next(operand for operand in smig["operands"] if operand.get("kind") == "memory-pack")
    assert static_mask_operand["masked"] is True
    assert static_mask_operand["mask_operand"] == "Mask"
    assert static_mask_operand["mask_order"] == [0, 1, 2, 3]
    assert static_mask_operand["passthru_order"] == [0, 1, 2, 3]
    assert smig["store_sinks"][0]["masked"] is True
    assert smig["store_sinks"][0]["mask_order"] == [0, 1, 2, 3]
    assert static_mask_indexv["proof_status"] == "proved"
    assert smif["equivalence"] == "observable-result"
    assert contains_op(smif["before"], "ite")
    assert contains_op(smif["after"], "ite")
    assert static_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert static_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    variable_mask_index, variable_mask_indexv, variable_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-variable-mask-index-memory",
        "tests/fixtures/slp_transaction_graph_masked_memory_variable_mask_snippet.cpp",
    )
    vmig = variable_mask_index["optimization_transaction"]["transaction_graph"]
    vmif = variable_mask_indexv["intent_candidate"]["formal"]
    variable_mask_operand = next(operand for operand in vmig["operands"] if operand.get("kind") == "memory-pack")
    assert variable_mask_operand["masked"] is True
    assert variable_mask_operand["mask_operand"] == "Mask"
    assert variable_mask_operand["mask_order"] == [-1, 1, 2, 3]
    assert variable_mask_operand["mask_conditions"][0]["op"] == "indexed-mask"
    assert variable_mask_operand["mask_conditions"][0]["name"] == "Mask"
    assert variable_mask_operand["mask_conditions"][0]["index"] == "Lane"
    assert variable_mask_operand["mask_conditions"][0]["lane"] == 0
    assert variable_mask_operand["mask_conditions"][0]["source"] == "Mask[Lane]"
    assert vmig["store_sinks"][0]["masked"] is True
    assert vmig["store_sinks"][0]["mask_order"] == [-1, 1, 2, 3]
    assert vmig["store_sinks"][0]["mask_conditions"][0]["op"] == "indexed-mask"
    assert vmig["store_sinks"][0]["mask_conditions"][0]["index"] == "Lane"
    assert variable_mask_indexv["proof_status"] == "proved"
    assert vmif["equivalence"] == "observable-result"
    assert contains_op(vmif["before"], "ite")
    assert contains_op(vmif["after"], "ite")
    assert variable_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert variable_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    symbolic_mask_index, symbolic_mask_indexv, symbolic_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-symbolic-mask-index-memory",
        "tests/fixtures/slp_transaction_graph_symbolic_mask_index_memory_snippet.cpp",
    )
    symig = symbolic_mask_index["optimization_transaction"]["transaction_graph"]
    symif = symbolic_mask_indexv["intent_candidate"]["formal"]
    symbolic_mask_operand = next(operand for operand in symig["operands"] if operand.get("kind") == "memory-pack")
    assert symbolic_mask_operand["masked"] is True
    assert symbolic_mask_operand["mask_order"] == [-1, 1, 2, 3]
    assert symbolic_mask_operand["mask_conditions"][0]["op"] == "indexed-mask"
    assert symbolic_mask_operand["mask_conditions"][0]["name"] == "Mask"
    assert symbolic_mask_operand["mask_conditions"][0]["index"] == "Lane + 1"
    assert symbolic_mask_operand["mask_conditions"][0]["source"] == "Mask[Lane + 1]"
    assert symig["store_sinks"][0]["masked"] is True
    assert symig["store_sinks"][0]["mask_conditions"][0]["index"] == "Lane + 1"
    assert symbolic_mask_indexv["proof_status"] == "proved"
    assert symif["equivalence"] == "observable-result"
    assert "Mask_Lane_plus_1" in symif["variables"]
    assert contains_op(symif["before"], "ite")
    assert contains_op(symif["after"], "ite")
    assert symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert symbolic_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    static_symbolic_mask_index, static_symbolic_mask_indexv, static_symbolic_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-static-symbolic-mask-index-memory",
        "tests/fixtures/slp_transaction_graph_static_symbolic_mask_index_memory_snippet.cpp",
    )
    ssymig = static_symbolic_mask_index["optimization_transaction"]["transaction_graph"]
    ssymif = static_symbolic_mask_indexv["intent_candidate"]["formal"]
    static_symbolic_mask_operand = next(operand for operand in ssymig["operands"] if operand.get("kind") == "memory-pack")
    assert static_symbolic_mask_operand["masked"] is True
    assert static_symbolic_mask_operand["mask_order"] == [-1, 1, 2, 3]
    assert static_symbolic_mask_operand["mask_conditions"][0]["op"] == "indexed-mask"
    assert static_symbolic_mask_operand["mask_conditions"][0]["name"] == "Mask"
    assert static_symbolic_mask_operand["mask_conditions"][0]["index"] == "Lane + 1"
    assert static_symbolic_mask_operand["mask_conditions"][0]["source"] == "Mask[Lane + MaskDelta]"
    assert ssymig["store_sinks"][0]["mask_conditions"][0]["index"] == "Lane + 1"
    assert static_symbolic_mask_indexv["proof_status"] == "proved"
    assert ssymif["equivalence"] == "observable-result"
    assert "Mask_Lane_plus_1" in ssymif["variables"]
    assert static_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert static_symbolic_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    guarded_symbolic_mask_index, guarded_symbolic_mask_indexv, guarded_symbolic_mask_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-guarded-symbolic-mask-index-memory",
        "tests/fixtures/slp_transaction_graph_guarded_symbolic_mask_index_memory_snippet.cpp",
    )
    gsymig = guarded_symbolic_mask_index["optimization_transaction"]["transaction_graph"]
    gsymif = guarded_symbolic_mask_indexv["intent_candidate"]["formal"]
    guarded_symbolic_mask_operand = next(operand for operand in gsymig["operands"] if operand.get("kind") == "memory-pack")
    assert guarded_symbolic_mask_operand["masked"] is True
    assert guarded_symbolic_mask_operand["mask_order"] == [-1, 1, 2, 3]
    assert guarded_symbolic_mask_operand["mask_conditions"][0]["op"] == "indexed-mask"
    assert guarded_symbolic_mask_operand["mask_conditions"][0]["index"] == "(Lane & 3)"
    assert guarded_symbolic_mask_operand["mask_conditions"][0]["source"] == "Mask[(Lane & 3)]"
    assert gsymig["store_sinks"][0]["masked"] is True
    assert gsymig["store_sinks"][0]["mask_conditions"][0]["index"] == "(Lane & 3)"
    assert guarded_symbolic_mask_indexv["proof_status"] == "proved"
    assert gsymif["equivalence"] == "observable-result"
    assert "Mask_Lane_and_3" in gsymif["variables"]
    assert contains_op(gsymif["before"], "ite")
    assert contains_op(gsymif["after"], "ite")
    assert guarded_symbolic_mask_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert guarded_symbolic_mask_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    static_memory_index, static_memory_indexv, static_memory_indexver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-static-memory-index",
        "tests/fixtures/slp_transaction_graph_static_memory_index_snippet.cpp",
    )
    smemg = static_memory_index["optimization_transaction"]["transaction_graph"]
    smemf = static_memory_indexv["intent_candidate"]["formal"]
    static_memory_operand = next(operand for operand in smemg["operands"] if operand.get("kind") == "memory-pack")
    assert static_memory_operand["address_order"] == [0, 2, 4, 6]
    assert static_memory_operand["passthru_order"] == [0, 1, 2, 3]
    assert static_memory_operand["memory_contract"] == "masked-static-gather-pack-v1"
    assert smemg["store_sinks"][0]["address_order"] == [0, 2, 4, 6]
    assert smemg["store_sinks"][0]["store_contract"] == "masked-static-scatter-store-pack-v1"
    assert static_memory_indexv["proof_status"] == "proved"
    assert smemf["equivalence"] == "observable-result"
    assert contains_op(smemf["before"], "ite")
    assert contains_op(smemf["after"], "ite")
    assert static_memory_indexv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert static_memory_indexver["summary"]["provenance_coverage"] == {"passed": 1}

    masked_gather_scatter, masked_gather_scatterv, masked_gather_scatterver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-masked-gather-scatter-memory",
        "tests/fixtures/slp_transaction_graph_masked_gather_scatter_memory_snippet.cpp",
    )
    mgsg = masked_gather_scatter["optimization_transaction"]["transaction_graph"]
    mgsf = masked_gather_scatterv["intent_candidate"]["formal"]
    masked_gather_operand = next(operand for operand in mgsg["operands"] if operand.get("kind") == "memory-pack")
    assert masked_gather_operand["address_order"] == [0, 2, 4, 6]
    assert masked_gather_operand["memory_contract"] == "masked-static-gather-pack-v1"
    assert mgsg["store_sinks"][0]["address_order"] == [0, 2, 4, 6]
    assert mgsg["store_sinks"][0]["store_contract"] == "masked-static-scatter-store-pack-v1"
    assert masked_gather_scatterv["proof_status"] == "proved"
    assert mgsf["equivalence"] == "observable-result"
    assert contains_op(mgsf["before"], "ite")
    assert contains_op(mgsf["after"], "ite")
    assert masked_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert masked_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-static-gather-pack-v1"
    assert masked_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "masked-static-scatter-store-pack-v1"
    assert masked_gather_scatterver["summary"]["provenance_coverage"] == {"passed": 1}

    mask_provenance, mask_provenancev, mask_provenancever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-mask-provenance-memory",
        "tests/fixtures/slp_transaction_graph_mask_provenance_memory_snippet.cpp",
    )
    mpg = mask_provenance["optimization_transaction"]["transaction_graph"]
    mpf = mask_provenancev["intent_candidate"]["formal"]
    mp_operand = next(operand for operand in mpg["operands"] if operand.get("kind") == "memory-pack")
    assert mp_operand["masked"] is True
    assert len(mp_operand["mask_conditions"]) == 4
    assert mp_operand["mask_conditions"][0]["predicate"] == "eq"
    assert mp_operand["mask_conditions"][0]["lhs"] == "Cmp[0]"
    assert mp_operand["mask_conditions"][0]["rhs"] == "Passthru[0]"
    assert len(mpg["store_sinks"][0]["mask_conditions"]) == 4
    assert mask_provenancev["proof_status"] == "proved"
    assert mpf["equivalence"] == "observable-result"
    assert contains_op(mpf["before"], "eq")
    assert contains_op(mpf["after"], "eq")
    assert mask_provenancev["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert mask_provenancever["summary"]["provenance_coverage"] == {"passed": 1}

    alias_mask, alias_maskv, alias_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-alias-mask-memory",
        "tests/fixtures/slp_transaction_graph_alias_mask_memory_snippet.cpp",
    )
    amg = alias_mask["optimization_transaction"]["transaction_graph"]
    amf = alias_maskv["intent_candidate"]["formal"]
    alias_operand = next(operand for operand in amg["operands"] if operand.get("kind") == "memory-pack")
    assert alias_operand["masked"] is True
    assert len(alias_operand["mask_conditions"]) == 4
    assert alias_operand["mask_conditions"][0]["op"] == "and"
    assert alias_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert alias_operand["mask_conditions"][1]["predicate"] == "eq"
    assert alias_operand["mask_conditions"][2]["predicate"] == "eq"
    assert alias_operand["mask_conditions"][3]["op"] == "or"
    assert amg["store_sinks"][0]["mask_conditions"][0]["predicate"] == "eq"
    assert amg["store_sinks"][0]["mask_conditions"][1]["op"] == "and"
    assert alias_maskv["proof_status"] == "proved"
    assert amf["equivalence"] == "observable-result"
    assert contains_op(amf["before"], "and")
    assert contains_op(amf["before"], "or")
    assert contains_op(amf["after"], "and")
    assert alias_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert alias_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    split_mask, split_maskv, split_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-split-mask-assignment-memory",
        "tests/fixtures/slp_transaction_graph_split_mask_assignment_memory_snippet.cpp",
    )
    spmg = split_mask["optimization_transaction"]["transaction_graph"]
    spmf = split_maskv["intent_candidate"]["formal"]
    split_operand = next(operand for operand in spmg["operands"] if operand.get("kind") == "memory-pack")
    assert split_operand["masked"] is True
    assert len(split_operand["mask_conditions"]) == 4
    assert all(condition["predicate"] == "eq" for condition in split_operand["mask_conditions"])
    assert len(spmg["store_sinks"][0]["mask_conditions"]) == 4
    assert all(condition["predicate"] == "eq" for condition in spmg["store_sinks"][0]["mask_conditions"])
    assert split_maskv["proof_status"] == "proved"
    assert spmf["equivalence"] == "observable-result"
    assert contains_op(spmf["before"], "eq")
    assert contains_op(spmf["after"], "eq")
    assert split_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert split_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    branch_mask, branch_maskv, branch_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-branch-mask-memory",
        "tests/fixtures/slp_transaction_graph_branch_mask_memory_snippet.cpp",
    )
    brmg = branch_mask["optimization_transaction"]["transaction_graph"]
    brmf = branch_maskv["intent_candidate"]["formal"]
    branch_operand = next(operand for operand in brmg["operands"] if operand.get("kind") == "memory-pack")
    assert branch_operand["masked"] is True
    assert len(branch_operand["mask_conditions"]) == 4
    assert branch_operand["mask_conditions"][0]["op"] == "select"
    assert branch_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert branch_operand["mask_conditions"][0]["args"][1]["predicate"] == "ne"
    assert branch_operand["mask_conditions"][0]["args"][2]["predicate"] == "eq"
    assert branch_maskv["proof_status"] == "proved"
    assert brmf["equivalence"] == "observable-result"
    assert contains_op(brmf["before"], "ite")
    assert contains_op(brmf["before"], "eq")
    assert contains_op(brmf["before"], "ne")
    assert branch_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert branch_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    branch_alias_mask, branch_alias_maskv, branch_alias_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-branch-alias-mask-memory",
        "tests/fixtures/slp_transaction_graph_branch_alias_mask_memory_snippet.cpp",
    )
    bamg = branch_alias_mask["optimization_transaction"]["transaction_graph"]
    bamf = branch_alias_maskv["intent_candidate"]["formal"]
    branch_alias_operand = next(operand for operand in bamg["operands"] if operand.get("kind") == "memory-pack")
    assert branch_alias_operand["masked"] is True
    assert len(branch_alias_operand["mask_conditions"]) == 4
    assert branch_alias_operand["mask_conditions"][0]["op"] == "select"
    assert branch_alias_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert branch_alias_operand["mask_conditions"][0]["args"][1]["predicate"] == "ne"
    assert branch_alias_operand["mask_conditions"][0]["args"][2]["predicate"] == "eq"
    assert "Alias0 = M0" in branch_alias_operand["mask_conditions"][0]["source"]
    assert branch_alias_maskv["proof_status"] == "proved"
    assert bamf["equivalence"] == "observable-result"
    assert contains_op(bamf["before"], "ite")
    assert contains_op(bamf["after"], "ite")
    assert branch_alias_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert branch_alias_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    nested_branch_mask, nested_branch_maskv, nested_branch_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-nested-branch-mask-memory",
        "tests/fixtures/slp_transaction_graph_nested_branch_mask_memory_snippet.cpp",
    )
    nbmg = nested_branch_mask["optimization_transaction"]["transaction_graph"]
    nbmf = nested_branch_maskv["intent_candidate"]["formal"]
    nested_branch_operand = next(operand for operand in nbmg["operands"] if operand.get("kind") == "memory-pack")
    assert nested_branch_operand["masked"] is True
    assert len(nested_branch_operand["mask_conditions"]) == 4
    assert nested_branch_operand["mask_conditions"][0]["op"] == "select"
    assert nested_branch_operand["mask_conditions"][0]["args"][1]["op"] == "select"
    assert nested_branch_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert nested_branch_operand["mask_conditions"][0]["args"][1]["args"][0]["predicate"] == "ne"
    assert nested_branch_maskv["proof_status"] == "proved"
    assert nbmf["equivalence"] == "vector-result"
    assert nested_branch_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert nested_branch_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    branch_store_mask, branch_store_maskv, branch_store_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-branch-store-mask-memory",
        "tests/fixtures/slp_transaction_graph_branch_store_mask_memory_snippet.cpp",
    )
    bsmg = branch_store_mask["optimization_transaction"]["transaction_graph"]
    bsmf = branch_store_maskv["intent_candidate"]["formal"]
    branch_store_sink = bsmg["store_sinks"][0]
    assert branch_store_sink["masked"] is True
    assert len(branch_store_sink["mask_conditions"]) == 4
    assert branch_store_sink["mask_conditions"][0]["op"] == "select"
    assert branch_store_sink["mask_conditions"][0]["args"][0]["op"] == "indexed-mask"
    assert branch_store_sink["mask_conditions"][0]["args"][0]["name"] == "Gate"
    assert branch_store_sink["mask_conditions"][0]["args"][1]["predicate"] == "eq"
    assert branch_store_sink["mask_conditions"][0]["args"][2]["predicate"] == "ne"
    assert branch_store_maskv["proof_status"] == "proved"
    assert bsmf["equivalence"] == "observable-result"
    assert contains_op(bsmf["after"], "ite")
    assert branch_store_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert branch_store_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    generalized_mask_syntax, generalized_mask_syntaxv, generalized_mask_syntaxver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-generalized-mask-syntax-memory",
        "tests/fixtures/slp_transaction_graph_generalized_mask_syntax_memory_snippet.cpp",
    )
    gmsg = generalized_mask_syntax["optimization_transaction"]["transaction_graph"]
    gmsf = generalized_mask_syntaxv["intent_candidate"]["formal"]
    generalized_operand = next(operand for operand in gmsg["operands"] if operand.get("kind") == "memory-pack")
    assert generalized_operand["masked"] is True
    assert len(generalized_operand["mask_conditions"]) == 4
    assert generalized_operand["mask_conditions"][0]["op"] == "and"
    assert generalized_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert "MaskBuilder->CreateICmp" in generalized_operand["mask_conditions"][0]["args"][0]["source"]
    assert generalized_operand["mask_conditions"][1]["op"] == "select"
    assert generalized_operand["mask_conditions"][1]["args"][1]["op"] == "not"
    assert gmsg["store_sinks"][0]["mask_conditions"][0]["op"] == "or"
    assert gmsg["store_sinks"][0]["mask_conditions"][1]["op"] == "select"
    assert generalized_mask_syntaxv["proof_status"] == "proved"
    assert gmsf["equivalence"] == "observable-result"
    assert contains_op(gmsf["before"], "and")
    assert contains_op(gmsf["after"], "ite")
    assert generalized_mask_syntaxv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert generalized_mask_syntaxver["summary"]["provenance_coverage"] == {"passed": 1}

    helper_mask, helper_maskv, helper_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-mask-memory",
        "tests/fixtures/slp_transaction_graph_helper_mask_memory_snippet.cpp",
    )
    hmg = helper_mask["optimization_transaction"]["transaction_graph"]
    hmf = helper_maskv["intent_candidate"]["formal"]
    helper_mask_operand = next(operand for operand in hmg["operands"] if operand.get("kind") == "memory-pack")
    assert len(helper_mask_operand["mask_conditions"]) == 4
    assert helper_mask_operand["mask_conditions"][0]["predicate"] == "eq"
    assert helper_mask_operand["mask_conditions"][0]["lhs"] == "Cmp[0]"
    assert helper_mask_operand["mask_conditions"][0]["rhs"] == "Passthru[0]"
    assert len(hmg["store_sinks"][0]["mask_conditions"]) == 4
    assert helper_maskv["proof_status"] == "proved"
    assert hmf["equivalence"] == "observable-result"
    assert contains_op(hmf["before"], "eq")
    assert contains_op(hmf["after"], "eq")
    assert helper_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    opaque_helper_mask, opaque_helper_maskv, opaque_helper_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-opaque-mask-memory",
        "tests/fixtures/slp_transaction_graph_helper_unresolved_mask_memory_snippet.cpp",
    )
    ohmg = opaque_helper_mask["optimization_transaction"]["transaction_graph"]
    ohmf = opaque_helper_maskv["intent_candidate"]["formal"]
    opaque_operand = next(operand for operand in ohmg["operands"] if operand.get("kind") == "memory-pack")
    assert opaque_operand["masked"] is True
    assert len(opaque_operand["mask_conditions"]) == 4
    assert opaque_operand["mask_conditions"][0]["op"] == "opaque-mask"
    assert opaque_operand["mask_conditions"][0]["name"] == "M0"
    assert opaque_operand["mask_conditions"][0]["lane"] == 0
    assert opaque_operand["mask_conditions"][3]["name"] == "M3"
    assert len(ohmg["store_sinks"][0]["mask_conditions"]) == 4
    assert ohmg["store_sinks"][0]["mask_conditions"][0]["op"] == "opaque-mask"
    assert ohmg["store_sinks"][0]["mask_conditions"][0]["name"] == "S0"
    assert opaque_helper_maskv["proof_status"] == "proved"
    assert ohmf["equivalence"] == "observable-result"
    assert contains_op(ohmf["before"], "ne")
    assert contains_op(ohmf["after"], "ne")
    assert opaque_helper_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert opaque_helper_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    unresolved_helper_mask, unresolved_helper_maskv, unresolved_helper_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-unresolved-opaque-slice",
        "tests/fixtures/slp_transaction_graph_helper_unresolved_slice_snippet.cpp",
    )
    uhmg = unresolved_helper_mask["optimization_transaction"]["transaction_graph"]
    uhmf = unresolved_helper_maskv["intent_candidate"]["formal"]
    unresolved_operand = next(operand for operand in uhmg["operands"] if operand.get("kind") == "memory-pack")
    assert unresolved_operand["masked"] is True
    assert len(unresolved_operand["mask_conditions"]) == 4
    assert unresolved_operand["mask_conditions"][0]["op"] == "opaque-mask"
    assert unresolved_operand["mask_conditions"][0]["name"] == "M0"
    assert unresolved_operand["mask_conditions"][0]["lane"] == 0
    assert unresolved_operand["mask_conditions"][0]["source"] == "Value *M0 = unresolvedMask(Cmp[0]);"
    assert unresolved_helper_maskv["proof_status"] == "proved"
    assert uhmf["equivalence"] == "vector-result"
    assert unresolved_helper_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert unresolved_helper_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    indexed_helper_mask, indexed_helper_maskv, indexed_helper_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-indexed-slice",
        "tests/fixtures/slp_transaction_graph_helper_indexed_slice_snippet.cpp",
    )
    ihmg = indexed_helper_mask["optimization_transaction"]["transaction_graph"]
    ihmf = indexed_helper_maskv["intent_candidate"]["formal"]
    indexed_helper_operand = next(operand for operand in ihmg["operands"] if operand.get("kind") == "memory-pack")
    assert len(indexed_helper_operand["mask_conditions"]) == 4
    assert indexed_helper_operand["mask_conditions"][0]["predicate"] == "eq"
    assert indexed_helper_operand["mask_conditions"][1]["lhs"] == "Cmp[1]"
    assert indexed_helper_operand["mask_conditions"][2]["rhs"] == "Passthru[2]"
    assert indexed_helper_maskv["proof_status"] == "proved"
    assert ihmf["equivalence"] == "vector-result"
    assert indexed_helper_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert indexed_helper_maskv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-contiguous-load-pack-v1"
    assert indexed_helper_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    default_args_helper_mask, default_args_helper_maskv, default_args_helper_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-default-args-slice",
        "tests/fixtures/slp_transaction_graph_helper_default_args_slice_snippet.cpp",
    )
    dahg = default_args_helper_mask["optimization_transaction"]["transaction_graph"]
    dahf = default_args_helper_maskv["intent_candidate"]["formal"]
    default_args_operand = next(operand for operand in dahg["operands"] if operand.get("kind") == "memory-pack")
    assert len(default_args_operand["mask_conditions"]) == 4
    assert default_args_operand["mask_conditions"][0]["predicate"] == "eq"
    assert default_args_operand["mask_conditions"][0]["lhs"] == "Cmp[0]"
    assert default_args_operand["mask_conditions"][0]["rhs"] == "0"
    assert default_args_helper_maskv["proof_status"] == "proved"
    assert dahf["equivalence"] == "vector-result"
    assert default_args_helper_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert default_args_helper_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    simple_multi_return_mask, simple_multi_return_maskv, simple_multi_return_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-simple-multi-return-slice",
        "tests/fixtures/slp_transaction_graph_helper_simple_multi_return_slice_snippet.cpp",
    )
    smrg = simple_multi_return_mask["optimization_transaction"]["transaction_graph"]
    smrf = simple_multi_return_maskv["intent_candidate"]["formal"]
    simple_multi_return_operand = next(operand for operand in smrg["operands"] if operand.get("kind") == "memory-pack")
    assert len(simple_multi_return_operand["mask_conditions"]) == 4
    assert simple_multi_return_operand["mask_conditions"][0]["op"] == "select"
    assert simple_multi_return_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert simple_multi_return_operand["mask_conditions"][0]["args"][1]["predicate"] == "ne"
    assert simple_multi_return_operand["mask_conditions"][0]["args"][2]["predicate"] == "eq"
    assert simple_multi_return_maskv["proof_status"] == "proved"
    assert smrf["equivalence"] == "vector-result"
    assert simple_multi_return_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert simple_multi_return_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    opaque_multi_return_mask, opaque_multi_return_maskv, opaque_multi_return_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-opaque-multi-return-slice",
        "tests/fixtures/slp_transaction_graph_helper_multi_return_slice_snippet.cpp",
    )
    omrg = opaque_multi_return_mask["optimization_transaction"]["transaction_graph"]
    omrf = opaque_multi_return_maskv["intent_candidate"]["formal"]
    opaque_multi_return_operand = next(operand for operand in omrg["operands"] if operand.get("kind") == "memory-pack")
    assert len(opaque_multi_return_operand["mask_conditions"]) == 4
    assert opaque_multi_return_operand["mask_conditions"][0]["op"] == "select"
    assert opaque_multi_return_operand["mask_conditions"][0]["args"][0]["op"] == "opaque-mask"
    assert opaque_multi_return_operand["mask_conditions"][0]["args"][0]["name"] == "M0_cond"
    assert opaque_multi_return_operand["mask_conditions"][0]["args"][1]["predicate"] == "ne"
    assert opaque_multi_return_operand["mask_conditions"][0]["args"][2]["predicate"] == "eq"
    assert opaque_multi_return_maskv["proof_status"] == "proved"
    assert omrf["equivalence"] == "vector-result"
    assert opaque_multi_return_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert opaque_multi_return_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    deep_chain_mask, deep_chain_maskv, deep_chain_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-deep-chain-slice",
        "tests/fixtures/slp_transaction_graph_helper_deep_chain_slice_snippet.cpp",
    )
    dcg = deep_chain_mask["optimization_transaction"]["transaction_graph"]
    dcf = deep_chain_maskv["intent_candidate"]["formal"]
    deep_chain_operand = next(operand for operand in dcg["operands"] if operand.get("kind") == "memory-pack")
    assert len(deep_chain_operand["mask_conditions"]) == 4
    assert deep_chain_operand["mask_conditions"][0]["predicate"] == "eq"
    assert deep_chain_operand["mask_conditions"][0]["lhs"] == "Cmp[0]"
    assert deep_chain_operand["mask_conditions"][0]["rhs"] == "Passthru[0]"
    assert deep_chain_maskv["proof_status"] == "proved"
    assert dcf["equivalence"] == "vector-result"
    assert deep_chain_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert deep_chain_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    depth_limit_mask, depth_limit_maskv, depth_limit_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-depth-limit-slice",
        "tests/fixtures/slp_transaction_graph_helper_depth_limit_slice_snippet.cpp",
    )
    dlg = depth_limit_mask["optimization_transaction"]["transaction_graph"]
    dlf = depth_limit_maskv["intent_candidate"]["formal"]
    depth_limit_operand = next(operand for operand in dlg["operands"] if operand.get("kind") == "memory-pack")
    assert len(depth_limit_operand["mask_conditions"]) == 4
    assert depth_limit_operand["mask_conditions"][0]["predicate"] == "eq"
    assert depth_limit_operand["mask_conditions"][0]["lhs"] == "Cmp[0]"
    assert depth_limit_operand["mask_conditions"][0]["rhs"] == "Passthru[0]"
    assert depth_limit_maskv["proof_status"] == "proved"
    assert dlf["equivalence"] == "vector-result"
    assert depth_limit_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert depth_limit_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    helper_boolean_mask, helper_boolean_maskv, helper_boolean_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-boolean-mask-memory",
        "tests/fixtures/slp_transaction_graph_helper_boolean_mask_memory_snippet.cpp",
    )
    hbmg = helper_boolean_mask["optimization_transaction"]["transaction_graph"]
    hbmf = helper_boolean_maskv["intent_candidate"]["formal"]
    helper_boolean_operand = next(operand for operand in hbmg["operands"] if operand.get("kind") == "memory-pack")
    assert helper_boolean_operand["mask_conditions"][0]["op"] == "and"
    assert helper_boolean_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert helper_boolean_operand["mask_conditions"][0]["args"][1]["op"] == "not"
    assert hbmg["store_sinks"][0]["mask_conditions"][0]["op"] == "and"
    assert helper_boolean_maskv["proof_status"] == "proved"
    assert hbmf["equivalence"] == "observable-result"
    assert contains_op(hbmf["before"], "and")
    assert contains_op(hbmf["before"], "not")
    assert helper_boolean_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    nested_helper_memory, nested_helper_memoryv, nested_helper_memoryver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-nested-helper-memory",
        "tests/fixtures/slp_transaction_graph_nested_helper_memory_snippet.cpp",
    )
    nhmg = nested_helper_memory["optimization_transaction"]["transaction_graph"]
    nhmf = nested_helper_memoryv["intent_candidate"]["formal"]
    nested_operand = next(operand for operand in nhmg["operands"] if operand.get("kind") == "memory-pack")
    assert nested_operand["memory_contract"] == "masked-contiguous-load-pack-v1"
    assert len(nested_operand["mask_conditions"]) == 4
    assert nested_operand["mask_conditions"][0]["predicate"] == "eq"
    assert nhmg["store_sinks"][0]["store_contract"] == "masked-contiguous-store-pack-v1"
    assert nested_helper_memoryv["proof_status"] == "proved"
    assert nhmf["equivalence"] == "observable-result"
    assert contains_op(nhmf["before"], "eq")
    assert nested_helper_memoryver["summary"]["provenance_coverage"] == {"passed": 1}

    helper_store_sink, helper_store_sinkv, helper_store_sinkver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-helper-store-sink",
        "tests/fixtures/slp_transaction_graph_helper_store_sink_snippet.cpp",
    )
    hssg = helper_store_sink["optimization_transaction"]["transaction_graph"]
    hssf = helper_store_sinkv["intent_candidate"]["formal"]
    helper_store_operand = next(operand for operand in hssg["operands"] if operand.get("kind") == "memory-pack")
    assert len(helper_store_operand["mask_conditions"]) == 4
    assert hssg["store_sinks"][0]["store_contract"] == "masked-contiguous-store-pack-v1"
    assert len(hssg["store_sinks"][0]["mask_conditions"]) == 4
    assert helper_store_sinkv["proof_status"] == "proved"
    assert hssf["equivalence"] == "observable-result"
    assert contains_op(hssf["after"], "eq")
    assert helper_store_sinkver["summary"]["provenance_coverage"] == {"passed": 1}

    boolean_mask, boolean_maskv, boolean_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-boolean-mask-memory",
        "tests/fixtures/slp_transaction_graph_boolean_mask_memory_snippet.cpp",
    )
    bmg = boolean_mask["optimization_transaction"]["transaction_graph"]
    bmf = boolean_maskv["intent_candidate"]["formal"]
    bm_operand = next(operand for operand in bmg["operands"] if operand.get("kind") == "memory-pack")
    assert bm_operand["mask_conditions"][0]["op"] == "and"
    assert bm_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert bm_operand["mask_conditions"][0]["args"][1]["predicate"] == "ne"
    assert bmg["store_sinks"][0]["mask_conditions"][0]["op"] == "or"
    assert boolean_maskv["proof_status"] == "proved"
    assert bmf["equivalence"] == "observable-result"
    assert contains_op(bmf["before"], "and")
    assert contains_op(bmf["after"], "or")
    assert contains_op(bmf["before"], "eq")
    assert contains_op(bmf["before"], "ne")
    assert boolean_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    rich_mask, rich_maskv, rich_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-rich-mask-memory",
        "tests/fixtures/slp_transaction_graph_rich_mask_memory_snippet.cpp",
    )
    rmg = rich_mask["optimization_transaction"]["transaction_graph"]
    rmf = rich_maskv["intent_candidate"]["formal"]
    rm_operand = next(operand for operand in rmg["operands"] if operand.get("kind") == "memory-pack")
    assert rm_operand["mask_conditions"][0]["op"] == "select"
    assert rm_operand["mask_conditions"][0]["args"][1]["op"] == "not"
    assert rmg["store_sinks"][0]["mask_conditions"][0]["op"] == "select"
    assert rmg["store_sinks"][0]["mask_conditions"][0]["args"][1]["op"] == "not"
    assert rich_maskv["proof_status"] == "proved"
    assert rmf["equivalence"] == "observable-result"
    assert contains_op(rmf["before"], "not")
    assert contains_op(rmf["before"], "ite")
    assert contains_op(rmf["after"], "not")
    assert contains_op(rmf["after"], "ite")
    assert rich_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert rich_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    normalized_mask, normalized_maskv, normalized_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-normalized-mask-memory",
        "tests/fixtures/slp_transaction_graph_normalized_mask_memory_snippet.cpp",
    )
    nmg = normalized_mask["optimization_transaction"]["transaction_graph"]
    nmf = normalized_maskv["intent_candidate"]["formal"]
    nm_operand = next(operand for operand in nmg["operands"] if operand.get("kind") == "memory-pack")
    assert nm_operand["mask_conditions"][0]["op"] == "not"
    assert nm_operand["mask_conditions"][1]["predicate"] == "ne"
    assert nm_operand["mask_conditions"][2]["op"] == "const"
    assert nm_operand["mask_conditions"][2]["value"] is True
    assert nmg["store_sinks"][0]["mask_conditions"][0]["op"] == "not"
    assert nmg["store_sinks"][0]["mask_conditions"][1]["predicate"] == "ne"
    assert nmg["store_sinks"][0]["mask_conditions"][2]["op"] == "const"
    assert normalized_maskv["proof_status"] == "proved"
    assert nmf["equivalence"] == "observable-result"
    assert contains_op(nmf["before"], "not")
    assert contains_op(nmf["before"], "eq")
    assert contains_op(nmf["after"], "not")
    assert contains_op(nmf["after"], "eq")
    assert normalized_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert normalized_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    guarded_temp_mask, guarded_temp_maskv, guarded_temp_maskver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-guarded-temp-mask-memory",
        "tests/fixtures/slp_transaction_graph_guarded_temp_mask_memory_snippet.cpp",
    )
    gtmg = guarded_temp_mask["optimization_transaction"]["transaction_graph"]
    gtmf = guarded_temp_maskv["intent_candidate"]["formal"]
    gtm_operand = next(operand for operand in gtmg["operands"] if operand.get("kind") == "memory-pack")
    assert gtm_operand["masked"] is True
    assert len(gtm_operand["mask_conditions"]) == 4
    assert gtm_operand["mask_conditions"][0]["op"] == "and"
    assert gtm_operand["mask_conditions"][0]["args"][0]["predicate"] == "eq"
    assert gtm_operand["mask_conditions"][0]["args"][1]["predicate"] == "ne"
    assert gtmg["store_sinks"][0]["mask_conditions"][0]["op"] == "or"
    assert guarded_temp_maskv["proof_status"] == "proved"
    assert gtmf["equivalence"] == "observable-result"
    assert contains_op(gtmf["before"], "and")
    assert contains_op(gtmf["after"], "or")
    assert guarded_temp_maskv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert guarded_temp_maskver["summary"]["provenance_coverage"] == {"passed": 1}

    guarded_masked_load_store, guarded_masked_load_storev, guarded_masked_load_storever = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-guarded-masked-load-store-memory",
        "tests/fixtures/slp_transaction_graph_guarded_masked_load_store_memory_snippet.cpp",
    )
    gmlsg = guarded_masked_load_store["optimization_transaction"]["transaction_graph"]
    gmlsf = guarded_masked_load_storev["intent_candidate"]["formal"]
    guarded_operand = next(operand for operand in gmlsg["operands"] if operand.get("kind") == "memory-pack")
    assert guarded_operand["masked"] is True
    assert guarded_operand["mask_operand"] == "Mask"
    assert guarded_operand["mask_order"] == [0, 1, 2, 3]
    assert guarded_operand["passthru_operand"] == "Passthru"
    assert guarded_operand["passthru_order"] == [0, 1, 2, 3]
    assert guarded_operand["memory_contract"] == "masked-contiguous-load-pack-v1"
    assert gmlsg["store_sinks"][0]["masked"] is True
    assert gmlsg["store_sinks"][0]["store_contract"] == "masked-contiguous-store-pack-v1"
    assert guarded_masked_load_storev["proof_status"] == "proved"
    assert gmlsf["equivalence"] == "observable-result"
    assert contains_op(gmlsf["before"], "ite")
    assert contains_op(gmlsf["after"], "ite")
    assert guarded_masked_load_storev["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert guarded_masked_load_storev["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-contiguous-load-pack-v1"
    assert guarded_masked_load_storev["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "masked-contiguous-store-pack-v1"
    assert guarded_masked_load_storever["summary"]["provenance_coverage"] == {"passed": 1}

    guarded_masked_gather_scatter, guarded_masked_gather_scatterv, guarded_masked_gather_scatterver = prove_case(
        args.repo,
        args.work,
        args.miner,
        args.z3,
        "ast-slp-transaction-graph-guarded-masked-gather-scatter-memory",
        "tests/fixtures/slp_transaction_graph_guarded_masked_gather_scatter_memory_snippet.cpp",
    )
    gmgsg = guarded_masked_gather_scatter["optimization_transaction"]["transaction_graph"]
    gmgsf = guarded_masked_gather_scatterv["intent_candidate"]["formal"]
    guarded_gather_operand = next(operand for operand in gmgsg["operands"] if operand.get("kind") == "memory-pack")
    assert guarded_gather_operand["address_order"] == [0, 2, 4, 6]
    assert guarded_gather_operand["memory_contract"] == "masked-static-gather-pack-v1"
    assert gmgsg["store_sinks"][0]["address_order"] == [0, 2, 4, 6]
    assert gmgsg["store_sinks"][0]["store_contract"] == "masked-static-scatter-store-pack-v1"
    assert guarded_masked_gather_scatterv["proof_status"] == "proved"
    assert gmgsf["equivalence"] == "observable-result"
    assert contains_op(gmgsf["before"], "ite")
    assert contains_op(gmgsf["after"], "ite")
    assert guarded_masked_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.masked_memory"] is True
    assert guarded_masked_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.memory_contract"] == "masked-static-gather-pack-v1"
    assert guarded_masked_gather_scatterv["evidence"]["formal_parameters"]["transaction.graph.store_contract"] == "masked-static-scatter-store-pack-v1"
    assert guarded_masked_gather_scatterver["summary"]["provenance_coverage"] == {"passed": 1}

    for case in [
        (
            "ast-slp-transaction-graph-memory-pack-volatile",
            "tests/fixtures/slp_transaction_graph_memory_pack_volatile_snippet.cpp",
            "unsupported-volatile-or-atomic-memory",
        ),
        (
            "ast-slp-transaction-graph-memory-pack-store",
            "tests/fixtures/slp_transaction_graph_memory_pack_store_snippet.cpp",
            "unsupported-intervening-store",
        ),
        (
            "ast-slp-transaction-graph-memory-pack-unknown-call",
            "tests/fixtures/slp_transaction_graph_memory_pack_unknown_call_snippet.cpp",
            "unsupported-memory-effect-call",
        ),
        (
            "ast-slp-transaction-graph-memory-pack-mixed-base",
            "tests/fixtures/slp_transaction_graph_memory_pack_mixed_base_snippet.cpp",
            "unsupported-ambiguous-memory-base",
        ),
        (
            "ast-slp-transaction-graph-memory-pack-pointer-mutation",
            "tests/fixtures/slp_transaction_graph_memory_pack_pointer_mutation_snippet.cpp",
            "unsupported-pointer-mutation",
        ),
        (
            "ast-slp-transaction-graph-memory-gather-variable",
            "tests/fixtures/slp_transaction_graph_memory_gather_variable_snippet.cpp",
            "unsupported-variable-gather-index",
            "",
            "unsafe-gather-index",
            "A",
            "memory-pack",
        ),
        (
            "ast-slp-transaction-graph-masked-gather-variable-index",
            "tests/fixtures/slp_transaction_graph_masked_gather_variable_index_snippet.cpp",
            "unsupported-variable-gather-index",
            "",
            "unsafe-gather-index",
            "In",
            "memory-pack",
        ),
        (
            "ast-slp-transaction-graph-guarded-gather-variable-index",
            "tests/fixtures/slp_transaction_graph_guarded_gather_variable_index_snippet.cpp",
            "unsupported-variable-gather-index",
            "",
            "unsafe-gather-index",
            "In",
            "memory-pack",
        ),
        (
            "ast-slp-transaction-graph-memory-gather-duplicate",
            "tests/fixtures/slp_transaction_graph_memory_gather_duplicate_snippet.cpp",
            "unsupported-duplicate-gather-lane",
        ),
        (
            "ast-slp-transaction-graph-conflicting-mask-assignment-memory",
            "tests/fixtures/slp_transaction_graph_conflicting_mask_assignment_memory_snippet.cpp",
            "unsupported-unresolved-memory-mask",
            "",
            "conflicting-assignment",
        ),
        (
            "ast-slp-transaction-graph-incomplete-branch-mask-memory",
            "tests/fixtures/slp_transaction_graph_incomplete_branch_mask_memory_snippet.cpp",
            "unsupported-unresolved-memory-mask",
            "",
            "incomplete-branch-assignment",
            "M0",
        ),
        (
            "ast-slp-transaction-graph-unsafe-mask-index-memory",
            "tests/fixtures/slp_transaction_graph_unsafe_mask_index_memory_snippet.cpp",
            "unsupported-variable-mask-index",
            "",
            "unsafe-mask-index",
            "Mask",
        ),
        (
            "ast-slp-transaction-graph-unknown-mask-index-memory",
            "tests/fixtures/slp_transaction_graph_unknown_mask_index_memory_snippet.cpp",
            "unsupported-variable-mask-index",
            "",
            "unsafe-mask-index",
            "Mask",
        ),
        (
            "ast-slp-scalable-transaction-graph-unsafe-mask-index-memory",
            "tests/fixtures/slp_scalable_transaction_graph_unsafe_mask_index_memory_snippet.cpp",
            "unsupported-variable-mask-index",
            "",
            "unsafe-mask-index",
            "Mask",
        ),
        (
            "ast-slp-scalable-transaction-graph-incomplete-mask-tuple-memory",
            "tests/fixtures/slp_scalable_transaction_graph_incomplete_mask_tuple_memory_snippet.cpp",
            "unsupported-unresolved-memory-mask",
            "",
            "incomplete-branch-assignment",
            "M2",
        ),
        (
            "ast-slp-scalable-transaction-graph-mask-syntax-memory",
            "tests/fixtures/slp_scalable_transaction_graph_mask_syntax_memory_snippet.cpp",
            "unsupported-scalable-masked-memory",
            "",
            "scalable-mask-syntax",
        ),
        (
            "ast-slp-scalable-transaction-graph-missing-passthru-memory",
            "tests/fixtures/slp_scalable_transaction_graph_missing_passthru_memory_snippet.cpp",
            "unsupported-missing-masked-load-passthru",
            "",
            "missing-passthru",
            "P0",
        ),
        (
            "ast-slp-transaction-graph-incomplete-branch-store-mask-memory",
            "tests/fixtures/slp_transaction_graph_incomplete_branch_store_mask_memory_snippet.cpp",
            "unsupported-unresolved-memory-mask",
            "",
            "incomplete-branch-assignment",
            "S0",
            "memory-store",
        ),
        (
            "ast-slp-transaction-graph-unknown-passthru-alias-memory",
            "tests/fixtures/slp_transaction_graph_unknown_passthru_alias_memory_snippet.cpp",
            "unsupported-missing-masked-load-passthru",
            "",
            "missing-passthru",
            "P0",
        ),
        (
            "ast-slp-transaction-graph-load-store-alias-unknown",
            "tests/fixtures/slp_transaction_graph_load_store_alias_unknown_snippet.cpp",
            "unsupported-unresolved-memory-alias",
        ),
        (
            "ast-slp-transaction-graph-symbolic-gather-store-alias-unknown",
            "tests/fixtures/slp_transaction_graph_symbolic_gather_store_alias_unknown_snippet.cpp",
            "unsupported-unresolved-memory-alias",
        ),
        (
            "ast-slp-transaction-graph-store-variable",
            "tests/fixtures/slp_transaction_graph_store_variable_snippet.cpp",
            "unsupported-variable-store-index",
            "",
            "unsafe-store-index",
            "Out",
            "memory-store",
        ),
        (
            "ast-slp-transaction-graph-masked-store-variable-index",
            "tests/fixtures/slp_transaction_graph_masked_store_variable_index_snippet.cpp",
            "unsupported-variable-store-index",
            "",
            "unsafe-store-index",
            "Out",
            "memory-store",
        ),
        (
            "ast-slp-transaction-graph-guarded-store-variable-index",
            "tests/fixtures/slp_transaction_graph_guarded_store_variable_index_snippet.cpp",
            "unsupported-variable-store-index",
            "",
            "unsafe-store-index",
            "Out",
            "memory-store",
        ),
        (
            "ast-slp-transaction-graph-store-duplicate",
            "tests/fixtures/slp_transaction_graph_store_duplicate_snippet.cpp",
            "unsupported-duplicate-scatter-lane",
        ),
        (
            "ast-slp-transaction-graph-helper-recursive-slice",
            "tests/fixtures/slp_transaction_graph_helper_recursive_slice_snippet.cpp",
            "unsupported-recursive-helper-slice",
            "recursiveMask",
        ),
        (
            "ast-slp-transaction-graph-helper-ambiguous-slice",
            "tests/fixtures/slp_transaction_graph_helper_ambiguous_slice_snippet.cpp",
            "unsupported-unresolved-helper-slice",
            "ambiguousMask",
        ),
        (
            "ast-slp-transaction-graph-helper-incomplete-args-slice",
            "tests/fixtures/slp_transaction_graph_helper_incomplete_args_slice_snippet.cpp",
            "unsupported-incomplete-helper-arguments",
            "missingRequiredMask",
        ),
    ]:
        stem, fixture, reason = case[:3]
        helper = case[3] if len(case) > 3 else ""
        detail = case[4] if len(case) > 4 else ""
        temp = case[5] if len(case) > 5 else ""
        role = case[6] if len(case) > 6 else ""
        findings_path = args.work / f"{stem}-findings.json"
        run([
            str(args.miner),
            "--registry",
            str(args.repo / "constraints/pass_constraints.json"),
            str(args.repo / fixture),
            "--",
            "-std=c++17",
        ], findings_path)
        finding = load_first_json(findings_path)
        tx = finding["optimization_transaction"]
        assert "transaction_graph" not in tx
        assert tx["transaction_graph_absent_reasons"] == [reason]
        if detail:
            diagnostics = tx["transaction_graph_absent_diagnostics"]
            assert any(
                diagnostic["reason"] == reason and diagnostic.get("detail") == detail
                for diagnostic in diagnostics
            )
        if temp:
            diagnostics = tx["transaction_graph_absent_diagnostics"]
            assert any(
                diagnostic["reason"] == reason and diagnostic.get("temp") == temp
                for diagnostic in diagnostics
            )
        if role:
            diagnostics = tx["transaction_graph_absent_diagnostics"]
            assert any(
                diagnostic["reason"] == reason and diagnostic.get("role") == role
                for diagnostic in diagnostics
            )
        if helper:
            diagnostics = tx["transaction_graph_absent_diagnostics"]
            assert any(
                diagnostic["reason"] == reason and diagnostic["helper"] == helper
                for diagnostic in diagnostics
            )
            assert all("role" in diagnostic for diagnostic in diagnostics)
            assert all("expansion_stack" in diagnostic for diagnostic in diagnostics)

    unresolved_path = args.work / "ast-slp-transaction-graph-shuffle-unresolved-findings.json"
    run([
        str(args.miner),
        "--registry",
        str(args.repo / "constraints/pass_constraints.json"),
        str(args.repo / "tests/fixtures/slp_transaction_graph_shuffle_unresolved_snippet.cpp"),
        "--",
        "-std=c++17",
    ], unresolved_path)
    unresolved = load_first_json(unresolved_path)
    tx = unresolved["optimization_transaction"]
    assert "transaction_graph" not in tx
    assert tx["transaction_graph_absent_reasons"] == ["unresolved-shuffle-mask"]


if __name__ == "__main__":
    main()
