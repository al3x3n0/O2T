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


def _gen_cfg(rng, n_insns=3, w=32) -> str:
    """A diamond CFG (entry -> {t,f} -> m with a phi) -- fuzzes the multi-block symbolic-execution path.
    SSA dominance respected: t/f use only entry+params; m uses the phi + entry values."""
    params = ["%a", "%b", "%c"]
    idx = [0]

    def straight(pool):
        pool = list(pool)
        out = []
        for _ in range(n_insns):
            op = rng.choice(_BINOPS)
            fl = " " + rng.choice(_FLAGS[op]) if op in _FLAGS and rng.random() < 0.4 else ""

            def o():
                return str(rng.choice([0, 1, -1, rng.randint(-8, 8)])) if rng.random() < 0.3 else rng.choice(pool)
            v = f"%v{idx[0]}"; idx[0] += 1
            out.append(f"  {v} = {op}{fl} i{w} {o()}, {o()}")
            pool.append(v)
        return out, pool

    ev, epool = straight(params)
    ev.append(f"  %cnd = icmp {rng.choice(_PREDS)} i{w} {rng.choice(epool)}, {rng.choice(epool)}")
    ev.append("  br i1 %cnd, label %t, label %f")
    tv, tpool = straight(epool); tv.append("  br label %m")
    fv, fpool = straight(epool); fv.append("  br label %m")
    mv, mpool = straight(epool + ["%ph"])
    mv.insert(0, f"  %ph = phi i{w} [ {rng.choice(tpool)}, %t ], [ {rng.choice(fpool)}, %f ]")
    mv.append(f"  ret i{w} {rng.choice(mpool)}")
    sig = ", ".join(f"i{w} {p}" for p in params)
    return ("\n".join([f"define i{w} @f({sig}) {{", "entry:"] + ev + ["t:"] + tv + ["f:"] + fv
                       + ["m:"] + mv + ["}"]) + "\n")


def _gen_freeze(rng, n_insns=6, w=32):
    """A (before, after) pair for the FREEZE encoding -- the one shape `opt` cannot be used to reach.

    Every other shape derives `after` by running real `opt`, but InstCombine 18 essentially never
    emits `freeze` on random straight-line IR (measured: 0 of 400 generated functions), so the freeze
    model would go unfuzzed. Here the target is SYNTHESIZED instead: take a random function and insert
    `freeze` at a random SSA value on the target side only -- exactly the transform shape a pass
    performs when it launders poison. That is still a real differential, because the verdict is decided
    against reference Alive2, not against a claim of what `opt` would do: a synthesized target may be
    sound (freezing a value whose poison the source never returns) or unsound (freezing NEWLY poisoned
    or otherwise altered values), and O2T must agree with Alive2 either way.
    """
    vals = [f"%p{i}" for i in range(2)]
    lines, idx = [], 0

    def opnd():
        return str(rng.choice([0, 1, -1, rng.randint(0, w - 1)])) if rng.random() < 0.3 else rng.choice(vals)

    for _ in range(n_insns):
        op = rng.choice(_BINOPS)
        fl = " " + rng.choice(_FLAGS[op]) if op in _FLAGS and rng.random() < 0.6 else ""
        v = f"%v{idx}"; idx += 1
        lines.append(f"  {v} = {op}{fl} i{w} {opnd()}, {opnd()}")
        vals.append(v)
    ret = rng.choice(vals)
    sig = ", ".join(f"i{w} %p{i}" for i in range(2))
    before = f"define i{w} @f({sig}) {{\n" + "\n".join(lines) + f"\n  ret i{w} {ret}\n}}\n"

    # The target freezes one value. Half the time it also perturbs a flag, so the batch contains both
    # sound and unsound targets -- a fuzzer that only generates sound pairs cannot detect over-permissive
    # proving.
    tgt = list(lines)
    if rng.random() < 0.5 and tgt:
        i = rng.randrange(len(tgt))
        for f in ("nsw", "nuw", "exact", "disjoint"):
            if f" {f} " in tgt[i]:
                tgt[i] = tgt[i].replace(f" {f} ", " ", 1)
                break
        else:
            op = tgt[i].split("=")[1].split()[0]
            if op in _FLAGS:
                tgt[i] = tgt[i].replace(f"= {op} ", f"= {op} {rng.choice(_FLAGS[op])} ", 1)
    fz = rng.choice(vals)
    after = (f"define i{w} @f({sig}) {{\n" + "\n".join(tgt)
             + f"\n  %fz = freeze i{w} {fz}\n  ret i{w} " + (f"%fz\n}}\n" if fz == ret else f"{ret}\n}}\n"))
    return before, after


