#!/usr/bin/env python3
"""SOURCE-FILE mode: the Clang-AST front-end recovers folds from source parsed against REAL headers.

The stub-mode front-end (clang_tree_fixture) proved the parser-free principle but reached 0 verbatim
upstream folds -- it parses against a minimal API stub. This gates the real thing: fold bodies
(verbatim upstream, in tests/fixtures/vendor_folds/) are compiled against the ACTUAL LLVM 18 public
headers (PatternMatch.h / IRBuilder.h / Instructions.h), so clang produces the genuine AST -- no
stub, no approximation -- and the front-end reads its matcher/rewrite trees from it with O2T's regex
parser fully out of the loop. Each obligation is byte-identical to the regex path, proved/refuted
correctly. Skips unless clang 18 (with its LLVM headers) is present. Needs z3 + clang 18 headers.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.mine import clang_tree as ct  # noqa: E402
from o2t.intent import pass_graph as pg, corpus  # noqa: E402
from o2t import mini_alive as ma  # noqa: E402

_HOMEBREW_CLANG = "/opt/homebrew/opt/llvm@18/bin/clang"
VENDOR = Path(__file__).resolve().parent / "vendor_folds" / "instcombine_real_folds.cpp"
FOLDS = [("combineAddSubWithShlAddSub", "proved"),
         ("foldMulAddZeroOne", "proved"),
         ("foldSubWrong", "refuted")]


def main() -> int:
    z3 = shutil.which("z3")
    clang = shutil.which("clang") or (_HOMEBREW_CLANG if Path(_HOMEBREW_CLANG).exists() else None)
    inc = ct.llvm_include_dir(clang) if clang else None
    if z3 is None or clang is None or inc is None:
        print("clang_tree_source_fixture: needs z3 + clang 18 with LLVM headers, skipped")
        return 0

    body_text = VENDOR.read_text()
    bodies = {f["name"]: f["full"] for f in corpus.extract_functions(body_text)}
    proved = refuted = 0
    for name, expect in FOLDS:
        # 1. Recover from the REAL-headers AST -- the regex parser is NOT in this path, and the
        #    source is parsed against actual LLVM 18 headers (no stub).
        pair = ct.recover_from_source_file(str(VENDOR), name, [inc], clang_bin=clang)
        assert pair is not None, ("source-file mode must recover", name)

        # 2. Verdict is correct (teeth: the wrong sub-fold refutes via the real-AST path).
        status, cex = ma.prove(pair, z3)
        assert status == expect, (name, status, expect)
        if status == "refuted":
            assert cex, (name, "refutation needs a witness")
            refuted += 1
        else:
            proved += 1
            assert pg.reconcile(pair, z3)["agree"], (name, "reconcile must agree")

        # 3. CROSS-FRONT-END AGREEMENT on REAL source: the obligation from the real-headers AST is
        #    byte-identical to the regex path's reading of the same fold body. Two independent
        #    front-ends -- one via the C++ compiler's parser, one via O2T's regex -- agree.
        regex_pair = pg.recover_from_function(bodies[name])
        assert regex_pair is not None, name
        assert pair["before"] == regex_pair["before"], (name, "before diverged")
        assert pair["after"] == regex_pair["after"], (name, "after diverged")
        assert pair["variables"] == regex_pair["variables"], name

    # 4. CASCADE: a verbatim multi-arm fold (foldXorToXor, 3 arms) recovers every arm from the
    #    real-headers AST -- the assert/getOperand prelude is tolerated (scoped per-arm), and each
    #    arm is a real Boolean identity (A&B)^(A|B) -> A^B that proves. arm 0 is a pass-level claim;
    #    later arms are standalone (the pass_graph cascade caveat).
    arms = ct.recover_folds_from_source_file(str(VENDOR), "foldXorToXor", [inc], clang_bin=clang)
    assert len(arms) == 3, ("cascade must recover 3 arms", len(arms))
    assert [a["standalone"] for a in arms] == [False, True, True]
    assert all(ma.prove(a, z3)[0] == "proved" for a in arms), \
        [ma.prove(a, z3)[0] for a in arms]
    proved += 3

    print(f"clang_tree_source_fixture OK: {proved} proved + {refuted} refuted recovered from fold "
          "source parsed against the REAL LLVM 18 headers (no stub) -- including a VERBATIM upstream "
          "combineAddSubWithShlAddSub -- each obligation byte-identical to the regex path, the wrong "
          "fold refuted with a witness. Verbatim reach: the regex parser is out of the loop on real "
          "compiler-parsed source, not a stub approximation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
