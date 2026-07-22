#!/usr/bin/env python3
"""Closed-loop translation validation for InstCombine: prove the REAL `opt -passes=instcombine` output.

Extends the real-opt closed loop (indvars / simplifycfg / dse) to scalar peephole combining. It is
a small Alive2-style translation validator: a single-basic-block integer function is translated to
an SMT term for its returned value as a function of the parameters, the actual `opt -passes=
instcombine` is run, the optimized function is translated the same way, and the two return terms
are proved EQUAL for all inputs (QF_BV). So the proof is about the instructions InstCombine really
emitted -- a corrupted fold (e.g. `add`->`sub`) is refuted with a concrete input witness.

Supported (else the function is soundly declined as `unsupported`, never falsely proved): integer
add/sub/mul/and/or/xor, shl/lshr/ashr, udiv/sdiv/urem/srem, icmp (-> i1), select, zext/sext/trunc,
constants, and a single `ret`. Every value is modeled as a bitvector of its own width.

The obligation is Alive2-style REFINEMENT, not raw value-equality: alongside each value we
carry a `poison` term (true when the value is poison) and a `ub` term (true when computing it is
undefined behaviour), and prove the optimized function refines the source --
``src_ub  OR  (NOT src_poison) ==> (NOT tgt_ub AND NOT tgt_poison AND src == tgt)``. This makes
the validator catch the poison/UB-introducing miscompiles raw equality misses: a fold that ADDS an
unjustified `nsw`/`nuw`/`exact`/`disjoint` flag, an oversize plain shift, or a freshly introduced
div/rem-by-zero (or `INT_MIN/-1`) is refuted with a witness, while a fold that only DROPS a flag or
removes UB still proves (it is a sound refinement).
"""

from __future__ import annotations

import re
import subprocess

from o2t.formal_ir import VALID_FLAGS, flag_poison_smt, smt_and, smt_or

_BIN = {"add": "bvadd", "sub": "bvsub", "mul": "bvmul", "and": "bvand", "or": "bvor",
        "xor": "bvxor", "shl": "bvshl", "lshr": "bvlshr", "ashr": "bvashr",
        "udiv": "bvudiv", "sdiv": "bvsdiv", "urem": "bvurem", "srem": "bvsrem"}
_ICMP = {"eq": "(= {a} {b})", "ne": "(distinct {a} {b})",
         "ult": "(bvult {a} {b})", "ule": "(bvule {a} {b})",
         "ugt": "(bvugt {a} {b})", "uge": "(bvuge {a} {b})",
         "slt": "(bvslt {a} {b})", "sle": "(bvsle {a} {b})",
         "sgt": "(bvsgt {a} {b})", "sge": "(bvsge {a} {b})"}


class Unsupported(Exception):
    pass


def _const(value, width):
    return f"(_ bv{value % (1 << width)} {width})"


def _function_body(ll_text, func):
    m = re.search(r"define\b[^@]*@" + re.escape(func) + r"\s*\([^)]*\)[^{]*\{", ll_text)
    if not m:
        return None
    depth, j = 1, m.end()
    while j < len(ll_text) and depth:
        depth += {"{": 1, "}": -1}.get(ll_text[j], 0)
        j += 1
    return ll_text[m.end():j - 1]


