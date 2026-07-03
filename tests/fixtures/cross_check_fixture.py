#!/usr/bin/env python3
"""Cover witness re-validation + second-solver cross-check (meta/cross_check.py).

Asserts that every refutation's witness is independently confirmed (substituting it makes the
obligation false), that proved obligations are replayed through every available solver, and that
the harness has its OWN teeth: a deliberately broken second solver that answers `sat` to a proved
obligation is caught as a disagreement, and a bogus (non-falsifying) witness is NOT confirmed.
cvc5/cvc4 are auto-detected; the suite gates on the z3-only witness re-validation and the
mechanism teeth, so it does not require a second solver to be installed. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.meta import cross_check as cc
from o2t.validate import slp_model

FX = ROOT / "tests" / "fixtures"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("cross_check_fixture: z3 not found, skipped")
        return 0

    rep = cc.run_cross_check(z3)

    # 1) witness re-validation gates today (z3 alone): every refutation's witness is confirmed.
    assert rep["reval_ok"] and rep["ok"], rep
    assert rep["witnesses_revalidated"] >= 4, rep
    assert rep["witnesses_confirmed"] == rep["witnesses_revalidated"], rep
    for r in rep["reval_rows"]:
        if not r["array"]:
            assert r["refuted"] and r["confirmed"], ("witness not confirmed", r)
        else:
            assert r["confirmed"] is None, ("array witness should be skipped, not claimed", r)

    # 2) every proved obligation was replayed and is unsat on z3 (cross-check mechanism runs).
    assert {r["obligation"] for r in rep["proof_rows"]} >= {
        "slp-pack", "slp-reduction", "globalopt-default", "licm-invariance",
        "dce-dead-instruction", "dce-dead-loop-instruction", "dce-unused-alloca",
        "licm-safety", "dse-overwrite"}, rep
    assert all(r["agree"] for r in rep["proof_rows"]), rep

    # 2b) When a real independent solver (bitwuzla/cvc5/cvc4) is auto-detected, the cross-check is a
    #     genuine INDEPENDENT pass: it is marked cross-checked, every obligation agrees across all
    #     solvers, and `ok` reflects that agreement (not just z3-self-consistency).
    detected = [name for name, _ in cc.detect_solvers(z3) if name != "z3"]
    if detected:
        assert rep["second_solver"] and rep["cross_checked"] and rep["cross_agree"], rep
        assert all(len(r["results"]) >= 2 and r["cross_checked"] for r in rep["proof_rows"]), rep
        assert rep["ok"] and rep["cross_agree"], ("auto-detected second solver must gate ok", rep)

    # 3) CROSS-CHECK TEETH: a second solver that always answers `sat` DISAGREES on a proved
    #    obligation -> caught (cross_agree False, ok False). Proves the harness isn't trivial.
    stub = str((FX / "cross_check_sat_stub.sh").resolve())
    bad = cc.run_cross_check(z3, extra_solvers=[("fakesat", stub)])
    assert bad["second_solver"] and not bad["cross_agree"] and not bad["ok"], \
        ("a lying second solver must be caught", bad)
    # ...and using z3 itself as the "second solver" agrees on every obligation (multi-solver path).
    twin = cc.run_cross_check(z3, extra_solvers=[("z3b", z3)])
    assert twin["second_solver"] and twin["cross_agree"] and twin["ok"], twin

    # 4) RE-VALIDATION TEETH: a bogus witness (arbitrary values that do NOT falsify a valid goal)
    #    is NOT confirmed -- asserting a valid goal under any assignment is satisfiable, not unsat.
    logic, decls, premises, goal = slp_model.pack_obligation("add", 4, [0, 1, 2, 3], [0, 1, 2, 3])
    bogus = {f"a{i}": "#x00000000" for i in range(4)}
    bogus.update({f"b{i}": "#x00000000" for i in range(4)})
    head = cc.run_solver("z3", z3, cc.revalidation_smt(logic, decls, premises, goal, bogus))
    assert head == "sat", ("a sound goal must stay satisfiable under a non-counterexample", head)

    # 5) the CLI agrees and exits 0 (z3-only witness re-validation gates).
    tool = ROOT / "tools" / "cv-cross-check.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout
    assert '"witnesses_confirmed": 7' in proc.stdout, proc.stdout

    print(f"cross_check_fixture OK: {rep['witnesses_confirmed']} refutation witnesses independently "
          f"re-validated; {len(rep['proof_rows'])} proved obligations replayed across solvers "
          f"{rep['solvers']} (independent={rep['second_solver']}); a lying second solver and a bogus "
          "witness are both caught (bitwuzla/cvc5/cvc4 auto-detected when present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
