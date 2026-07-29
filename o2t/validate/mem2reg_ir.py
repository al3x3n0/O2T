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

from o2t.validate import ir_model as ir
from o2t.validate.scalar_ir import _BIN, _ICMP, _const


class Unsupported(Exception):
    pass


def blocks_of(ll_text, func):
    """Ordered blocks of a function as [(label, [Instruction], terminator Instruction)].

    The shared CFG reader for this module AND the loop track. It used to split a body string on
    `label:` lines and hand back raw text, which every consumer then re-parsed with its own regexes;
    LLVM already gives the block structure, the terminator, and each instruction, so the shape
    analyses below work on data instead of formatting."""
    # A module LLVM cannot parse is one we cannot analyse, so it DECLINES with the parser's own
    # reason rather than propagating. The shape fixtures deliberately feed malformed functions (a
    # phi removed, leaving its uses dangling) to check exactly that they are declined.
    try:
        fn = ir.parse(ll_text).function(func)
    except ir.IrParseError as exc:
        raise Unsupported(f"unparseable module: {str(exc).splitlines()[0][:120]}") from None
    if fn is None or fn.is_declaration:
        raise Unsupported(f"function {func} not found")
    out = []
    for blk in fn.blocks:
        instrs = blk.instructions
        if not instrs:
            raise Unsupported(f"empty block {blk.name}")
        out.append((blk.name, instrs[:-1], instrs[-1]))
    return out


def _blocks(body_or_text, func=None):
    """Back-compat shim: `blocks_of` when given (text, func). The old body-string form is gone --
    every caller in the tree passes the module text and a function name."""
    if func is None:
        raise Unsupported("blocks now require (ll_text, func); the body-string reader is retired")
    return blocks_of(body_or_text, func)


def _params(ll_text, func):
    """Integer parameter name -> width, from the parse."""
    fn = ir.parse(ll_text).function(func)
    return fn.int_params if fn else {}


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


def _resolve(ctx, value, width):
    """An operand -> (term, sort)."""
    if getattr(value, "is_reg", False):
        if value.name in ctx.ssa:
            return ctx.ssa[value.name]
        raise Unsupported(f"operand {value.name!r}")
    kind = getattr(value, "kind", None)
    if kind == "int":
        if width == 1 and value.type is not None and value.type.is_int(1):
            return ("true" if value.int_value else "false"), "bool"
        return _const(value.int_value, width), f"bv{width}"
    raise Unsupported(f"operand {kind!r}")


def _edges(term, label):
    """Outgoing (successor, guard) edges of a terminator, read from the parse."""
    if term.op == "br":
        if term.conditional:
            cond = term.operands[0]
            return [(term.successors[0], ("cond", cond)),
                    (term.successors[1], ("ncond", cond))]
        return [(term.successors[0], ("true", None))]
    if term.op == "ret":
        return []
    raise Unsupported(f"terminator {term.op!r}")


def _cond_expr(ctx, kind, value):
    if kind == "true":
        return "true"
    t, sort = _resolve(ctx, value, 1)
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


def _inst(ctx, inst, mem, after):
    """Interpret one instruction of a promoted-memory function into `ctx.ssa` / `mem`."""
    op, dst = inst.op, inst.result

    if op == "alloca":
        return
    if op == "store":
        val, ptr = inst.operands[0], inst.operands[1]
        if not ptr.is_reg or ptr.name not in ctx.allocas:
            raise Unsupported("store to non-promoted pointer")
        if not val.type.is_int():
            raise Unsupported(f"store of {val.type}")
        mem[ptr.name] = _resolve(ctx, val, val.type.bits)[0]
        return
    if op == "load" and dst:
        ptr = inst.operands[0]
        if not ptr.is_reg or ptr.name not in ctx.allocas:
            raise Unsupported("load from non-promoted pointer")
        if not inst.type.is_int():
            raise Unsupported(f"load of {inst.type}")
        ctx.ssa[dst] = (mem[ptr.name], f"bv{inst.type.bits}")
        return
    if op == "phi" and dst:
        if not inst.type.is_int():
            raise Unsupported(f"phi of {inst.type}")
        w = inst.type.bits
        arms = inst.incoming
        if not arms:
            raise Unsupported("phi without incoming values")
        acc = _resolve(ctx, arms[-1][0], w)[0]
        for val, pred in reversed(arms[:-1]):
            acc = f"(ite {ctx.came[(pred, _phi_block(ctx))]} {_resolve(ctx, val, w)[0]} {acc})"
        ctx.ssa[dst] = (acc, f"bv{w}")
        return
    if op == "icmp" and dst and inst.pred in _ICMP:
        operand_t = inst.operands[0].type
        if not operand_t.is_int():
            raise Unsupported(f"icmp on {operand_t}")
        w = operand_t.bits
        a = _resolve(ctx, inst.operands[0], w)[0]
        b = _resolve(ctx, inst.operands[1], w)[0]
        ctx.ssa[dst] = (_ICMP[inst.pred].format(a=a, b=b), "bool")
        return
    if op in _BIN and dst:
        if not inst.type.is_int():
            raise Unsupported(f"{op} on {inst.type}")
        w = inst.type.bits
        a = _resolve(ctx, inst.operands[0], w)[0]
        b = _resolve(ctx, inst.operands[1], w)[0]
        ctx.ssa[dst] = (f"({_BIN[op]} {a} {b})", f"bv{w}")
        return
    raise Unsupported(f"instruction {op!r}")


