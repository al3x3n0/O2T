#!/usr/bin/env python3
"""Reach lift: a unified Track B dispatcher routes to the pointer-memory and vector validators.

Whole-function TV ran only the SCALAR validator (scalar_ir), so functions with pointer-side-effect
memory or vectors declined as `unsupported` even though O2T HAS validators for them (mem_state_tv,
vec_tv, svec_tv) -- they just were not wired into corpus_tv. corpus_tv.validate_transform_ex now tries
scalar first and, on decline, falls through to those validators (each sound within its scope, declining
out-of-scope, so the first proved/refuted is the answer). validate_file uses it.

Gated here: memory / fixed-vector / scalable-vector functions the scalar path REPORTS `unsupported`
now PROVE via the right validator; a memory miscompile still refutes; scalar is unaffected; and a mixed
corpus that scalar-only would leave largely `unsupported` reaches 100% proved. Needs z3 + opt 18.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import toolchain  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.corpus_tv import validate_transform_ex as ex, validate_file  # noqa: E402


def main() -> int:
    z3 = shutil.which("z3")
    opt = toolchain.resolve_opt("opt")
    if z3 is None or opt is None:
        print("track_b_dispatch_fixture: z3 or opt(18) not found, skipped")
        return 0

    # 1. MEMORY: store-then-load-forward. scalar declines (ptr arg); the dispatcher proves via mem_state.
    mem_b = "define i32 @f(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n"
    mem_a = "define i32 @f(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  ret i32 %x\n}\n"
    assert si.validate_transform(z3, mem_b, mem_a, "f")["status"] == "unsupported", "scalar declines memory"
    r = ex(z3, mem_b, mem_a, "f")
    assert r["status"] == "proved" and r.get("via") == "mem_state", ("dispatcher proves memory via mem_state", r)

    # 2. FIXED VECTOR: and X,<-1,..> -> X. scalar declines; dispatcher proves via vec.
    vec_b = "define <2 x i32> @g(<2 x i32> %x) {\n  %r = and <2 x i32> %x, <i32 -1, i32 -1>\n  ret <2 x i32> %r\n}\n"
    vec_a = "define <2 x i32> @g(<2 x i32> %x) {\n  ret <2 x i32> %x\n}\n"
    assert si.validate_transform(z3, vec_b, vec_a, "g")["status"] == "unsupported", "scalar declines vectors"
    assert ex(z3, vec_b, vec_a, "g").get("via") == "vec", "dispatcher proves fixed vector via vec"

    # 3. SCALABLE VECTOR: add X,0 -> X. dispatcher proves via svec.
    sv_b = "define <vscale x 4 x i32> @s(<vscale x 4 x i32> %x) {\n  %r = add <vscale x 4 x i32> %x, zeroinitializer\n  ret <vscale x 4 x i32> %r\n}\n"
    sv_a = "define <vscale x 4 x i32> @s(<vscale x 4 x i32> %x) {\n  ret <vscale x 4 x i32> %x\n}\n"
    assert ex(z3, sv_b, sv_a, "s").get("via") == "svec", "dispatcher proves scalable vector via svec"

    # 4. TEETH: a memory miscompile still refutes through the dispatcher.
    mem_bad = "define i32 @f(ptr %p, i32 %x) {\n  store i32 %x, ptr %p\n  %y = add i32 %x, 1\n  ret i32 %y\n}\n"
    assert ex(z3, mem_b, mem_bad, "f")["status"] == "refuted", "a memory miscompile must refute"

    # 5. SCALAR unaffected: handled by scalar_ir directly (no fall-through `via`).
    sc = "define i32 @h(i32 %x) {\n  %r = add i32 %x, 0\n  ret i32 %r\n}\n"
    sca = "define i32 @h(i32 %x) {\n  ret i32 %x\n}\n"
    rs = ex(z3, sc, sca, "h")
    assert rs["status"] == "proved" and "via" not in rs, ("scalar stays on the scalar path", rs)

    # 6. OPERATIONALIZED on a mixed corpus via validate_file: scalar + memory + fixed/scalable vector all
    #    PROVE (scalar-only would leave 3 of 4 `unsupported`).
    corpus = ("define i32 @scalar(i32 %x) {\n  %a = add i32 %x, 0\n  ret i32 %a\n}\n"
              "define void @mem(ptr %p, i32 %x) {\n  store i32 1, ptr %p\n  store i32 %x, ptr %p\n  ret void\n}\n"
              "define <4 x i32> @vec(<4 x i32> %x) {\n  %r = add <4 x i32> %x, zeroinitializer\n  ret <4 x i32> %r\n}\n"
              "define <vscale x 4 x i32> @svec(<vscale x 4 x i32> %x) {\n  %r = and <vscale x 4 x i32> %x, splat (i32 -1)\n  ret <vscale x 4 x i32> %r\n}\n")
    vf = validate_file(z3, corpus, opt)
    assert vf["counts"].get("proved") == 4, ("mixed corpus must reach 100% proved", vf["counts"])
    vias = {f["function"]: f.get("via") for f in vf["functions"]}
    assert vias["mem"] == "mem_state" and vias["vec"] == "vec" and vias["svec"] == "svec", vias

    # 7. SOUNDNESS of the reach lift: the memory/vector proofs are OUTSIDE concrete_tv's scalar scope,
    #    but reference Alive2 handles memory and vectors -- independently confirm the new proofs against
    #    the ground-truth oracle (and a memory miscompile refutes there too). Optional (needs alive-tv).
    alive = shutil.which("alive-tv")
    if alive:
        from o2t.validate.alive_diff import alive_refines
        assert alive_refines(mem_b, mem_a, alive)["status"] == "proved", "Alive2 must confirm the memory proof"
        assert alive_refines(vec_b, vec_a, alive)["status"] == "proved", "Alive2 must confirm the vector proof"
        assert alive_refines(mem_b, mem_bad, alive)["status"] == "refuted", "Alive2 must refute the memory miscompile"

    print("track_b_dispatch_fixture OK: whole-function TV now DISPATCHES -- scalar first, then the "
          "pointer-memory / fixed-vector / scalable-vector validators on decline. Memory + vector "
          "functions the scalar path called `unsupported` now PROVE (via mem_state/vec/svec); a memory "
          "miscompile still refutes; a mixed corpus reaches 4/4 proved where scalar-only reached 1/4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
