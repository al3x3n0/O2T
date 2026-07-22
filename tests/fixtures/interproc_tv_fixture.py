#!/usr/bin/env python3
"""Interprocedural composition: model calls, so inlining / IPSCCP transforms are verifiable.

Whole-function and module TV are per-function and treat a `call` as opaque. This models a direct call
`call @g(args)` by translating the callee `g` with its parameters BOUND to the argument terms --
inlining g's semantics into the caller's SMT term (o2t/validate/scalar_ir.py, bounded recursion). Two
things follow: a CALLER function becomes translatable, and INTERPROCEDURAL transforms are checked --

  * INLINING: `opt -passes=inline,instcombine` folds foo(x)=bar(x)+1 (bar(y)=y*2) to 2x+1; TV proves
    the before (foo calls bar) refines to the after (bar inlined) -- across the call boundary;
  * IPSCCP-style: a call with a constant argument, foo()=bar(5), is resolved and its constant-folded
    form proved;
  * TEETH: a WRONG inline (2x+2 for 2x+1) is REFUTED;
  * recursion / external callees are a BOUNDED, sound decline (never an infinite loop or mis-model).

Scope: single-BB scalar callees, bounded call depth. Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402

MOD = ("define i32 @bar(i32 %y) {\n  %m = mul i32 %y, 2\n  ret i32 %m\n}\n"
       "define i32 @foo(i32 %x) {\n  %c = call i32 @bar(i32 %x)\n  %r = add i32 %c, 1\n  ret i32 %r\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("interproc_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. INLINING verified across the call boundary: foo (which calls bar) refines to the inlined form.
    after = si.run_passes(MOD, "inline,instcombine", opt)
    assert after is not None
    assert si.validate_transform(z3, MOD, after, "foo")["status"] == "proved", "inlining must prove"

    # 2. The caller is translatable because the call is resolved: foo(x) = 2x + 1.
    _, ret, _, _, _ = si.translate(MOD, "foo")
    assert ret == "(bvadd (bvmul %x (_ bv2 32)) (_ bv1 32))", ("call must resolve to 2x+1", ret)

    # 3. TEETH -- a WRONG inline (2x+2 instead of 2x+1) is refuted with a witness.
    bad = ("define i32 @bar(i32 %y) {\n  %m = mul i32 %y, 2\n  ret i32 %m\n}\n"
           "define i32 @foo(i32 %x) {\n  %r = add i32 %x, 2\n  ret i32 %r\n}\n")   # ignores *2: wrong
    v = si.validate_transform(z3, MOD, bad, "foo")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong inline must refute", v)

    # 4. IPSCCP-style: a constant-argument call foo2() = bar(5) resolves and constant-folds; opt's
    #    inlined result (10) is proved a refinement.
    ip = ("define i32 @bar(i32 %y) {\n  %m = mul i32 %y, 5\n  ret i32 %m\n}\n"
          "define i32 @foo2(i32 %z) {\n  %c = call i32 @bar(i32 2)\n  ret i32 %c\n}\n")   # bar(2)=10
    ip_after = si.run_passes(ip, "inline,instcombine", opt)
    assert si.validate_transform(z3, ip, ip_after, "foo2")["status"] == "proved", "IPSCCP call must prove"

    # 5. Recursion / external callee: a BOUNDED, sound decline (unsupported), never an infinite loop.
    rec = "define i32 @r(i32 %x) {\n  %c = call i32 @r(i32 %x)\n  ret i32 %c\n}\n"
    assert si.validate_transform(z3, rec, rec, "r")["status"] == "unsupported", "recursion must decline"

    print("interproc_tv_fixture OK: function calls are modeled by inlining the callee's semantics -- so "
          "INLINING is verified across the call boundary (foo(x)=bar(x)+1 -> 2x+1 proved), a caller is "
          "translatable, a constant-argument call (IPSCCP-style) constant-folds and proves, a WRONG "
          "inline is refuted with a witness, and recursion is a bounded sound decline. The "
          "interprocedural value-flow gap, opened")
    return 0


if __name__ == "__main__":
    sys.exit(main())
