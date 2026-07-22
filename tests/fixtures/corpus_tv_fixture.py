#!/usr/bin/env python3
"""Whole-function translation validation over a real InstCombine corpus (Track B, end-to-end).

The per-fold observational check grounds ONE recovered fold against opt on minimal IR. This goes
end-to-end on REAL code: for every function in a corpus it runs the ACTUAL `opt -passes=instcombine`
and proves the WHOLE function's transformation sound (o2t/validate/corpus_tv.py -> scalar_ir's
Alive2-style refinement TV). It verifies the COMPOSITION of whatever folds fired, not an isolated
obligation -- directly attacking the "obligations, not passes" gap.

The gated corpus is 14 verbatim single-BB scalar functions from LLVM 18's own InstCombine tests
(and/or/xor/add.ll); each is transformed by real opt and each transform is proved sound. Teeth: a
hand-built WRONG optimization (`and X, 0 -> X`) is REFUTED with a witness, so a real miscompile would
not slip through. Anything scalar_ir cannot model would decline (unsupported), never mis-prove.
(Measured reach on the full files, not gated here: 93/207 whole-function transforms proved on
InstCombine/and.ll alone, 0 false refutations.) Needs z3 AND opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.corpus_tv import validate_file  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "vendor_folds" / "instcombine_scalar_tests.ll"


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("corpus_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. Every function in the real-test corpus: opt's WHOLE-function transform is proved sound.
    result = validate_file(z3, CORPUS.read_text(), opt)
    assert result["opt_ok"], "opt must run on the corpus"
    counts = result["counts"]
    assert counts.get("refuted", 0) == 0, ("no real transform may refute", result["functions"])
    assert counts.get("error", 0) == 0, ("no function may error", result["functions"])
    proved = counts.get("proved", 0)
    assert proved == len(result["functions"]) >= 14, ("all corpus transforms must prove", counts)

    # 2. TEETH: a hand-built WRONG "optimization" (and X, 0 -> X, which is unsound -- it is 0) is
    #    REFUTED with a witness by whole-function TV, so a miscompiling pass would be caught.
    src = "define i32 @t(i32 %A) {\n  %r = and i32 %A, 0\n  ret i32 %r\n}\n"
    bad_opt = "define i32 @t(i32 %A) {\n  ret i32 %A\n}\n"
    v = si.validate_transform(z3, src, bad_opt, "t")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong optimization must refute", v)

    # 3. ...and the CORRECT optimization of the same function proves (the teeth are not vacuous).
    good_opt = "define i32 @t(i32 %A) {\n  ret i32 0\n}\n"
    assert si.validate_transform(z3, src, good_opt, "t")["status"] == "proved"

    print(f"corpus_tv_fixture OK: whole-function translation validation proved {proved} real "
          "InstCombine test transforms sound END-TO-END (real IR -> real `opt -passes=instcombine` -> "
          "Alive2-style refinement proof over the WHOLE function, verifying the composition of whatever "
          "folds fired), 0 refuted; a hand-built wrong optimization (and X,0 -> X) is refuted with a "
          "witness while the correct one proves -- the miscompile teeth bite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
