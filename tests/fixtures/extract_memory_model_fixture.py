#!/usr/bin/env python3
"""Cover source-recovered memory-transform verification (extract_memory_model.py).

Asserts the miner recovers each DSE/forwarding fold's op-sequence and the legality facts from
its OWN guards, proves the sound folds over a theory of arrays, and REFUTES a fold whose guards
are insufficient (removes a store without establishing an overwrite) with a concrete
colliding-address witness -- catching an unsound pass from its source. Needs z3 only."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import extract_memory_model as em


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("extract_memory_model_fixture: z3 not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "dse_memory_folds.cpp").read_text()

    # 1) recognition: each fold lifts to the right kind with the right guard facts.
    models = em.mine_source(src)
    assert models["eliminateOverwrittenStore"]["kind"] == "dse-remove", models
    assert models["eliminateOverwrittenStore"]["guards"]["overwrite"] is True
    assert models["forwardStoreToLoad"]["kind"] == "store-forward", models
    assert models["forwardStoreToLoad"]["guards"]["no_alias"] is True
    assert models["forwardStoreToLoadNoAliasMissing"]["kind"] == "store-forward", models
    assert models["forwardStoreToLoadNoAliasMissing"]["guards"]["no_alias"] is False, models
    # the unsound fold lifts but WITHOUT the overwrite guard.
    assert models["eliminateStoreNoOverwriteGuard"]["guards"]["overwrite"] is False, models

    # 2) discharge: sound folds proved; insufficient-guard folds refuted with witnesses.
    by = {r["function"]: r for r in em.verify_source(z3, src)}
    assert by["eliminateOverwrittenStore"]["status"] == "proved", by
    assert by["forwardStoreToLoad"]["status"] == "proved", by
    bad_forward = by["forwardStoreToLoadNoAliasMissing"]
    assert bad_forward["status"] == "refuted" and bad_forward.get("witness"), (
        "missing no-alias store-forward not caught", bad_forward)
    bad = by["eliminateStoreNoOverwriteGuard"]
    assert bad["status"] == "refuted" and bad.get("witness"), ("unsound fold not caught", bad)
    # the witness collides the killer/dead addresses (the guard that's missing).
    w = bad["witness"]
    assert w.get("Dead_p") != w.get("Killing_p"), ("witness should diverge addresses", w)

    # 3) a non-transform helper is declared, not mis-verified.
    none_src = ("namespace llvm { struct StoreInst{}; bool isOverwrite(void*,void*); }\n"
                "using namespace llvm;\n"
                "bool queryOnly(StoreInst &S, StoreInst &T) { return isOverwrite(&S, &T); }\n")
    nm = em.verify_source(z3, none_src)
    assert any(r["function"] == "queryOnly" and r["status"] == "not-a-transform" for r in nm), nm

    # 4) the CLI: 2 proved, 2 refuted, ok (every mined transform decided).
    tool = ROOT / "tools" / "cv-mine-memory-pass.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"refuted": 2' in proc.stdout and '"proved": 2' in proc.stdout, proc.stdout

    print("extract_memory_model_fixture OK: memory transforms recovered from pass source and "
          "discharged; insufficient-guard fold refuted with a colliding-address witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
