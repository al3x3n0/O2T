#!/usr/bin/env python3
"""Assert access-path provenance on transaction graph memory evidence."""

from __future__ import annotations

import json
import sys
from typing import Any


def arr(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_tx(path: str) -> dict[str, Any]:
    return json.load(open(path, encoding="utf-8"))[0]["optimization_transaction"]


def symbols(paths: list[Any]) -> set[str]:
    return {str(item.get("symbol")) for item in paths if isinstance(item, dict)}


def main() -> int:
    memory_tx = load_tx(sys.argv[1])
    memory_graph = memory_tx["transaction_graph"]
    memory_operand = next(
        operand
        for operand in memory_graph["operands"]
        if operand.get("kind") == "memory-pack"
    )
    memory_symbols = symbols(arr(memory_operand.get("source_access_paths")))
    assert {"Base[0]", "Base[1]", "Base[2]", "Base[3]"} <= memory_symbols
    assert memory_operand["base"] == "A"

    if len(sys.argv) > 2:
        store_tx = load_tx(sys.argv[2])
        store_graph = store_tx["transaction_graph"]
        sink = store_graph["store_sinks"][0]
        store_symbols = symbols(arr(sink.get("source_access_paths")))
        assert {"Out[0]", "Out[1]", "Out[2]", "Out[3]"} <= store_symbols
        assert sink["base"] == "Out"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