def _gen_synth(rng, n_insns=6, w=32):
    """A (before, after) pair whose TARGET IS SYNTHESIZED -- the fuzzer's blind spot, made visible.

    Every opt-driven shape derives `after` by running real InstCombine, so the target distribution is
    "what InstCombine emits". That means the oracles can only ever audit the assumptions InstCombine
    happens to RESPECT, and it is precisely why the undeclared-`noundef` false proofs survived 2,600+
    fuzzed functions and the whole corpus proved set: InstCombine never introduces a duplicated
    argument use, so nothing in the campaign ever asked `validate_transform` about one. They were
    found by hand instead.

    But `validate_transform` is a general API: `compose_tv`, `module_tv`, `argprom_tv` and anyone
    validating their own pass all reach it, and a buggy pass can emit anything. So here the target is
    the source with one or two plausible PASS-LIKE REWRITES applied -- some sound, many not:

      * duplicate a parameter use (the undef/noundef class -- sound only under `noundef`);
      * add or drop a poison flag (the surface both 2026-07 false proofs lived on);
      * swap a binop's operands, or substitute one operand for another value in scope;
      * return a different in-scope value, or a constant;
      * freeze the returned value.

    Alive2 decides, not a claim about what opt would do. O2T proving where Alive2 refutes is a false
    proof; the reverse is a false refutation. Declines are fine and expected -- the point is that the
    QUESTION now gets asked.
    """
    params = [f"%p{i}" for i in range(2)]
    vals, lines, idx = list(params), [], 0

    def opnd():
        return str(rng.choice([0, 1, -1, rng.randint(-8, 8)])) if rng.random() < 0.3 else rng.choice(vals)

    for _ in range(n_insns):
        op = rng.choice(_BINOPS)
        fl = " " + rng.choice(_FLAGS[op]) if op in _FLAGS and rng.random() < 0.4 else ""
        v = f"%v{idx}"; idx += 1
        lines.append(f"  {v} = {op}{fl} i{w} {opnd()}, {opnd()}")
        vals.append(v)
    ret = rng.choice(vals)
    sig = ", ".join(f"i{w} {p}" for p in params)
    before = f"define i{w} @f({sig}) {{\n" + "\n".join(lines) + f"\n  ret i{w} {ret}\n}}\n"

    # A dedicated shape for the undeclared-`noundef` class, because the generic mutations below do
    # NOT reach it: that class needs a source whose result is INDEPENDENT of a parameter and a target
    # that makes it depend on one through a self-combining expression (`ret 0` -> `xor %p, %p`, sound
    # only if the argument cannot be `undef`). Sources here use their parameters everywhere, so
    # duplicating a use just yields a sound target. Verified by disabling the undef guard: without
    # this branch the campaign surfaced ZERO false proofs, i.e. it could not see the very bug it was
    # written to catch.
    if rng.random() < 0.35:
        p_indep = rng.choice(params)
        self_op = rng.choice(["xor", "sub", "and", "or"])
        const = rng.randint(-3, 3)
        before = (f"define i{w} @f({sig}) {{\n  ret i{w} {const}\n}}\n")
        after = (f"define i{w} @f({sig}) {{\n"
                 f"  %u = {self_op} i{w} {p_indep}, {p_indep}\n  ret i{w} %u\n}}\n")
        return before, after

    tgt, tret, extra = list(lines), ret, []
    for _ in range(rng.randint(1, 2)):
        kind = rng.choice(["dup_param", "add_flag", "drop_flag", "swap", "sub_operand",
                           "ret_other", "ret_const", "freeze_ret"])
        i = rng.randrange(len(tgt)) if tgt else 0
        if kind == "dup_param" and tgt:
            # the undef class: make the target read a parameter twice where the source may not have
            p = rng.choice(params)
            head, _, tail = tgt[i].partition(" i%d " % w)
            if tail:
                tgt[i] = f"{head} i{w} {p}, {p}"
        elif kind == "add_flag" and tgt:
            op = tgt[i].split("=")[1].split()[0]
            if op in _FLAGS and not any(f" {f} " in tgt[i] for f in _FLAGS[op]):
                tgt[i] = tgt[i].replace(f"= {op} ", f"= {op} {rng.choice(_FLAGS[op])} ", 1)
        elif kind == "drop_flag" and tgt:
            for f in ("nsw", "nuw", "exact", "disjoint"):
                if f" {f} " in tgt[i]:
                    tgt[i] = tgt[i].replace(f" {f} ", " ", 1)
                    break
        elif kind == "swap" and tgt:
            head, _, ops = tgt[i].rpartition(f" i{w} ")
            if "," in ops:
                a, b = [x.strip() for x in ops.split(",", 1)]
                tgt[i] = f"{head} i{w} {b}, {a}"
        elif kind == "sub_operand" and tgt:
            head, _, ops = tgt[i].rpartition(f" i{w} ")
            if "," in ops:
                a, b = [x.strip() for x in ops.split(",", 1)]
                tgt[i] = f"{head} i{w} {rng.choice(vals)}, {b}"
        elif kind == "ret_other":
            tret = rng.choice(vals)
        elif kind == "ret_const":
            tret = str(rng.randint(-4, 4))
        elif kind == "freeze_ret":
            extra.append(f"  %fz = freeze i{w} {tret}")
            tret = "%fz"
    after = (f"define i{w} @f({sig}) {{\n" + "\n".join(tgt + extra)
             + f"\n  ret i{w} {tret}\n}}\n")
    return before, after


