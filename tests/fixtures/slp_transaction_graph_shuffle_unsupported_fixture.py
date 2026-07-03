#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path


UNSUPPORTED_CASES = [
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
]


def run(cmd: list[str], stdout: Path | None = None) -> None:
    if stdout is None:
        subprocess.run(cmd, check=True)
        return
    with stdout.open("w") as handle:
        subprocess.run(cmd, check=True, stdout=handle)


def load_first_json(path: Path) -> dict:
    return json.loads(path.read_text())[0]


def mine(repo: Path, work: Path, miner: Path, stem: str, fixture: str) -> dict:
    findings_path = work / f"{stem}-findings.json"
    run(
        [
            str(miner),
            "--registry",
            str(repo / "constraints/pass_constraints.json"),
            str(repo / fixture),
            "--",
            "-std=c++17",
        ],
        findings_path,
    )
    return load_first_json(findings_path)


def verify_absent_reason(finding: dict, case: tuple[str, ...]) -> None:
    stem, _fixture, reason = case[:3]
    helper = case[3] if len(case) > 3 else ""
    detail = case[4] if len(case) > 4 else ""
    temp = case[5] if len(case) > 5 else ""
    role = case[6] if len(case) > 6 else ""
    tx = finding["optimization_transaction"]
    assert "transaction_graph" not in tx, stem
    assert tx["transaction_graph_absent_reasons"] == [reason], stem
    diagnostics = tx.get("transaction_graph_absent_diagnostics", [])
    if detail:
        assert any(
            diagnostic["reason"] == reason and diagnostic.get("detail") == detail
            for diagnostic in diagnostics
        ), stem
    if temp:
        assert any(
            diagnostic["reason"] == reason and diagnostic.get("temp") == temp
            for diagnostic in diagnostics
        ), stem
    if role:
        assert any(
            diagnostic["reason"] == reason and diagnostic.get("role") == role
            for diagnostic in diagnostics
        ), stem
    if helper:
        assert any(
            diagnostic["reason"] == reason and diagnostic["helper"] == helper
            for diagnostic in diagnostics
        ), stem
        assert all("role" in diagnostic for diagnostic in diagnostics), stem
        assert all("expansion_stack" in diagnostic for diagnostic in diagnostics), stem


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--miner", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    for case in UNSUPPORTED_CASES:
        stem, fixture = case[:2]
        print(f"[shuffle-unsupported] start {stem}", flush=True)
        verify_absent_reason(mine(args.repo, args.work, args.miner, stem, fixture), case)
        print(f"[shuffle-unsupported] done {stem}", flush=True)

    unresolved = mine(
        args.repo,
        args.work,
        args.miner,
        "ast-slp-transaction-graph-shuffle-unresolved",
        "tests/fixtures/slp_transaction_graph_shuffle_unresolved_snippet.cpp",
    )
    tx = unresolved["optimization_transaction"]
    assert "transaction_graph" not in tx
    assert tx["transaction_graph_absent_reasons"] == ["unresolved-shuffle-mask"]


if __name__ == "__main__":
    main()
