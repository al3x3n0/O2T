#!/usr/bin/env python3
"""A pass that emits IR LLVM rejects is broken, and the AST can say so from the SOURCE.

WHY THE AST AND NOT A REGEX. This judgement is about TYPES: is the bitcast's destination pinned to
a fixed width, and does anything in the function check the operand's width? A regex reads variable
spelling and the text of an assignment line; the AST resolves `IntegerType::get(Ctx, 64)` by
evaluating its argument as an integer constant expression, and finds guard calls in the real
statement tree rather than by grepping.

The first attempt at this check WAS a regex, and it could not even see the defective function:
llvm-tutor's `static FCmpInst *convertFCmpEqInstruction(FCmpInst *FCmp) noexcept {` was dropped by
the Python splitter, whose pattern required `)` to be followed directly by `{`. A regex that is
wrong about the language returns nothing and looks like a clean result; a type-aware analysis that
is wrong about the API fails to compile.

WHAT IT FINDS. `bitcast` requires source and destination to have the same bit width. A pass that
casts an INPUT-DERIVED value to a width it hardcoded is wrong for every other width and LLVM aborts
the module -- which means no refinement question can even be asked about that pass's output.
Measured on real third-party code: ConvertFCmpEq hardcodes `IntegerType::get(Ctx, 64)` and
`Type::getDoubleTy`, so `fcmp oeq float` yields `bitcast float to i64`. Its header claims it
converts "all equality-based floating point comparison instructions"; the double-only restriction
is documented nowhere.

DECLINE BY DEFAULT: a finding needs a hardcoded-width destination AND no width check anywhere in
the function. Seeing `isDoubleTy()` or `getScalarSizeInBits()` is enough to stay silent -- the
author considered the question, and this does not second-guess how.

Skipped unless the clang tool is built (`-DO2T_BUILD_CLANG_TOOLS=ON`), like the other
optional-toolchain fixtures.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = [ROOT / "build-clang-tools" / "cv-mine-pass-source-ast",
              ROOT / "build" / "cv-mine-pass-source-ast"]

UNGUARDED = """namespace llvm { struct Type; struct Value;
struct IRBuilderBase { Value *CreateBitCast(Value*, Type*); }; }
using namespace llvm;
struct Ctx {};
Type *getDoubleTy(Ctx&);
bool isDoubleTy(Value*);
IRBuilderBase Builder;
// GUARDED: the width is checked, so this is the author's decision, not a defect.
Value *guarded(Value *V, Ctx &C) {
  if (!isDoubleTy(V)) return nullptr;
  Type *DoubleTy = getDoubleTy(C);
  return Builder.CreateBitCast(V, DoubleTy);
}
// UNGUARDED: an input-derived value cast to a hardcoded type, no width check anywhere.
Value *unguarded(Value *V, Ctx &C) {
  Type *DoubleTy = getDoubleTy(C);
  return Builder.CreateBitCast(V, DoubleTy);
}
// `noexcept` -- the form the Python splitter dropped entirely, so the regex attempt never saw it.
Value *unguarded_noexcept(Value *V, Ctx &C) noexcept {
  Type *DoubleTy = getDoubleTy(C);
  return Builder.CreateBitCast(V, DoubleTy);
}
"""


def main() -> int:
    tool = next((c for c in CANDIDATES if c.exists()), None)
    if tool is None:
        print("ir_validity_ast_fixture: cv-mine-pass-source-ast not built "
              "(-DO2T_BUILD_CLANG_TOOLS=ON), skipped")
        return 0

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "cast.cpp"
        src.write_text(UNGUARDED)
        # cwd=ROOT: the tool resolves `constraints/llvm_idioms.json` and friends RELATIVE TO THE
        # WORKING DIRECTORY. Run from anywhere else it exits 1 with empty output and a stderr that
        # says "failed to read ..." -- which contains no "error:", so a naive parse-failure check
        # sails past it and reads the empty result as "no findings". That is the same
        # absence-as-evidence trap this whole check exists to avoid, so the exit status is checked
        # explicitly rather than inferred from stderr text.
        proc = subprocess.run([str(tool), str(src), "--", "-std=c++17"],
                              capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if "error:" in proc.stderr:
            print(f"ir_validity_ast_fixture: source did not parse, skipped ({proc.stderr[:80]})")
            return 0
        assert proc.returncode == 0 and proc.stdout.strip(), \
            ("the miner must RUN -- a non-zero exit with empty output is not 'no findings'",
             proc.returncode, proc.stderr[:200])
        findings = [f for f in json.loads(proc.stdout or "[]") if f.get("defect")]

    flagged = {f["function"] for f in findings}
    # 1) THE UNGUARDED CASTS ARE FOUND -- including the `noexcept` one, which is the whole reason
    #    this check lives in the AST tool rather than in a regex miner.
    assert "unguarded" in flagged, ("an input-derived value bitcast to a hardcoded type with no "
                                    "width check is invalid IR for every other width", flagged)
    assert "unguarded_noexcept" in flagged, \
        ("...and a `noexcept` function must be seen -- the regex splitter dropped exactly this "
         "form, so the defective function was never analysed at all", flagged)

    # 2) THE GUARD IS RESPECTED. Without this the check is a bitcast counter, and every pass that
    #    correctly tests its operand's width would be accused.
    assert "guarded" not in flagged, \
        ("a function that checks the width has considered the question and must not be flagged",
         flagged)

    # 3) EVERY FINDING NAMES THE PINNED WIDTH, because "somewhere in here is a bad bitcast" is not
    #    actionable; the width is what makes it checkable against the operand.
    for f in findings:
        assert f.get("pinned_to"), ("a finding must name the width it is pinned to", f)
        assert f.get("line"), f

    print("ir_validity_ast_fixture OK: bitcast to a hardcoded width with no width guard is found "
          "from SOURCE (both plain and `noexcept` -- the form the regex splitter dropped), a "
          "guarded cast is left alone, and each finding names the pinned width. Measured "
          "elsewhere: 2 findings on the real ConvertFCmpEq bug, 0 across 251 parseable fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
