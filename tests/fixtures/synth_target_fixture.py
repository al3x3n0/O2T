#!/usr/bin/env python3
"""The fuzzer's blind spot: audit the API's decision surface, not `opt`'s output distribution.

Every opt-driven fuzz shape derives its target by running real InstCombine. That means the oracles
can only ever audit the assumptions InstCombine happens to RESPECT -- and it is exactly why the
undeclared-`noundef` false proofs survived 2,600+ fuzzed functions and the entire corpus proved set.
InstCombine never introduces a duplicated argument use, so nothing in any campaign ever asked
`validate_transform` about one. They were found by hand instead.

But `validate_transform` is a general API: `compose_tv`, `module_tv`, `argprom_tv` and anyone
validating their own pass reach it, and a buggy pass can emit anything. The `synth` shape therefore
SYNTHESIZES targets -- the source with plausible pass-like rewrites applied, many of them unsound --
and lets reference Alive2 decide. O2T proving where Alive2 refutes is a false proof.

THE POINT OF THIS FIXTURE IS THE ACID TEST, not the campaign. A fuzzer that looks thorough and cannot
reach the bug it was written for is worth nothing, and the first version of this shape was exactly
that: its generic mutations (duplicate a parameter use, add/drop a flag, swap operands, return a
different value) produced only SOUND duplications, because its sources already used their parameters
everywhere. Disabling the undef guard surfaced ZERO false proofs -- the campaign was blind to the very
class it targeted. Reaching it needs a source whose result is INDEPENDENT of a parameter and a target
that makes it depend on one (`ret 0` -> `xor %p, %p`, sound only under `noundef`), which the shape now
generates deliberately.

So this fixture disables the guard and requires the fuzzer to CATCH it. That is the same discipline
`concrete_tv_fixture` and `alive_diff_fixture` use -- inject the bug, watch the oracle bite -- applied
to the fuzzer's own coverage rather than to a validator.

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
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402

TOOL = ROOT / "tools" / "cv-fuzz-differential.py"


def _fuzzer():
    spec = importlib.util.spec_from_file_location("cv_fuzz_differential", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _campaign(fz, z3, alive, n, seed):
    """Run n synthesized pairs; return (proved, contradicted_by_alive2)."""
    rng = random.Random(seed)
    proved = contradicted = 0
    for _ in range(n):
        before, after = fz._gen_synth(rng, 6)
        v = si.validate_transform(z3, before, after, "f", timeout=15)
        if v["status"] != "proved":
            continue
        proved += 1
        if alive_refines(before, after, alive).get("status") == "refuted":
            contradicted += 1
    return proved, contradicted


def main() -> int:
    z3 = shutil.which("z3")
    alive = shutil.which("alive-tv")
    if z3 is None or alive is None:
        print("synth_target_fixture: needs z3 + alive-tv, skipped")
        return 0
    fz = _fuzzer()

    # 1) AS SHIPPED: synthesized targets, decided against Alive2. Proofs must stand.
    proved, contradicted = _campaign(fz, z3, alive, n=60, seed=5)
    assert proved > 0, "the shape must produce provable transforms, not only refuted ones"
    assert contradicted == 0, (f"{contradicted} synthesized transforms O2T proved and Alive2 refutes "
                               "-- a FALSE PROOF on the API's decision surface")

    # 2) THE ACID TEST. Disable the protection against the undeclared-`noundef` assumption -- the
    #    exact bug that escaped every opt-driven campaign -- and require this shape to SEE it. Without
    #    this, a green campaign would mean only that the fuzzer never asked the question.
    #
    #    TWO independent mechanisms now cover that class and BOTH have to be disabled for the shape to
    #    surface it, which is itself the point: the undef-risk guard declines when the target's result
    #    depends on a possibly-undef parameter the source's does not, and, separately, a parameter
    #    without `noundef` carries a poison flag, so a target returning one is poison where a definite
    #    source is not. Disabling only the guard leaves the poison flag refuting the same transforms,
    #    and the campaign stays clean for a real reason rather than a blind one.
    saved, saved_flag = si._noundef_params, si.param_poison_flag
    try:
        si._noundef_params = lambda ll, fn: set(si._params(ll, fn))   # pretend every arg is noundef
        si.param_poison_flag = lambda name: "false"                   # ...and that none can be poison
        _, blind_hits = _campaign(fz, z3, alive, n=60, seed=5)
    finally:
        si._noundef_params, si.param_poison_flag = saved, saved_flag
    assert blind_hits > 0, ("the synthesized-target shape must REACH the noundef class: with the "
                            "guard disabled it has to surface false proofs, or it is not auditing "
                            "the assumption at all")

    # 3) ...and with the guard restored the same campaign is clean again, so what the acid test
    #    detected was the injected bug and not flakiness in the shape.
    _, again = _campaign(fz, z3, alive, n=60, seed=5)
    assert again == 0, ("the campaign must be clean once the guard is restored", again)

    print(f"synth_target_fixture OK: the fuzzer now audits the API's DECISION SURFACE, not just "
          f"`opt`'s output distribution. Synthesized targets (duplicated argument uses, added/dropped "
          f"poison flags, swapped operands, substituted returns, introduced freezes) are decided "
          f"against reference Alive2 -- {proved} proved, 0 contradicted. And the ACID TEST holds: "
          f"disabling the undeclared-`noundef` guard makes {blind_hits} false proofs surface, so this "
          "shape can see the class that survived 2,600+ opt-driven functions and the whole corpus "
          "proved set. An earlier version of the shape could NOT -- its sources used their parameters "
          "everywhere, so duplicating a use only ever produced sound targets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
