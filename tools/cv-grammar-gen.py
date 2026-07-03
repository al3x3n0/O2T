#!/usr/bin/env python3
"""Grammar-based random LLVM IR generator (a Csmith-for-IR front-end).

The config/KLEE generator emits IR from a fixed catalog of shape templates. This
generator instead builds *random valid programs*: it grows a typed pool of SSA
values and, instruction by instruction, draws operands from that pool (so values
are reused -> the result is a DAG with real CSE/GVN material, not a tree), mixing
a broad opcode set across i1..i64 with poison flags (nsw/nuw/exact/disjoint),
casts, memory ops through pointer params, and rich/special constants
(signmin/max, powers of two, all-ones). Everything lives in one basic block, so
the IR is valid by construction (dominance is trivial; no phi/CFG legality to
get wrong).

Each module is deterministic in its `--seed`. With `--validate` it runs the
Csmith loop locally: `llvm-as` the module, `opt` it, `llvm-as` the result, and
report any module that fails to verify (a generator gap) or makes `opt` crash or
emit invalid IR (an opt finding). Needs `opt`/`llvm-as` only for `--validate`
(set CV_LLVM_BIN, default /opt/homebrew/opt/llvm@18/bin, then PATH).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

INT_TYPES = ["i1", "i8", "i16", "i32", "i64"]
WIDTH = {"i1": 1, "i8": 8, "i16": 16, "i32": 32, "i64": 64}

# (opcode, flag-pool) -- flags applied randomly. or/and/xor get disjoint only for or.
BINOPS = [
    ("add", ("nsw", "nuw")), ("sub", ("nsw", "nuw")), ("mul", ("nsw", "nuw")),
    ("shl", ("nsw", "nuw")), ("and", ()), ("or", ("disjoint",)), ("xor", ()),
    ("lshr", ("exact",)), ("ashr", ("exact",)),
    ("udiv", ("exact",)), ("sdiv", ("exact",)), ("urem", ()), ("srem", ()),
]
DIV_OPS = {"udiv", "sdiv", "urem", "srem"}
ICMP_PREDS = ["eq", "ne", "slt", "sgt", "sle", "sge", "ult", "ugt", "ule", "uge"]

# Floating point. Fast-math flags license the optimizer to reassociate/refine (nnan/ninf/nsz/arcp/
# contract/reassoc), which is exactly the fold surface to fuzz -- but it also makes O0 and O3
# LEGALLY disagree on the numeric result, so FP is emitted only in the NON-executed `--validate`
# path (opt applies the folds; llvm-as must still accept the output) and NEVER in the executable
# `--main` differential, whose soundness rests on every optimizer preserving the exact result.
FP_TYPES = ["float", "double"]
FP_BINOPS = ["fadd", "fsub", "fmul", "fdiv"]
FMF_FLAGS = ["nnan", "ninf", "nsz", "arcp", "contract", "reassoc"]
FCMP_PREDS = ["oeq", "ogt", "oge", "olt", "ole", "one", "ord",
              "ueq", "ugt", "uge", "ult", "ule", "une", "uno"]
FP_CONSTS = ["0.0", "-0.0", "1.0", "-1.0", "2.0", "-2.0", "0.5", "4.0",
             "0x7FF0000000000000", "0xFFF0000000000000", "0x7FF8000000000000"]  # +inf, -inf, nan

# Integer vectors. Unlike FP, integer vector arithmetic is deterministic and (on the safe op subset)
# UB-free, so vectors DO run in the executable `--main` differential -- reaching the vector-InstCombine
# / VectorCombine / shuffle-fold families for real miscompile detection. Vectors feed the scalar
# return via `extractelement`, so the @main driver needs no changes.
VEC_TYPES = ["<2 x i32>", "<4 x i32>", "<8 x i32>", "<4 x i8>",
             "<8 x i16>", "<2 x i64>", "<4 x i16>", "<16 x i8>"]
VEC_SAFE_OPS = ["add", "sub", "mul", "and", "or", "xor"]                 # UB-free element-wise
VEC_ALL_OPS = VEC_SAFE_OPS + ["shl", "lshr", "ashr", "udiv", "sdiv", "urem", "srem"]
# Floating-point vectors: validate-only, like scalar FP (element-wise fast-math reassociation is the
# fold surface, but it is licensed nondeterminism, so these never enter the executable --main path).
VFP_TYPES = ["<2 x float>", "<4 x float>", "<8 x float>", "<2 x double>", "<4 x double>"]
# Scalable vectors (SVE/RVV). Validate-only: the length is unknown at compile time, so they cannot
# execute on a non-scalable host (incl. Apple Silicon / x86), and their constants are limited to
# `zeroinitializer` + splats and their shuffles to a zeroinitializer (splat) mask.
SVEC_TYPES = ["<vscale x 2 x i32>", "<vscale x 4 x i32>", "<vscale x 2 x i64>",
              "<vscale x 8 x i16>", "<vscale x 16 x i8>", "<vscale x 4 x i16>"]
_VEC_RE = re.compile(r"<(?:vscale x )?(\d+) x (i\d+|float|double)>")


def vparse(vt: str) -> tuple[int, str]:
    m = _VEC_RE.fullmatch(vt)
    return (int(m.group(1)), m.group(2))


def is_scalable(vt: str) -> bool:
    return vt.startswith("<vscale")

# Integer intrinsics: (name, arity, has_poison_flag, valid_types). These reach InstCombine's large
# intrinsic-fold family (min/max/abs, bit-counting, byte/bit reversal) that plain binops cannot.
# The poison flag -- is_int_min_poison for `abs`, is_zero_poison for `ctlz`/`cttz` -- is always
# emitted `false`, so every result is fully defined and safe for the executable UB-free driver.
# `bswap` requires a bit width that is a multiple of 16 (so no i8).
INT_INTRINSICS = [
    ("smax", 2, False, ("i8", "i16", "i32", "i64")),
    ("smin", 2, False, ("i8", "i16", "i32", "i64")),
    ("umax", 2, False, ("i8", "i16", "i32", "i64")),
    ("umin", 2, False, ("i8", "i16", "i32", "i64")),
    ("abs", 1, True, ("i8", "i16", "i32", "i64")),
    ("ctlz", 1, True, ("i8", "i16", "i32", "i64")),
    ("cttz", 1, True, ("i8", "i16", "i32", "i64")),
    ("ctpop", 1, False, ("i8", "i16", "i32", "i64")),
    ("bitreverse", 1, False, ("i8", "i16", "i32", "i64")),
    ("bswap", 1, False, ("i16", "i32", "i64")),
    # Saturating arithmetic and funnel shifts: 2-/3-operand, deterministic and UB-free (saturation
    # clamps; funnel-shift amount is taken modulo the width), so they run in --main too. They reach
    # InstCombine's saturating/funnel-shift fold family that plain binops cannot.
    ("sadd.sat", 2, False, ("i8", "i16", "i32", "i64")),
    ("uadd.sat", 2, False, ("i8", "i16", "i32", "i64")),
    ("ssub.sat", 2, False, ("i8", "i16", "i32", "i64")),
    ("usub.sat", 2, False, ("i8", "i16", "i32", "i64")),
    ("fshl", 3, False, ("i8", "i16", "i32", "i64")),
    ("fshr", 3, False, ("i8", "i16", "i32", "i64")),
]


def const_for(t: str, rng: random.Random) -> str:
    if t.startswith("<vscale"):            # scalable: length unknown -> zeroinitializer only
        return "zeroinitializer"
    if t.startswith("<"):                  # fixed vector: element-wise constant
        n, elem = vparse(t)
        return "<" + ", ".join(f"{elem} {const_for(elem, rng)}" for _ in range(n)) + ">"
    if t in FP_TYPES:
        return rng.choice(FP_CONSTS)      # LLVM accepts the double-hex form for `float` too
    if t == "i1":
        return rng.choice(["0", "1"])
    w = WIDTH[t]
    specials = [0, 1, -1, 2, -2,
                (1 << (w - 1)) - 1,      # signed max
                -(1 << (w - 1)),         # signed min
                1 << (w - 1),            # sign bit (valid: <= 2^w-1)
                (1 << w) - 1 if w <= 16 else (1 << (w - 1)) - 1,  # all-ones (small widths)
                1 << rng.randrange(w),   # random power of two
                rng.randint(-8, 8)]
    return str(rng.choice(specials))


class Generator:
    def __init__(self, seed: int, n_instructions: int, cfg: bool = False,
                 cfg_regions: int = 3, cfg_depth: int = 2, ub_free: bool = False,
                 emit_main: bool = False) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.n = n_instructions
        self.cfg = cfg
        self.cfg_regions = cfg_regions
        self.cfg_depth = cfg_depth
        # A self-driving `main` must be executable, so it forces UB-free codegen.
        self.emit_main = emit_main
        self.ub_free = ub_free or emit_main
        # Scalar int, scalar FP, integer-vector, FP-vector, and scalable-vector (--validate) pools.
        self.pool: dict[str, list[str]] = {
            t: [] for t in INT_TYPES + FP_TYPES + VEC_TYPES + VFP_TYPES + SVEC_TYPES}
        self.ptrs: list[str] = []
        self.decls: set[str] = set()          # module-scope intrinsic declarations used by @test
        self.lines: list[str] = []
        self.counter = 0
        self.block_counter = 0
        self.cur_block = "entry"
        self.arg_specs: list[tuple[str, str]] = []

    def fresh(self) -> str:
        name = f"%v{self.counter}"
        self.counter += 1
        return name

    def fresh_label(self) -> str:
        lbl = f"bb{self.block_counter}"
        self.block_counter += 1
        return lbl

    def start_block(self, label: str) -> None:
        self.lines.append("")
        self.lines.append(f"{label}:")
        self.cur_block = label

    def value_of(self, t: str) -> str:
        """A pool value of type t (reuse) or a constant."""
        if self.pool[t] and self.rng.random() < 0.75:
            return self.rng.choice(self.pool[t])
        return const_for(t, self.rng)

    def types_with_values(self) -> list[str]:
        return [t for t in INT_TYPES if self.pool[t]]

    def add(self, t: str, name: str) -> None:
        self.pool[t].append(name)

    def emit_binop(self) -> None:
        op, flagpool = self.rng.choice(BINOPS)
        # i1 arithmetic is mostly degenerate; bias toward wider types.
        t = self.rng.choice(["i8", "i16", "i32", "i64"])
        lhs = self.value_of(t)
        if op in DIV_OPS:
            if self.ub_free:
                # Divisor in [1, 7]: avoids div-by-zero and the sdiv INT_MIN/-1
                # overflow, so the result is fully defined.
                masked = self.fresh()
                self.lines.append(f"  {masked} = and {t} {self.value_of(t)}, 7")
                guard = self.fresh()
                self.lines.append(f"  {guard} = or {t} {masked}, 1")
            else:
                guard = self.fresh()
                self.lines.append(f"  {guard} = or {t} {self.value_of(t)}, 1")
            rhs = guard
        elif op in ("shl", "lshr", "ashr") and self.ub_free:
            # Mask the shift amount below the bit width to avoid poison.
            sm = self.fresh()
            self.lines.append(f"  {sm} = and {t} {self.value_of(t)}, {WIDTH[t] - 1}")
            rhs = sm
        else:
            rhs = self.value_of(t)
        flags = ("" if self.ub_free else
                 "".join(f" {f}" for f in flagpool if self.rng.random() < 0.4))
        res = self.fresh()
        self.lines.append(f"  {res} = {op}{flags} {t} {lhs}, {rhs}")
        self.add(t, res)

    def emit_icmp(self) -> None:
        t = self.rng.choice(self.types_with_values() or ["i32"])
        pred = self.rng.choice(ICMP_PREDS)
        res = self.fresh()
        self.lines.append(
            f"  {res} = icmp {pred} {t} {self.value_of(t)}, {self.value_of(t)}")
        self.add("i1", res)

    def emit_select(self) -> None:
        if not self.pool["i1"]:
            self.emit_icmp()
        cond = self.rng.choice(self.pool["i1"])
        t = self.rng.choice(self.types_with_values() or ["i32"])
        res = self.fresh()
        self.lines.append(
            f"  {res} = select i1 {cond}, {t} {self.value_of(t)}, {t} {self.value_of(t)}")
        self.add(t, res)

    def emit_cast(self) -> None:
        avail = self.types_with_values()
        if not avail:
            return self.emit_binop()
        src = self.rng.choice(avail)
        targets = [t for t in INT_TYPES if t != src]
        dst = self.rng.choice(targets)
        val = self.rng.choice(self.pool[src])
        if WIDTH[dst] < WIDTH[src]:
            op = "trunc"
        else:
            op = self.rng.choice(["zext", "sext"])
        res = self.fresh()
        self.lines.append(f"  {res} = {op} {src} {val} to {dst}")
        self.add(dst, res)

    def emit_load(self) -> None:
        if not self.ptrs:
            return self.emit_binop()
        t = self.rng.choice(INT_TYPES)
        res = self.fresh()
        self.lines.append(
            f"  {res} = load {t}, ptr {self.rng.choice(self.ptrs)}, align 1")
        self.add(t, res)

    def emit_store(self) -> None:
        if not self.ptrs:
            return self.emit_binop()
        t = self.rng.choice(self.types_with_values() or ["i32"])
        self.lines.append(
            f"  store {t} {self.value_of(t)}, ptr {self.rng.choice(self.ptrs)}, align 1")

    def emit_gep(self) -> None:
        if not self.ptrs:
            return self.emit_binop()
        res = self.fresh()
        self.lines.append(
            f"  {res} = getelementptr i8, ptr {self.rng.choice(self.ptrs)}, "
            f"i64 {self.value_of('i64')}")
        self.ptrs.append(res)

    def emit_intrinsic(self) -> None:
        name, arity, has_flag, valid = self.rng.choice(INT_INTRINSICS)
        pooled = [t for t in valid if self.pool[t]]        # prefer a type with real values to reuse
        t = self.rng.choice(pooled or list(valid))
        args = ", ".join(f"{t} {self.value_of(t)}" for _ in range(arity))
        if has_flag:
            args += ", i1 false"                            # is_*_poison = false -> fully defined
        sig = ", ".join([t] * arity + (["i1"] if has_flag else []))
        self.decls.add(f"declare {t} @llvm.{name}.{t}({sig})")
        res = self.fresh()
        self.lines.append(f"  {res} = call {t} @llvm.{name}.{t}({args})")
        self.add(t, res)

    def _fmf(self) -> str:
        """A random fast-math flag string. Never in the executable path (would license O0/O3 to
        disagree); in --validate mode ~1/6 of the time the `fast` umbrella, else a random subset."""
        if self.ub_free:
            return ""
        if self.rng.random() < 0.16:
            return " fast"
        return "".join(f" {f}" for f in FMF_FLAGS if self.rng.random() < 0.3)

    def emit_fbinop(self) -> None:
        op = self.rng.choice(FP_BINOPS)
        t = self.rng.choice(FP_TYPES)
        res = self.fresh()
        self.lines.append(f"  {res} = {op}{self._fmf()} {t} {self.value_of(t)}, {self.value_of(t)}")
        self.add(t, res)

    def emit_fneg(self) -> None:
        t = self.rng.choice(FP_TYPES)
        res = self.fresh()
        self.lines.append(f"  {res} = fneg{self._fmf()} {t} {self.value_of(t)}")
        self.add(t, res)

    def emit_fcmp(self) -> None:
        t = self.rng.choice(FP_TYPES)
        res = self.fresh()
        self.lines.append(f"  {res} = fcmp{self._fmf()} {self.rng.choice(FCMP_PREDS)} "
                          f"{t} {self.value_of(t)}, {self.value_of(t)}")
        self.add("i1", res)

    def emit_fpcast(self) -> None:
        """Bridge the integer and FP pools (and float<->double), so FP values are reachable and
        feed the returned integer via fptosi/fptoui."""
        op = self.rng.choice(["sitofp", "uitofp", "fptosi", "fptoui", "fpext", "fptrunc"])
        if op in ("sitofp", "uitofp"):
            src, dst = self.rng.choice(["i8", "i16", "i32", "i64"]), self.rng.choice(FP_TYPES)
        elif op in ("fptosi", "fptoui"):
            src, dst = self.rng.choice(FP_TYPES), self.rng.choice(["i8", "i16", "i32", "i64"])
        elif op == "fpext":
            src, dst = "float", "double"
        else:
            src, dst = "double", "float"
        res = self.fresh()
        self.lines.append(f"  {res} = {op} {src} {self.value_of(src)} to {dst}")
        self.add(dst, res)

    # --- integer vectors (executable in --main: deterministic + UB-free on the safe op subset) ----
    def emit_vbinop(self) -> None:
        vt = self.rng.choice(VEC_TYPES)
        op = self.rng.choice(VEC_SAFE_OPS if self.ub_free else VEC_ALL_OPS)
        res = self.fresh()
        self.lines.append(f"  {res} = {op} {vt} {self.value_of(vt)}, {self.value_of(vt)}")
        self.add(vt, res)

    def emit_extractelement(self) -> None:
        # FP vectors are candidates only in --validate; the fallback stays integer so --main is FP-free.
        candidates = VEC_TYPES + (VFP_TYPES if not self.ub_free else [])
        vt = self.rng.choice([v for v in candidates if self.pool[v]] or VEC_TYPES)
        n, elem = vparse(vt)
        res = self.fresh()
        self.lines.append(f"  {res} = extractelement {vt} {self.value_of(vt)}, i32 {self.rng.randrange(n)}")
        self.add(elem, res)                 # scalar lane -> feeds the scalar return (fp lanes via fptosi)

    def emit_vfbinop(self) -> None:
        vt = self.rng.choice(VFP_TYPES)
        op = self.rng.choice(FP_BINOPS)
        res = self.fresh()
        self.lines.append(f"  {res} = {op}{self._fmf()} {vt} {self.value_of(vt)}, {self.value_of(vt)}")
        self.add(vt, res)

    def emit_vfneg(self) -> None:
        vt = self.rng.choice(VFP_TYPES)
        res = self.fresh()
        self.lines.append(f"  {res} = fneg{self._fmf()} {vt} {self.value_of(vt)}")
        self.add(vt, res)

    # --- scalable vectors (SVE/RVV): validate-only, unknown length -----------------------------
    def emit_svbinop(self) -> None:
        vt = self.rng.choice(SVEC_TYPES)
        op = self.rng.choice(VEC_SAFE_OPS)
        res = self.fresh()
        self.lines.append(f"  {res} = {op} {vt} {self.value_of(vt)}, {self.value_of(vt)}")
        self.add(vt, res)

    def emit_ssplat(self) -> None:
        """Scalable splat: insert lane 0, broadcast via the only mask a scalable shuffle allows
        (a zeroinitializer) -- the canonical scalable-vector idiom that InstCombine folds."""
        vt = self.rng.choice(SVEC_TYPES)
        n, elem = vparse(vt)
        tmp = self.fresh()
        self.lines.append(f"  {tmp} = insertelement {vt} poison, {elem} {self.value_of(elem)}, i32 0")
        res = self.fresh()
        self.lines.append(f"  {res} = shufflevector {vt} {tmp}, {vt} poison, "
                          f"<vscale x {n} x i32> zeroinitializer")
        self.add(vt, res)

    def emit_sextract(self) -> None:
        vt = self.rng.choice([v for v in SVEC_TYPES if self.pool[v]] or SVEC_TYPES)
        _, elem = vparse(vt)
        res = self.fresh()
        self.lines.append(f"  {res} = extractelement {vt} {self.value_of(vt)}, i32 0")
        self.add(elem, res)

    def emit_insertelement(self) -> None:
        vt = self.rng.choice(VEC_TYPES)
        n, elem = vparse(vt)
        res = self.fresh()
        self.lines.append(f"  {res} = insertelement {vt} {self.value_of(vt)}, "
                          f"{elem} {self.value_of(elem)}, i32 {self.rng.randrange(n)}")
        self.add(vt, res)

    def emit_shuffle(self) -> None:
        vt = self.rng.choice(VEC_TYPES)
        n, _ = vparse(vt)
        if self.ub_free:
            mask = [str(self.rng.randrange(2 * n)) for _ in range(n)]        # in-range -> UB-free
        else:
            mask = ["poison" if self.rng.random() < 0.2 else str(self.rng.randrange(2 * n))
                    for _ in range(n)]
        mask_str = ", ".join(f"i32 {m}" for m in mask)
        res = self.fresh()
        self.lines.append(f"  {res} = shufflevector {vt} {self.value_of(vt)}, "
                          f"{vt} {self.value_of(vt)}, <{n} x i32> <{mask_str}>")
        self.add(vt, res)

    def emit_splat(self) -> None:
        """The canonical broadcast idiom (insertelement lane 0 + zero-mask shuffle) that
        vector-InstCombine / VectorCombine repeatedly fold -- UB-free."""
        vt = self.rng.choice(VEC_TYPES)
        n, elem = vparse(vt)
        tmp = self.fresh()
        self.lines.append(f"  {tmp} = insertelement {vt} poison, {elem} {self.value_of(elem)}, i32 0")
        res = self.fresh()
        self.lines.append(f"  {res} = shufflevector {vt} {tmp}, {vt} poison, "
                          f"<{n} x i32> <{', '.join(['i32 0'] * n)}>")
        self.add(vt, res)

    def emit(self, text: str) -> None:
        self.lines.append(f"  {text}")

    def emit_dag(self, n: int) -> None:
        emitters = [self.emit_binop] * 5 + \
                   [self.emit_icmp, self.emit_select, self.emit_cast, self.emit_intrinsic] * 2 + \
                   [self.emit_load, self.emit_store, self.emit_gep] + \
                   [self.emit_vbinop] * 2 + \
                   [self.emit_extractelement, self.emit_insertelement, self.emit_shuffle, self.emit_splat]
        if not self.ub_free:
            # Scalar + vector floating point and scalable vectors only in the non-executed --validate
            # path (FP is licensed-nondeterministic; scalable vectors cannot execute on this host).
            emitters += [self.emit_fbinop] * 3 + [self.emit_fneg, self.emit_fcmp, self.emit_fpcast] * 2
            emitters += [self.emit_vfbinop] * 2 + [self.emit_vfneg]
            emitters += [self.emit_svbinop] * 2 + [self.emit_ssplat, self.emit_sextract]
        for _ in range(n):
            self.rng.choice(emitters)()

    def _snapshot(self):
        # Capture BOTH the typed value pool and the pointer pool: a gep result
        # created inside a branch/loop body must not leak into a later region.
        return ({t: list(v) for t, v in self.pool.items()}, list(self.ptrs))

    def _restore(self, snap) -> None:
        self.pool = {t: list(v) for t, v in snap[0].items()}
        self.ptrs = list(snap[1])

    def make_cond(self) -> str:
        if not self.pool["i1"]:
            self.emit_icmp()
        return self.rng.choice(self.pool["i1"])

    # --- Single-entry/single-exit control regions ---------------------------
    # Each region keeps the IR valid by construction: the pre-region pool
    # dominates the region's continuation point, and values created inside a
    # branch only re-enter the pool through phi nodes whose incomings are defined
    # in (and so dominate the terminator of) the matching predecessor.

    def gen_region(self, depth: int) -> None:
        if depth <= 0 or self.rng.random() < 0.45:
            self.emit_dag(self.rng.randint(2, 6))
            return
        self.rng.choice([self._diamond, self._ifthen, self._loop])(depth)

    def _lift_phis(self, a_pool: dict[str, list[str]], a_label: str,
                   b_pool: dict[str, list[str]], b_label: str) -> None:
        common = [t for t in INT_TYPES if a_pool[t] and b_pool[t]]
        self.rng.shuffle(common)
        for t in common[: self.rng.randint(1, 2)]:
            va = self.rng.choice(a_pool[t])
            vb = self.rng.choice(b_pool[t])
            phi = self.fresh()
            self.emit(f"{phi} = phi {t} [ {va}, %{a_label} ], [ {vb}, %{b_label} ]")
            self.add(t, phi)

    def _diamond(self, depth: int) -> None:
        cond = self.make_cond()
        then_l, else_l, merge_l = (self.fresh_label() for _ in range(3))
        self.emit(f"br i1 {cond}, label %{then_l}, label %{else_l}")
        base = self._snapshot()
        self.start_block(then_l)
        self.gen_region(depth - 1)
        then_pool, then_exit = self._snapshot()[0], self.cur_block
        self.emit(f"br label %{merge_l}")
        self._restore(base)
        self.start_block(else_l)
        self.gen_region(depth - 1)
        else_pool, else_exit = self._snapshot()[0], self.cur_block
        self.emit(f"br label %{merge_l}")
        self.start_block(merge_l)
        self._restore(base)  # the diamond entry dominates the merge
        self._lift_phis(then_pool, then_exit, else_pool, else_exit)

    def _ifthen(self, depth: int) -> None:
        entry_block = self.cur_block
        cond = self.make_cond()
        then_l, merge_l = self.fresh_label(), self.fresh_label()
        self.emit(f"br i1 {cond}, label %{then_l}, label %{merge_l}")
        base = self._snapshot()
        self.start_block(then_l)
        self.gen_region(depth - 1)
        then_pool, then_exit = self._snapshot()[0], self.cur_block
        self.emit(f"br label %{merge_l}")
        self.start_block(merge_l)
        self._restore(base)
        # Fall-through incoming comes from the conditional-branch block itself.
        self._lift_phis(then_pool, then_exit, base[0], entry_block)

    def _loop(self, depth: int) -> None:
        pre = self.cur_block
        header, body, latch, exit_l = (self.fresh_label() for _ in range(4))
        acc_t = self.rng.choice(self.types_with_values() or ["i32"])
        acc_seed = self.value_of(acc_t)
        limit = self.rng.randint(2, 8)
        i, i_next = self.fresh(), self.fresh()
        acc, acc_next = self.fresh(), self.fresh()

        self.emit(f"br label %{header}")
        self.start_block(header)
        # i_next/acc_next are forward refs defined in the latch -- legal for phi.
        self.emit(f"{i} = phi i32 [ 0, %{pre} ], [ {i_next}, %{latch} ]")
        self.emit(f"{acc} = phi {acc_t} [ {acc_seed}, %{pre} ], [ {acc_next}, %{latch} ]")
        cnd = self.fresh()
        self.emit(f"{cnd} = icmp slt i32 {i}, {limit}")
        self.emit(f"br i1 {cnd}, label %{body}, label %{exit_l}")

        base = self._snapshot()
        self.start_block(body)
        self.add("i32", i)
        self.add(acc_t, acc)
        self.gen_region(depth - 1)
        body_val = self.value_of(acc_t)
        self.emit(f"br label %{latch}")

        self.start_block(latch)
        self.emit(f"{i_next} = add i32 {i}, 1")
        self.emit(f"{acc_next} = add {acc_t} {acc}, {body_val}")
        self.emit(f"br label %{header}")

        self.start_block(exit_l)
        self._restore(base)  # pre-loop pool dominates the exit
        self.add("i32", i)   # induction + accumulator (header) dominate the exit
        self.add(acc_t, acc)

    def module(self) -> str:
        # Signature: 2-4 int args of random widths + 0-2 pointer params.
        n_int = self.rng.randint(2, 4)
        sig: list[str] = []
        ai = 0
        for _ in range(n_int):
            t = self.rng.choice(["i8", "i16", "i32", "i64"])
            name = f"%a{ai}"
            ai += 1
            sig.append(f"{t} {name}")
            self.arg_specs.append((name, t))
            self.add(t, name)
        # Pointers (and the memory ops they enable) are excluded in UB-free mode:
        # an executable driver can't guarantee in-bounds accesses.
        n_ptr = 0 if self.ub_free else self.rng.randint(0, 2)
        noalias = "noalias " if self.rng.random() < 0.5 else ""
        for j in range(n_ptr):
            name = f"%p{j}"
            sig.append(f"ptr {noalias}{name}")
            self.ptrs.append(name)

        if self.cfg:
            for _ in range(self.cfg_regions):
                self.gen_region(self.cfg_depth)
        else:
            self.emit_dag(self.n)

        ret_t = self.rng.choice(self.types_with_values() or ["i32"])
        self.emit(f"ret {ret_t} {self.value_of(ret_t)}")

        out = ["; ModuleID = 'o2t-grammar'",
               "source_filename = \"o2t-grammar.ll\"",
               f"; grammar_seed={self.seed} instructions={self.n} cfg={int(self.cfg)}"
               f" ub_free={int(self.ub_free)} main={int(self.emit_main)}",
               ""]
        out.extend(sorted(self.decls))              # module-scope intrinsic declarations
        if self.decls:
            out.append("")
        out.append(f"define {ret_t} @test({', '.join(sig)}) {{")
        out.append("entry:")
        out.extend(self.lines)
        out.append("}")
        if self.emit_main:
            out.append("")
            out.append("declare i32 @putchar(i32)")
            out.append("")
            out.extend(self._main_driver(ret_t))
        return "\n".join(out) + "\n"

    def _main_driver(self, ret_t: str) -> list[str]:
        # A deterministic @main that calls @test over a range of derived inputs and folds the
        # results into an observable. Two observables are emitted: the 8-bit process exit code
        # (`ret i32 %m.fin`, for crash/quick checks) AND -- the strong signal -- the FULL 32-bit
        # accumulator streamed to stdout. The accumulator is an ORDER-SENSITIVE rolling hash
        # (`acc = acc*1000003 + r`), not a commutative sum, so a miscompile that changes one call's
        # result cannot cancel against another, and the 32-bit stdout digest catches value
        # divergences the 8-bit exit code aliases away. Still UB-free and deterministic.
        out = ["define i32 @main() {", "entry:", "  br label %m.loop", "",
               "m.loop:",
               "  %m.k = phi i32 [ 0, %entry ], [ %m.knext, %m.loop ]",
               "  %m.acc = phi i32 [ 0, %entry ], [ %m.accnext, %m.loop ]"]
        call_args = []
        for idx, (_, t) in enumerate(self.arg_specs):
            mix = f"%m.mix{idx}"
            out.append(f"  {mix} = add i32 %m.k, {idx * 7 + 1}")
            if t == "i32":
                val = mix
            elif t == "i64":
                val = f"%m.a{idx}"
                out.append(f"  {val} = sext i32 {mix} to i64")
            else:  # i8 / i16
                val = f"%m.a{idx}"
                out.append(f"  {val} = trunc i32 {mix} to {t}")
            call_args.append(f"{t} {val}")
        out.append(f"  %m.r = call {ret_t} @test({', '.join(call_args)})")
        if ret_t == "i32":
            r32 = "%m.r"
        elif ret_t == "i64":
            r32 = "%m.r32"
            out.append(f"  {r32} = trunc i64 %m.r to i32")
        else:  # i1 / i8 / i16
            r32 = "%m.r32"
            out.append(f"  {r32} = zext {ret_t} %m.r to i32")
        out.append("  %m.mul = mul i32 %m.acc, 1000003")     # order-sensitive rolling hash
        out.append(f"  %m.accnext = add i32 %m.mul, {r32}")
        out.append("  %m.knext = add i32 %m.k, 1")
        out.append("  %m.cont = icmp slt i32 %m.knext, 13")
        out.append("  br i1 %m.cont, label %m.loop, label %m.done")
        out.append("")
        out.append("m.done:")
        out.append("  %m.h1 = lshr i32 %m.acc, 8")
        out.append("  %m.x1 = xor i32 %m.acc, %m.h1")
        out.append("  %m.h2 = lshr i32 %m.x1, 16")
        out.append("  %m.x2 = xor i32 %m.x1, %m.h2")
        out.append("  %m.fin = and i32 %m.x2, 255")
        # Strong observable: stream the full 32-bit accumulator to stdout (little-endian bytes) so a
        # value miscompile that collides in the 8-bit exit fold is still caught by the stdout digest.
        out.append("  %m.o0 = and i32 %m.acc, 255")
        out.append("  %m.w0 = call i32 @putchar(i32 %m.o0)")
        out.append("  %m.q1 = lshr i32 %m.acc, 8")
        out.append("  %m.o1 = and i32 %m.q1, 255")
        out.append("  %m.w1 = call i32 @putchar(i32 %m.o1)")
        out.append("  %m.q2 = lshr i32 %m.acc, 16")
        out.append("  %m.o2 = and i32 %m.q2, 255")
        out.append("  %m.w2 = call i32 @putchar(i32 %m.o2)")
        out.append("  %m.o3 = lshr i32 %m.acc, 24")
        out.append("  %m.w3 = call i32 @putchar(i32 %m.o3)")
        out.append("  ret i32 %m.fin")
        out.append("}")
        return out


def resolve_tool(explicit: str | None, name: str) -> str:
    if explicit:
        return explicit
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    return str(base / name) if (base / name).exists() else name


def first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=1,
                        help="emit modules for seeds seed..seed+count-1")
    parser.add_argument("--instructions", type=int, default=30)
    parser.add_argument("--cfg", action="store_true",
                        help="generate control flow (branches/phi/loops), not just one block")
    parser.add_argument("--cfg-regions", type=int, default=3,
                        help="number of top-level control regions when --cfg")
    parser.add_argument("--cfg-depth", type=int, default=2,
                        help="max nesting depth of control regions when --cfg")
    parser.add_argument("--ub-free", action="store_true",
                        help="emit defined behavior only (no poison flags, safe div/shift, no memory)")
    parser.add_argument("--main", action="store_true",
                        help="append a deterministic @main driver (implies --ub-free) for execution")
    parser.add_argument("--out", type=Path, help="single-module output (count==1)")
    parser.add_argument("--out-dir", type=Path, help="directory for multiple modules")
    parser.add_argument("--validate", action="store_true",
                        help="run llvm-as/opt on each module and report findings")
    parser.add_argument("--passes", default="default<O2>")
    parser.add_argument("--opt")
    parser.add_argument("--llvm-as", dest="llvm_as")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--minimize", action="store_true",
                        help="shrink each opt finding with cv-reduce-ir.py --opt-invalid")
    parser.add_argument("--reducer", type=Path,
                        default=Path(__file__).resolve().parent / "cv-reduce-ir.py")
    parser.add_argument("--llvm-reduce", dest="llvm_reduce",
                        help="llvm-reduce path forwarded to the reducer")
    args = parser.parse_args()

    if args.minimize and not args.out_dir:
        print("error: --minimize requires --out-dir for the witness outputs",
              file=sys.stderr)
        return 2

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    opt = resolve_tool(args.opt, "opt")
    llvm_as = resolve_tool(args.llvm_as, "llvm-as")

    findings = []
    gen_invalid = 0
    generated = 0

    import tempfile
    with tempfile.TemporaryDirectory(prefix="cv-grammar-") as tmp:
        tmpd = Path(tmp)
        for i in range(args.count):
            seed = args.seed + i
            text = Generator(seed, args.instructions, cfg=args.cfg,
                             cfg_regions=args.cfg_regions,
                             cfg_depth=args.cfg_depth, ub_free=args.ub_free,
                             emit_main=args.main).module()
            generated += 1

            if args.out and args.count == 1 and not args.out_dir:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text)
            if args.out_dir:
                (args.out_dir / f"grammar{seed:05d}.ll").write_text(text)
            if not args.validate and not args.out and not args.out_dir:
                sys.stdout.write(text)

            if args.validate:
                ll = tmpd / "m.ll"
                ll.write_text(text)
                as_in = subprocess.run([llvm_as, str(ll), "-o", os.devnull],
                                       capture_output=True, text=True)
                if as_in.returncode != 0:
                    gen_invalid += 1
                    findings.append({"seed": seed, "kind": "generator-invalid",
                                     "reason": first_line(as_in.stderr)})
                    continue
                out = tmpd / "o.ll"
                op = subprocess.run([opt, "-S", "-passes=" + args.passes,
                                     str(ll), "-o", str(out)],
                                    capture_output=True, text=True)
                if op.returncode != 0:
                    findings.append({"seed": seed, "kind": "opt-crash",
                                     "reason": first_line(op.stderr), "_text": text})
                    continue
                as_out = subprocess.run([llvm_as, str(out), "-o", os.devnull],
                                        capture_output=True, text=True)
                if as_out.returncode != 0:
                    findings.append({"seed": seed, "kind": "opt-invalid-output",
                                     "reason": first_line(as_out.stderr), "_text": text})

    minimized = 0
    if args.minimize:
        fdir = args.out_dir / "findings"
        mdir = args.out_dir / "minimized"
        fdir.mkdir(parents=True, exist_ok=True)
        mdir.mkdir(parents=True, exist_ok=True)
        for rec in findings:
            if rec["kind"] == "generator-invalid":
                continue
            fpath = fdir / f"grammar{rec['seed']:05d}.ll"
            mpath = mdir / f"grammar{rec['seed']:05d}.ll"
            fpath.write_text(rec["_text"])
            cmd = [sys.executable, str(args.reducer), "--input", str(fpath),
                   "--out", str(mpath), "--opt-invalid", "--passes", args.passes,
                   "--opt", opt, "--llvm-as", llvm_as]
            if args.llvm_reduce:
                cmd += ["--llvm-reduce", args.llvm_reduce]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            rec["finding_path"] = str(fpath)
            rec["minimized_path"] = str(mpath) if proc.returncode == 0 else None
            rec["minimize_rc"] = proc.returncode
            if proc.returncode == 0:
                minimized += 1

    for rec in findings:
        rec.pop("_text", None)

    if args.report:
        args.report.write_text(json.dumps(
            {"generated": generated, "generator_invalid": gen_invalid,
             "opt_findings": [f for f in findings if f["kind"] != "generator-invalid"],
             "minimized": minimized,
             "findings": findings}, indent=2) + "\n")

    if args.validate:
        opt_findings = sum(1 for f in findings if f["kind"] != "generator-invalid")
        print(f"grammar: {generated} generated, {gen_invalid} generator-invalid, "
              f"{opt_findings} opt finding(s)", file=sys.stderr)
        # generator-invalid is our bug; opt findings are the interesting ones.
        return 1 if gen_invalid else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
