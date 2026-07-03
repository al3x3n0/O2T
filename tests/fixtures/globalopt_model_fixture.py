#!/usr/bin/env python3
"""Cover the deep GlobalOpt dead-initializer model (validate/globalopt_model.py).

Asserts that defaulting an internal global's initializer to null is proved observationally
behavior-preserving when the initializer is unobservable (no uses, or every load after a store),
and REFUTED with an `init != 0` witness when it is observable -- a read-before-store, a read-only
global, or an externally-visible (non-internal) global. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import globalopt_model as g


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("globalopt_model_fixture: z3 not found, skipped")
        return 0

    # 1) the canonical contracts: 2 proved, 3 refuted (teeth), all matching expectations.
    res = g.run_contracts(z3)
    assert all(r["ok"] for r in res.values()), res
    assert res["default-internal-use-empty"]["status"] == "proved"
    assert res["default-stored-before-read"]["status"] == "proved"
    for teeth in ("default-read-before-store", "default-read-only", "default-external-linkage"):
        assert res[teeth]["status"] == "refuted" and res[teeth]["witness"], (teeth, res[teeth])

    # 2) the prover directly: never-read internal -> proved; read-only internal -> refuted.
    assert g.prove_initializer_default(z3, [])[0] == "proved"
    assert g.prove_initializer_default(z3, [("store", None), ("load",)])[0] == "proved"
    st, info = g.prove_initializer_default(z3, [("load",), ("store", None)])
    assert st == "refuted" and info.get("model"), st
    # external linkage makes even a never-read global observable -> refuted.
    assert g.prove_initializer_default(z3, [], external=True)[0] == "refuted"

    # 3) the CLI: 5 contracts, 2 proved / 3 refuted, ok.
    tool = ROOT / "tools" / "cv-validate-globalopt.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout
    assert '"proved": 2' in proc.stdout and '"refuted": 3' in proc.stdout, proc.stdout

    print("globalopt_model_fixture OK: dead-initializer defaulting proved behavior-preserving "
          "when the initializer is unobservable; read-before-store / read-only / external-linkage "
          "refuted with witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
