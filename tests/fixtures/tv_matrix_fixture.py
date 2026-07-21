#!/usr/bin/env python3
"""E1: the closed-loop translation-validation coverage matrix is gated -- zero false refutations.

Runs the ACTUAL loop passes over the benchmark and pins the headline soundness invariant: across
every (sound pass x loop function) cell, NO output-not-preserved verdict -- correct LLVM is never
falsely accused. Separately, the teeth: a mutated recurrence (one phi initial value corrupted in
opt's output) MUST be refuted with a witness, proving a real miscompile would be caught. Every
cell is a positive verdict (proved / proved-closed-form) or the honest `loop-eliminated` (a real
transform the closed-form validator does not yet cover), never a silent pass. Needs z3 AND opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("tv_matrix_fixture: z3 or opt(18) not found, skipped")
        return 0

    r = tv.run(opt, z3)

    # 1. HEADLINE: a full matrix of real cells, and ZERO false refutations on sound passes.
    assert r["cells"] >= 30, r["cells"]
    assert r["false_refutations"] == [], r["false_refutations"]

    # 2. Real positive coverage exists (not everything punted to loop-eliminated): a solid
    #    majority of cells are proved / proved-closed-form.
    assert r["positive_verdicts"] >= r["cells"] // 2, (r["positive_verdicts"], r["cells"])

    # 3. Every cell is a KNOWN status -- positive, the honest loop-eliminated, or (never here) a
    #    refutation; nothing silently unclassified.
    known = tv.POSITIVE | {"loop-eliminated", tv.NEGATIVE}
    for p, row in r["matrix"].items():
        for f, s in row.items():
            assert s in known, (p, f, s)

    # 4. TEETH (the miscompile-catch proof): a corrupted recurrence is refuted with a witness.
    t = tv.teeth(opt, z3)
    assert t["caught"] and t["witness"], t

    print(f"tv_matrix_fixture OK: {r['cells']} translation-validation cells across "
          f"{len(r['passes'])} real opt passes x {len(r['functions'])} loops -- "
          f"{r['positive_verdicts']} positive verdicts, ZERO false refutations on sound LLVM, "
          "every cell a positive or the honest loop-eliminated; a mutated recurrence is refuted "
          "with a witness (a real miscompile would be caught)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
