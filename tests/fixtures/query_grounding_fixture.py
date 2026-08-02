#!/usr/bin/env python3
"""Every analysis fact the shim can establish must be GROUNDED, or its absence must be visible.

When a fold asks an analysis question -- `isKnownToBeAPowerOfTwo(X)`, `MaskedValueIsZero(X, M)` --
and takes the branch where the answer is true, the executor records that the branch established a
fact, and the discharge is supposed to assume it. Each such query is a TRUST EDGE to LLVM's own
analysis, and the edge has two ways to go wrong.

  * THE FACT IS WRONG. `isKnownToBeAPowerOfTwo(V, OrZero=true)` establishes something strictly
    WEAKER than the plain query -- the value may be zero. Grounding both identically would assert
    non-zero when the caller proved no such thing, which is the shape of an unsound proof. They are
    separate queries with separate groundings, checked by `predicate_algebra_fixture`.

  * THE FACT SILENTLY VANISHES. A recorded query with no grounding contributes no constraint, so the
    path is discharged over a LARGER input space than the branch admits. That cannot cause a false
    proof -- proving under fewer assumptions is strictly stronger -- but it can cause a SPURIOUS
    REFUTATION, on a counterexample the missing fact excludes. `masked-zero` was exactly this: the
    shim recorded it, nothing could ground it, and the assumption disappeared. Worse, the shim's
    `MaskedValueIsZero` took ONE argument where LLVM's takes (V, Mask), so the mask -- the entire
    content of the fact -- was dropped before it could even be recorded.

Gated here:
  * every query name the shim can record has a grounding, so the set cannot drift apart again;
  * an ungrounded query DOWNGRADES a refutation to a non-answer, and leaves a proof alone;
  * `MaskedValueIsZero(V, Mask)` emits the exact fact `(V & Mask) == 0`, mask included.

Needs clang++ and z3; self-skips without them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.symexec import real_pass as R  # noqa: E402

HEADER = ROOT / "o2t" / "symexec" / "symbolic_llvm.h"


def _clang():
    for cand in ("clang++", "/opt/homebrew/opt/llvm@18/bin/clang++", "/usr/bin/clang++"):
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


def main() -> int:
    z3, clang = shutil.which("z3"), _clang()
    if z3 is None:
        print("query_grounding_fixture: needs z3, skipped")
        return 0

    # 1) NO DRIFT: everything the shim can record, the discharger can ground.
    #    The name is not always a literal in call position -- the OrZero form of
    #    isKnownToBeAPowerOfTwo selects between two names with a ternary -- so scan every string
    #    literal inside each cv_query(...) call rather than only the first argument. A checker that
    #    cannot see half the call sites gives false confidence, which is the failure this file is
    #    about in the first place.
    src = HEADER.read_text()
    recorded = set()
    for m in re.finditer(r"cv_query\(", src):
        i, depth = m.end(), 1
        while i < len(src) and depth:
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        recorded.update(re.findall(r'"([a-z0-9-]+)"', src[m.end():i]))
    assert recorded, "the shim must expose some analysis queries"
    #    ACID TEST for the scan itself: the ternary-selected name must be among them.
    assert "power-of-two-or-zero" in recorded, \
        ("the drift check must see names recorded through a ternary, not only literal first "
         "arguments -- otherwise it silently skips call sites", sorted(recorded))
    missing = sorted(recorded - set(R._QUERY_FACT))
    assert not missing, (f"{len(missing)} analysis quer(y/ies) the shim records cannot be grounded, "
                         "so the fact silently vanishes from the path condition", missing)

    # 2) An UNGROUNDED query downgrades a refutation to a non-answer. The obligation below is false
    #    outright (sdiv vs udiv), so it would refute; because the branch established something the
    #    discharger cannot express, that refutation is not trustworthy.
    base = {"input": "(bvsdiv X Y)", "output": "(bvudiv X Y)", "constraints": [],
            "input_poison": "false", "output_poison": "false", "logic": "QF_BV"}
    ung = R.discharge_path(z3, dict(base, decisions=[{"q": "not-a-modelled-fact", "arg": "X", "v": 1}]))
    assert ung["status"] == "error" and ung["ungrounded"] == ["not-a-modelled-fact"], ung
    assert not ung["witness"], "a downgraded refutation must not ship a witness as if it were real"

    #    ...and with every fact grounded, the same obligation refutes normally, so the rule is not a
    #    blanket suppression of refutations.
    assert R.discharge_path(z3, dict(base, decisions=[]))["status"] == "refuted"

    #    A PROOF is left alone: it holds over a superset of the admissible inputs, so a missing
    #    assumption cannot invalidate it.
    ok = R.discharge_path(z3, dict(base, output="(bvsdiv X Y)",
                                   decisions=[{"q": "not-a-modelled-fact", "arg": "X", "v": 1}]))
    assert ok["status"] == "proved", ("a proof under FEWER assumptions is still sound and must not "
                                      "be downgraded", ok)

    # 3) MaskedValueIsZero carries its MASK into the fact it establishes.
    if clang is None:
        print("query_grounding_fixture OK (mask half skipped: no clang++): every recorded analysis "
              "query is grounded, and an ungrounded one downgrades a refutation rather than "
              "vanishing")
        return 0
    probe = ('#include "symbolic_llvm.h"\n#include <cstring>\n'
             'int main(int argc, char **argv){ if (argc < 2) return 1; cv_setup(argc, argv);\n'
             '  Value X{"X"}, M{"M"};\n'
             '  std::string input = "X"; Value *out = nullptr;\n'
             '  if (MaskedValueIsZero(X, M)) out = cv_keep(Value{"X"});\n'
             '  cv_emit(input, out); return 0; }\n')
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "maskprobe.cpp"
        src.write_text(probe)
        exe = R.compile_harness(str(src), clang=clang)
        assert exe is not None, "the mask probe must compile -- MaskedValueIsZero takes (V, Mask)"
        paths, crashes = R.explore(exe, "any")
        assert not crashes, crashes
        cons = [c for p in paths for c in p.get("constraints", [])]
    assert any("bvand X M" in c and "bv0 32" in c for c in cons), \
        ("the established fact must be exactly `(X & M) == 0`, mask included", cons)

    print("query_grounding_fixture OK: every analysis query the shim can record is GROUNDED in the "
          f"discharger ({len(recorded)} of them), so a fact can no longer vanish between the two. An "
          "ungrounded query now downgrades a REFUTATION to a non-answer -- the witness may be an "
          "input the missing assumption excludes -- while leaving a PROOF alone, since proving under "
          "fewer assumptions is strictly stronger. And MaskedValueIsZero takes its MASK, emitting "
          "`(V & Mask) == 0`; it previously took one argument, dropping the mask entirely and "
          "recording a query name nothing could ground")
    return 0


if __name__ == "__main__":
    sys.exit(main())
