#!/usr/bin/env python3
"""UNBOUNDED equivalence for NESTED loops, compositionally (summarize the inner loop).

A nested loop's outer step runs an entire inner loop, so it cannot be a single straight-line
transition. We prove two nested loops equivalent COMPOSITIONALLY:

  1. INNER: prove the two inner loops define the same transition (equal init/guard/step over the
     enclosing-loop variables, treated as free inputs). Equal transitions => the inner loops are
     the SAME function of their inputs.
  2. OUTER: abstract the inner loop as a single uninterpreted function `INNER(inner-live-in)` and
     prove the two outer loops equivalent with that SAME `INNER` (an uninterpreted-function query).

By the compositional theorem -- inner_B == inner_A as functions, and outer_B[INNER] == outer_A[INNER]
-- the nested loops return equal values for all inputs and all trip counts. A transform that changes
the inner body inconsistently fails the INNER check; one that changes the outer fails the OUTER
check. Canonical doubly-nested shape (one inner loop in the outer body); other shapes are declined.
"""

from __future__ import annotations

import re

from o2t.validate.loop_induction import _eval, _resolve
from o2t.validate.mem2reg_ir import _blocks, _params, _function_body, Unsupported
import subprocess

from o2t.validate.loop_simulation import _check


def _check_uf(z3_bin, decls, goal):
    """Validity check in QF_UFBV (uninterpreted functions over bitvectors)."""
    smt = "\n".join(["(set-logic QF_UFBV)", *decls,
                     f"(assert (not {goal}))", "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    return ("proved" if head == "unsat" else "refuted" if head == "sat" else "error"), out


def _phi(line):
    pm = re.fullmatch(r"(%[\w.]+)\s*=\s*phi\s+i(\d+)\s+(.+)", line)
    if not pm:
        return None
    return pm.group(1), int(pm.group(2)), re.findall(r"\[\s*([^][,]+?)\s*,\s*%([\w.]+)\s*\]", pm.group(3))


def _bool(t):
    return t[0] if t[1] == "bool" else f"(= {t[0]} (_ bv1 1))"


def _succs(term):
    return re.findall(r"label\s+%([\w.]+)", term)


def _dominators(blocks, bmap):
    """Iterative dominator sets. dom(entry)={entry}; dom(n)={n} U (intersect dom(p) for preds)."""
    labels = [lab for lab, _l, _t in blocks]
    entry = labels[0]
    preds = {lab: [] for lab in labels}
    for lab, _l, term in blocks:
        for s in _succs(term):
            if s in preds:
                preds[s].append(lab)
    dom = {lab: set(labels) for lab in labels}
    dom[entry] = {entry}
    changed = True
    while changed:
        changed = False
        for lab in labels:
            if lab == entry:
                continue
            ps = [dom[p] for p in preds[lab]]
            new = {lab} | (set.intersection(*ps) if ps else set())
            if new != dom[lab]:
                dom[lab] = new
                changed = True
    return dom


def _loop_headers(blocks, bmap):
    """Loop headers in program order: a phi block H that is the target of a BACK-EDGE B->H, i.e.
    H dominates B (so B is inside H's loop)."""
    dom = _dominators(blocks, bmap)
    out = []
    for lab, lines, _t in blocks:
        if not any(_phi(ln) for ln in lines):
            continue
        preds = [b for b, _l, term in blocks if lab in _succs(term)]
        if any(lab in dom.get(b, set()) for b in preds):
            out.append(lab)
    return out


def _single_body_loop(bmap, dom, header, free_names):
    """Extract (phis, widths, inits, guard, step) for a single-body natural loop at `header`. The
    latch incoming is the one whose block the header DOMINATES (a back-edge); the other is the
    preheader (init)."""
    hlines, hterm = bmap[header]
    gm = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%([\w.]+),\s+label\s+%([\w.]+)", hterm)
    if not gm:
        raise Unsupported("loop header not a conditional branch")
    latch = None
    phis, widths, init_tok, next_tok = [], [], [], []
    for ln in hlines:
        a = _phi(ln)
        if not a:
            continue
        name, w, incs = a
        li = [(v, b) for v, b in incs if header in dom.get(b, set())]
        pi = [(v, b) for v, b in incs if header not in dom.get(b, set())]
        if not li or not pi:
            raise Unsupported("nested loop phi without preheader+latch")
        phis.append(name); widths.append(w); init_tok.append(pi[0][0]); next_tok.append(li[0][0])
        latch = li[0][1]
    return phis, widths, init_tok, next_tok, latch, gm.group(1).rstrip(",")


def _inner_model(ll_text, func, inner_hdr, outer_phis, outer_widths, prefix):
    body = _function_body(ll_text, func)
    blocks = _blocks(body)
    bmap = {lab: (lines, term) for lab, lines, term in blocks}
    dom = _dominators(blocks, bmap)
    params = dict(_params(ll_text, func))
    penv = {n: (n.lstrip("%").replace(".", "_"), "bool" if w == 1 else f"bv{w}")
            for n, w in params.items()}
    # enclosing-loop variables are free symbolic inputs to the inner loop.
    for nm, w in zip(outer_phis, outer_widths):
        penv[nm] = (nm.lstrip("%").replace(".", "_"), f"bv{w}")
    phis, widths, init_tok, next_tok, latch, cond = _single_body_loop(bmap, dom, inner_hdr, penv)
    senv = dict(penv)
    for i, nm in enumerate(phis):
        senv[nm] = (f"{prefix}{i}", f"bv{widths[i]}")
    state = [f"{prefix}{i}" for i in range(len(phis))]
    init = [_resolve(penv, t, w)[0] for t, w in zip(init_tok, widths)]
    benv = dict(senv)
    for ln in bmap[latch][0]:
        if not _phi(ln):
            _eval(benv, ln)
    step = [_resolve(benv, t, widths[i])[0] for i, t in enumerate(next_tok)]
    genv = dict(senv)
    for ln in bmap[inner_hdr][0]:
        if not _phi(ln):
            _eval(genv, ln)
    guard = _bool(_resolve(genv, cond, 1))
    decls = ([f"(declare-const {p[0]} {'Bool' if p[1] == 'bool' else f'(_ BitVec {p[1][2:]})'})"
              for n, p in penv.items() if n not in phis]
             + [f"(declare-const {s} (_ BitVec {w}))" for s, w in zip(state, widths)])
    return {"widths": widths, "init": init, "guard": guard, "step": step,
            "state": state, "decls": decls}


def validate_nested(z3_bin, ll_before, ll_after, func):
    """Compositional nested-loop equivalence: prove inner transitions equal, then outer-with-UF."""
    try:
        bb = {lab: (l, t) for lab, l, t in _blocks(_function_body(ll_before, func))}
        ba = {lab: (l, t) for lab, l, t in _blocks(_function_body(ll_after, func))}
        hb = _loop_headers(_blocks(_function_body(ll_before, func)), bb)
        ha = _loop_headers(_blocks(_function_body(ll_after, func)), ba)
        if len(hb) != 2 or len(ha) != 2 or hb != ha:
            return {"status": "unsupported", "reason": "not a matching doubly-nested loop"}
        outer_hdr, inner_hdr = hb[0], hb[1]
        dom_b = _dominators(_blocks(_function_body(ll_before, func)), bb)
        op, ow, _it, _nt, _lt, _c = _single_body_loop(bb, dom_b, outer_hdr, {})
    except Unsupported as exc:
        return {"status": "unsupported", "reason": str(exc)}

    # 1) INNER equivalence: the inner transitions (init/guard/step) must match.
    ib = _inner_model(ll_before, func, inner_hdr, op, ow, "s")
    ia = _inner_model(ll_after, func, inner_hdr, op, ow, "s")
    if ib["widths"] != ia["widths"]:
        return {"status": "unsupported", "reason": "inner state shape differs"}
    decls = ib["decls"]
    for part, goal in (("inner-init", "(and " + " ".join(f"(= {b} {a})" for b, a in zip(ib["init"], ia["init"])) + ")"),
                       ("inner-guard", f"(= {ib['guard']} {ia['guard']})"),
                       ("inner-step", "(and " + " ".join(f"(= {b} {a})" for b, a in zip(ib["step"], ia["step"])) + ")")):
        if _check(z3_bin, decls, goal)[0] != "proved":
            return {"status": "refuted", "failed": part, "function": func}

    # 2) OUTER equivalence with the inner abstracted as a shared uninterpreted function INNER.
    ob = _outer_abstract(ll_before, func, outer_hdr, inner_hdr)
    oa = _outer_abstract(ll_after, func, outer_hdr, inner_hdr)
    for part, decls2, goal in _outer_obligations(ob, oa):
        if _check_uf(z3_bin, decls2, goal)[0] != "proved":
            return {"status": "refuted", "failed": part, "function": func}
    return {"status": "proved", "function": func, "inner_checked": True, "outer_checked": True}


def _outer_abstract(ll_text, func, outer_hdr, inner_hdr):
    """Outer-loop model with the inner loop replaced by an uninterpreted function INNER applied to
    the inner's live-in (the enclosing state). The outer IV steps normally; the outer accumulator
    (fed from the inner) becomes INNER(state)."""
    body = _function_body(ll_text, func)
    blocks = _blocks(body)
    bmap = {lab: (lines, term) for lab, lines, term in blocks}
    dom = _dominators(blocks, bmap)
    params = dict(_params(ll_text, func))
    penv = {n: (n.lstrip("%").replace(".", "_"), "bool" if w == 1 else f"bv{w}")
            for n, w in params.items()}
    phis, widths, init_tok, next_tok, latch, cond = _single_body_loop(bmap, dom, outer_hdr, penv)
    senv = dict(penv)
    for i, nm in enumerate(phis):
        senv[nm] = (f"o{i}", f"bv{widths[i]}")
    state = [f"o{i}" for i in range(len(phis))]
    init = [_resolve(penv, t, w)[0] for t, w in zip(init_tok, widths)]
    # the outer latch computes the IV step(s); values flowing from the inner are abstracted.
    benv = dict(senv)
    for ln in bmap[latch][0]:
        if not _phi(ln):
            _eval(benv, ln)
    inner_call = "(INNER " + " ".join(state) + ")"
    step = []
    for i, t in enumerate(next_tok):
        # a latch-incoming whose definition is not resolvable from the outer latch comes from the
        # inner loop -> abstract it as INNER(state); otherwise it is an outer computation.
        try:
            step.append(_resolve(benv, t, widths[i])[0])
        except Unsupported:
            step.append(inner_call)
    genv = dict(senv)
    for ln in bmap[outer_hdr][0]:
        if not _phi(ln):
            _eval(genv, ln)
    guard = _bool(_resolve(genv, cond, 1))
    return {"widths": widths, "params": params, "init": init, "guard": guard,
            "step": step, "state": state}


def _outer_obligations(ob, oa):
    if ob["widths"] != oa["widths"] or ob["params"] != oa["params"]:
        return [("outer-shape", [], "false")]
    state, widths, params = ob["state"], ob["widths"], ob["params"]
    decls = ["(declare-fun INNER (" + " ".join(f"(_ BitVec {w})" for w in widths)
             + ") (_ BitVec " + str(widths[-1]) + "))"]
    decls += [f"(declare-const {n.lstrip('%').replace('.', '_')} "
              f"{'Bool' if w == 1 else f'(_ BitVec {w})'})" for n, w in params.items()]
    decls += [f"(declare-const {s} (_ BitVec {w}))" for s, w in zip(state, widths)]
    return [
        ("outer-init", [d for d in decls if "declare-const" in d and not any(s in d for s in state)],
         "(and " + " ".join(f"(= {b} {a})" for b, a in zip(ob["init"], oa["init"])) + ")"),
        ("outer-guard", decls, f"(= {ob['guard']} {oa['guard']})"),
        ("outer-step", decls, f"(=> {ob['guard']} (and "
         + " ".join(f"(= {b} {a})" for b, a in zip(ob["step"], oa["step"])) + "))"),
    ]


def function_names(ll_text):
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)
