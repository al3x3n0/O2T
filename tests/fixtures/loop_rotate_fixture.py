#!/usr/bin/env python3
"""Cover UNBOUNDED loop-rotate validation (validate/loop_rotate.py).

Asserts that the real `opt -passes=loop-rotate` output is proved equivalent to the original loop
for ALL trip counts -- by reconstructing a canonical guard-on-current model from the rotated
do-while IR, self-verifying it against the emitted instructions, and proving equivalence with
auto-inferred relation (the permuted state) -- with two-sided teeth: a corrupted BOTTOM GUARD fails
a SELF-CHECK (declined, the reconstruction no longer matches the IR), and a corrupted BODY fails the
equivalence proof (refuted). Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import loop_rotate as R


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("loop_rotate_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "loop_rotate_cases.ll").read_text()
    rot = R.run_rotate(src, opt)
    assert rot is not None, "opt -passes=loop-rotate failed"
    # rotation really happened (non-vacuous): a pre-guard + lcssa appeared.
    assert "lr.ph" in rot and "lcssa" in rot, ("loop-rotate did not rotate", rot)

    # 1) both rotated loops are proved equivalent to the original for ALL trip counts, with the
    #    self-checks passing and the permuted state auto-related.
    for fn in ("sumloop", "polyloop"):
        r = R.validate_rotate(z3, src, rot, fn)
        assert r["status"] == "proved", ("loop-rotate not proved", fn, r)
        assert r["self_checked"] == ["bottom-guard", "loop-result"], r
        assert r["parts"] == {"init": "proved", "guard": "proved",
                              "step": "proved", "result": "proved"}, r

    # 2) TEETH (self-check): corrupt the BOTTOM GUARD in the rotated IR -> the reconstruction no
    #    longer matches the emitted instructions -> declined via the bottom-guard self-check.
    bg = rot.replace("%c = icmp slt i32 %i.n, %n", "%c = icmp sgt i32 %i.n, %n", 1)
    assert bg != rot, "no bottom guard to corrupt"
    rbad = R.validate_rotate(z3, src, bg, "sumloop")
    assert rbad["status"] == "unsupported" and "bottom-guard" in rbad["reason"], rbad

    # 3) TEETH (equivalence): corrupt the BODY step (acc + i -> acc + 1) -> the canonical model is
    #    still faithfully reconstructed (self-checks pass) but is NOT equivalent -> refuted.
    st = rot.replace("%acc.n = add i32 %acc3, %i2", "%acc.n = add i32 %acc3, 1", 1)
    assert st != rot, "no body step to corrupt"
    rstep = R.validate_rotate(z3, src, st, "sumloop")
    assert rstep["status"] == "refuted", ("a wrong body not caught", rstep)

    # 4) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-loop-rotate.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 2' in proc.stdout, proc.stdout

    print("loop_rotate_fixture OK: real `opt -passes=loop-rotate` proved equivalent to the original "
          "loop for ALL trip counts (canonical model reconstructed + self-verified, permuted state "
          "auto-related); a corrupted bottom guard fails a self-check, a corrupted body is refuted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
