#!/usr/bin/env python3
"""Operationalized cross-check: run the INDEPENDENT oracles over O2T's real corpus verdicts.

The weak-spot fixes gave Track B two oracles that do NOT share its SMT encoding -- concrete_tv (lli
value execution) and alive_diff (reference Alive2, poison/undef/UB). This fixture runs BOTH over the
actual whole-function TV verdicts on a real vendored InstCombine corpus (o2t/validate/corpus_tv.py's
cross_check_file), not just demo pairs: for every function O2T PROVES, lli must agree on values and
Alive2 must not call it incorrect.

The assertion `disagreements == []` is the standing guard -- if a future encoding change makes Track B
FALSELY prove any corpus function, an independent oracle contradicts it and this fixture fails. Today
it confirms O2T's proved set on real code is backed by two oracles. Needs z3 + opt + lli + alive-tv.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402
from o2t.validate.corpus_tv import _extract_define, cross_check_file  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "vendor_folds" / "instcombine_scalar_tests.ll"


def main() -> int:
    z3 = shutil.which("z3")
    opt = toolchain.resolve_opt("opt")
    lli = toolchain.resolve_lli()
    alive = shutil.which("alive-tv")
    if not (z3 and opt and lli and alive):
        print("corpus_cross_check_fixture: needs z3 + opt + lli + alive-tv, skipped")
        return 0

    r = cross_check_file(z3, CORPUS.read_text(), opt, lli_bin=lli, alive_bin=alive)
    proved = r["base"].get("proved", 0)
    assert proved > 0, ("Track B must prove some corpus functions", r["base"])
    assert r["cross_checked"] == proved, ("every proved function must be cross-checked", r)
    assert r["disagreements"] == [], \
        ("an INDEPENDENT oracle contradicts an O2T `proved` -- a FALSE PROOF on real code", r["disagreements"])

    # THE PAIR HANDED TO THE ORACLE MUST BE THE TRANSFORM THAT HAPPENED. A function extracted without
    # its module's `target datalayout` is a different program: LLVM falls back to defaults, so the
    # alignment `opt` inferred and wrote onto the optimized store is stricter than the un-annotated
    # source's, and Alive2 correctly refutes a pair that never existed. Four such FALSE DISAGREEMENTS
    # appeared on LLVM's own sub.ll -- noise in the one place it must not be, since a real disagreement
    # would look identical. The header travels with the extraction, and this pins it.
    dl = 'target datalayout = "e-p:64:64:64-i64:64:64"'
    mod = dl + "\ndefine i64 @g(i64 %a) {\n  ret i64 %a\n}\n"
    got = _extract_define(mod, "g")
    assert dl in got, ("the extracted function must carry the module's datalayout, or the oracle is "
                       "asked about a different program", got)

    print(f"corpus_cross_check_fixture OK: O2T proved {proved} real InstCombine functions and BOTH "
          "independent oracles confirmed every one -- lli (value execution) and reference Alive2 "
          "(poison/undef/UB), neither sharing O2T's SMT encoding, found ZERO disagreements. Track B's "
          "proved set on real code is independently verified; a future false proof here fails this gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
