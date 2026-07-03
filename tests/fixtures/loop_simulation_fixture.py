#!/usr/bin/env python3
"""Cover simulation-relation loop equivalence (validate/loop_simulation.py).

Asserts that two loops with a DIFFERENT state shape (a redundant extra accumulator -> 3 vs 2
loop-carried states, so positional equality cannot apply) are proved equivalent for ALL trip counts
under an inductive simulation relation R -- with the four obligations (init / guard / step / result)
all valid -- and that the prover has two-sided teeth: a miscompiled loop fails `step` and an
insufficient relation fails `result`, each with a witness. Also pins that an UNSOUND relation (an
induction-variable offset that diverges under bitvector overflow) is refuted, not accepted. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_simulation as S


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("loop_simulation_fixture: z3 not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "loop_simulation_cases.ll").read_text()

    # 1) the redundant-state simulation proves for ALL trip counts (all four obligations).
    R = S.mapped_relation([(0, 0), (1, 1)], extra=[(1, 2)])
    r = S.validate_simulation(z3, src, "base", src, "dup", R)
    assert r["status"] == "proved", ("simulation not proved", r)
    assert r["parts"] == {"init": "proved", "guard": "proved",
                          "step": "proved", "result": "proved"}, r

    # 2) TEETH (step): a miscompiled `dup` body (acc2 += 1 instead of += i) fails the STEP obligation.
    bad = src.replace("%acc2.n = add i32 %acc2, %i", "%acc2.n = add i32 %acc2, 1", 1)
    rs = S.validate_simulation(z3, src, "base", bad, "dup", R)
    assert rs["status"] == "refuted" and rs.get("failed") == "step" and rs.get("witness"), rs

    # 3) TEETH (R): an insufficient relation (omits the redundant acc2 == acc) fails RESULT --
    #    the relation must be strong enough to carry the proof to the exit.
    weak = S.mapped_relation([(0, 0), (1, 1)])
    rw = S.validate_simulation(z3, src, "base", src, "dup", weak)
    assert rw["status"] == "refuted" and rw.get("failed") == "result", rw

    # 4) UNSOUND relation rejected: an IV offset (t == s + 5) makes the guards diverge under BV
    #    overflow, so it is NOT a valid simulation -- the prover refutes it (does not falsely accept).
    self_eq = S.equality_relation(2)
    assert S.validate_simulation(z3, src, "base", src, "base", self_eq)["status"] == "proved"
    offset = S.mapped_relation([(0, 0), (1, 1)])

    def bad_off(sv, tv):  # t_iv == s_iv + 5 (diverges on overflow)
        return f"(and (= {tv[0]} (bvadd {sv[0]} #x00000005)) (= {sv[1]} {tv[1]}))"
    assert S.validate_simulation(z3, src, "base", src, "base", bad_off)["status"] == "refuted"

    # 5) AUTOMATIC R-INFERENCE (Houdini): no hand-given relation -- the prover infers it and proves.
    auto = S.validate_simulation_auto(z3, src, "base", src, "dup")
    assert auto["status"] == "proved", ("auto-inference failed to prove", auto)
    assert auto["inferred_atoms"] == [(0, 0), (1, 1), (1, 2)], \
        ("did not recover the redundant-state relation", auto["inferred_atoms"])
    # a genuinely inequivalent loop: inference drops the non-inductive atom -> cannot prove -> refuted.
    bad2 = src.replace("%acc2.n = add i32 %acc2, %i", "%acc2.n = add i32 %acc2, 1", 1)
    assert S.validate_simulation_auto(z3, src, "base", bad2, "dup")["status"] == "refuted"

    # 5a) STRENGTH REDUCTION: an affine relation (j == 3*i) is inferred automatically -- pure
    #     equality cannot bridge a multiply replaced by a strided accumulator. Proved for all n;
    #     a wrong stride is refuted.
    sr = S.validate_simulation_auto(z3, src, "withmul", src, "strred")
    assert sr["status"] == "proved", ("strength-reduction not proved", sr)
    assert any(at[0] == "affine" for at in sr["inferred_atoms"] if len(at) > 2), \
        ("no affine atom inferred", sr["inferred_atoms"])
    wrong = src.replace("%j.n = add i32 %j, 3", "%j.n = add i32 %j, 2", 1)
    assert S.validate_simulation_auto(z3, src, "withmul", wrong, "strred")["status"] == "refuted"

    # 5a2) NON-UNIT-STRIDE strength reduction: IV strides by 2, accumulator by 10 -> coefficient
    #      c = 10/2 = 5 inferred (j == 5*i), proved for all n; a misaligned stride is refuted.
    nu = S.validate_simulation_auto(z3, src, "mul2", src, "sr2")
    assert nu["status"] == "proved", ("non-unit-stride strength reduction not proved", nu)
    assert any(at[0] == "affine" and at[3] == "(_ bv5 32)" for at in nu["inferred_atoms"]
               if len(at) > 2), ("expected coefficient 5", nu["inferred_atoms"])
    misaligned = src.replace("%j.n = add i32 %j, 10", "%j.n = add i32 %j, 9", 1)
    assert S.validate_simulation_auto(z3, src, "mul2", misaligned, "sr2")["status"] == "refuted"

    # 5b) auto-inference on a REAL pass (instcombine inside a loop), if opt is available: the
    #     identity relation is inferred and the loops proved equal for all n -- no hand-given R.
    opt = shutil.which("opt") or ("/opt/homebrew/opt/llvm@18/bin/opt"
                                  if Path("/opt/homebrew/opt/llvm@18/bin/opt").exists() else None)
    if opt:
        from o2t.validate import scalar_ir as sc
        lf = (ROOT / "tests" / "fixtures" / "loop_induction_cases.ll").read_text()
        folded = sc.run_passes(lf, "instcombine", opt)
        ra = S.validate_simulation_auto(z3, lf, "loopfold", folded, "loopfold")
        assert ra["status"] == "proved" and ra["inferred_atoms"] == [(0, 0), (1, 1)], ra

    # 6) the CLI (canonical contracts incl. auto-inference) agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-loop-simulation.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 4' in proc.stdout, proc.stdout

    print("loop_simulation_fixture OK: structurally-different loops proved equivalent for ALL trip "
          "counts under an AUTO-INFERRED simulation relation (Houdini over equality + affine atoms) "
          "-- redundant-state, a real instcombine-in-loop (identity), and STRENGTH REDUCTION "
          "(j == 3*i affine inferred); miscompiled loop -> step, insufficient R -> result, unsound R "
          "refuted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
