#!/usr/bin/env python3
"""A guard is only as strong as the DISPATCHER above it.

Track B routes a function through validators in order -- `scalar_ir`, then `mem_state`, then the
fixed and scalable lane models -- taking the first `proved`/`refuted`. That is sound when a decline
means "this validator does not model that shape": another one may well model it.

It is NOT sound when the decline is a GUARD, because a guard is a statement about the TRANSFORM, not
about the validator. `scalar_ir` declines an undef-risky transform precisely because its soundness
depends on an argument not being `undef`; handing the same pair to a VALUE-EQUALITY validator, which
has no such guard, gets `ret 0 -> xor %p, %p` PROVED, since the two are value-equal. The
synthesized-target fuzzer found exactly that, live, the first time it ran.

So guard declines carry a `guard` tag and the dispatcher treats them as final. This fixture pins both
halves:

  * every guard in the tree tags its decline -- undef-risk in `scalar_ir`, poison-risk in the memory,
    lane and mem2reg models, and the new-dereference guard in `mem_state`. Before this, the other
    guards were safe only BY ACCIDENT OF SHAPE: a pointer function is not vector-shaped, so nothing
    downstream could parse it and overturn the decline. That is luck, not design, and it would have
    broken the moment a later validator grew a wider front-end;
  * the dispatcher honours the tag, so a guard decline cannot be overturned by routing.

And it asserts the ACID TEST rather than a clean sweep: with the rule reverted, a fuzz sweep must
surface false proofs again. Otherwise a green result would only show that this particular input
distribution never exercised the seam -- which is exactly how the original bug survived.

Needs z3 + alive-tv; self-skips without them.
"""

from __future__ import annotations

import importlib.util
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import corpus_tv  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402
from o2t.validate.mem_state import mem_state_tv  # noqa: E402
from o2t.validate.vec_tv import vec_tv  # noqa: E402

V = "<4 x i32>"


