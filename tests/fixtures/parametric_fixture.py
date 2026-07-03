#!/usr/bin/env python3
"""Cover the multi-width / parametric-n generalization of the deep contracts (meta/parametric.py).

Asserts every width-parametric deep contract proves at i8/i16/i32/i64 (and the SLP contracts also
at n=2/4/8/16), AND that its single-point corruption is refuted at every width -- so the universal
claim is proof-backed at every width/arity and the teeth bite everywhere, not just at i32/n=4.
Also spot-checks the threaded provers directly and the i32/n=4 backward-compat default. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.meta import parametric as pm
from o2t.validate import slp_model, globalopt_model, loop_structural_model, memory_model


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("parametric_fixture: z3 not found, skipped")
        return 0

    rep = pm.run_parametric(z3)

    # 1) the whole grid passes: every point proves AND its corruption refutes, no failures.
    assert rep["ok"] and not rep["failures"], rep["failures"]
    assert rep["widths"] == [8, 16, 32, 64] and rep["lane_counts"] == [2, 4, 8, 16], rep
    # every grid point's proof held; the teeth-bearing points all bit.
    assert rep["proofs_held"] == rep["points"], rep
    teeth_points = [r for r in rep["rows"] if r["teeth"] is not None]
    assert rep["teeth_bit"] == len(teeth_points) and len(teeth_points) >= 40, rep
    assert set(rep["contracts"]) == {"slp-pack", "slp-reduction", "globalopt-default",
                                     "licm-invariance", "dse-overwrite",
                                     "dce-dead-instruction", "dce-dead-loop-instruction",
                                     "dce-unused-alloca"}, rep["contracts"]

    # 2) coverage: every contract appears at every width; SLP at every n.
    for w in (8, 16, 32, 64):
        present = {r["contract"] for r in rep["rows"] if r["width"] == w}
        assert present == set(rep["contracts"]), (w, present)
    for n in (2, 4, 8, 16):
        assert any(r["contract"] == "slp-pack" and r["n"] == n for r in rep["rows"]), n

    # 3) the threaded provers directly: a width where the SOUND case proves and the corruption
    #    refutes -- at a NON-default width (i8) and a NON-default arity (n=16).
    assert slp_model.prove_pack_binop(z3, "add", 16, list(range(16)), list(range(16)), width=8)[0] == "proved"
    bad = list(range(16)); bad[0], bad[1] = bad[1], bad[0]
    assert slp_model.prove_pack_binop(z3, "add", 16, list(range(16)), bad, width=8)[0] == "refuted"
    assert globalopt_model.prove_initializer_default(z3, [], external=True, width=64)[0] == "refuted"
    assert loop_structural_model.prove_hoist_invariance(z3, False, width=16)[0] == "refuted"

    # 4) BACKWARD COMPAT: the default (no width arg) is exactly the i32 behavior.
    assert slp_model.prove_pack_binop(z3, "add", 4, [0, 1, 2, 3], [0, 1, 2, 3])[0] == "proved"
    name, before, after, observable, assumptions = memory_model.CONTRACTS[0]
    assert memory_model.prove_memory_transform(z3, before, after, observable, assumptions)[0] == "proved"

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-prove-parametric.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"failures": 0' in proc.stdout, proc.stdout

    print(f"parametric_fixture OK: {len(rep['contracts'])} deep contracts re-proved across "
          f"{rep['points']} (width x n) points -- all proofs held at i8/i16/i32/i64, all "
          f"{rep['teeth_bit']} corruptions refuted at every width; i32/n=4 default unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
