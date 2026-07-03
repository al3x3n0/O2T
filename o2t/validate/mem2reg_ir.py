#!/usr/bin/env python3
"""Closed-loop translation validation for Mem2Reg/promotion: prove the REAL `opt -passes=mem2reg`.

Mem2Reg is the one transform that bridges MEMORY and SSA: it deletes an `alloca`/`store`/`load` and
constructs `phi` nodes at merge points. None of the other validators handle it -- it needs
MULTI-BLOCK reasoning with phis. This is the first such validator.

Both the before (memory) and after (SSA+phi) functions share the SAME control-flow graph, so the
"which predecessor did control come from" conditions are a real, shared fact of execution. We
symbolically execute both over the CFG: in the BEFORE function the promoted cell's value is threaded
through stores and merged at each block by those came-via conditions (last store on the taken path
wins); in the AFTER function each `phi` is resolved by the same came-via conditions over its listed
[value, predecessor] pairs. Then we prove the returned value equal for all inputs and all branch
conditions (QF_BV + booleans). So the proof checks that mem2reg placed the phi to match the memory:
a phi with swapped incoming values is refuted with a concrete witness.

Acyclic CFGs only (loops need phi cycles -- declined `unsupported`). Supported: alloca / store / load
of a promoted pointer, phi, conditional+unconditional br, ret, integer binops, and icmp (-> i1).
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate.scalar_ir import _BIN, _ICMP, _const, _function_body


class Unsupported(Exception):
    pass


def _blocks(body):
    """Parse a function body into ordered blocks: [(label, [lines], terminator_line)]."""
    # an implicit entry label for the first block.
    lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith(";")]
    blocks, cur_label, cur = [], "entry", []
    started = False
    for ln in lines:
        lm = re.fullmatch(r"([\w.]+):(?:\s*;.*)?", ln)
        if lm:
            if started:
                blocks.append((cur_label, cur))
            cur_label, cur, started = lm.group(1), [], True
            continue
        started = True
        cur.append(ln)
    blocks.append((cur_label, cur))
    out = []
    for label, body_lines in blocks:
        if not body_lines:
            raise Unsupported(f"empty block {label}")
        out.append((label, body_lines[:-1], body_lines[-1]))
    return out


def _params(ll_text, func):
    m = re.search(r"@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
    out = {}
    if m:
        for part in m.group(1).split(","):
            pm = re.search(r"i(\d+)\s+(%[\w.]+)", part.strip())
            if pm:
                out[pm.group(2)] = int(pm.group(1))
    return out


class _Ctx:
    def __init__(self, params):
        self.ssa = {}                       # %name -> (term, sort)  global SSA defs
        for name, w in params.items():
            self.ssa[name] = (name.lstrip("%").replace(".", "_"),
                              "bool" if w == 1 else f"bv{w}")
        self.params = params
        self.reach = {}                     # label -> bool expr
        self.came = {}                      # (pred,label) -> bool expr
        self.exit_mem = {}                  # label -> {alloca -> term}
        self.allocas = set()
        self.ret = None


def _decl_syms(ctx):
    out = []
    for name, w in ctx.params.items():
        sym = name.lstrip("%").replace(".", "_")
        out.append(f"(declare-const {sym} {'Bool' if w == 1 else f'(_ BitVec {w})'})")
    return out


def _resolve(ctx, tok, width):
    """An operand -> (term, sort)."""
    tok = tok.strip().rstrip(",")
    if tok in ctx.ssa:
        return ctx.ssa[tok]
    if re.fullmatch(r"-?\d+", tok):
        return _const(int(tok), width), f"bv{width}"
    if tok in ("true", "false"):
        return tok, "bool"
    raise Unsupported(f"operand {tok!r}")


def _edges(term, label):
    bm = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%([\w.]+),\s+label\s+%([\w.]+)", term)
    if bm:
        return [(bm.group(2), ("cond", bm.group(1).rstrip(","))),
                (bm.group(3), ("ncond", bm.group(1).rstrip(",")))]
    um = re.fullmatch(r"br\s+label\s+%([\w.]+)", term)
    if um:
        return [(um.group(1), ("true", None))]
    if re.fullmatch(r"ret\s+.*", term):
        return []
    raise Unsupported(f"terminator {term!r}")


def _cond_expr(ctx, kind, tok):
    if kind == "true":
        return "true"
    t, sort = _resolve(ctx, tok, 1)
    if sort != "bool":
        t = f"(= {t} {_const(1, 1)})"
    return t if kind == "cond" else f"(not {t})"


def _topo(blocks, preds):
    order, seen = [], set()
    labels = [b[0] for b in blocks]
    changed = True
    while len(order) < len(labels):
        progressed = False
        for lab in labels:
            if lab in seen:
                continue
            if all(p in seen for p in preds.get(lab, [])):
                order.append(lab); seen.add(lab); progressed = True
        if not progressed:
            raise Unsupported("cyclic CFG (loop)")
    return order


def _merge_mem(ctx, label, preds_list):
    """The promoted cells' values on entry to `label`, merged over predecessors by came-via."""
    if not preds_list:
        return {a: f"init_{a.lstrip('%')}" for a in ctx.allocas}
    state = {}
    for a in ctx.allocas:
        acc = ctx.exit_mem[preds_list[-1]][a]
        for p in reversed(preds_list[:-1]):
            acc = f"(ite {ctx.came[(p, label)]} {ctx.exit_mem[p][a]} {acc})"
        state[a] = acc
    return state


