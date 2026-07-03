#!/usr/bin/env python3
"""Cover closed-loop InstCombine translation validation (validate/scalar_ir.py).

Asserts that the LITERAL output of the real `opt -passes=instcombine` is proved equivalent to the
input for every supported single-BB integer function (the returned value matches for all inputs),
with translation-validation teeth: a faulty output (wrong returned operand) is REFUTED with a
concrete input witness. The obligation is Alive2 REFINEMENT (not raw value-equality), so a fold
that introduces poison (an unjustified nsw/nuw/exact/disjoint) or UB (a fresh div-by-zero) is also
refuted -- even when the returned value is unchanged -- while dropping a flag / removing UB still
proves. Also checks the soundness boundary -- an unmodeled instruction is declined (`unsupported`),
never falsely proved -- and that the cascade really folded (non-vacuous). Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir as si


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("instcombine_ir_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "instcombine_ir_cases.ll").read_text()
    opt_text = si.run_instcombine(src, opt)
    assert opt_text is not None, "opt -passes=instcombine failed"

    # 1) the REAL InstCombine output proves equivalent for every function (incl. select->smin,
    #    and the multi-fold cascade collapsing to a single return).
    by = {fn: si.validate_instcombine(z3, src, opt_text, fn) for fn in si.function_names(src)}
    for fn, r in by.items():
        assert r["status"] == "proved", ("real InstCombine output not proved", fn, r)
    # the cascade really folded away its instructions (non-vacuous: the output is just a return).
    assert "ret i32 %b" in opt_text and "add i32" not in opt_text.split("@cascade")[1].split("}")[0]

    # 2) TEETH: corrupt the optimized @cascade (return %a instead of %b) -> must REFUTE with a
    #    witness where %a and %b differ.
    faulty = opt_text.replace("  ret i32 %b\n", "  ret i32 %a\n", 1)
    assert faulty != opt_text, "fault injection did not match the folded return"
    bad = si.validate_instcombine(z3, src, faulty, "cascade")
    assert bad["status"] == "refuted" and bad.get("witness"), ("a wrong fold not caught", bad)

    # 3) SOUNDNESS boundary: an unmodeled instruction is declined, not mis-proved.
    weird = ("define i32 @weird(i32 %a, ptr %p) {\n"
             "  %v = load i32, ptr %p\n  %r = add i32 %a, %v\n  ret i32 %r\n}\n")
    wout = si.run_instcombine(weird, opt)
    assert si.validate_instcombine(z3, weird, wout, "weird")["status"] == "unsupported"

    # 4) translator sanity: a known fold's returned term is over the parameters.
    params, term, width, poison, ub = si.translate(
        "define i32 @id(i32 %a){\n%x = add i32 %a, 0\nret i32 %x\n}", "id")
    assert params == {"%a": 32} and width == 32 and "%a" in term, (params, term, width)
    assert poison == "false" and ub == "false", (poison, ub)

    # 5) POISON/UB-REFINEMENT teeth: the validator is Alive2 refinement, not raw value-equality, so
    #    a fold that INTRODUCES poison (an unjustified nsw/nuw/exact/disjoint) or UB (a fresh
    #    div/rem-by-zero) is refuted even when the returned VALUE is unchanged -- while the sound
    #    reverse (dropping a flag, removing UB) still proves. This is the soundness hole raw
    #    equality missed.
    def fn(body):
        return f"define i32 @f(i32 %a, i32 %b){{\n{body}\n}}"
    refine = [  # (before, after, expected)
        (fn("%x = add i32 %a, %b\nret i32 %x"),      # add -> add nsw : same value, new poison
         fn("%x = add nsw i32 %a, %b\nret i32 %x"), "refuted"),
        (fn("%x = add nsw i32 %a, %b\nret i32 %x"),  # add nsw -> add : flag DROP is sound
         fn("%x = add i32 %a, %b\nret i32 %x"), "proved"),
        (fn("%x = or i32 %a, %b\nret i32 %x"),       # or -> or disjoint : new poison
         fn("%x = or disjoint i32 %a, %b\nret i32 %x"), "refuted"),
        (fn("%x = lshr i32 %a, %b\nret i32 %x"),     # lshr -> lshr exact : new poison
         fn("%x = lshr exact i32 %a, %b\nret i32 %x"), "refuted"),
        (fn("ret i32 %a"),                            # introduce a (dead) div-by-zero : new UB
         fn("%bad = udiv i32 %a, %b\nret i32 %a"), "refuted"),
        (fn("%bad = udiv i32 %a, %b\nret i32 %a"),   # remove the div : UB removal is sound
         fn("ret i32 %a"), "proved"),
    ]
    for before, after, want in refine:
        got = si.validate_transform(z3, before, after, "f")
        assert got["status"] == want, ("refinement teeth", want, got, after)
        if want == "refuted":
            assert got.get("witness"), ("refutation lacks a witness", got)

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-instcombine-ir.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 5' in proc.stdout, proc.stdout

    print("instcombine_ir_fixture OK: real `opt -passes=instcombine` output proved equivalent "
          "over scalar IR->SMT for every function (incl. select->smin canonicalization); a wrong "
          "fold refuted with a witness; poison/UB-introducing folds (new nsw/nuw/exact/disjoint or "
          "div-by-zero) refuted by Alive2 refinement while flag/UB removal still proves; an "
          "unmodeled instruction soundly declined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
