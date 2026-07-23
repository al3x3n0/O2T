#!/usr/bin/env python3
"""EXHAUSTIVE poison-flag coverage: every (op, flag) O2T claims to model must actually generate poison.

Both false proofs found in the 2026-07 soundness review shared a root cause -- a test that CLAIMED
coverage it never exercised (the `exact` flag was tested only on shifts, so `udiv exact` was a silent
no-op that FALSELY proved). This fixture removes that failure mode for the flag/poison model: it
enumerates `formal_ir.VALID_FLAGS` and, for EVERY (op, flag), asserts

  * INTRODUCTION  `op x,y -> op <flag> x,y`  is REFUTED (an unjustified flag introduces poison), and
  * REMOVAL       `op <flag> x,y -> op x,y`  is PROVED  (dropping a flag is a sound refinement).

Crucially it also asserts COMPLETENESS: the set of (op, flag) pairs exercised equals VALID_FLAGS
exactly -- so adding a new flag to the model without a poison check makes this fixture fail. A flag
that is silently a no-op (like `udiv exact` was) can no longer slip through. Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.formal_ir import VALID_FLAGS  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402

# The SMT op names VALID_FLAGS is keyed on -> their LLVM opcodes. Must cover every VALID_FLAGS key
# (a new op without an entry KeyErrors here, forcing the mapping to be kept honest).
_SMT_TO_LLVM = {
    "bvadd": "add", "bvsub": "sub", "bvmul": "mul", "bvshl": "shl",
    "bvlshr": "lshr", "bvashr": "ashr", "bvudiv": "udiv", "bvsdiv": "sdiv", "bvor": "or",
}


def _fn(opcode, flag):
    fl = f" {flag}" if flag else ""
    return f"define i32 @f(i32 %x, i32 %y) {{\n  %r = {opcode}{fl} i32 %x, %y\n  ret i32 %r\n}}\n"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("flag_matrix_fixture: z3 not found, skipped")
        return 0

    missing = set(VALID_FLAGS) - set(_SMT_TO_LLVM)
    assert not missing, ("VALID_FLAGS has ops with no LLVM opcode mapping -- update _SMT_TO_LLVM", missing)

    covered = set()
    for smt_op, flags in sorted(VALID_FLAGS.items()):
        opcode = _SMT_TO_LLVM[smt_op]
        for flag in sorted(flags):
            plain, flagged = _fn(opcode, ""), _fn(opcode, flag)
            intro = si.validate_transform(z3, plain, flagged, "f")
            assert intro["status"] == "refuted" and intro.get("witness"), \
                (f"introducing `{opcode} {flag}` must REFUTE (poison), got", intro)
            remove = si.validate_transform(z3, flagged, plain, "f")
            assert remove["status"] == "proved", \
                (f"removing `{opcode} {flag}` must PROVE (more defined), got", remove)
            covered.add((smt_op, flag))

    expected = {(op, f) for op, fs in VALID_FLAGS.items() for f in fs}
    assert covered == expected, \
        ("COMPLETENESS: every modeled (op, flag) must be exercised; missing", expected - covered)

    pairs = ", ".join(f"{_SMT_TO_LLVM[o]}:{f}" for o, f in sorted(expected))
    print(f"flag_matrix_fixture OK: all {len(expected)} modeled poison flags [{pairs}] verified -- "
          "each REFUTES on introduction and PROVES on removal, and the set exercised EQUALS VALID_FLAGS "
          "(completeness). A flag silently modeled as a no-op (as `udiv exact` once was) can no longer pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