def _gen_synth_memory(rng, n_insns=6, w=32):
    """A (before, after) pair over POINTER MEMORY whose target is synthesized.

    `mem_state` has only ever been fuzzed with opt-produced targets, and it is one of the WEAKEST
    models in the tree: it compares values and final memory, with no poison refinement, which is why
    it needs a `poison_risk` guard to avoid false refutations at all. So its decision surface deserves
    the same adversarial pressure the scalar path just got -- these are the rewrites a buggy memory
    pass plausibly emits, most of them unsound:

      * drop a store (sound only if a later store to the SAME address overwrites it);
      * reorder two stores (sound only if the addresses cannot alias);
      * forward a load from a DIFFERENT pointer (alias-unsound);
      * change a stored value, or introduce a load the source never performs (a new dereference).
    """
    ptrs, vals, lines, idx = ["%p", "%q"], ["%x", "%y"], [], 0

    def opnd():
        return str(rng.choice([0, 1, -1, rng.randint(-8, 8)])) if rng.random() < 0.3 else rng.choice(vals)

    for _ in range(n_insns):
        r = rng.random()
        if r < 0.4:
            lines.append(f"  store i{w} {opnd()}, ptr {rng.choice(ptrs)}")
        elif r < 0.7:
            v = f"%v{idx}"; idx += 1
            lines.append(f"  {v} = load i{w}, ptr {rng.choice(ptrs)}")
            vals.append(v)
        else:
            v = f"%v{idx}"; idx += 1
            lines.append(f"  {v} = {rng.choice(_BINOPS)} i{w} {opnd()}, {opnd()}")
            vals.append(v)
    ret = rng.choice(vals)
    sig = f"ptr %p, ptr %q, i{w} %x, i{w} %y"
    before = f"define i{w} @f({sig}) {{\n" + "\n".join(lines) + f"\n  ret i{w} {ret}\n}}\n"

    tgt = list(lines)
    stores = [i for i, l in enumerate(tgt) if l.lstrip().startswith("store")]
    loads = [i for i, l in enumerate(tgt) if " = load " in l]
    kind = rng.choice(["drop_store", "reorder", "realias_load", "change_value", "new_load"])
    if kind == "drop_store" and stores:
        del tgt[rng.choice(stores)]
    elif kind == "reorder" and len(stores) >= 2:
        i, j = rng.sample(stores, 2)
        tgt[i], tgt[j] = tgt[j], tgt[i]
    elif kind == "realias_load" and loads:
        i = rng.choice(loads)
        tgt[i] = tgt[i].replace("ptr %p", "ptr %__T").replace("ptr %q", "ptr %p").replace("%__T", "%q")
    elif kind == "change_value" and stores:
        i = rng.choice(stores)
        head, _, rest = tgt[i].partition(f"store i{w} ")
        tgt[i] = f"{head}store i{w} {rng.choice(vals + ['0', '1'])}," + rest.partition(",")[2]
    elif kind == "new_load":
        tgt.append(f"  %nl = load i{w}, ptr {rng.choice(ptrs)}")
    after = f"define i{w} @f({sig}) {{\n" + "\n".join(tgt) + f"\n  ret i{w} {ret}\n}}\n"
    return before, after


