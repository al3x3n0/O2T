#!/usr/bin/env python3
"""Cover the deep SLP/(G)SLP verifier (slp_model.py): lane mapping + reduction associativity.

Asserts a consistent pack/extract lane mapping is proved value-equivalent to the scalars and a
mismatched one is REFUTED; integer reductions equal their vector (tree) reduce while FLOATING-
POINT reductions without fast-math are REFUTED (the reassociation changes the result) -- the
real SLP-reduction correctness subtlety, with two-sided teeth. Needs z3 (with FP theory)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import slp_model as slp


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("slp_model_fixture: z3 not found, skipped")
        return 0

    # 1) LANE MAPPING: a consistent pack/extract proves; a mismatched extract is refuted.
    assert slp.prove_pack_binop(z3, "add", 4, [0, 1, 2, 3], [0, 1, 2, 3])[0] == "proved"
    bad = slp.prove_pack_binop(z3, "add", 4, [0, 1, 2, 3], [1, 0, 2, 3])
    assert bad[0] == "refuted" and bad[1].get("model"), ("lane-mapping bug not caught", bad)
    # a non-trivial but CONSISTENT permutation (pack and extract are inverses) is still sound.
    assert slp.prove_pack_binop(z3, "add", 4, [2, 0, 3, 1], [1, 3, 0, 2])[0] == "proved"

    # 2) REDUCTION: integer ops are associative -> the tree reduce equals the scalar chain.
    for op in ("add", "mul", "and", "or", "xor"):
        assert slp.prove_reduction(z3, op, 4, fp=False)[0] == "proved", op

    # 3) FP TEETH: a floating-point reduction WITHOUT fast-math reassociates -> REFUTED.
    fp_add = slp.prove_reduction(z3, "add", 4, fp=True)
    assert fp_add[0] == "refuted" and fp_add[1].get("model"), ("FP add reassoc not caught", fp_add)
    assert slp.prove_reduction(z3, "mul", 4, fp=True)[0] == "refuted", "FP mul reassoc not caught"
    # ...and the integer counterpart of the SAME shape IS sound (the difference is FP semantics).
    assert slp.prove_reduction(z3, "add", 4, fp=False)[0] == "proved"

    # 4) the CLI runs all contracts (8) clean.
    tool = ROOT / "tools" / "cv-validate-slp.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout

    print("slp_model_fixture OK: SLP lane mapping proved (mismatch refuted); integer reductions "
          "proved associative; FP reductions without fast-math refuted with a witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
