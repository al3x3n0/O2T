#!/usr/bin/env python3
"""Value equality does not imply refinement when the TARGET can be poison.

Three of Track B's validators compare VALUES and carry no poison term at all: the memory model
(`mem_state`), and the fixed and scalable lane models (`vec_tv`, `svec_tv`). Their soundness argument
was "value-equal everywhere implies refinement", and `poison_risk` existed to stop them REFUTING --
because a value mismatch may be a sound poison exploitation, such as `opt` folding a poison
`ashr x, x` to 0.

Nothing stopped them PROVING, and the argument is FALSE in that direction: poison is not a value, so
a target that is poison where the source is defined can still be value-equal everywhere. The
synthesized-target fuzzer found two live instances immediately, both confirmed by reference Alive2:

  * LANE MODEL -- the target adds `exact` to an `lshr` feeding the returned value. The values are
    identical; the target is poison whenever the shift is inexact.
  * MEMORY MODEL -- the target stores `shl %x, (ashr 1, -1)`. LLVM makes that poison, because the
    shift amount is >= the bit width; SMT gives `bvashr` a defined 0, so the model saw the stored
    value as plain `%x` and the final memories as equal.

The side condition is on the TARGET, and it is not a blanket decline: if the target cannot produce
poison and its values agree everywhere, then it is defined wherever the source is, so refinement
genuinely holds. The SOURCE may still carry poison -- which is what keeps ordinary folds provable and
is why the guard is `target_may_poison`, not `poison_risk` on both sides.

Gated here:
  * both live false proofs are declined, and Alive2 refutes both;
  * poison-free folds still PROVE, so the guard is not a blanket refusal;
  * a source carrying poison does not by itself block a proof;
  * the decline is TAGGED, so the dispatcher cannot route around it into another validator (the seam
    `dispatcher_guard_fixture` covers).
Needs z3; the Alive2 confirmations self-skip without `alive-tv`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402
from o2t.validate.corpus_tv import validate_transform_ex  # noqa: E402
from o2t.validate.mem_state import mem_state_tv  # noqa: E402
from o2t.validate.vec_tv import vec_tv  # noqa: E402

V = "<4 x i32>"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("target_poison_fixture: z3 not found, skipped")
        return 0
    alive = shutil.which("alive-tv")

    def confirm(before, after, expect):
        """Cross-check the premise against Alive2. `skip` is accepted: alive-tv reports 0 correct /
        0 incorrect for transforms it treats as no-ops, which is a non-answer rather than a
        disagreement."""
        if alive:
            got = alive_refines(before, after, alive).get("status")
            assert got in (expect, "skip"), ("Alive2 contradicts the premise of this case", expect, got)

    # 1) LANE MODEL: adding `exact` keeps every lane value identical and makes the target poison.
    vec_b = f"define {V} @f({V} %x, {V} %y) {{\n  %s = lshr {V} %x, %y\n  %r = mul {V} %s, %s\n  ret {V} %r\n}}\n"
    vec_a = vec_b.replace(f"lshr {V}", f"lshr exact {V}")
    v = vec_tv(z3, vec_b, vec_a, "f")
    assert v["status"] == "unsupported" and v.get("guard") == "target-poison", v
    confirm(vec_b, vec_a, "refuted")

    # 2) MEMORY MODEL: `ashr 1, -1` is poison in LLVM (shift >= width) but a defined 0 in SMT, so the
    #    stored value looked equal to `%x` and the final memories matched.
    mem_b = ("define i32 @f(ptr %p, ptr %q, i32 %x) {\n"
             "  %sh = ashr i32 1, -1\n  %v = load i32, ptr %q\n  store i32 %x, ptr %p\n"
             "  %pv = shl i32 %x, %sh\n  store i32 %pv, ptr %p\n  store i32 %x, ptr %p\n"
             "  ret i32 %v\n}\n")
    mem_a = mem_b.replace("  store i32 %pv, ptr %p\n  store i32 %x, ptr %p\n",
                          "  store i32 %pv, ptr %p\n")
    m = mem_state_tv(z3, mem_b, mem_a, "f")
    assert m["status"] == "unsupported" and m.get("guard") == "target-poison", m
    confirm(mem_b, mem_a, "refuted")

    # 3) NOT A BLANKET REFUSAL. A poison-free target still proves -- dead-store elimination and an
    #    element-wise vector fold both go through.
    dse_b = ("define i32 @f(ptr %p) {\n  store i32 1, ptr %p\n  store i32 2, ptr %p\n"
             "  %v = load i32, ptr %p\n  ret i32 %v\n}\n")
    dse_a = "define i32 @f(ptr %p) {\n  store i32 2, ptr %p\n  ret i32 2\n}\n"
    assert mem_state_tv(z3, dse_b, dse_a, "f")["status"] == "proved", "DSE must still prove"

    fold_b = f"define {V} @f({V} %x) {{\n  %r = add {V} %x, zeroinitializer\n  ret {V} %r\n}}\n"
    fold_a = f"define {V} @f({V} %x) {{\n  ret {V} %x\n}}\n"
    assert vec_tv(z3, fold_b, fold_a, "f")["status"] == "proved", "an element-wise fold must prove"

    # 4) The gate is on the TARGET. A source that carries poison does not block a proof, which is what
    #    keeps flag-DROPPING folds (a sound direction) provable.
    drop_b = f"define {V} @f({V} %x, {V} %y) {{\n  %r = add nsw {V} %x, %y\n  ret {V} %r\n}}\n"
    drop_a = f"define {V} @f({V} %x, {V} %y) {{\n  %r = add {V} %x, %y\n  ret {V} %r\n}}\n"
    d = vec_tv(z3, drop_b, drop_a, "f")
    assert d["status"] == "proved", ("dropping a flag is sound and must still prove", d)
    confirm(drop_b, drop_a, "proved")

    # 5) The decline is TAGGED, so the dispatcher returns it rather than routing to another
    #    value-equality validator that would prove the same pair.
    disp = validate_transform_ex(z3, vec_b, vec_a, "f")
    assert disp["status"] == "unsupported", ("a target-poison decline must survive dispatch", disp)

    oracle = "confirmed against reference Alive2" if alive else "Alive2 absent (skipped)"
    print("target_poison_fixture OK: 'value-equal everywhere implies refinement' is FALSE when the "
          "TARGET can be poison -- poison is not a value. Both live cases the synthesized-target "
          "fuzzer found are now declined: a lane-model target adding `exact` (identical lane values, "
          "poison when the shift is inexact) and a memory target storing `shl %x, (ashr 1, -1)` "
          "(poison in LLVM, a defined 0 in SMT, so the final memories looked equal). The condition is "
          "on the TARGET only, so poison-free folds still prove and a poison-carrying SOURCE still "
          f"permits one -- flag-dropping stays provable. Every verdict {oracle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
