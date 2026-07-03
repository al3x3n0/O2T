#!/usr/bin/env python3
"""Optional real CBMC/ESBMC smoke tests for generated modelcheck harnesses."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.symexec import modelcheck as M  # noqa: E402
from o2t.symexec.modelcheck_cfg import run_cfg_source_modelcheck  # noqa: E402
from o2t.symexec.modelcheck_intents import run_intent_modelcheck  # noqa: E402
from o2t.symexec.modelcheck_memory import run_memory_source_modelcheck  # noqa: E402

FX = ROOT / "tests" / "fixtures"


def var(name: str) -> dict:
    return {"op": "var", "name": name}


def c(value: int) -> dict:
    return {"op": "bvconst", "bits": 32, "value": value}


def scalar_records() -> list[dict]:
    return [
        {
            "marker": "probe.synthetic.real-add-zero",
            "file": "real-sound.cpp",
            "line": 1,
            "intent_candidate": {
                "formal": {
                    "domain": "scalar-bv32",
                    "equivalence": "result",
                    "variables": ["x"],
                    "poison_variables": [],
                    "refinement": "refinement",
                    "before": {"op": "bvadd", "args": [var("x"), c(0)]},
                    "after": var("x"),
                },
            },
        },
        {
            "marker": "probe.synthetic.real-add-one",
            "file": "real-bad.cpp",
            "line": 2,
            "intent_candidate": {
                "formal": {
                    "domain": "scalar-bv32",
                    "equivalence": "result",
                    "variables": ["x"],
                    "poison_variables": [],
                    "refinement": "refinement",
                    "before": var("x"),
                    "after": {"op": "bvadd", "args": [var("x"), c(1)]},
                },
            },
        },
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    engine_path, engine = M.resolve_engine("auto")
    if engine_path is None:
        print("modelcheck_generated_real_fixture: cbmc/esbmc not found, skipped")
        return 0

    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        scalar_input = td / "scalar.jsonl"
        write_jsonl(scalar_input, scalar_records())
        scalar = run_intent_modelcheck(
            scalar_input,
            td / "scalar",
            engine=engine,
            timeout_s=60,
        )
        assert scalar["generated"] == 2 and scalar["proved"] == 1 and scalar["refuted"] == 1, scalar

        cfg = run_cfg_source_modelcheck(
            [FX / "cfg_ifconv_folds.cpp"],
            td / "cfg",
            engine=engine,
            timeout_s=60,
        )
        assert cfg["generated"] == 3 and cfg["proved"] == 2 and cfg["refuted"] == 1, cfg

        memory = run_memory_source_modelcheck(
            [FX / "dse_memory_folds.cpp"],
            td / "memory",
            engine=engine,
            timeout_s=60,
        )
        assert memory["generated"] == 4 and memory["proved"] == 2 and memory["refuted"] == 2, memory

    print(f"modelcheck_generated_real_fixture OK: {engine} checked generated scalar, CFG, "
          "and memory harnesses with expected proved/refuted teeth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