# phi resolution needs the current block label; stash it during _exec via a tiny shim.
def _phi_block(ctx):
    return ctx._cur


def run_mem2reg(src_text, opt_bin="opt"):
    proc = subprocess.run([opt_bin, "-passes=mem2reg", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


_POISON_UB_TOK = re.compile(r"\b(nsw|nuw|exact|disjoint|udiv|sdiv|urem|srem)\b")


def _poison_ub_counts(ll_text, func):
    """How many poison-generating flags and UB-capable ops a function contains, per kind.

    Counted from the PARSE. The regex this replaces searched the body TEXT for
    `nsw|nuw|exact|disjoint|udiv|sdiv|urem|srem`, which also matched those words inside COMMENTS --
    and LLVM's own test files are full of `; CHECK-NEXT: ... add nsw ...` lines. That is not merely
    imprecise here: this count gates a DECLINE by comparing before against after, so a comment in the
    SOURCE inflates the baseline and can mask a flag the optimized IR genuinely introduced, letting a
    poison-introducing transform through to a value-equality proof."""
    counts = {}
    try:
        fn = ir.parse(ll_text).function(func)
    except ir.IrParseError:
        return counts
    if fn is None:
        return counts
    for inst in fn.instructions():
        for flag in inst.flags:
            if flag in ("nsw", "nuw", "exact", "disjoint"):
                counts[flag] = counts.get(flag, 0) + 1
        if inst.op in ("udiv", "sdiv", "urem", "srem"):
            counts[inst.op] = counts.get(inst.op, 0) + 1
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
    cb = _poison_ub_counts(src_text, func)
    ca = _poison_ub_counts(opt_text, func)
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
        # value-equality: a mismatch is a genuine miscompile only when the source is poison-free
        # (a poison-exploiting source fold would else false-refute -- the class the fuzzer found).
        from o2t.validate.scalar_ir import poison_risk
        if poison_risk(src_text, func):
            return {"status": "unsupported", "function": func,
                    "guard": "poison-risk",
                    "reason": "value mismatch under possible poison (mem2reg model lacks poison refinement)"}
        return {"status": "refuted", "function": func, "witness": out}
    return {"status": "error", "function": func, "reason": head}


def _exec_blocks(ll_text, func, after):
    """_exec with the current-block label tracked for phi resolution."""
    ctx = _Ctx(_params(ll_text, func))
    blocks = blocks_of(ll_text, func)
    block_term = {lab: term for lab, _, term in blocks}
    preds = {}
    for lab, _, term in blocks:
        for tgt, _cond in _edges(term, lab):
            preds.setdefault(tgt, []).append(lab)
    for lab, instrs, _term in blocks:
        for inst in instrs:
            if inst.op == "alloca" and inst.result:
                ctx.allocas.add(inst.result)
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
        for inst in lines:
            _inst(ctx, inst, mem, after)
        if not after:
            ctx.exit_mem[lab] = dict(mem)
        if term.op == "ret" and term.operands and term.operands[0].type.is_int():
            ctx.ret = _resolve(ctx, term.operands[0], term.operands[0].type.bits)
    if ctx.ret is None:
        raise Unsupported("no scalar ret")
    return ctx


def function_names(ll_text):
    return ir.parse(ll_text).defined_names
