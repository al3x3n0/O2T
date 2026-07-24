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
# the modeled intrinsics (o2t/validate/scalar_ir.py); the fuzzer exercises these encodings vs Alive2.
_I_UNARY = ["ctpop"]
_I_FLAGGED = ["abs", "ctlz", "cttz"]                       # unary + an i1 flag
_I_BINARY = ["smin", "smax", "umin", "umax", "sadd.sat", "ssub.sat", "uadd.sat", "usub.sat"]
_I_TERNARY = ["fshl", "fshr"]


def _declares(w):
    d = [f"declare i{w} @llvm.{n}.i{w}(i{w})" for n in _I_UNARY]
    d += [f"declare i{w} @llvm.{n}.i{w}(i{w}, i1)" for n in _I_FLAGGED]
    d += [f"declare i{w} @llvm.{n}.i{w}(i{w}, i{w})" for n in _I_BINARY]
    d += [f"declare i{w} @llvm.{n}.i{w}(i{w}, i{w}, i{w})" for n in _I_TERNARY]
    return "\n".join(d) + "\n"


def _gen(rng, n_params=3, n_insns=8, w=32, intrinsics=False) -> str:
    vals = [f"%p{i}" for i in range(n_params)]
    lines, idx = [], 0

    def opnd():
        if rng.random() < 0.3:
            return str(rng.choice([0, 1, 2, -1, rng.randint(0, w - 1), rng.randint(-8, 8)]))
        return rng.choice(vals)

    def flag():
        return rng.choice(["true", "false"])

    for _ in range(n_insns):
        r = rng.random()
        v = f"%v{idx}"; idx += 1
        if intrinsics and r < 0.35:                       # an intrinsic call -- fuzz its encoding
            k = rng.random()
            if k < 0.25:
                lines.append(f"  {v} = call i{w} @llvm.{rng.choice(_I_UNARY)}.i{w}(i{w} {opnd()})")
            elif k < 0.5:
                lines.append(f"  {v} = call i{w} @llvm.{rng.choice(_I_FLAGGED)}.i{w}(i{w} {opnd()}, i1 {flag()})")
            elif k < 0.8:
                lines.append(f"  {v} = call i{w} @llvm.{rng.choice(_I_BINARY)}.i{w}(i{w} {opnd()}, i{w} {opnd()})")
            else:
                lines.append(f"  {v} = call i{w} @llvm.{rng.choice(_I_TERNARY)}.i{w}(i{w} {opnd()}, i{w} {opnd()}, i{w} {opnd()})")
            vals.append(v)
        elif r < 0.8:
            op = rng.choice(_BINOPS)
            fl = " " + rng.choice(_FLAGS[op]) if op in _FLAGS and rng.random() < 0.5 else ""
            lines.append(f"  {v} = {op}{fl} i{w} {opnd()}, {opnd()}")
            vals.append(v)
        else:
            c = f"%c{idx}"
            lines.append(f"  {c} = icmp {rng.choice(_PREDS)} i{w} {opnd()}, {opnd()}")
            lines.append(f"  {v} = select i1 {c}, i{w} {opnd()}, i{w} {opnd()}")
            vals.append(v)
    sig = ", ".join(f"i{w} %p{i}" for i in range(n_params))
    body = f"define i{w} @f({sig}) {{\n" + "\n".join(lines) + f"\n  ret i{w} {rng.choice(vals)}\n}}\n"
    return (_declares(w) + body) if intrinsics else body


def _gen_memory(rng, n_insns=7, w=32) -> str:
    """Single-BB pointer-side-effect memory (mem_state scope): random stores/loads to ptr args."""
    ptrs, vals, lines, idx = ["%p", "%q"], ["%x", "%y"], [], 0

    def opnd():
        return str(rng.choice([0, 1, -1, rng.randint(-8, 8)])) if rng.random() < 0.3 else rng.choice(vals)

    for _ in range(n_insns):
        r = rng.random()
        if r < 0.35:
            lines.append(f"  store i{w} {opnd()}, ptr {rng.choice(ptrs)}")
        elif r < 0.65:
            v = f"%v{idx}"; idx += 1
            lines.append(f"  {v} = load i{w}, ptr {rng.choice(ptrs)}")
            vals.append(v)
        else:
            v = f"%v{idx}"; idx += 1
            lines.append(f"  {v} = {rng.choice(_BINOPS)} i{w} {opnd()}, {opnd()}")
            vals.append(v)
    return (f"define i{w} @f(ptr %p, ptr %q, i{w} %x, i{w} %y) {{\n" + "\n".join(lines)
            + f"\n  ret i{w} {rng.choice(vals)}\n}}\n")


def _gen_vector(rng, n_insns=6, w=32, lanes=4) -> str:
    """Element-wise fixed-vector functions (vec_tv scope)."""
    vt = f"<{lanes} x i{w}>"
    vals, lines, idx = ["%x", "%y"], [], 0

    def opnd():
        if rng.random() < 0.3:
            elts = ", ".join(f"i{w} {rng.choice([0, 1, -1, rng.randint(-4, 4)])}" for _ in range(lanes))
            return f"<{elts}>"
        return rng.choice(vals)

    for _ in range(n_insns):
        v = f"%v{idx}"; idx += 1
        lines.append(f"  {v} = {rng.choice(_BINOPS)} {vt} {opnd()}, {opnd()}")
        vals.append(v)
    return f"define {vt} @f({vt} %x, {vt} %y) {{\n" + "\n".join(lines) + f"\n  ret {vt} {rng.choice(vals)}\n}}\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0x02704)
    ap.add_argument("--insns", type=int, default=8)
    ap.add_argument("--params", type=int, default=3)
    ap.add_argument("--intrinsics", action="store_true",
                    help="also generate the modeled intrinsic calls (fuzz their encodings vs Alive2)")
    ap.add_argument("--shape", choices=["scalar", "memory", "vector"], default="scalar",
                    help="what to generate -- scalar (default), pointer memory, or fixed vectors")
    ap.add_argument("--passes", help="opt pipeline (default: instcombine; memory: instcombine,gvn,dse)")
    args = ap.parse_args(argv)

    z3 = shutil.which("z3")
    opt = toolchain.resolve_opt("opt")
    alive = shutil.which("alive-tv")
    if not (z3 and opt and alive):
        print("cv-fuzz-differential: needs z3 + opt(18) + alive-tv", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    passes = args.passes or ("instcombine,gvn,dse" if args.shape == "memory" else "instcombine")
    gen = {"scalar": lambda: _gen(rng, args.params, args.insns, intrinsics=args.intrinsics),
           "memory": lambda: _gen_memory(rng, args.insns),
           "vector": lambda: _gen_vector(rng, args.insns)}[args.shape]
    pair, alive_v, disagreements, opt_fail = Counter(), Counter(), [], 0
    for i in range(args.count):
        before = gen()
        after = si.run_passes(before, passes, opt)
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

    tag = args.shape + ("+intrinsics" if args.intrinsics else "")
    print(f"[{tag} / {passes}] generated {args.count} (opt-failed {opt_fail}); "
          f"O2T {dict(pair)}; Alive2 {dict(alive_v)}")
    print(f"DISAGREEMENTS: {len(disagreements)}")
    for kind, i, b, a in disagreements[:10]:
        print(f"\n!! {kind} at #{i}\n-- before --\n{b}-- after --\n{a}")
    return 1 if disagreements else 0


if __name__ == "__main__":
    raise SystemExit(main())