def _inst(ctx, ln, mem, after):
    m = re.fullmatch(r"(%[\w.]+)\s*=\s*(.+)", ln)
    dst, rhs = (m.group(1), m.group(2)) if m else (None, ln)

    if rhs.startswith("alloca"):
        return
    sm = re.fullmatch(r"store\s+i(\d+)\s+(\S+),\s+ptr\s+(%[\w.]+)", rhs)
    if sm:
        if sm.group(3) not in ctx.allocas:
            raise Unsupported("store to non-promoted pointer")
        mem[sm.group(3)] = _resolve(ctx, sm.group(2), int(sm.group(1)))[0]
        return
    lm = re.fullmatch(r"load\s+i(\d+),\s+ptr\s+(%[\w.]+)", rhs)
    if lm and dst:
        if lm.group(2) not in ctx.allocas:
            raise Unsupported("load from non-promoted pointer")
        ctx.ssa[dst] = (mem[lm.group(2)], f"bv{lm.group(1)}")
        return
    pm = re.fullmatch(r"phi\s+i(\d+)\s+(.+)", rhs)
    if pm and dst:
        w = int(pm.group(1))
        arms = re.findall(r"\[\s*(\S+),\s*%([\w.]+)\s*\]", pm.group(2))
        acc = _resolve(ctx, arms[-1][0], w)[0]
        for val, pred in reversed(arms[:-1]):
            acc = f"(ite {ctx.came[(pred, _phi_block(ctx))]} {_resolve(ctx, val, w)[0]} {acc})"
        ctx.ssa[dst] = (acc, f"bv{w}")
        return
    im = re.fullmatch(r"icmp\s+(\w+)\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if im and dst and im.group(1) in _ICMP:
        w = int(im.group(2))
        a = _resolve(ctx, im.group(3), w)[0]
        b = _resolve(ctx, im.group(4), w)[0]
        ctx.ssa[dst] = (_ICMP[im.group(1)].format(a=a, b=b), "bool")
        return
    bm = re.fullmatch(r"(\w+)(?:\s+(?:nsw|nuw))*\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if bm and dst and bm.group(1) in _BIN:
        w = int(bm.group(2))
        a = _resolve(ctx, bm.group(3), w)[0]
        b = _resolve(ctx, bm.group(4), w)[0]
        ctx.ssa[dst] = (f"({_BIN[bm.group(1)]} {a} {b})", f"bv{w}")
        return
    raise Unsupported(rhs)


# phi resolution needs the current block label; stash it during _exec via a tiny shim.
def _phi_block(ctx):
    return ctx._cur


def run_mem2reg(src_text, opt_bin="opt"):
    proc = subprocess.run([opt_bin, "-passes=mem2reg", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


_POISON_UB_TOK = re.compile(r"\b(nsw|nuw|exact|disjoint|udiv|sdiv|urem|srem)\b")


def _poison_ub_counts(body):
    counts = {}
    for tok in _POISON_UB_TOK.findall(body or ""):
        counts[tok] = counts.get(tok, 0) + 1
    return counts


def validate_mem2reg(z3_bin, src_text, opt_text, func):
    """Prove the promoted (after) function returns the same value as the memory (before) one.

    Mem2Reg is modeled as flag-neutral: it deletes alloca/store/load and inserts phis but never
    rewrites a binop's poison flags, so dropping those flags symmetrically on both sides is sound
    (the same instruction appears, identically flagged, before and after). To keep that assumption
    honest we DECLINE rather than prove if the optimized IR introduces a poison-generating flag or a
    div/rem op the source lacked -- refinement of a flag-rewriting pass is out of scope for this
    value-equality validator (use scalar_ir / loop_induction, which thread poison/UB)."""
    try:
        b = _exec_blocks(src_text, func, after=False)
        a = _exec_blocks(opt_text, func, after=True)
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    if b.params != a.params:
        return {"status": "error", "function": func, "reason": "signature changed"}
    cb = _poison_ub_counts(_function_body(src_text, func))
    ca = _poison_ub_counts(_function_body(opt_text, func))
    if any(ca[k] > cb.get(k, 0) for k in ca):
        return {"status": "unsupported", "function": func,
                "reason": "optimized IR introduces a poison-generating flag / UB op "
                          "(mem2reg modeled as flag-neutral; refinement out of scope)"}
    smt = "\n".join(["(set-logic QF_BV)", *_decl_syms(b),
                     f"(assert (not (= {b.ret[0]} {a.ret[0]})))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return {"status": "proved", "function": func}
    if head == "sat":
        return {"status": "refuted", "function": func, "witness": out}
    return {"status": "error", "function": func, "reason": head}


def _exec_blocks(ll_text, func, after):
    """_exec with the current-block label tracked for phi resolution."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    ctx = _Ctx(_params(ll_text, func))
    blocks = _blocks(body)
    block_term = {lab: term for lab, _, term in blocks}
    preds = {}
    for lab, _, term in blocks:
        for tgt, _cond in _edges(term, lab):
            preds.setdefault(tgt, []).append(lab)
    for lab, lines, _term in blocks:
        for ln in lines:
            if re.search(r"=\s*alloca\b", ln):
                ctx.allocas.add(ln.split("=")[0].strip())
    order = _topo(blocks, preds)
    bmap = {lab: (lines, term) for lab, lines, term in blocks}
    for lab in order:
        ctx._cur = lab
        lines, term = bmap[lab]
        plist = preds.get(lab, [])
        ctx.reach[lab] = "true" if not plist else \
            "(or " + " ".join(f"(and {ctx.reach[p]} {_cond_expr(ctx, *next(c for t, c in _edges(block_term[p], p) if t == lab))})"
                              for p in plist) + ")"
        for p in plist:
            cond = next(c for t, c in _edges(block_term[p], p) if t == lab)
            ctx.came[(p, lab)] = f"(and {ctx.reach[p]} {_cond_expr(ctx, *cond)})"
        mem = _merge_mem(ctx, lab, plist) if not after else {}
        for ln in lines:
            _inst(ctx, ln, mem, after)
        if not after:
            ctx.exit_mem[lab] = dict(mem)
        rm = re.fullmatch(r"ret\s+i(\d+)\s+(\S+)", term)
        if rm:
            ctx.ret = _resolve(ctx, rm.group(2), int(rm.group(1)))
    if ctx.ret is None:
        raise Unsupported("no scalar ret")
    return ctx


def function_names(ll_text):
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)
