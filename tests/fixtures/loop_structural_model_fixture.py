#!/usr/bin/env python3
"""Cover the deep LICM hoist model (validate/loop_structural_model.py).

Asserts that hoisting a loop-invariant, safe-to-execute op out of a loop is proved
behavior-preserving, and REFUTED with a witness when illegal: a varying (non-invariant) operand
(stale value) or a trapping op that is neither guaranteed-to-execute nor speculatable (a new
trap). Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_structural_model as ls


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("loop_structural_model_fixture: z3 not found, skipped")
        return 0

    # 1) canonical contracts: 3 proved, 2 refuted (teeth), all matching expectations.
    res = ls.run_contracts(z3)
    assert all(r["ok"] for r in res.values()), res
    assert res["hoist-invariant-operand"]["status"] == "proved"
    assert res["hoist-guaranteed-execute"]["status"] == "proved"
    assert res["hoist-speculatable"]["status"] == "proved"
    for teeth in ("hoist-variant-operand", "hoist-trapping-not-guaranteed"):
        assert res[teeth]["status"] == "refuted" and res[teeth]["witness"], (teeth, res[teeth])

    # 2) the provers directly: invariance and safety each have two-sided teeth.
    assert ls.prove_hoist_invariance(z3, invariant=True)[0] == "proved"
    st, info = ls.prove_hoist_invariance(z3, invariant=False)
    assert st == "refuted" and info.get("model"), st          # stale value
    assert ls.prove_hoist_safety(z3, guaranteed=True, speculatable=False)[0] == "proved"
    assert ls.prove_hoist_safety(z3, guaranteed=False, speculatable=True)[0] == "proved"
    st2, info2 = ls.prove_hoist_safety(z3, guaranteed=False, speculatable=False)
    assert st2 == "refuted" and info2.get("model"), st2       # new trap

    # 3) the CLI: 5 contracts, 3 proved / 2 refuted, ok.
    tool = ROOT / "tools" / "cv-validate-licm.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout
    assert '"proved": 3' in proc.stdout and '"refuted": 2' in proc.stdout, proc.stdout

    print("loop_structural_model_fixture OK: LICM hoist proved sound when invariant + safe; a "
          "varying operand (stale value) and a trapping not-guaranteed op (new trap) refuted "
          "with witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
