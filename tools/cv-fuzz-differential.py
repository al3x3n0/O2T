#!/usr/bin/env python3
"""Differential fuzzer: hunt for a Track B false proof by disagreeing with reference Alive2 at scale.

The 2026-07 soundness review found false proofs by hand-built adversarial cases. This automates that
hunt: generate random small scalar functions (with random poison FLAGS -- the surface where both
found false proofs lived), run the real `opt -passes=instcombine`, and cross-check O2T's whole-function
TV against reference Alive2. A disagreement where O2T is more permissive -- O2T `proved` but Alive2
calls it incorrect -- is a FALSE PROOF; the reverse is a false refutation. Deterministic given --seed.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t import toolchain  # noqa: E402
from o2t.validate import scalar_ir as si  # noqa: E402
from o2t.validate.corpus_tv import validate_transform_ex  # noqa: E402
from o2t.validate.alive_diff import alive_refines  # noqa: E402

_BINOPS = ["add", "sub", "mul", "and", "or", "xor", "shl", "lshr", "ashr"]
_FLAGS = {"add": ["nsw", "nuw"], "sub": ["nsw", "nuw"], "mul": ["nsw", "nuw"],
          "shl": ["nsw", "nuw"], "lshr": ["exact"], "ashr": ["exact"], "or": ["disjoint"]}
_PREDS = ["eq", "ne", "slt", "sle", "sgt", "sge", "ult", "ule", "ugt", "uge"]


def _gen(rng, n_params=3, n_insns=8, w=32) -> str:
    vals = [f"%p{i}" for i in range(n_params)]
    lines, idx = [], 0

    def opnd():
        if rng.random() < 0.3:
            return str(rng.choice([0, 1, 2, -1, rng.randint(0, w - 1), rng.randint(-8, 8)]))
        return rng.choice(vals)

    for _ in range(n_insns):
        if rng.random() < 0.75:
            op = rng.choice(_BINOPS)
            flag = ""
            if op in _FLAGS and rng.random() < 0.5:      # random poison flag -- the target surface
                flag = " " + rng.choice(_FLAGS[op])
            v = f"%v{idx}"; idx += 1
            lines.append(f"  {v} = {op}{flag} i{w} {opnd()}, {opnd()}")
            vals.append(v)
        else:
            c, v = f"%c{idx}", f"%v{idx}"; idx += 1
            lines.append(f"  {c} = icmp {rng.choice(_PREDS)} i{w} {opnd()}, {opnd()}")
            lines.append(f"  {v} = select i1 {c}, i{w} {opnd()}, i{w} {opnd()}")
            vals.append(v)
    sig = ", ".join(f"i{w} %p{i}" for i in range(n_params))
    return f"define i{w} @f({sig}) {{\n" + "\n".join(lines) + f"\n  ret i{w} {rng.choice(vals)}\n}}\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0x02704)
    ap.add_argument("--insns", type=int, default=8)
    ap.add_argument("--params", type=int, default=3)
    args = ap.parse_args(argv)

    z3 = shutil.which("z3")
    opt = toolchain.resolve_opt("opt")
    alive = shutil.which("alive-tv")
    if not (z3 and opt and alive):
        print("cv-fuzz-differential: needs z3 + opt(18) + alive-tv", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    pair, alive_v, disagreements, opt_fail = Counter(), Counter(), [], 0
    for i in range(args.count):
        before = _gen(rng, args.params, args.insns)
        after = si.run_instcombine(before, opt)
        if after is None:
            opt_fail += 1
            continue
        o2t = validate_transform_ex(z3, before, after, "f")["status"]
        pair[o2t] += 1
        if o2t not in ("proved", "refuted"):
            continue                                     # only the decisive verdicts can disagree
        av = alive_refines(before, after, alive)["status"]
        alive_v[av] += 1
        if o2t == "proved" and av == "refuted":
            disagreements.append(("FALSE-PROOF", i, before, after))
        elif o2t == "refuted" and av == "proved":
            disagreements.append(("FALSE-REFUTATION", i, before, after))

    print(f"generated {args.count} (opt-failed {opt_fail}); O2T {dict(pair)}; Alive2 {dict(alive_v)}")
    print(f"DISAGREEMENTS: {len(disagreements)}")
    for kind, i, b, a in disagreements[:10]:
        print(f"\n!! {kind} at #{i}\n-- before --\n{b}-- after --\n{a}")
    return 1 if disagreements else 0


if __name__ == "__main__":
    raise SystemExit(main())
