#!/usr/bin/env python3
"""The IR front-end: LLVM's own parser replaces O2T's regexes in Track B's trusted base.

Track B used to read LLVM IR with per-module regexes, and that produced a recurring bug CLASS rather
than isolated bugs. This fixture pins each historical failure as now IMPOSSIBLE BY CONSTRUCTION, not
merely fixed:

  * a forward-reference CALL SITE above a definition was read as the function's signature, in five
    modules at once (2026-07 review round 1) -- lookup is now by identity, so there is no "first
    textual match" to get wrong;
  * a same-name overload / substring name was guessed at (round 2) -- `foo` cannot resolve to
    `foobar`;
  * a signature capture stopped at the first `)`, silently dropping every parameter after an
    attribute containing parentheses -- `ptr byval({ i32, i64 }) %s` is valid LLVM 18 and contains a
    comma inside braces;
  * an attributed parameter (`i32 noundef %x`) failed to match at all, declining the function.

It also pins the data the validators depend on and used to re-derive from text: poison flags from
LLVM's own accessors (the surface every false proof in the review lived on), structured types (so gep
field offsets and vector lane counts stop being string surgery), shufflevector masks with -1 for an
undef lane, and arbitrary-width constants (an i128 literal must not truncate to 64 bits).

And it pins the two REFUSALS that keep the front-end honest: a module LLVM rejects raises
`IrParseError` carrying LLVM's own diagnostic rather than being partially read, and a missing
`cv-ir-dump` raises `IrDumpUnavailable` rather than falling back to a text reader -- a silent second
parser is exactly the dual-path drift this replaces.

Needs `cv-ir-dump` (built against LLVM 18); self-skips if it is not built.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import ir_model as ir  # noqa: E402


def main() -> int:
    if not ir.available():
        print("ir_model_fixture: cv-ir-dump not built, skipped")
        return 0

    # 1) ROUND-1 CLASS: a call site ABOVE the definition. The regex readers matched the first
    #    `@f(...)` in the text, which is the CALL, and bound the callee's parameters to the caller's
    #    argument names. Identity lookup has no first-match to get wrong.
    fwd = """
define i32 @caller(i32 %caller_arg) {
  %r = call i32 @f(i32 %caller_arg)
  ret i32 %r
}
define i32 @f(i32 %callee_param) {
  ret i32 %callee_param
}
"""
    f = ir.parse(fwd).function("f")
    assert [p.name for p in f.params] == ["%callee_param"], \
        ("a forward-reference call site must not be read as the signature", [p.name for p in f.params])

    # 2) ROUND-2 CLASS: substring names and same-name confusion.
    subs = "define i32 @foo(i32 %a){ ret i32 %a }\ndefine i32 @foobar(i32 %a){ ret i32 7 }\n"
    m = ir.parse(subs)
    assert m.function("foo").blocks[0].instructions[-1].operands[0].is_reg
    assert m.function("foobar").blocks[0].instructions[-1].operands[0].int_value == 7
    assert m.function("nosuch") is None, "an absent function resolves to None, not a guess"

    # 3) PAREN/COMMA CLASS: an attribute containing a comma inside braces. A naive `([^)]*)` capture
    #    truncated the list and a naive comma split severed the type from the name.
    attr = ir.parse("define i32 @f(ptr byval({ i32, i64 }) align 8 %s, i32 noundef %y){ ret i32 %y }")
    fn = attr.function("f")
    assert [p.name for p in fn.params] == ["%s", "%y"], \
        ("every parameter must survive an attribute containing a comma", [p.name for p in fn.params])
    assert fn.params[0].type.kind == "ptr" and not fn.params[0].noundef
    assert fn.params[1].type.is_int(32) and fn.params[1].noundef, "noundef must be visible"
    assert fn.int_params == {"%y": 32}, "and the integer view must skip the pointer"

    # 4) POISON FLAGS come from LLVM's accessors. `disjoint`/`nneg` are LLVM 18 features that older
    #    parsers cannot even read, and `exact` on a division is where a live false proof once hid.
    flags = ir.parse("""
define i64 @f(i32 %x, i32 %y) {
  %a = or disjoint i32 %x, %y
  %b = add nsw nuw i32 %a, %x
  %c = udiv exact i32 %b, %y
  %d = zext nneg i32 %c to i64
  ret i64 %d
}
""").function("f")
    got = {i.op: i.flags for i in flags.instructions() if i.flags}
    assert got["or"] == {"disjoint"}, got
    assert got["add"] == {"nsw", "nuw"}, got
    assert got["udiv"] == {"exact"}, got
    assert got["zext"] == {"nneg"}, got

    # 5) STRUCTURED TYPES: a gep's source type arrives as a real struct, so field offsets are a
    #    computation over types rather than a regex over `{i32, i64}` text.
    gep = ir.parse("""
