#!/usr/bin/env python3
"""Cover source-recovered SLP reduction verification (extract_slp_model.py).

Asserts the miner recovers each reduction fold's operation + FP-ness + fast-math guard, proves
integer reductions, accepts a fast-math-guarded FP reduction (`reassoc-allowed`), and REFUTES an
FP reduction emitted WITHOUT a reassoc guard -- catching an unsound vectorizer from its source.
Needs z3 (with FP theory)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import extract_slp_model as es


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("extract_slp_model_fixture: z3 not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "slp_reduction_folds.cpp").read_text()

    # 1) recognition: operation, FP-ness, and the fast-math guard are recovered.
    by = {r["function"]: r for r in es.verify_source(z3, src)}
    assert by["vectorizeIntAddReduction"]["reduction"] == "add"
    assert by["vectorizeFPAddReductionGuarded"]["fp"] is True
    assert by["vectorizeFPAddReductionGuarded"]["reassoc_guard"] is True
    assert by["vectorizeFPAddReductionUnguarded"]["fp"] is True
    assert by["vectorizeFPAddReductionUnguarded"]["reassoc_guard"] is False

    # 2) discharge: integers proved; FP-guarded allowed; FP-unguarded REFUTED with a witness.
    assert by["vectorizeIntAddReduction"]["status"] == "proved", by
    assert by["vectorizeIntMulReduction"]["status"] == "proved", by
    assert by["vectorizeFPAddReductionGuarded"]["status"] == "reassoc-allowed", by
    bad = by["vectorizeFPAddReductionUnguarded"]
    assert bad["status"] == "refuted" and bad.get("witness"), ("unsound FP reduction not caught", bad)

    # 3) recognition helper directly: an FP call without a guard is the unsound shape.
    m = es.recognize_reduction_fold("Value *f() { return CreateFAddReduce(V, V); }")
    assert m and m["is_fp"] and not m["reassoc_guard"], m
    g = es.recognize_reduction_fold("Value *f() { if (getFastMathFlags(I).allowReassoc()) "
                                    "return CreateFAddReduce(V, V); return 0; }")
    assert g and g["is_fp"] and g["reassoc_guard"], g
    assert es.recognize_reduction_fold("Value *q() { return doNothing(); }") is None

    # 4) the CLI: 3 proved/allowed, 1 refuted, ok.
    tool = ROOT / "tools" / "cv-mine-slp-pass.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"refuted": 1' in proc.stdout and '"proved": 3' in proc.stdout, proc.stdout

    # 5) PACK / lane-mapping recovery: a binop pack's insert/extract lanes are recovered and the
    #    lane mapping is discharged. An identity pack and a consistent reverse pack prove; an
    #    extract-lane SWAP (scalar reads the wrong lane) is REFUTED with a witness.
    psrc = (ROOT / "tests" / "fixtures" / "slp_pack_folds.cpp").read_text()
    pby = {r["function"]: r for r in es.verify_source(z3, psrc)}
    assert pby["vectorizeAddPack"]["kind"] == "pack"
    assert pby["vectorizeAddPack"]["ext_lanes"] == [0, 1]
    assert pby["vectorizeAddPack"]["status"] == "proved", pby
    assert pby["vectorizeMulPackReversed"]["status"] == "proved", pby      # consistent reverse
    pbad = pby["vectorizeAddPackSwappedExtract"]
    assert pbad["status"] == "refuted" and pbad.get("witness"), ("swapped-lane pack not caught", pbad)

    # recognition helper directly: a pack fold is recognized; a reduction fold is NOT a pack.
    pm = es.recognize_pack_fold("void f(){ InsertElement(v,a,0); InsertElement(v,a,1); "
                                "CreateAdd(x,y); ExtractElement(r,0); ExtractElement(r,1); }")
    assert pm and pm["insert_lanes"] == [0, 1] and pm["ext_lanes"] == [0, 1], pm
    assert es.recognize_pack_fold("Value *f(){ return CreateFAddReduce(V,V); }") is None

    # the CLI on the pack fixture: 2 proved, 1 refuted, ok.
    pproc = subprocess.run([sys.executable, str(tool), "--source",
                            str(ROOT / "tests" / "fixtures" / "slp_pack_folds.cpp")],
                           capture_output=True, text=True)
    assert pproc.returncode == 0 and '"refuted": 1' in pproc.stdout and '"proved": 2' in pproc.stdout, pproc.stdout

    print("extract_slp_model_fixture OK: SLP reductions AND binop-pack lane mappings recovered "
          "from source and discharged; an unguarded FP reduction and a swapped-lane pack both "
          "refuted with witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
