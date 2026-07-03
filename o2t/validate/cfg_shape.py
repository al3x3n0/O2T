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
import subprocess
from pathlib import Path

_SIG_RE = re.compile(r"define\b[^@]*@(\w+)\s*\(([^)]*)\)")
_BR_RE = re.compile(r"\bbr\s+i1\s+(%[\w.]+),\s*label\s+(%[\w.]+),\s*label\s+(%[\w.]+)")
_PHI_RE = re.compile(r"(%[\w.]+)\s*=\s*phi\s+\w+\s*"
                     r"\[\s*([^,]+),\s*(%[\w.]+)\s*\]\s*,\s*\[\s*([^,]+),\s*(%[\w.]+)\s*\]")
_SELECT_RE = re.compile(r"(%[\w.]+)\s*=\s*select\s+i1\s+(%[\w.]+|true|false),\s*\w+\s+([^,]+),\s*\w+\s+(\S+)")
_XOR_NOT_RE = re.compile(r"(%[\w.]+)\s*=\s*xor\s+i1\s+(%[\w.]+),\s*true")


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
    """name -> SMT sort, from the function signature (i1 -> Bool, iN -> (_ BitVec N))."""
    m = re.search(r"@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
    out = {}
    if not m:
        return out
    for part in m.group(1).split(","):
        toks = part.split()
        if len(toks) >= 2 and toks[-1].startswith("%"):
            ty = toks[-2] if len(toks) >= 2 else toks[0]
            name = toks[-1]
            tm = re.fullmatch(r"i(\d+)", ty)
            if tm:
                out[name] = "Bool" if tm.group(1) == "1" else f"(_ BitVec {tm.group(1)})"
    return out


def parse_diamond(ll_text, func):
    """The source diamond's merge value as (cond, then_value, else_value) SSA names, or None.
    The phi operands are mapped to branches by their incoming-block labels."""
    body = _function_body(ll_text, func)
    if body is None:
        return None
    br = _BR_RE.search(body)
    phi = _PHI_RE.search(body)
    if not br or not phi:
        return None
    cond, then_lbl, else_lbl = br.group(1), br.group(2), br.group(3)
    v1, b1, v2, b2 = phi.group(2).strip(), phi.group(3), phi.group(4).strip(), phi.group(5)
    by_block = {b1: v1, b2: v2}
    if then_lbl not in by_block or else_lbl not in by_block:
        return None
    return {"cond": cond, "then": by_block[then_lbl], "else": by_block[else_lbl]}


def parse_select(ll_text, func, source_text=""):
    """The optimized `select` as (cond, true_value, false_value, negated). `negated` is True
    when the select condition is `xor %c, true` of the source branch condition."""
    body = _function_body(ll_text, func)
    if body is None:
        return None
    sel = _SELECT_RE.search(body)
    if not sel:
        return None
    cond, tv, fv = sel.group(2), sel.group(3).strip(), sel.group(4).strip()
    negated = False
    nm = next((m for m in _XOR_NOT_RE.finditer(body) if m.group(1) == cond), None)
    if nm:
        cond, negated = nm.group(2), True
    return {"cond": cond, "true": tv, "false": fv, "negated": negated}


def _smt_atom(tok, params):
    """An i1/iN SSA operand -> SMT term. Params are declared; literals are constants."""
    if tok in params:
        return tok.lstrip("%").replace(".", "_")
    if tok in ("true", "false"):
        return tok
    m = re.fullmatch(r"-?\d+", tok)
    if m:
        return tok  # decimal bv literal handled by caller width; kept simple for params-only
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
