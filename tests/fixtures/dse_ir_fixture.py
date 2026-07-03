#!/usr/bin/env python3
"""Cover closed-loop DSE translation validation (validate/dse_ir.py).

Asserts that the LITERAL output of the real `opt -passes=dse` is proved equivalent to the input
over a theory of arrays (final memory preserved) for every escaping-memory function, with
translation-validation teeth: a faulty output that drops a still-LIVE store is REFUTED with a
colliding-address witness. Also checks the soundness boundary -- a function with a local alloca
(non-escaping) is declined, not over-refuted. Needs z3 and opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import dse_ir


def _resolve(name, fallback):
    return shutil.which(name) or (fallback if Path(fallback).exists() else None)


def main() -> int:
    z3 = _resolve("z3", "/opt/homebrew/bin/z3")
    opt = _resolve("opt", "/opt/homebrew/opt/llvm@18/bin/opt")
    if z3 is None or opt is None:
        print("dse_ir_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "dse_ir_cases.ll").read_text()
    opt_text = dse_ir.run_dse(src, opt)
    assert opt_text is not None, "opt -passes=dse failed"

    # 1) the REAL DSE output proves equivalent for every function (final memory preserved).
    by = {fn: dse_ir.validate_dse(z3, src, opt_text, fn) for fn in dse_ir.function_names(src)}
    for fn, r in by.items():
        assert r["status"] == "proved", ("real DSE output not proved", fn, r)
    # the dead-store function really did shrink (opt removed an instruction) -- non-vacuous.
    assert by["dead_store"]["after_ops"] < by["dead_store"]["before_ops"], by["dead_store"]

    # 2) TEETH: corrupt the optimized output by deleting a still-LIVE store -> must REFUTE.
    #    For @two_pointer the store to %q is live; dropping it changes the final memory at q.
    faulty = opt_text.replace("  store i32 2, ptr %q, align 4\n", "", 1)
    assert faulty != opt_text, "fault injection did not match a live store line"
    bad = dse_ir.validate_dse(z3, src, faulty, "two_pointer")
    assert bad["status"] == "refuted" and bad.get("witness"), ("a dropped live store not caught", bad)

    # 3) SOUNDNESS boundary: a local-alloca function is declined (not over-refuted), because a
    #    store to non-escaping memory may be legally dead at exit.
    alloca_ll = ("define i32 @local() {\nentry:\n  %a = alloca i32\n"
                 "  store i32 1, ptr %a\n  ret i32 0\n}\n")
    aout = dse_ir.run_dse(alloca_ll, opt)
    av = dse_ir.validate_dse(z3, alloca_ll, aout, "local")
    assert av["status"] == "unsupported", ("alloca case must be declined, not refuted", av)

    # 3b) SOUNDNESS teeth for the strict parser + load observability. Each of these was either
    #     mis-modeled as a plain word store (a FALSE PROOF) or unobserved by the old final-memory-only
    #     check; all are now declined or refuted.
    def f(body):
        return f"define {body}"
    # mixed-width partial overwrite: a wide store partially overwritten by a narrow one then dropped.
    mixed_b = f("void @m(ptr %p){\n  store i32 0, ptr %p\n  store i8 1, ptr %p\n  ret void\n}")
    mixed_a = f("void @m(ptr %p){\n  store i8 1, ptr %p\n  ret void\n}")
    assert dse_ir.validate_dse(z3, mixed_b, mixed_a, "m")["status"] == "unsupported", "partial overwrite not declined"
    # volatile store: sync semantics not modeled by the word model -> declined, not proved.
    vol_b = f("void @v(ptr %p){\n  store volatile i32 0, ptr %p\n  store i32 1, ptr %p\n  ret void\n}")
    vol_a = f("void @v(ptr %p){\n  store i32 1, ptr %p\n  ret void\n}")
    assert dse_ir.validate_dse(z3, vol_b, vol_a, "v")["status"] == "unsupported", "volatile not declined"
    # a call may write memory -> declined rather than silently skipped.
    call_b = f("void @c(ptr %p){\n  store i32 0, ptr %p\n  call void @g(ptr %p)\n  store i32 1, ptr %p\n  ret void\n}")
    call_a = f("void @c(ptr %p){\n  call void @g(ptr %p)\n  store i32 1, ptr %p\n  ret void\n}")
    assert dse_ir.validate_dse(z3, call_b, call_a, "c")["status"] == "unsupported", "call not declined"
    # load observability: a store dropped though a surviving load reads it, with the SAME final
    # memory (a later store overwrites) -> missed by final-memory alone, now refuted at the load.
    live_b = f("i32 @r(ptr %p){\n  store i32 0, ptr %p\n  %r = load i32, ptr %p\n  store i32 1, ptr %p\n  ret i32 %r\n}")
    live_a = f("i32 @r(ptr %p){\n  %r = load i32, ptr %p\n  store i32 1, ptr %p\n  ret i32 %r\n}")
    rl = dse_ir.validate_dse(z3, live_b, live_a, "r")
    assert rl["status"] == "refuted" and rl.get("observable", "").startswith("load:"), \
        ("eliminated store read by a live load not caught at the load observable", rl)

    # 4) parsing sanity: the before/after op lists are the literal instructions.
    ops = dse_ir.parse_mem_ops(src, "dead_store")
    assert ops == [{"op": "store", "addr": "p", "val": "lit_1"},
                   {"op": "store", "addr": "p", "val": "lit_2"},
                   {"op": "load", "name": "v", "addr": "p"}], ops

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-validate-dse-ir.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout and '"proved": 3' in proc.stdout, proc.stdout

    print("dse_ir_fixture OK: real `opt -passes=dse` output proved over a theory of arrays (final "
          "memory AND surviving-load values) for every escaping function; a dropped live store "
          "refuted with a witness; mixed-width/volatile/call shapes soundly declined; an eliminated "
          "store read by a live load refuted at the load observable; the alloca case declined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