def _params(ll_text, func):
    """Parameter name -> width, from the signature (declared as bitvectors)."""
    m = re.search(r"@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
    out = {}
    if m:
        for part in m.group(1).split(","):
            pm = re.search(r"i(\d+)\s+(%[\w.]+)", part.strip())
            if pm:
                out[pm.group(2)] = int(pm.group(1))
    return out


def _operand(tok, width, env):
    """An SSA operand or integer literal -> (term, width, poison, ub).

    Parameters and constants are defined inputs (poison/ub = "false"); derived SSA values carry the
    poison/ub terms accumulated by `_instruction`."""
    tok = tok.strip()
    if tok in env:
        return env[tok]
    if re.fullmatch(r"-?\d+", tok):
        return _const(int(tok), width), width, "false", "false"
    if tok in ("true", "false"):
        return _const(1 if tok == "true" else 0, 1), 1, "false", "false"
    raise Unsupported(f"operand {tok!r}")


def translate(ll_text, func):
    """Translate a single-BB integer function to (params, ret_term, ret_width, ret_poison, ret_ub).
    Raises Unsupported on any unmodeled instruction/shape (so it is declined, not mis-proved)."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    params = _params(ll_text, func)
    env = {name: (name, w, "false", "false") for name, w in params.items()}
    labels = re.findall(r"^\s*[\w.]+:", body, re.M)
    if len(labels) > 1:
        raise Unsupported("multi-block function")
    ret_term = ret_width = None
    ret_poison = ret_ub = "false"
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or re.fullmatch(r"[\w.]+:", line):
            continue
        rm = re.fullmatch(r"ret\s+i(\d+)\s+(\S+)", line)
        if rm:
            w = int(rm.group(1))
            ret_term, _, ret_poison, ret_ub = _operand(rm.group(2), w, env)
            ret_width = w
            break
        if line == "ret void":
            raise Unsupported("void return")
        _instruction(line, env)
    if ret_term is None:
        raise Unsupported("no scalar ret")
    # UB is a whole-function property: a div-by-zero / INT_MIN-/-1 anywhere is UB even if its result
    # is dead. So accumulate every computed value's ub (poison, by contrast, only matters when it
    # reaches the returned value, so it stays on ret_poison).
    func_ub = smt_or([ret_ub, *(v[3] for v in env.values())])
    return params, ret_term, ret_width, ret_poison, func_ub


def _own_poison(name, op, flags, a, b, w):
    """Poison introduced by the op itself (independent of operand poison), LLVM-faithful."""
    conds = []
    fl = [f for f in flags if f in VALID_FLAGS.get(op, set())]
    if fl:  # nsw/nuw overflow, exact remainder, and oversize *flagged* shifts
        conds.append(flag_poison_smt(op, fl, a, b, w))
    if name == "or" and "disjoint" in flags:  # `or disjoint` requires no common bits
        conds.append(f"(not (= (bvand {a} {b}) (_ bv0 {w})))")
    if name in ("shl", "lshr", "ashr"):  # a plain shift by >= bitwidth is poison too
        conds.append(f"(bvuge {b} (_ bv{w} {w}))")
    return smt_or(conds)


def _own_ub(name, a, b, w):
    """Undefined behaviour introduced by the op itself (div/rem by zero; signed INT_MIN/-1)."""
    conds = []
    if name in ("udiv", "sdiv", "urem", "srem"):
        conds.append(f"(= {b} (_ bv0 {w}))")
    if name in ("sdiv", "srem"):
        imin, ones = _const(1 << (w - 1), w), _const((1 << w) - 1, w)
        conds.append(f"(and (= {a} {imin}) (= {b} {ones}))")
    return smt_or(conds)


def _instruction(line, env):
    m = re.fullmatch(r"(%[\w.]+)\s*=\s*(.+)", line)
    if not m:
        raise Unsupported(line)
    dst, rhs = m.group(1), m.group(2)

    bm = re.fullmatch(r"(\w+)((?:\s+(?:nsw|nuw|exact|disjoint))*)\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if bm and bm.group(1) in _BIN:
        name, flags, w = bm.group(1), re.findall(r"nsw|nuw|exact|disjoint", bm.group(2)), int(bm.group(3))
        a, _, ap, au = _operand(bm.group(4).rstrip(","), w, env)
        b, _, bp, bu = _operand(bm.group(5), w, env)
        op = _BIN[name]
        poison = smt_or([ap, bp, _own_poison(name, op, flags, a, b, w)])
        # div/rem by zero is UB; so is a poison divisor (poison used to control the result).
        div_ub = bp if name in ("udiv", "sdiv", "urem", "srem") else "false"
        ub = smt_or([au, bu, div_ub, _own_ub(name, a, b, w)])
        env[dst] = (f"({op} {a} {b})", w, poison, ub)
        return

    im = re.fullmatch(r"icmp\s+(\w+)\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if im and im.group(1) in _ICMP:
        w = int(im.group(2))
        a, _, ap, au = _operand(im.group(3).rstrip(","), w, env)
        b, _, bp, bu = _operand(im.group(4), w, env)
        pred = _ICMP[im.group(1)].format(a=a, b=b)
        env[dst] = (f"(ite {pred} {_const(1, 1)} {_const(0, 1)})", 1, smt_or([ap, bp]), smt_or([au, bu]))
        return

    sm = re.fullmatch(r"select\s+i1\s+(\S+),\s+i(\d+)\s+(\S+),\s+i\d+\s+(\S+)", rhs)
    if sm:
        w = int(sm.group(2))
        c, _, cp, cu = _operand(sm.group(1).rstrip(","), 1, env)
        t, _, tp, tu = _operand(sm.group(3).rstrip(","), w, env)
        f, _, fp, fu = _operand(sm.group(4), w, env)
        picks_t = f"(= {c} {_const(1, 1)})"
        # poison: the condition always propagates; only the SELECTED arm's poison reaches the result.
        arm_poison = tp if tp == fp else f"(ite {picks_t} {tp} {fp})" if "false" not in (tp, fp) \
            else smt_and([picks_t, tp]) if fp == "false" else smt_and([f"(not {picks_t})", fp])
        poison = smt_or([cp, arm_poison])
        env[dst] = (f"(ite {picks_t} {t} {f})", w, poison, smt_or([cu, tu, fu]))
        return

    # min/max intrinsics InstCombine canonicalizes select+icmp into.
    mm = re.fullmatch(r"call\s+i(\d+)\s+@llvm\.(smin|smax|umin|umax)\.i\d+\("
                      r"i\d+\s+(\S+),\s+i\d+\s+(\S+)\)", rhs)
    if mm:
        w = int(mm.group(1))
        a, _, ap, au = _operand(mm.group(3).rstrip(","), w, env)
        b, _, bp, bu = _operand(mm.group(4), w, env)
        cmp = {"smin": "bvsle", "smax": "bvsge", "umin": "bvule", "umax": "bvuge"}[mm.group(2)]
        env[dst] = (f"(ite ({cmp} {a} {b}) {a} {b})", w, smt_or([ap, bp]), smt_or([au, bu]))
        return

    cm = re.fullmatch(r"(zext|sext|trunc)\s+i(\d+)\s+(\S+)\s+to\s+i(\d+)", rhs)
    if cm:
        src_w, dst_w = int(cm.group(2)), int(cm.group(4))
        v, _, vp, vu = _operand(cm.group(3), src_w, env)
        if cm.group(1) == "trunc":
            env[dst] = (f"((_ extract {dst_w - 1} 0) {v})", dst_w, vp, vu)
        else:
            ext = "zero_extend" if cm.group(1) == "zext" else "sign_extend"
            env[dst] = (f"((_ {ext} {dst_w - src_w}) {v})", dst_w, vp, vu)
        return

    raise Unsupported(rhs)


def run_passes(src_text, passes, opt_bin="opt"):
    """Run any `opt -passes=<passes>` pipeline and return the textual IR (or None on failure)."""
    proc = subprocess.run([opt_bin, f"-passes={passes}", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def run_instcombine(src_text, opt_bin="opt"):
    return run_passes(src_text, "instcombine", opt_bin)


def validate_transform(z3_bin, src_text, opt_text, func, timeout=None):
    """Translate before/after and prove the returned value equal for all inputs -- a closed-loop
    translation validation for ANY value-preserving scalar pass (instcombine, reassociate,
    early-cse, gvn, ...). Returns a verdict dict (status proved|refuted|unsupported|error|timeout).
    `timeout` (seconds) bounds the z3 call so one pathological function cannot hang a corpus sweep --
    a timeout is a sound DECLINE (no verdict), never a proof."""
    try:
        p0, r0, w0, sp, su = translate(src_text, func)   # src: value, poison, ub
        p1, r1, w1, tp, tu = translate(opt_text, func)   # tgt: value, poison, ub
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if p0 != p1 or w0 != w1:
        return {"status": "error", "function": func, "reason": "signature changed"}
    decls = [f"(declare-const {name} (_ BitVec {w}))" for name, w in sorted(p0.items())]
    # Alive2 refinement refutation: an input where the source is defined (no UB, value not poison)
    # but the target misbehaves -- it is UB, becomes poison, or returns a different value. (A pass
    # that only DROPS a flag / removes UB cannot satisfy this, so it still proves.)
    refute = smt_and([f"(not {su})",
                      smt_or([tu, smt_and([f"(not {sp})",
                                           smt_or([tp, f"(not (= {r0} {r1}))"])])])])
    smt = "\n".join(["(set-logic QF_BV)", *decls,
                     f"(assert {refute})", "(check-sat)", "(get-model)", ""])
    try:
        out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True,
                             timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "function": func}
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return {"status": "proved", "function": func}
    if head == "sat":
        return {"status": "refuted", "function": func, "witness": out}
    return {"status": "error", "function": func, "reason": head}


def validate_instcombine(z3_bin, src_text, opt_text, func):
    """Backward-compatible alias: InstCombine is one value-preserving scalar pass."""
    return validate_transform(z3_bin, src_text, opt_text, func)


def function_names(ll_text):
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)
