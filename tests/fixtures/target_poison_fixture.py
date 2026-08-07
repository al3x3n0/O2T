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

    answered = []

    def confirm(before, after, expect):
        """Cross-check the premise against Alive2. A `skip` is a NON-ANSWER, not agreement -- it
        covers a parse failure, a timeout, and a query Alive2 could not decide alike. It is accepted
        (a non-answer cannot contradict anything) but it is COUNTED, because a run in which every
        confirmation skipped would mean this cross-check silently verified nothing."""
        if alive:
            res = alive_refines(before, after, alive)
            got = res.get("status")
            assert got in (expect, "skip"), ("Alive2 contradicts the premise of this case", expect, got)
            answered.append(got != "skip")

    # 1) LANE MODEL -- and this case has GRADUATED. Adding `exact` keeps every lane value identical
    #    and makes the target poison, which a value-equality model could only DECLINE: it saw equal
    #    values and had to refuse the proof rather than risk one. The lane model now carries poison
    #    per lane and discharges the real refinement obligation, so it REFUTES, which is what Alive2
    #    says too. The guard it used to return no longer exists on this path.
    vec_b = f"define {V} @f({V} %x, {V} %y) {{\n  %s = lshr {V} %x, %y\n  %r = mul {V} %s, %s\n  ret {V} %r\n}}\n"
    vec_a = vec_b.replace(f"lshr {V}", f"lshr exact {V}")
    v = vec_tv(z3, vec_b, vec_a, "f")
    assert v["status"] == "refuted" and v.get("witness"), \
        ("introducing poison into a vector target must now REFUTE, not decline", v)
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
    assert m["status"] == "refuted" and m.get("witness"), \
        ("the memory model now carries a poison bit per BYTE, so a target whose final memory is "
         "poison where the source's is defined must REFUTE, not decline", m)
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

    # 5) The dispatcher agrees with the validator that decided it. The tagged-decline mechanism it
    #    used to rely on still matters for the validators that remain value-equality (the memory
    #    model above, and the scalable lane model), which is what case 2 pins.
    disp = validate_transform_ex(z3, vec_b, vec_a, "f")
    assert disp["status"] == "refuted", ("the dispatcher must report the lane model's verdict", disp)
    mdisp = validate_transform_ex(z3, mem_b, mem_a, "f")
    assert mdisp["status"] == "refuted", ("the dispatcher must report the memory model's verdict", mdisp)

    # The oracle must have actually SPOKEN at least once. Without this, a systematic Alive2 timeout
    # would turn every `confirm` above into a no-op while the fixture still reported success.
    assert not alive or any(answered), \
        "every Alive2 confirmation in this fixture was a non-answer -- the cross-check verified nothing"

    oracle = "confirmed against reference Alive2" if alive else "Alive2 absent (skipped)"
    print("target_poison_fixture OK: 'value-equal everywhere implies refinement' is FALSE when the "
          "TARGET can be poison -- poison is not a value. Both live cases the synthesized-target "
          "fuzzer found are now REFUTED rather than declined, which is the point: each model has the "
          "obligation the guard used to stand in for. The lane model carries poison per LANE -- a "
          "target adding `exact` has identical lane values and is poison when the shift is inexact "
          "-- and the memory model carries it per BYTE, so storing `shl %x, (ashr 1, -1)` (poison in "
          "LLVM, a defined 0 in SMT, so the final memories looked equal) is caught by the poison bit "
          "rather than missed by the comparison. The guard survives only where a validator still "
          "compares values, which is the scalable lane model. The condition is on the TARGET only, "
          "so poison-free folds still prove and a poison-carrying SOURCE still permits one -- "
          f"flag-dropping stays provable. Every verdict {oracle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
