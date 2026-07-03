#!/usr/bin/env python3
"""Assert structured access-path provenance in mined SLP source graphs."""

from __future__ import annotations

import json
import sys
from typing import Any


def obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def arr(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def has_segment(fact: dict[str, Any], kind: str, value: str) -> bool:
    for segment in arr(fact.get("segments")):
        segment = obj(segment)
        if segment.get("kind") != kind:
            continue
        if kind == "member" and segment.get("name") == value:
            return True
        if kind == "index" and segment.get("source") == value:
            return True
    return False


def find_fact(
    facts: list[Any], *, role: str, function: str, symbol: str
) -> dict[str, Any]:
    for fact in facts:
        fact = obj(fact)
        if (
            fact.get("role") == role
            and fact.get("function") == function
            and fact.get("symbol") == symbol
        ):
            return fact
    raise AssertionError(f"missing {role} fact for {function}:{symbol}")


def main() -> int:
    tx = json.load(open(sys.argv[1], encoding="utf-8"))[0][
        "optimization_transaction"
    ]
    spg = tx["source_program_graph"]
    facts = arr(spg.get("access_path_facts"))
    assert facts
    assert arr(tx.get("source_access_path_provenance")) == facts

    s0_fact = find_fact(
        facts, role="use", function="buildLHS", symbol="Entry.Scalars[0]"
    )
    assert s0_fact["base"] == "Entry"
    assert has_segment(s0_fact, "member", "Scalars")
    assert has_segment(s0_fact, "index", "0")

    masked_fact = find_fact(
        facts,
        role="use",
        function="buildLHS",
        symbol="Entry.Scalars[ReorderMask[1]]",
    )
    assert masked_fact["base"] == "Entry"
    assert has_segment(masked_fact, "index", "ReorderMask[1]")

    nested_fact = find_fact(
        facts,
        role="use",
        function="discoverCandidate",
        symbol="Tree.Entries[0].Scalars[ReorderMask[1]]",
    )
    assert nested_fact["base"] == "Tree"
    assert has_segment(nested_fact, "member", "Entries")
    assert has_segment(nested_fact, "member", "Scalars")

    pointer_fact = find_fact(
        facts, role="use", function="inspectPointer", symbol="Entry->Scalars[0]"
    )
    assert pointer_fact["base"] == "Entry"
    assert has_segment(pointer_fact, "member", "Scalars")

    def_fact = find_fact(
        facts,
        role="def",
        function="replaceExternalUses",
        symbol="Entry.Scalars[1]",
    )
    assert def_fact["base"] == "Entry"

    lhs_paths = arr(tx["operand_lane_mappings"]["lhs"].get("source_access_paths"))
    assert any(p.get("symbol") == "Entry.Scalars[0]" for p in lhs_paths)
    assert any(
        p.get("symbol") == "Entry.Scalars[ReorderMask[1]]" for p in lhs_paths
    )

    result_paths = arr(tx["result_lane_mapping"].get("source_access_paths"))
    assert any(p.get("symbol") == "Entry.Scalars[1]" and p.get("role") == "def" for p in result_paths)

    nodes = spg["nodes"]
    s0_node = next(
        n
        for n in nodes
        if n.get("function") == "buildLHS" and "Value *S0" in n.get("source", "")
    )
    edge = next(
        e
        for e in spg["dfg_edges"]
        if e.get("to") == s0_node["id"] and e.get("symbol") == "Entry.Scalars[0]"
    )
    access_path = edge["access_path"]
    assert access_path["symbol"] == "Entry.Scalars[0]"
    assert access_path["base"] == "Entry"
    assert access_path["definition_match"] == "base-fallback"
    assert access_path["matched_base"] == "Entry"

    checks = arr(tx["source_slice"]["contract"].get("checks"))
    check = next(c for c in checks if c.get("id") == "source-graph:access-path-provenance")
    assert check["status"] == "passed"
    assert check["witness"]["access_path_facts"] >= 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
