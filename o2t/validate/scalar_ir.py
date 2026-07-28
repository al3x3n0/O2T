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
`freeze` (target-side only -- see the `freeze` case in `_instruction`), constants, and a single `ret`.
Every value is modeled as a bitvector of its own width.

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
from o2t.validate import ir_model as ir
from o2t.validate import semantics as sem

_BIN = {"add": "bvadd", "sub": "bvsub", "mul": "bvmul", "and": "bvand", "or": "bvor",
        "xor": "bvxor", "shl": "bvshl", "lshr": "bvlshr", "ashr": "bvashr",
        "udiv": "bvudiv", "sdiv": "bvsdiv", "urem": "bvurem", "srem": "bvsrem"}
_ICMP = {"eq": "(= {a} {b})", "ne": "(distinct {a} {b})",
         "ult": "(bvult {a} {b})", "ule": "(bvule {a} {b})",
         "ugt": "(bvugt {a} {b})", "uge": "(bvuge {a} {b})",
         "slt": "(bvslt {a} {b})", "sle": "(bvsle {a} {b})",
         "sgt": "(bvsgt {a} {b})", "sge": "(bvsge {a} {b})"}


# ONE decline type across the stack: the semantics layer raises it, and every caller that catches
# `scalar_ir.Unsupported` keeps working unchanged.
Unsupported = sem.Unsupported


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


# A parameter may carry attributes between its type and its name (`i32 noundef %x`,
# `i32 range(i32 0, 8) %x`, `i8 signext %c`). They are skipped for typing purposes, but `noundef` is
# read separately by `_noundef_params` because it is what JUSTIFIES modeling the parameter as a single
# definite value (see the undef-risk guard in `validate_transform`).
_PARAM_ATTRS = r"(?:[\w.]+(?:\([^)]*\))?\s+)*"


def _sig_text(ll_text, func):
    """The text between the parentheses of `define ... @func(...)`, scanning to the MATCHING close
    paren. A `([^)]*)` capture stops at the first `)`, which an attribute may contain
    (`i32 range(i32 0, 8) %y`), truncating the list and silently dropping every later parameter."""
    m = re.search(r"define\b[^@]*@" + re.escape(func) + r"\s*\(", ll_text)
    if not m:
        return None
    depth, j = 1, m.end()
    while j < len(ll_text) and depth:
        depth += {"(": 1, ")": -1}.get(ll_text[j], 0)
        j += 1
    return ll_text[m.end():j - 1]


def _split_params(sig):
    """Split a parameter list on commas at PAREN DEPTH ZERO. An attribute may itself contain a comma
    (`i32 range(i32 0, 8) %y`), and a naive split severs the parameter from its name, which drops it
    from the model and declines the whole function on an unresolvable operand."""
    parts, depth, cur = [], 0, ""
    for ch in sig:
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _params(ll_text, func):
    """Parameter name -> width, from the signature (declared as bitvectors). Anchored on the `define`
    so a forward-reference CALL SITE `@func(args)` above the definition is not misread as the
    signature (which would bind callee params to the caller's argument names). Parameter attributes
    between the type and the name are skipped, so an attributed parameter is modeled rather than
    making the whole function decline on an unresolvable operand."""
    sig = _sig_text(ll_text, func)
    out = {}
    if sig is not None:
        for part in _split_params(sig):
            pm = re.search(r"i(\d+)\s+" + _PARAM_ATTRS + r"(%[\w.]+)", part.strip())
            if pm:
                out[pm.group(2)] = int(pm.group(1))
    return out


def _noundef_params(ll_text, func):
    """The parameters declared `noundef`. Everything else may be `undef` at run time, and an `undef`
    value is NOT one value: each USE of it may observe a different one. This model gives every
    parameter a single SMT constant, i.e. it assumes `noundef` on every argument -- so this set is
    exactly where that assumption is DECLARED rather than assumed (see the undef-risk guard)."""
    sig = _sig_text(ll_text, func)
    out = set()
    if sig is not None:
        for part in _split_params(sig):
            pm = re.search(r"i\d+\s+" + _PARAM_ATTRS + r"(%[\w.]+)", part.strip())
            if pm and re.search(r"\bnoundef\b", part):
                out.add(pm.group(1))
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


_MAX_CALL_DEPTH = 6


def translate(ll_text, func, extra_ops=None, bindings=None, _module=None, _depth=0,
              side="source", fresh=None):
    """Translate a function to (params, ret_term, ret_width, ret_poison, ret_ub) over LLVM's OWN
    parse. Validated function-by-function against the text reader it replaces over LLVM 18's
    InstCombine tests: 500 identical SMT, 465 identical declines, 0 differences, 0 regressions, and 58
    functions the text reader declined only because of a trailing `; comment`, an `immarg` attribute
    or a `zeroinitializer` -- valid IR its regexes could not match."""
    module = ir.parse(_module if _module is not None else ll_text)
    return _translate_parsed(module, func, extra_ops, bindings, _depth, side, fresh)


