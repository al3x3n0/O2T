#!/usr/bin/env python3
"""Formal contract for SimplifyCFG's value-changing transform: diamond -> select.

Most CFG simplifications (block merge, unreachable removal, constant-fold a terminator) are
control-flow only -- they do not change any value, so they are sound by construction. The one
that DOES change the value computation is **if-conversion**: a diamond

    br i1 %c, then, else ;  then/else -> merge ;  merge: %r = phi [%a, then], [%b, else]

becomes `%r = select i1 %c, %a, %b`. This module validates the REAL `opt -passes=simplifycfg`
output: it parses the source diamond's merge-phi semantics (`%r = %a if %c else %b`) and the
optimized `select`, and proves them equal for ALL inputs via Z3 (the select IS the phi's
control-flow-as-value). A wrong conversion -- swapped operands, or a flipped condition without
the matching operand swap -- is REFUTED with a concrete witness. Closed-loop: like the loop
translation validator (§6), but for control-flow value equivalence rather than recurrences.
"""

from __future__ import annotations

import re

from o2t.validate import ir_model as ir
import subprocess
from pathlib import Path

_SIG_RE = re.compile(r"define\b[^@]*@(\w+)\s*\(([^)]*)\)")

def _params(ll_text, func):
    """name -> SMT sort, from the parsed signature (i1 -> Bool, iN -> (_ BitVec N)). The regex this
    replaces captured the parameter list with `([^)]*)` and split it on commas, so an attribute
    containing either -- `ptr byval({ i32, i64 }) %s` is valid LLVM 18 -- truncated the list."""
    fn = ir.parse(ll_text).function(func)
    if fn is None:
        return {}
    out = {}
    for prm in fn.params:
        if prm.type.is_int():
            out[prm.name] = "Bool" if prm.type.bits == 1 else f"(_ BitVec {prm.type.bits})"
    return out


def parse_diamond(ll_text, func):
    """The source diamond's merge value as (cond, then_value, else_value) SSA names, or None.

    Read STRUCTURALLY from the parse: find a conditional branch, then a `phi` whose incoming blocks
    are that branch's successors, and map each incoming value to the arm it arrives from. LLVM
    already knows the successor labels and the phi's incoming pairs, so nothing here has to recover
    them from instruction text -- which is where a shape reader is most easily fooled by formatting."""
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        return None
    cond = then_lbl = else_lbl = None
    for blk in fn.blocks:
        term = blk.terminator
        if term is not None and term.op == "br" and term.conditional:
            c = term.operands[0]
            cond = c.name if c.is_reg else None
            then_lbl, else_lbl = term.successors[0], term.successors[1]
            break
    if cond is None:
        return None
    for inst in fn.instructions():
        if inst.op != "phi" or len(inst.incoming) != 2:
            continue
        by_block = {lbl: (v.name if v.is_reg else str(v)) for v, lbl in inst.incoming}
        if then_lbl in by_block and else_lbl in by_block:
            return {"cond": cond, "then": by_block[then_lbl], "else": by_block[else_lbl]}
    return None


def parse_select(ll_text, func, source_text=""):
    """The optimized `select` as (cond, true_value, false_value, negated). `negated` is True when the
    select condition is `xor %c, true` of the source branch condition -- found by looking for that
    xor as an instruction, rather than by matching its printed form."""
    fn = ir.parse(ll_text).function(func)
    if fn is None or fn.is_declaration:
        return None
    sel = next((i for i in fn.instructions() if i.op == "select"), None)
    if sel is None:
        return None
    def _nm(v):
        return v.name if v.is_reg else str(v)
    cond, tv, fv = _nm(sel.operands[0]), _nm(sel.operands[1]), _nm(sel.operands[2])
    negated = False
    for inst in fn.instructions():
        if inst.op == "xor" and inst.result == cond and inst.type.is_int(1):
            ops = inst.operands
            ones = [o for o in ops if o.kind == "int" and o.int_value in (1, -1)]
            regs = [o for o in ops if o.is_reg]
            if ones and regs:
                cond, negated = regs[0].name, True
                break
    return {"cond": cond, "true": tv, "false": fv, "negated": negated}


def _smt_atom(tok, params):
    """An i1/iN SSA operand -> SMT term. Params are declared; literals are constants."""
    if tok in params:
        return tok.lstrip("%").replace(".", "_")
    if tok in ("true", "false"):
        return tok
    if tok.lstrip("-").isdigit():
        return tok
    return tok.lstrip("%").replace(".", "_")


def prove_if_conversion(z3_bin, params, diamond, select):
    """Prove `(ite cond then else) == (ite sel_cond sel_true sel_false)` for all inputs.
    Returns ("proved"|"refuted"|"error", witness)."""
    decls = []
    for name, sort in params.items():
        decls.append(f"(declare-const {_smt_atom(name, params)} {sort})")
    c = _smt_atom(diamond["cond"], params)
    src = f"(ite {c} {_smt_atom(diamond['then'], params)} {_smt_atom(diamond['else'], params)})"
    sc = _smt_atom(select["cond"], params)
    if select["negated"]:
        sc = f"(not {sc})"
    opt = f"(ite {sc} {_smt_atom(select['true'], params)} {_smt_atom(select['false'], params)})"
    smt = "\n".join(["(set-logic ALL)", *decls,
                     f"(assert (not (= {src} {opt})))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", {}
    if head == "sat":
        return "refuted", {"model": out}
    return "error", {"reason": head}


def validate_simplifycfg(z3_bin, opt_text, src_text, func):
    """Validate one diamond->select if-conversion: parse the source diamond and the optimized
    select, then prove equivalence. Returns a verdict dict."""
    diamond = parse_diamond(src_text, func)
    if diamond is None:
        return {"status": "unsupported", "reason": "no diamond merge-phi in source"}
    select = parse_select(opt_text, func, src_text)
    if select is None:
        return {"status": "unsupported", "reason": "no select in optimized output"}
    params = _params(src_text, func)
    status, info = prove_if_conversion(z3_bin, params, diamond, select)
    return {"status": status, "function": func, **info}


def run_simplifycfg(src_text, opt_bin="opt"):
    proc = subprocess.run([opt_bin, "-passes=simplifycfg", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None