def _gen_synth_vector(rng, n_insns=5, w=32, lanes=4):
    """A (before, after) pair over FIXED VECTORS whose target is synthesized.

    Same reasoning as the memory shape: the lane model compares per-lane VALUES with no poison
    refinement, and has only ever seen targets InstCombine chose to emit. These are the rewrites a
    buggy vectoriser plausibly emits -- a permuted shuffle mask, a changed lane, swapped operands, a
    dropped flag, a splat substituted for a general vector."""
    vt = f"<{lanes} x i{w}>"
    vals, lines, idx = ["%x", "%y"], [], 0

    def opnd():
        if rng.random() < 0.25:
            elts = ", ".join(f"i{w} {rng.choice([0, 1, -1, rng.randint(-4, 4)])}" for _ in range(lanes))
            return f"<{elts}>"
        return rng.choice(vals)

    for _ in range(n_insns):
        v = f"%v{idx}"; idx += 1
        # bias away from shifts: a lane-wise variable shift is a poison source, so an all-shift
        # generator would produce only functions the model must decline, and the shape would stop
        # exercising anything.
        op = rng.choice(_BINOPS if rng.random() < 0.35 else
                        ["add", "sub", "mul", "and", "or", "xor"])
        fl = " " + rng.choice(_FLAGS[op]) if op in _FLAGS and rng.random() < 0.35 else ""
        lines.append(f"  {v} = {op}{fl} {vt} {opnd()}, {opnd()}")
        vals.append(v)
    ret = rng.choice(vals)
    before = f"define {vt} @f({vt} %x, {vt} %y) {{\n" + "\n".join(lines) + f"\n  ret {vt} {ret}\n}}\n"

    tgt, tret = list(lines), ret
    kind = rng.choice(["shuffle", "swap", "drop_flag", "add_flag", "splat", "ret_other"])
    if kind == "shuffle":
        mask = ", ".join(f"i32 {rng.randrange(2 * lanes)}" for _ in range(lanes))
        tgt.append(f"  %sh = shufflevector {vt} {tret}, {vt} %y, <{lanes} x i32> <{mask}>")
        tret = "%sh"
    elif kind == "swap" and tgt:
        i = rng.randrange(len(tgt))
        head, _, ops = tgt[i].rpartition(f"{vt} ")
        # Split at ANGLE-BRACKET DEPTH ZERO: a vector literal `<i32 -1, i32 0>` contains commas, and
        # a naive split severs it -- the very mistake the lane model's own operand regex once made.
        depth, cut = 0, -1
        for k, ch in enumerate(ops):
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            elif ch == "," and depth == 0:
                cut = k
                break
        if cut > 0:
            a, b = ops[:cut], ops[cut + 1:]
            tgt[i] = f"{head}{vt} {b.strip()}, {a.strip()}"
    elif kind in ("drop_flag", "add_flag") and tgt:
        i = rng.randrange(len(tgt))
        if kind == "drop_flag":
            for f in ("nsw", "nuw", "exact", "disjoint"):
                if f" {f} " in tgt[i]:
                    tgt[i] = tgt[i].replace(f" {f} ", " ", 1); break
        else:
            op = tgt[i].split("=")[1].split()[0]
            if op in _FLAGS and not any(f" {f} " in tgt[i] for f in _FLAGS[op]):
                tgt[i] = tgt[i].replace(f"= {op} ", f"= {op} {rng.choice(_FLAGS[op])} ", 1)
    elif kind == "splat":
        c = rng.randint(-3, 3)
        tgt.append(f"  %sp = add {vt} {tret}, <" + ", ".join(f"i{w} {c}" for _ in range(lanes)) + ">")
        tret = "%sp"
    elif kind == "ret_other":
        tret = rng.choice(vals)
    after = f"define {vt} @f({vt} %x, {vt} %y) {{\n" + "\n".join(tgt) + f"\n  ret {vt} {tret}\n}}\n"
    return before, after


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0x02704)
    ap.add_argument("--insns", type=int, default=8)
    ap.add_argument("--params", type=int, default=3)
    ap.add_argument("--intrinsics", action="store_true",
                    help="also generate the modeled intrinsic calls (fuzz their encodings vs Alive2)")
    ap.add_argument("--shape", choices=["scalar", "memory", "vector", "cfg", "freeze", "synth",
                             "synth-memory", "synth-vector"],
                    default="scalar",
                    help="scalar (default), pointer memory, fixed vectors, a branch/phi diamond (cfg), "
                         "or freeze (target SYNTHESIZED, not opt output -- see _gen_freeze)")
    ap.add_argument("--passes", help="opt pipeline (default per shape)")
    args = ap.parse_args(argv)

    z3 = shutil.which("z3")
    opt = toolchain.resolve_opt("opt")
    alive = shutil.which("alive-tv")
    if not (z3 and opt and alive):
        print("cv-fuzz-differential: needs z3 + opt(18) + alive-tv", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    passes = args.passes or {"memory": "instcombine,gvn,dse",
                             "cfg": "instcombine,simplifycfg,sccp"}.get(args.shape, "instcombine")
    gen = {"scalar": lambda: _gen(rng, args.params, args.insns, intrinsics=args.intrinsics),
           "memory": lambda: _gen_memory(rng, args.insns),
           "vector": lambda: _gen_vector(rng, args.insns),
           "cfg": lambda: _gen_cfg(rng, args.insns),
           "freeze": lambda: _gen_freeze(rng, args.insns),
           "synth": lambda: _gen_synth(rng, args.insns),
           "synth-memory": lambda: _gen_synth_memory(rng, args.insns),
           "synth-vector": lambda: _gen_synth_vector(rng, args.insns)}[args.shape]
    pair, alive_v, disagreements, opt_fail = Counter(), Counter(), [], 0
    vacuous = 0
    for i in range(args.count):
        if args.shape.startswith("synth") or args.shape == "freeze":   # target SYNTHESIZED
            before, after = gen()
        else:
            before = gen()
            after = si.run_passes(before, passes, opt)
            if after is None:
                opt_fail += 1
                continue
        verdict = validate_transform_ex(z3, before, after, "f")
        o2t = verdict["status"]
        pair[o2t] += 1
        # A random generator readily emits functions that are UB on every input, and refinement holds
        # vacuously there -- a valid but information-free `proved` that Alive2 also accepts, so it can
        # never show up as a disagreement. Count them so the proved total is not read as reach.
        vacuous += 1 if verdict.get("vacuous") is True else 0
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
          f"O2T {dict(pair)} (of the proved, {vacuous} vacuous -- source UB/poison everywhere); "
          f"Alive2 {dict(alive_v)}")
    print(f"DISAGREEMENTS: {len(disagreements)}")
    for kind, i, b, a in disagreements[:10]:
        print(f"\n!! {kind} at #{i}\n-- before --\n{b}-- after --\n{a}")
    return 1 if disagreements else 0


if __name__ == "__main__":
    raise SystemExit(main())