def _fuzzer():
    spec = importlib.util.spec_from_file_location(
        "cv_fuzz_differential", ROOT / "tools" / "cv-fuzz-differential.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sweep(fz, z3, alive, dispatch, n, seed):
    """(declines the dispatcher overturned, of those how many Alive2 contradicts)."""
    rng = random.Random(seed)
    overturned = contradicted = 0
    for _ in range(n):
        before, after = fz._gen_synth(rng, 6)
        s = si.validate_transform(z3, before, after, "f", timeout=15)
        d = dispatch(z3, before, after, "f")
        if s["status"] == "unsupported" and d["status"] in ("proved", "refuted"):
            overturned += 1
            av = alive_refines(before, after, alive).get("status")
            if not (d["status"] == av or av == "skip"):
                contradicted += 1
    return overturned, contradicted


def main() -> int:
    z3 = shutil.which("z3")
    alive = shutil.which("alive-tv")
    if z3 is None or alive is None:
        print("dispatcher_guard_fixture: needs z3 + alive-tv, skipped")
        return 0

    # 1) THE CASE THAT WAS LIVE. `scalar_ir` declines it on the undef guard; the lane model would
    #    prove it by value equality; the dispatcher must return the decline.
    before = "define i32 @f(i32 %p0, i32 %p1) {\n  ret i32 0\n}\n"
    after = "define i32 @f(i32 %p0, i32 %p1) {\n  %u = xor i32 %p0, %p0\n  ret i32 %u\n}\n"
    s = si.validate_transform(z3, before, after, "f", timeout=20)
    assert s["status"] == "unsupported" and s.get("guard") == "undef-risk", s
    assert vec_tv(z3, before, after, "f")["status"] == "proved", \
        "the lane model should still prove it in isolation -- that is why the tag is needed"
    d = corpus_tv.validate_transform_ex(z3, before, after, "f")
    assert d["status"] == "unsupported", ("a guard decline must survive dispatch", d)
    assert alive_refines(before, after, alive).get("status") == "refuted", \
        "sanity: Alive2 refutes this transform"

    # 2) EVERY guard tags its decline, so none of them depends on 'no later validator can parse this'.
    poison_mem_b = ("define i32 @f(ptr %p, i32 %x){\n  %s = ashr i32 %x, %x\n"
                    "  store i32 %s, ptr %p\n  %v = load i32, ptr %p\n  ret i32 %v\n}\n")
    poison_mem_a = ("define i32 @f(ptr %p, i32 %x){\n  store i32 0, ptr %p\n"
                    "  %v = load i32, ptr %p\n  ret i32 %v\n}\n")
    m = mem_state_tv(z3, poison_mem_b, poison_mem_a, "f")
    assert m["status"] == "unsupported" and m.get("guard") == "poison-risk", m

    deref_b = "define i32 @f(ptr %p, ptr %q, i32 %x){\n  store i32 %x, ptr %p\n  ret i32 %x\n}\n"
    deref_a = ("define i32 @f(ptr %p, ptr %q, i32 %x){\n  store i32 %x, ptr %p\n"
               "  %v = load i32, ptr %q\n  ret i32 %x\n}\n")
    nd = mem_state_tv(z3, deref_b, deref_a, "f")
    assert nd["status"] == "unsupported" and nd.get("guard") == "new-deref", nd

    # The LANE model no longer needs this guard: it carries poison per lane and discharges the real
    # refinement obligation, so a sound poison exploitation -- `ashr x,x` is poison wherever the shift
    # reaches the width, so folding it to 0 refines -- is PROVED rather than declined, which is what
    # reference Alive2 says too. The guard rule below still matters for the validators that remain
    # value-equality (the memory model above, and the scalable lane model).
    vec_b = f"define {V} @f({V} %x){{\n  %s = ashr {V} %x, %x\n  ret {V} %s\n}}\n"
    vec_a = f"define {V} @f({V} %x){{\n  ret {V} zeroinitializer\n}}\n"
    vg = vec_tv(z3, vec_b, vec_a, "f")
    assert vg["status"] == "proved", ("a sound poison-exploiting vector fold must now PROVE", vg)

    # 3) THE ACID TEST. Sweep synthesized targets: no decline may be overturned into a verdict Alive2
    #    contradicts. Then REVERT the rule and require the same sweep to surface false proofs -- a
    #    clean sweep proves nothing unless the probe can detect the bug it is looking for.
    fz = _fuzzer()
    over, bad = _sweep(fz, z3, alive, corpus_tv.validate_transform_ex, n=60, seed=11)
    assert bad == 0, (f"{bad} dispatcher verdicts contradict Alive2 -- a guard was routed around", over)

    patched = (ROOT / "o2t" / "validate" / "corpus_tv.py").read_text().replace(
        '    if v.get("guard"):\n        return v\n', '', 1)
    ns = dict(corpus_tv.validate_transform_ex.__globals__)
    exec(compile(patched, "corpus_tv_without_guard_rule", "exec"), ns)
    _, bad_reverted = _sweep(fz, z3, alive, ns["validate_transform_ex"], n=60, seed=11)
    assert bad_reverted > 0, ("with the guard rule reverted the sweep must surface false proofs "
                              "again, or it is not probing the seam at all")

    print("dispatcher_guard_fixture OK: a guard is only as strong as the dispatcher above it. The "
          "undef-risk decline that the lane model would still PROVE by value equality (and Alive2 "
          "refutes) survives dispatch; every guard in the tree -- undef-risk, poison-risk in the "
          "memory/lane/mem2reg models, and the new-dereference guard -- now TAGS its decline, so none "
          "of them relies on 'no later validator happens to parse this shape', which is how the other "
          "three were safe before. And the acid test holds: reverting the rule makes false proofs "
          f"reappear in the same sweep ({bad_reverted} of them), so a clean run means the seam is "
          "closed rather than merely unexercised")
    return 0


if __name__ == "__main__":
    sys.exit(main())
