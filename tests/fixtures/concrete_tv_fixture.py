#!/usr/bin/env python3
"""Track B gets an INDEPENDENT execution oracle (o2t/validate/concrete_tv.py) -- the missing cross-check.

Track B's validate_transform proves refinement with ONE hand-written SMT encoding checked by ONE z3
call; the second solver reuses the SAME SMT, so an encoding-generation bug is invisible to it (how the
`udiv exact` false proof survived). concrete_tv adds the Track-A-`reconcile` analogue for Track B: it
RUNS both sides with `lli` (LLVM's real semantics) and checks the values agree -- z3 and lli are then
complementary independent oracles for the VALUE part.

Gated here:
  * corroboration -- a real opt transform: z3 proves AND lli agrees;
  * TEETH -- a value miscompile: lli DISAGREES with a concrete witness;
  * THE HEADLINE -- inject a fake ENCODING BUG into scalar_ir (encode `sub` as `bvadd`); z3 now FALSELY
    proves `sub -> add`, and the lli cross-check CATCHES it (`refuted-by-execution`). This is the exact
    failure mode (a value-encoding false proof z3's single encoding produces) that had no independent
    check before;
  * HONEST LIMIT -- add -> add nsw agrees (lli is a value oracle; poison stays z3's job).
Needs z3 + lli (LLVM 18).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate import semantics as sem  # noqa: E402
from o2t.validate.concrete_tv import concrete_tv, cross_checked_tv  # noqa: E402


def _lli():
    return toolchain.resolve_lli()   # env $O2T_LLI -> PATH (lli-18/lli) -> homebrew llvm@18


def fn(body, name="f", params="i32 %x, i32 %y", ret="i32"):
    return f"define {ret} @{name}({params}) {{\n{body}\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    lli = _lli()
    opt = tv._resolve_opt("opt")
    if z3 is None or lli is None or opt is None:
        print("concrete_tv_fixture: z3 or lli/opt(18) not found, skipped")
        return 0

    # 1. CORROBORATION: a real opt transform -- z3 proves AND lli independently agrees.
    src = fn("  %a = add i32 %x, %y\n  %r = add i32 %a, 0\n  ret i32 %r")
    after = si.run_passes(src, "instcombine", opt)
    assert si.validate_transform(z3, src, after, "f")["status"] == "proved", "z3 must prove the fold"
    cc = concrete_tv(lli, src, after, "f")
    assert cc["status"] == "agree", ("lli must corroborate the sound fold", cc)
    x = cross_checked_tv(z3, lli, src, after, "f")
    assert x["status"] == "proved" and x["concrete"] == "agree", ("both oracles proved", x)

    # 2. TEETH: a value miscompile -- lli disagrees with a concrete witness.
    b = fn("  %r = add i32 %x, %y\n  ret i32 %r")
    bad = fn("  %r = add i32 %x, %x\n  ret i32 %r")
    d = concrete_tv(lli, b, bad, "f")
    assert d["status"] == "disagree" and d.get("witness"), ("lli must catch the miscompile", d)

    # 3. THE HEADLINE -- an ENCODING BUG in z3's world, caught by real execution. Monkeypatch scalar_ir
    #    to lower `sub` as `bvadd` (a value-encoding bug). z3 then FALSELY proves `sub x,y == add x,y`
    #    (both encode to bvadd). cross_checked_tv runs lli and CATCHES it: refuted-by-execution.
    sub_fn = fn("  %r = sub i32 %x, %y\n  ret i32 %r")
    add_fn = fn("  %r = add i32 %x, %y\n  ret i32 %r")
    saved = sem.BIN["sub"]
    try:
        sem.BIN["sub"] = "bvadd"                        # the injected encoding bug
        assert si.validate_transform(z3, sub_fn, add_fn, "f")["status"] == "proved", \
            "with the injected bug z3 should FALSELY prove sub==add (that is the false proof)"
        guarded = cross_checked_tv(z3, lli, sub_fn, add_fn, "f")
        assert guarded["status"] == "refuted-by-execution" and guarded.get("witness"), \
            ("the lli oracle must CATCH the value-encoding false proof", guarded)
    finally:
        sem.BIN["sub"] = saved
    # sanity: with the bug reverted, sub->add is correctly refuted by z3 itself.
    assert si.validate_transform(z3, sub_fn, add_fn, "f")["status"] == "refuted", "sub != add"

    # 4. HONEST LIMIT: add -> add nsw agrees on VALUES (poison-only diff -- lli does not trap poison;
    #    that refinement is z3's job, complemented by the exhaustive flag/poison matrix test).
    nsw = fn("  %r = add nsw i32 %x, %y\n  ret i32 %r")
    assert concrete_tv(lli, b, nsw, "f")["status"] == "agree", "lli is a value oracle (poison is z3's)"

    print("concrete_tv_fixture OK: Track B now has an INDEPENDENT lli execution oracle -- it corroborates "
          "a real fold, catches a value miscompile with a witness, and (the headline) CATCHES an injected "
          "value-encoding FALSE PROOF that z3's single encoding produced (refuted-by-execution). Poison-"
          "only differences stay z3's job (honest value-oracle limit). The Track-B cross-check gap closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