def _translate_text(ll_text, func, extra_ops=None, bindings=None, _module=None, _depth=0,
                    side="source", fresh=None):
    """Translate a single-BB integer function to (params, ret_term, ret_width, ret_poison, ret_ub).
    Raises Unsupported on any unmodeled instruction/shape (so it is declined, not mis-proved).
    `extra_ops` is an optional list of handlers `(rhs, env) -> (smt, w, poison, ub) | None` for
    instructions beyond the built-in fragment -- ENRICHMENTS that must be independently validated
    (o2t/validate/enrich.py) before they are installed here, so the core is grown, never guessed.
    `bindings`/`_module`/`_depth` support INTERPROCEDURAL resolution: a `call @g(args)` is modeled by
    translating `g` (from `_module`) with its params bound to the argument terms -- so a caller is
    translatable and inlining is verifiable. Recursion is bounded by `_MAX_CALL_DEPTH` (else declined).
    `side` ("source"|"target") and `fresh` (a list the caller appends `(name, width)` declarations to)
    support instructions whose semantics are NONDETERMINISTIC -- today just `freeze`, whose choice is
    existential on the target and universal on the source (see the `freeze` case in `_instruction`).
    The default is the conservative one: without a `fresh` list a source-side freeze simply declines."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    params = _params(ll_text, func)
    # `bindings` supplies a caller's argument terms for a resolved call (interprocedural inlining);
    # `_module` is where callees are looked up (defaults to this text); `_depth` bounds recursion.
    if bindings is not None:
        env = dict(bindings)
    else:
        env = {name: (name, w, "false", "false") for name, w in params.items()}
    call_ctx = {"module": _module if _module is not None else ll_text,
                "depth": _depth, "extra_ops": extra_ops, "side": side, "fresh": fresh}
    if re.search(r"^\s*br\b", body, re.M):             # any branch -> a multi-block (CFG) function
        return _translate_multiblock(body, params, env, extra_ops, call_ctx)
    # LOCAL scalar memory (single-BB only): symbolic mem2reg over non-escaping allocas. An escaping
    # pointer is never in `env` as a value, so its use declines naturally -- no aliasing is assumed.
    call_ctx["mem"] = {"cell": {}, "val": {}}          # alloca ptr -> cell id ; cell id -> (term, poison)
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
        sm = re.fullmatch(r"store\s+i(\d+)\s+(\S+),\s+ptr\s+(%[\w.]+)(?:,.*)?", line)
        if sm:                                         # store v, ptr %p (%p must be a local alloca)
            mem = call_ctx["mem"]
            if sm.group(3) not in mem["cell"]:
                raise Unsupported("store to a non-local/escaped pointer")
            vt, _, vp, _ = _operand(sm.group(2), int(sm.group(1)), env)
            mem["val"][mem["cell"][sm.group(3)]] = (vt, vp)
            continue
        _instruction(line, env, extra_ops, call_ctx)
    if ret_term is None:
        raise Unsupported("no scalar ret")
    # UB is a whole-function property: a div-by-zero / INT_MIN-/-1 anywhere is UB even if its result
    # is dead. So accumulate every computed value's ub (poison, by contrast, only matters when it
    # reaches the returned value, so it stays on ret_poison).
    func_ub = smt_or([ret_ub, *(v[3] for v in env.values())])
    return params, ret_term, ret_width, ret_poison, func_ub


def _bool_of(term, width):
    """An iW value -> an SMT boolean (true iff nonzero) -- the sense of a branch/select condition."""
    return f"(not (= {term} {_const(0, width)}))"


def _parse_blocks(body):
    """[(label, [instruction lines], terminator line)] for each basic block; None if malformed. The
    entry block (leading instructions before the first label) is named `entry`."""
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith(";")]
    raw, label, cur = [], None, []
    for l in lines:
        m = re.fullmatch(r"([\w.]+):", l)
        if m:
            if label is not None or cur:
                raw.append((label or "entry", cur))
            label, cur = m.group(1), []
        else:
            cur.append(l)
    raw.append((label or "entry", cur))
    out = []
    for lab, insts in raw:
        if not insts:
            return None
        out.append((lab, insts[:-1], insts[-1]))
    return out


def _translate_multiblock(body, params, env, extra_ops, call_ctx):
    """Symbolically execute an ACYCLIC single-function CFG to the same 5-tuple `translate` returns.
    Each block gets a path condition; a `phi` becomes an `ite` over the predecessors' reached-from
    conditions; returns are combined by path condition. SOUND-BY-SCOPE: div/rem (the only UB sources)
    make it DECLINE (so whole-function UB is `false` and needs no path-conditioning); a back-edge
    (loop) or an unhandled terminator/shape DECLINES. Poison propagates through the phi/return ites;
    branch-on-poison is not modeled (a documented conservative gap, not a false proof)."""
    blocks = _parse_blocks(body)
    if not blocks:
        raise Unsupported("unparseable CFG")
    order = [lab for lab, _, _ in blocks]
    binfo = {lab: (insts, term) for lab, insts, term in blocks}
    succ = {}
    for lab, insts, term in blocks:
        if any(re.search(r"\b[us](?:div|rem)\b", ins) for ins in insts):
            raise Unsupported("div/rem in multi-block (UB path-conditioning not modeled)")
        if re.fullmatch(r"ret\s+i\d+\s+\S+", term):
            succ[lab] = []
        elif (um := re.fullmatch(r"br\s+label\s+%([\w.]+)", term)):
            succ[lab] = [um.group(1)]
        elif (cm := re.fullmatch(r"br\s+i1\s+\S+,\s+label\s+%([\w.]+),\s+label\s+%([\w.]+)", term)):
            succ[lab] = [cm.group(1), cm.group(2)]
        else:
            raise Unsupported(f"terminator {term!r}")
    preds = {lab: [] for lab in order}
    for lab in order:
        for s in succ[lab]:
            if s not in preds:
                raise Unsupported(f"branch to unknown block %{s}")
            preds[s].append(lab)

    path, edge, rets, branch_poison = {order[0]: "true"}, {}, [], []
    done, todo, progress = set(), list(order), True
    while todo and progress:
        progress = False
        for lab in list(todo):
            if lab != order[0] and any(p not in done for p in preds[lab]):
                continue
            if lab != order[0]:
                parts = [f"(and {path[p]} {edge[(p, lab)]})" for p in preds[lab]]
                path[lab] = parts[0] if len(parts) == 1 else "(or " + " ".join(parts) + ")"
            insts, term = binfo[lab]
            for ins in insts:
                pm = re.fullmatch(r"(%[\w.]+)\s*=\s*phi\s+i(\d+)\s+(.+)", ins)
                if pm:
                    dst, w = pm.group(1), int(pm.group(2))
                    incs = re.findall(r"\[\s*([^,\]]+?)\s*,\s*%([\w.]+)\s*\]", pm.group(3))
                    if not incs:
                        raise Unsupported("phi parse")
                    val = poi = None
                    for vtok, plab in incs:
                        vt, _, vp, _ = _operand(vtok.strip(), w, env)
                        rf = f"(and {path.get(plab, 'false')} {edge.get((plab, lab), 'false')})"
                        val = vt if val is None else f"(ite {rf} {vt} {val})"
                        poi = vp if poi is None else f"(ite {rf} {vp} {poi})"
                    env[dst] = (val, w, poi, "false")
                    continue
                _instruction(ins, env, extra_ops, call_ctx)
            if term.startswith("ret"):
                rm = re.fullmatch(r"ret\s+i(\d+)\s+(\S+)", term)
                w = int(rm.group(1))
                rt, _, rp, _ = _operand(rm.group(2), w, env)
                rets.append((rt, rp, w, path[lab]))
            elif (um := re.fullmatch(r"br\s+label\s+%([\w.]+)", term)):
                edge[(lab, um.group(1))] = "true"
            else:
                cm = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%([\w.]+),\s+label\s+%([\w.]+)", term)
                cv, _, cvp, _ = _operand(cm.group(1), 1, env)
                if cvp != "false":                     # branching on POISON poisons the whole result
                    branch_poison.append(f"(and {path[lab]} {cvp})")
                cb = _bool_of(cv, 1)
                edge[(lab, cm.group(2))], edge[(lab, cm.group(3))] = cb, f"(not {cb})"
            done.add(lab); todo.remove(lab); progress = True
    if todo:
        raise Unsupported("cyclic CFG (loop) -- not modeled")
    if not rets:
        raise Unsupported("no scalar ret")
    w = rets[0][2]
    term, poison = rets[-1][0], rets[-1][1]
    for rt, rp, _, pc in reversed(rets[:-1]):
        term, poison = f"(ite {pc} {rt} {term})", f"(ite {pc} {rp} {poison})"
    if branch_poison:                                  # a poison branch condition poisons the result
        poison = smt_or([poison, *branch_poison])
    return params, term, w, poison, "false"


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


def poison_risk(ll_text, func):
    """Does `func`'s body contain a poison-generating op that a VALUE-equality validator does not
    refine? A flagged binop (nsw/nuw/exact/disjoint) or a shift whose amount is not a scalar in-range
    constant (a variable, an oversize constant, or any vector shift). Such a validator may PROVE
    soundly (value-equal => refinement) but must NOT REFUTE here -- a value mismatch could be a sound
    poison exploitation (opt folding a poison `ashr x,x` to 0), so callers decline instead."""
    body = _function_body(ll_text, func) or ""
    if re.search(r"\b(nsw|nuw|exact|disjoint)\b", body):
        return True
    for m in re.finditer(r"\b(?:shl|lshr|ashr)\s+(i(\d+)|<[^>]+>)\s+[^,]+,\s*(\S+)", body):
        width, amt = m.group(2), m.group(3).rstrip(",")
        if width is None:                              # a vector shift -> conservatively poison risk
            return True
        if not re.fullmatch(r"-?\d+", amt) or not (0 <= int(amt) < int(width)):
            return True                                # variable or out-of-range scalar shift
    return False


def _own_ub(name, a, b, w):
    """Undefined behaviour introduced by the op itself (div/rem by zero; signed INT_MIN/-1)."""
    conds = []
    if name in ("udiv", "sdiv", "urem", "srem"):
        conds.append(f"(= {b} (_ bv0 {w}))")
    if name in ("sdiv", "srem"):
        imin, ones = _const(1 << (w - 1), w), _const((1 << w) - 1, w)
        conds.append(f"(and (= {a} {imin}) (= {b} {ones}))")
    return smt_or(conds)


# --- common integer intrinsics (SMT models; each lli-validated by intrinsics_ir_fixture) ----------
def _intr_args(arg_str, w, env):
    """Parse `iN v, iN v, ...` intrinsic args to a list of _operand tuples (types stripped)."""
    out = []
    for part in arg_str.split(","):
        m = re.fullmatch(r"\s*i\d+\s+(\S+)\s*", part)
        if not m:
            raise Unsupported(f"intrinsic arg {part!r}")
        out.append(_operand(m.group(1), w, env))
    return out


def _p(ops):                                          # combined operand poison / ub
    return smt_or([o[2] for o in ops]), smt_or([o[3] for o in ops])


def _intr_ctpop(ops, w):
    a, (p, u) = ops[0][0], _p(ops)
    bits = [f"((_ zero_extend {w - 1}) ((_ extract {i} {i}) {a}))" for i in range(w)]
    return f"(bvadd {' '.join(bits)})", w, p, u


def _intr_abs(ops, w):
    if len(ops) != 2:
        raise Unsupported("abs arity")
    a, np = ops[0][0], ops[1][0]                       # np = the i1 is_int_min_poison flag
    _, u = _p(ops[:1])
    val = f"(ite (bvslt {a} (_ bv0 {w})) (bvneg {a}) {a})"
    pois = smt_or([ops[0][2], f"(and (= {np} (_ bv1 1)) (= {a} {_const(1 << (w - 1), w)}))"])
    return val, w, pois, u


def _ctz(ops, w, leading):
    """ctlz/cttz as a bounded nested-ite over the bits: the position of the highest (ctlz) or lowest
    (cttz) set bit; W if the input is zero. Poison when the is_zero_poison flag is set and x == 0."""
    if len(ops) != 2:
        raise Unsupported("ct{l,t}z arity")
    a, izp = ops[0][0], ops[1][0]
    expr = _const(w, w)                                # x == 0 -> W
    order = range(w) if leading else range(w - 1, -1, -1)   # leading: MSB ends outermost
    for i in order:
        val = (w - 1 - i) if leading else i            # count of leading/trailing zeros if bit i is it
        expr = f"(ite (= ((_ extract {i} {i}) {a}) #b1) {_const(val, w)} {expr})"
    pois = smt_or([ops[0][2], f"(and (= {izp} (_ bv1 1)) (= {a} (_ bv0 {w})))"])
    return expr, w, pois, ops[0][3]


def _funnel(ops, w, right):
    if len(ops) != 3:
        raise Unsupported("funnel-shift arity")
    a, b, c = ops[0][0], ops[1][0], ops[2][0]
    p, u = _p(ops)
    s = f"(bvurem {c} (_ bv{w} {w}))"
    cat = f"(concat {a} {b})"
    if right:
        return f"((_ extract {w - 1} 0) (bvlshr {cat} ((_ zero_extend {w}) {s})))", w, p, u
    return f"((_ extract {2 * w - 1} {w}) (bvshl {cat} ((_ zero_extend {w}) {s})))", w, p, u


def _intr_uadd_sat(ops, w):
    a, b = ops[0][0], ops[1][0]
    p, u = _p(ops)
    s = f"(bvadd {a} {b})"
    return f"(ite (bvult {s} {a}) (bvnot (_ bv0 {w})) {s})", w, p, u


def _intr_usub_sat(ops, w):
    a, b = ops[0][0], ops[1][0]
    p, u = _p(ops)
    return f"(ite (bvult {a} {b}) (_ bv0 {w}) (bvsub {a} {b}))", w, p, u


def _s_sat(ops, w, sub):
    """s{add,sub}.sat: compute in w+1 bits (no overflow), then clamp to [INT_MIN, INT_MAX]."""
    a, b = ops[0][0], ops[1][0]
    p, u = _p(ops)
    a1, b1 = f"((_ sign_extend 1) {a})", f"((_ sign_extend 1) {b})"
    s = f"({'bvsub' if sub else 'bvadd'} {a1} {b1})"
    imax_w, imin_w = _const((1 << (w - 1)) - 1, w), _const(1 << (w - 1), w)   # 0x7f.. and 0x80..
    imax_e, imin_e = f"((_ sign_extend 1) {imax_w})", f"((_ sign_extend 1) {imin_w})"
    lo = f"((_ extract {w - 1} 0) {s})"
    return f"(ite (bvsgt {s} {imax_e}) {imax_w} (ite (bvslt {s} {imin_e}) {imin_w} {lo}))", w, p, u


# Note: `bswap` is deliberately NOT built in -- it is the worked example for the lli-gated
# self-enrichment path (enrich_fixture), which demonstrates growing the vocabulary from outside.
_INTRINSICS = {
    "ctpop": _intr_ctpop, "abs": _intr_abs,
    "ctlz": lambda ops, w: _ctz(ops, w, leading=True),
    "cttz": lambda ops, w: _ctz(ops, w, leading=False),
    "fshl": lambda ops, w: _funnel(ops, w, right=False),
    "fshr": lambda ops, w: _funnel(ops, w, right=True),
    "uadd.sat": _intr_uadd_sat, "usub.sat": _intr_usub_sat,
    "sadd.sat": lambda ops, w: _s_sat(ops, w, sub=False),
    "ssub.sat": lambda ops, w: _s_sat(ops, w, sub=True),
}


def _instruction(line, env, extra_ops=None, call_ctx=None):
    m = re.fullmatch(r"(%[\w.]+)\s*=\s*(.+)", line)
    if not m:
        raise Unsupported(line)
    dst, rhs = m.group(1), m.group(2)

    if call_ctx is not None and "mem" in call_ctx:     # LOCAL scalar memory (single-BB symbolic mem2reg)
        am = re.fullmatch(r"alloca\s+i(\d+)(?:,.*)?", rhs)
        if am:
            mem = call_ctx["mem"]
            mem["cell"][dst] = len(mem["cell"])        # a fresh, distinct cell per alloca
            return
        lm = re.fullmatch(r"load\s+i(\d+),\s+ptr\s+(%[\w.]+)(?:,.*)?", rhs)
        if lm:
            mem = call_ctx["mem"]
            cell = mem["cell"].get(lm.group(2))
            if cell is None or cell not in mem["val"]:
                raise Unsupported("load from an escaped/uninitialized pointer")
            vt, vp = mem["val"][cell]                   # last store to this alloca (textual = execution order)
            env[dst] = (vt, int(lm.group(1)), vp, "false")
            return

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

    # Common integer intrinsics InstCombine produces/folds. Each SMT model is lli-validated
    # (intrinsics_ir_fixture) -- the model is not trusted on its own. Value semantics; operand poison
    # propagates, and `abs`'s int-min poison flag is modeled.
    im2 = re.fullmatch(r"call\s+i(\d+)\s+@llvm\.([a-z]+(?:\.sat)?)\.i\d+\((.*)\)", rhs)
    if im2 and im2.group(2) in _INTRINSICS:
        w = int(im2.group(1))
        ops = _intr_args(im2.group(3), w, env)
        env[dst] = _INTRINSICS[im2.group(2)](ops, w)
        return

    # `freeze` -- the instruction InstCombine INTRODUCES to launder poison (select->or, and the whole
    # isGuaranteedNotToBePoison family), so declining it blinds Track B on exactly the poison-critical
    # folds. Semantics: if the operand is not poison, freeze is the identity; if it is, freeze yields
    # ONE arbitrary value, fixed for the execution (hence a single fresh constant per `freeze`, reused
    # at every use of `dst`), and the result is never poison.
    #
    # That choice is NONDETERMINISM, and its quantifier depends on which side we are translating.
    # Refinement is `every TARGET behaviour is one the SOURCE could have produced`, so in the
    # refutation query the target's pick is EXISTENTIAL (a free constant the solver may choose to
    # expose a difference -- correct: a target that freezes poison where the source returns a definite
    # value really is unsound) while the source's pick is UNIVERSAL. A free constant for the source
    # would let the solver pick the one value that differs and report a FALSE REFUTATION, and QF_BV
    # cannot carry the quantifier -- so a SOURCE-side freeze always DECLINES.
    #
    # It declines even when the operand's poison term is "false", which looks like a needless refusal
    # (freeze of a definite value is the identity) but is not: this model has no `undef`, and it treats
    # PARAMETERS as definite. LLVM does not -- an argument may be `undef` unless `noundef`, and
    # `freeze` is precisely the instruction that observes the difference. Taking the identity shortcut
    # makes `freeze %x -> %x` prove, which reference Alive2 REFUTES (target `%x` may be undef, source
    # `%z` is one fixed value). Removing a freeze is therefore outside the fragment until undef is
    # modeled; introducing one -- what InstCombine actually does -- is inside it.
    fz = re.fullmatch(r"freeze\s+i(\d+)\s+(\S+)", rhs)
    if fz:
        w = int(fz.group(1))
        v, _, vp, vu = _operand(fz.group(2), w, env)
        if call_ctx is None or call_ctx.get("side") != "target" or call_ctx.get("fresh") is None:
            raise Unsupported("freeze in the source (its nondeterministic choice is universal, and "
                              "this model has no undef -- so the identity shortcut is unsound)")
        fresh = call_ctx["fresh"]
        name = f"frz{len(fresh)}_{call_ctx['side']}"
        fresh.append((name, w))
        env[dst] = (f"(ite {vp} {name} {v})", w, "false", vu)
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

    # Interprocedural: a direct call `call iN @g(args)` to a function defined in the module is modeled
    # by translating g with its params bound to the argument terms (inlining its semantics). Bounded
    # recursion; an external/undefined/over-deep/recursive callee declines (never a mis-model).
    callm = re.fullmatch(r"call\s+i(\d+)\s+@([\w.$]+)\s*\((.*)\)", rhs)
    if callm and call_ctx is not None and _function_body(call_ctx["module"], callm.group(2)) is not None:
        module, depth = call_ctx["module"], call_ctx["depth"]  # a DEFINED module function -> inline it
        callee, arg_str = callm.group(2), callm.group(3)       # (declared/intrinsic calls fall through)
        if depth >= _MAX_CALL_DEPTH:
            raise Unsupported("call too deep / recursion")
        cparams = _params(module, callee)
        arg_toks = [a.strip() for a in arg_str.split(",")] if arg_str.strip() else []
        if len(arg_toks) != len(cparams):
            raise Unsupported("call arity mismatch")
        bindings = {}
        for (pname, pw), tok in zip(cparams.items(), arg_toks):
            am = re.fullmatch(r"i\d+\s+(\S+)", tok)
            if not am:
                raise Unsupported(f"call arg {tok!r}")
            bindings[pname] = _operand(am.group(1), pw, env)
        _, cret, cw, cp, cu = translate(module, callee, call_ctx["extra_ops"], bindings,
                                        module, depth + 1)
        env[dst] = (cret, cw, cp, cu)
        return

    for handler in (extra_ops or ()):                 # validated enrichments (enrich.py)
        result = handler(rhs, env)
        if result is not None:
            env[dst] = result
            return
    raise Unsupported(rhs)


def run_passes(src_text, passes, opt_bin="opt"):
    """Run any `opt -passes=<passes>` pipeline and return the textual IR (or None on failure)."""
    proc = subprocess.run([opt_bin, f"-passes={passes}", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def run_instcombine(src_text, opt_bin="opt"):
    return run_passes(src_text, "instcombine", opt_bin)


def _query(z3_bin, smt, timeout):
    """Run one SMT-LIB2 query through z3; return (first-result-line, full stdout). A timeout raises
    subprocess.TimeoutExpired so the caller can decline rather than guess."""
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True,
                         timeout=timeout).stdout
    return (out.strip().splitlines()[0].strip() if out.strip() else "error"), out


def _mentions(name, *terms):
    """Does this parameter's SMT constant appear in any of these terms? Word-boundary matched so `%x`
    does not match `%x1`."""
    pat = re.compile(re.escape(name) + r"(?![\w.])")
    return any(pat.search(t) for t in terms if t)


def _smt(decls, goal, get_model=False):
    lines = ["(set-logic QF_BV)", *decls, f"(assert {goal})", "(check-sat)"]
    if get_model:
        lines.append("(get-model)")
    return "\n".join(lines) + "\n"


def cross_check_smt(smt, expect, z3_bin=None, extra_solvers=()):
    """Replay one query through every OTHER SMT-LIB2 solver on PATH (bitwuzla/cvc5/cvc4) and report
    whether they all reproduce `expect` (sat|unsat). Track B's verdict is a single z3 call over a
    hand-built encoding: the encoding is cross-checked by lli/Alive2, but the SOLVER is not. Replaying
    the IDENTICAL script through an independently implemented solver closes that hole -- a
    disagreement is a solver (or SMT-LIB) bug, not an encoding bug, and no other oracle can see it.
    Reported `skipped` (honest) when no second solver is installed, never silently passed."""
    from o2t.meta.cross_check import detect_solvers, run_solver     # lazy: avoids an import cycle
    solvers = [(n, b) for n, b in detect_solvers(z3_bin or "z3", extra_solvers) if n != "z3"]
    if not solvers:
        return {"status": "skipped", "reason": "no second solver on PATH", "solvers": {}}
    results = {name: run_solver(name, binary, smt) for name, binary in solvers}
    agree = all(r == expect for r in results.values())
    return {"status": "agree" if agree else "disagree", "expect": expect, "solvers": results}


def validate_transform(z3_bin, src_text, opt_text, func, timeout=None, extra_ops=None,
                       check_vacuity=True, cross_check=False, extra_solvers=()):
    """Translate before/after and prove the returned value equal for all inputs -- a closed-loop
    translation validation for ANY value-preserving scalar pass (instcombine, reassociate,
    early-cse, gvn, ...). Returns a verdict dict (status proved|refuted|unsupported|error|timeout).
    `timeout` (seconds) bounds the z3 call so one pathological function cannot hang a corpus sweep --
    a timeout is a sound DECLINE (no verdict), never a proof. `extra_ops` are validated enrichment
    handlers (o2t/validate/enrich.py) that widen the modeled instruction set.

    `check_vacuity` (default on) probes whether the SOURCE is defined anywhere. Refinement is
    vacuously true when the source is UB or poison on EVERY input -- `udiv %x, 0` legitimately
    refines to `ret 12345` -- so such a `proved` is valid but carries no information about the
    transform. It is also the exact signature of an OVER-APPROXIMATED UB/poison model: claiming UB
    where LLVM has none turns a would-be refutation into a proof -- the same failure SHAPE as the two
    false proofs the 2026-07 review found by hand (a model corner that silently converts a refutation
    into a proof), and the one shape the encoding oracles cannot see, since lli and Alive2 are
    consulted only on the proved set and agree that a UB source refines to anything. The verdict
    carries `vacuous: True|False|None` (None = the probe was inconclusive); Track A has had this guard
    since mini_alive's premise-satisfiability check, Track B had none.

    `cross_check` replays the decided query through a second, independently implemented solver."""
    fresh: list = []                                   # nondeterministic choices (freeze), declared below
    try:
        p0, r0, w0, sp, su = translate(src_text, func, extra_ops, side="source", fresh=fresh)
        p1, r1, w1, tp, tu = translate(opt_text, func, extra_ops, side="target", fresh=fresh)
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    except ir.IrParseError as exc:
        return {"status": "error", "function": func,
                "reason": f"module is not valid LLVM IR: {str(exc).splitlines()[0][:120]}"}
    if p0 != p1 or w0 != w1:
        return {"status": "error", "function": func, "reason": "signature changed"}

    # UNDEF-RISK GUARD. Every parameter is modeled as ONE definite SMT constant, which silently
    # assumes `noundef` on every argument. LLVM does not: an argument may be `undef`, and an `undef`
    # value is not one value -- each USE of it may observe a different one. The assumption becomes
    # LOAD-BEARING exactly when the TARGET's result depends on such a parameter and the SOURCE's does
    # not: the source is then determined where the target is not, so the target has behaviours the
    # source lacks. `ret i32 0 -> xor %x, %x` is the canonical case -- it PROVED here (both sides are
    # 0 under one constant) while reference Alive2 refutes it, and adding `noundef %x` makes Alive2
    # prove it, which pins the mechanism. Neither the lli nor the Alive2 oracle catches this in the
    # corpus sweeps, because real InstCombine never introduces a duplicated argument use; it is
    # reachable through this API, which compose_tv/module_tv/argprom_tv and user passes all go through.
    # Measured cost on LLVM 18 and/or/xor/add/select/freeze.ll: 0 of 447 proofs (the 10 functions
    # where `opt` legitimately multiplies a parameter use all have a source that already depends on
    # it, and Alive2 confirms all 10 sound).
    # The test is on the returned VALUE and its poison, not on UB: UB is checked existentially over
    # the parameter's whole range either way (`udiv %a, %b` is UB for some `%b` whether that `%b` is
    # one constant or undef), so including it only over-declines -- it wrongly declined the
    # introduce-a-dead-div-by-zero teeth, which must still refute.
    risky = [n for n in sorted(p0) if n not in _noundef_params(src_text, func)
             and _mentions(n, r1, tp) and not _mentions(n, r0, sp)]
    if risky:
        return {"status": "unsupported", "function": func,
                "reason": f"target result depends on possibly-undef parameter(s) "
                          f"{', '.join(risky)} the source result does not (add `noundef` to declare "
                          f"them defined; an undef argument may read differently at each use)"}

    decls = [f"(declare-const {name} (_ BitVec {w}))" for name, w in sorted(p0.items())]
    decls += [f"(declare-const {name} (_ BitVec {w}))" for name, w in fresh]
    # Alive2 refinement refutation: an input where the source is defined (no UB, value not poison)
    # but the target misbehaves -- it is UB, becomes poison, or returns a different value. (A pass
    # that only DROPS a flag / removes UB cannot satisfy this, so it still proves.)
    refute = smt_and([f"(not {su})",
                      smt_or([tu, smt_and([f"(not {sp})",
                                           smt_or([tp, f"(not (= {r0} {r1}))"])])])])
    smt = _smt(decls, refute, get_model=True)
    try:
        head, out = _query(z3_bin, smt, timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "function": func}
    if head == "unsat":
        verdict = {"status": "proved", "function": func}
    elif head == "sat":
        verdict = {"status": "refuted", "function": func, "witness": out}
    else:
        return {"status": "error", "function": func, "reason": head}

    if check_vacuity and head == "unsat":
        # Is the source defined on ANY input? sat => the proof is about real behaviour.
        defined = smt_and([f"(not {su})", f"(not {sp})"])
        try:
            dhead, _ = _query(z3_bin, _smt(decls, defined), timeout)
        except subprocess.TimeoutExpired:
            dhead = "timeout"
        verdict["vacuous"] = {"sat": False, "unsat": True}.get(dhead)   # None: inconclusive probe
    if cross_check:
        verdict["cross_check"] = cross_check_smt(smt, head, z3_bin, extra_solvers)
    return verdict


def validate_instcombine(z3_bin, src_text, opt_text, func):
    """Backward-compatible alias: InstCombine is one value-preserving scalar pass."""
    return validate_transform(z3_bin, src_text, opt_text, func)


def function_names(ll_text):
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)


# =================================================================================================
# The PARSED translator: LLVM's own parse (ir_model) + the shared semantics layer.
#
# This replaces the text reader above. It is a straight port -- same 5-tuple, same SMT strings, same
# declines -- validated against the text path function-by-function over LLVM's own InstCombine tests
# (see `translate_ab` and parsed_translate_fixture). What changes is not what is modeled but how the
# module is READ: by LLVM, so a call site above a definition cannot be mistaken for a signature, an
# attribute containing a comma cannot truncate a parameter list, and an unmodeled opcode DECLINES on
# its opcode instead of a regex quietly failing to match.
# =================================================================================================

def _p_value(v, env, width=None):
    return sem.value(v, env, width)


def _p_local_memory(inst, env, ctx):
    """The local-alloca model: a non-escaping `alloca` is a cell, a `store` updates it, a `load` reads
    the last store. An escaped pointer is never a value in `env`, so its use declines naturally and no
    aliasing is ever assumed. Returns True when the instruction was consumed."""
    mem = ctx.get("mem")
    if mem is None:
        return False
    if inst.op == "alloca":
        if not (inst.alloc_type and inst.alloc_type.is_int()):
            raise sem.Unsupported("alloca of a non-integer type")
        mem["cell"][inst.result] = len(mem["cell"])
        return True
    if inst.op == "store":
        val, ptr = inst.operands[0], inst.operands[1]
        cell = mem["cell"].get(ptr.name) if ptr.is_reg else None
        if cell is None:
            raise sem.Unsupported("store to a non-local/escaped pointer")
        w = val.type.bits if val.type.is_int() else None
        if w is None:
            raise sem.Unsupported("store of a non-integer value")
        vt, _, vp, _ = _p_value(val, env, w)
        mem["val"][cell] = (vt, vp)
        return True
    if inst.op == "load":
        ptr = inst.operands[0]
        cell = mem["cell"].get(ptr.name) if ptr.is_reg else None
        if cell is None or cell not in mem["val"]:
            raise sem.Unsupported("load from an escaped/uninitialized pointer")
        vt, vp = mem["val"][cell]
        env[inst.result] = (vt, sem.int_width(inst.type), vp, "false")
        return True
    return False


def _p_call_defined(inst, env, ctx):
    """A direct call to a module-DEFINED function is inlined by translating the callee with its
    parameters bound to the argument terms. Recursion and over-deep chains decline."""
    if inst.op != "call" or inst.indirect or sem.intrinsic_name(inst.callee) is not None:
        return False
    module = ctx["module"]
    callee = module.function(inst.callee) if inst.callee else None
    if callee is None or callee.is_declaration:
        return False                                   # declared/external -> extra_ops or decline
    if ctx["depth"] >= _MAX_CALL_DEPTH:
        raise sem.Unsupported("call too deep / recursion")
    cparams = callee.int_params
    if len(inst.args) != len(cparams):
        raise sem.Unsupported("call arity mismatch")
    bindings = {}
    for (pname, pw), arg in zip(cparams.items(), inst.args):
        bindings[pname] = _p_value(arg, env, pw)
    _, cret, cw, cp, cu = _translate_parsed(module, callee.name, ctx["extra_ops"], bindings,
                                            ctx["depth"] + 1, ctx.get("side", "source"),
                                            ctx.get("fresh"))
    env[inst.result] = (cret, cw, cp, cu)
    return True


def _p_instruction(inst, env, ctx):
    """One instruction: local memory, then an inlined call, then the shared semantics, then the
    lli-validated enrichment handlers. Anything left over declines on its OPCODE."""
    if _p_local_memory(inst, env, ctx):
        return
    if _p_call_defined(inst, env, ctx):
        return
    try:
        sem.evaluate(inst, env, ctx)
        return
    except sem.Unsupported:
        for handler in (ctx.get("extra_ops") or ()):   # validated enrichments (enrich.py)
            result = handler(inst, env)
            if result is not None:
                env[inst.result] = result
                return
        raise


def _p_multiblock(fn, params, env, ctx):
    """Symbolically execute an ACYCLIC CFG. Each block carries a path condition, a `phi` lowers to an
    `ite` over its predecessors' reached-from conditions, and returns are combined by path condition.
    Sound by scope: div/rem decline (so whole-function UB stays `false` and needs no path
    conditioning), and a back-edge declines. A conditional branch on a POISON condition is undefined
    behaviour, so its poison is accumulated into the result -- discarding it caused a false
    REFUTATION the CFG fuzzer found."""
    order = [b.name for b in fn.blocks]
    binfo = {b.name: b for b in fn.blocks}
    succ = {}
    for b in fn.blocks:
        body = b.instructions
        if any(i.op in ("udiv", "sdiv", "urem", "srem") for i in body[:-1]):
            raise sem.Unsupported("div/rem in multi-block (UB path-conditioning not modeled)")
        term = body[-1] if body else None
        if term is None:
            raise sem.Unsupported("empty block")
        if term.op == "ret":
            succ[b.name] = []
        elif term.op == "br":
            succ[b.name] = list(term.successors)
        else:
            raise sem.Unsupported(f"terminator {term.op!r}")
    preds = {lab: [] for lab in order}
    for lab in order:
        for s in succ[lab]:
            if s not in preds:
                raise sem.Unsupported(f"branch to unknown block %{s}")
            preds[s].append(lab)

    path, edge, rets, branch_poison = {order[0]: "true"}, {}, [], []
    done, todo, progress = set(), list(order), True
    while todo and progress:
        progress = False
        for lab in list(todo):
            if lab != order[0] and any(p not in done for p in preds[lab]):
                continue
            if lab != order[0]:
                parts = [f"(and {path[p]} {edge[(p, lab)]})" for p in preds[lab]]
                path[lab] = parts[0] if len(parts) == 1 else "(or " + " ".join(parts) + ")"
            body = binfo[lab].instructions
            for inst in body[:-1]:
                if inst.op == "phi":
                    w = sem.int_width(inst.type)
                    val = poi = None
                    for value, plab in inst.incoming:
                        vt, _, vp, _ = _p_value(value, env, w)
                        rf = f"(and {path.get(plab, 'false')} {edge.get((plab, lab), 'false')})"
                        val = vt if val is None else f"(ite {rf} {vt} {val})"
                        poi = vp if poi is None else f"(ite {rf} {vp} {poi})"
                    env[inst.result] = (val, w, poi, "false")
                    continue
                _p_instruction(inst, env, ctx)
            term = body[-1]
            if term.op == "ret":
                if not term.operands:
                    raise sem.Unsupported("void return")
                w = sem.int_width(term.operands[0].type)
                rt, _, rp, _ = _p_value(term.operands[0], env, w)
                rets.append((rt, rp, w, path[lab]))
            elif term.conditional:
                cv, _, cvp, _ = _p_value(term.operands[0], env, 1)
                if cvp != "false":                     # branching on POISON poisons the whole result
                    branch_poison.append(f"(and {path[lab]} {cvp})")
                cb = _bool_of(cv, 1)
                edge[(lab, term.successors[0])] = cb
                edge[(lab, term.successors[1])] = f"(not {cb})"
            else:
                edge[(lab, term.successors[0])] = "true"
            done.add(lab); todo.remove(lab); progress = True
    if todo:
        raise sem.Unsupported("cyclic CFG (loop) -- not modeled")
    if not rets:
        raise sem.Unsupported("no scalar ret")
    w = rets[0][2]
    term, poison = rets[-1][0], rets[-1][1]
    for rt, rp, _, pc in reversed(rets[:-1]):
        term, poison = f"(ite {pc} {rt} {term})", f"(ite {pc} {rp} {poison})"
    if branch_poison:
        poison = smt_or([poison, *branch_poison])
    return params, term, w, poison, "false"


def _translate_parsed(module, func, extra_ops=None, bindings=None, _depth=0,
                      side="source", fresh=None):
    """`translate` over a real parse. `module` is an `ir_model.Module`."""
    fn = module.function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    params = fn.int_params
    env = dict(bindings) if bindings is not None else \
        {name: (name, w, "false", "false") for name, w in params.items()}
    ctx = {"module": module, "depth": _depth, "extra_ops": extra_ops, "side": side, "fresh": fresh}

    if len(fn.blocks) > 1:
        return _p_multiblock(fn, params, env, ctx)

    ctx["mem"] = {"cell": {}, "val": {}}
    ret_term = ret_width = None
    ret_poison = ret_ub = "false"
    for inst in fn.blocks[0].instructions:
        if inst.op == "ret":
            if not inst.operands:
                raise sem.Unsupported("void return")
            if not inst.operands[0].type.is_int():
                raise sem.Unsupported("no scalar ret")
            ret_width = sem.int_width(inst.operands[0].type)
            ret_term, _, ret_poison, ret_ub = _p_value(inst.operands[0], env, ret_width)
            break
        _p_instruction(inst, env, ctx)
    if ret_term is None:
        raise sem.Unsupported("no scalar ret")
    # UB is a whole-function property: a div-by-zero anywhere is UB even if its result is dead.
    func_ub = smt_or([ret_ub, *(v[3] for v in env.values())])
    return params, ret_term, ret_width, ret_poison, func_ub
