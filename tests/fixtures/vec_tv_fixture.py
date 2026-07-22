#!/usr/bin/env python3
"""Vectors: whole-function TV via a LANE MODEL (element-wise + shuffle/extract/insert).

A vector value is a list of per-lane scalar SMT terms (a scalar is a 1-lane list), so element-wise ops
lower lane-by-lane and `extractelement`/`insertelement`/`shufflevector` are exact index/permutation
operations on the lists (o2t/validate/vec_tv.py). A transform is a refinement iff every result lane
agrees for all inputs.

  * vector folds prove: `and <2 x i32> %x, <-1,-1> -> %x`, `add <4 x i32> %x, zeroinitializer -> %x`;
  * a shufflevector is proved equal to its explicit extract/insert form (the lane model gets the
    permutation exactly);
  * TEETH -- a wrong lane (`and X, <-1,0>` claimed == X; a wrong shuffle mask) REFUTES;
  * scalable vectors / variable indices / reductions / undef masks are a sound decline.
Scope: fixed-width <N x iW>, single-BB, constant indices/masks. Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.vec_tv import vec_tv, svec_tv  # noqa: E402

AND = ("define <2 x i32> @f(<2 x i32> %x) {\n"
       "  %r = and <2 x i32> %x, <i32 -1, i32 -1>\n  ret <2 x i32> %r\n}\n")
ADD = ("define <4 x i32> @g(<4 x i32> %x) {\n"
       "  %r = add <4 x i32> %x, zeroinitializer\n  ret <4 x i32> %r\n}\n")
SHUF = ("define <2 x i32> @s(<2 x i32> %a, <2 x i32> %b) {\n"
        "  %r = shufflevector <2 x i32> %a, <2 x i32> %b, <2 x i32> <i32 0, i32 3>\n"
        "  ret <2 x i32> %r\n}\n")


def main() -> int:
    z3 = shutil.which("z3")
    opt = tv._resolve_opt("opt")
    if z3 is None or opt is None:
        print("vec_tv_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. Vector folds are proved against opt's own output, lane by lane.
    assert vec_tv(z3, AND, si.run_passes(AND, "instcombine", opt), "f")["status"] == "proved", "and X,-1->X"
    assert vec_tv(z3, ADD, si.run_passes(ADD, "instcombine", opt), "g")["status"] == "proved", "add X,0->X"

    # 2. A shufflevector <0,3> = <a0, b1> is proved EQUAL to its explicit extract/insert form -- the
    #    lane model captures the permutation exactly.
    eq = ("define <2 x i32> @s(<2 x i32> %a, <2 x i32> %b) {\n"
          "  %a0 = extractelement <2 x i32> %a, i32 0\n  %b1 = extractelement <2 x i32> %b, i32 1\n"
          "  %t = insertelement <2 x i32> zeroinitializer, i32 %a0, i32 0\n"
          "  %r = insertelement <2 x i32> %t, i32 %b1, i32 1\n  ret <2 x i32> %r\n}\n")
    assert vec_tv(z3, SHUF, eq, "s")["status"] == "proved", "shuffle == extract/insert form"

    # 3. TEETH -- a wrong lane refutes: `and X, <-1,0>` claimed to equal X (lane 1 is X&0=0 != X).
    bad_fold = ("define <2 x i32> @f(<2 x i32> %x) {\n  %r = and <2 x i32> %x, <i32 -1, i32 0>\n"
                "  ret <2 x i32> %r\n}\n")
    ident = "define <2 x i32> @f(<2 x i32> %x) {\n  ret <2 x i32> %x\n}\n"
    v = vec_tv(z3, bad_fold, ident, "f")
    assert v["status"] == "refuted" and v.get("witness"), ("a wrong vector lane must refute", v)
    #    ...and a wrong shuffle mask (<0,2> instead of <0,3>) refutes.
    wrong_shuf = SHUF.replace("i32 0, i32 3", "i32 0, i32 2")
    assert vec_tv(z3, SHUF, wrong_shuf, "s")["status"] == "refuted", "a wrong shuffle mask must refute"

    # 4. SCALABLE vectors (runtime length) are TV'd at ONE symbolic lane -- element-wise, so a proof
    #    for an unconstrained lane index covers all lanes. Folds prove; a wrong lane refutes; a
    #    cross-lane op (which the per-lane model cannot soundly handle) declines.
    sf = ("define <vscale x 4 x i32> @sf(<vscale x 4 x i32> %x) {\n"
          "  %r = add <vscale x 4 x i32> %x, zeroinitializer\n  ret <vscale x 4 x i32> %r\n}\n")
    assert svec_tv(z3, sf, si.run_passes(sf, "instcombine", opt), "sf")["status"] == "proved", "svec add X,0->X"
    sg = ("define <vscale x 4 x i32> @sg(<vscale x 4 x i32> %x) {\n"
          "  %r = and <vscale x 4 x i32> %x, splat (i32 -1)\n  ret <vscale x 4 x i32> %r\n}\n")
    assert svec_tv(z3, sg, si.run_passes(sg, "instcombine", opt), "sg")["status"] == "proved", "svec and X,-1->X"
    sbad = sg.replace("splat (i32 -1)", "splat (i32 0)")   # and X, 0 -> 0, not X
    sident = "define <vscale x 4 x i32> @sg(<vscale x 4 x i32> %x) {\n  ret <vscale x 4 x i32> %x\n}\n"
    assert svec_tv(z3, sbad, sident, "sg")["status"] == "refuted", "svec wrong lane must refute"
    xl = ("define i32 @h(<vscale x 4 x i32> %x) {\n"
          "  %e = extractelement <vscale x 4 x i32> %x, i32 0\n  ret i32 %e\n}\n")
    assert svec_tv(z3, xl, xl, "h")["status"] == "unsupported", "a cross-lane op must decline"

    print("vec_tv_fixture OK: FIXED vectors are TV'd via a lane model -- element-wise folds prove, a "
          "shufflevector is proved equal to its explicit extract/insert form, a wrong lane or shuffle "
          "mask REFUTES; SCALABLE vectors (runtime length) are TV'd at ONE symbolic lane -- element-wise "
          "folds prove (add X,0->X, and X,splat(-1)->X), a wrong lane refutes, and a cross-lane op "
          "declines (the per-lane model stays sound). The vector gap -- fixed and scalable -- closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