%T = type { i32, i64 }
define ptr @f(ptr %p) {
  %g = getelementptr inbounds %T, ptr %p, i64 0, i32 1
  ret ptr %g
}
""").function("f")
    g = next(i for i in gep.instructions() if i.op == "getelementptr")
    assert g.source_type.kind == "struct", g.source_type.raw
    assert [t.bits for t in g.source_type.fields] == [32, 64], g.source_type.raw

    # 6) VECTORS: lane count, scalable-ness and a shuffle mask (-1 is an undef lane).
    vec = ir.parse("""
define <4 x i32> @f(<4 x i32> %v) {
  %r = add <4 x i32> %v, splat (i32 3)
  %s = shufflevector <4 x i32> %r, <4 x i32> %v, <4 x i32> <i32 0, i32 5, i32 undef, i32 3>
  ret <4 x i32> %s
}
define <vscale x 4 x i32> @g(<vscale x 4 x i32> %v) { ret <vscale x 4 x i32> %v }
""")
    vf = vec.function("f")
    assert vf.params[0].type.kind == "vector" and vf.params[0].type.n == 4
    assert not vf.params[0].type.scalable
    sh = next(i for i in vf.instructions() if i.op == "shufflevector")
    assert sh.mask == [0, 5, -1, 3], sh.mask
    assert vec.function("g").params[0].type.scalable, "scalable vectors must be distinguishable"

    # 7) ARBITRARY-WIDTH CONSTANTS survive: an i128 literal must not truncate to 64 bits.
    big = ir.parse("define i128 @f(){ ret i128 170141183460469231731687303715884105727 }")
    v = big.function("f").blocks[0].instructions[-1].operands[0]
    assert v.int_value == (1 << 127) - 1, v.int_value

    # 8) An UNMODELED opcode still ARRIVES, with its name and operands, so a validator declines on it
    #    explicitly instead of a regex quietly failing to match.
    exotic = ir.parse("""
define float @f(float %a, float %b) {
  %r = frem float %a, %b
  ret float %r
}
""").function("f")
    ops = [i.op for i in exotic.instructions()]
    assert "frem" in ops, ops
    assert exotic.ret_type.kind == "float", exotic.ret_type.raw

    # 9) LINKAGE and declarations, which module-level composition depends on.
    mod = ir.parse("""
declare i32 @ext(i32)
define internal i32 @priv(i32 %a) { ret i32 %a }
define i32 @pub(i32 %a) { ret i32 %a }
""")
    assert mod.function("ext").is_declaration
    assert mod.function("priv").is_internal and not mod.function("pub").is_internal
    assert sorted(mod.defined_names) == ["priv", "pub"], mod.defined_names

    # 10) REFUSALS. Invalid IR raises with LLVM's own diagnostic; it is never partially read.
    try:
        ir.parse("define i32 @f(i32 %x) { %r = add i32 %x, ; truncated\n ret i32 %r }")
        raise AssertionError("invalid IR must raise, not parse partially")
    except ir.IrParseError as exc:
        assert str(exc), "the LLVM diagnostic must be carried through"

    # ...and a missing tool is a hard error, never a silent fallback to a text reader.
    saved = ir.dump_binary
    try:
        ir.dump_binary = lambda: (_ for _ in ()).throw(ir.IrDumpUnavailable("simulated"))
        try:
            ir.clear_cache()
            ir.parse("define i32 @f(){ ret i32 0 }")
            raise AssertionError("a missing cv-ir-dump must raise, not fall back")
        except ir.IrDumpUnavailable:
            pass
    finally:
        ir.dump_binary = saved
        ir.clear_cache()

    print("ir_model_fixture OK: Track B's IR syntax layer is LLVM 18's own parser, not O2T regexes. "
          "The four historical failures are impossible by construction -- a forward-reference call "
          "site cannot be read as a signature, `foo` cannot resolve to `foobar`, an attribute "
          "containing a comma cannot truncate the parameter list, and an attributed parameter is not "
          "dropped. Poison flags (disjoint/nneg/exact/nsw/nuw) come from LLVM's accessors, types are "
          "structured (struct fields, vector lanes, scalable-ness), shuffle masks carry -1 for undef "
          "lanes, and an i128 constant survives. Invalid IR raises LLVM's own diagnostic and a missing "
          "cv-ir-dump is a hard error -- there is no text fallback to drift from")
    return 0


if __name__ == "__main__":
    sys.exit(main())
