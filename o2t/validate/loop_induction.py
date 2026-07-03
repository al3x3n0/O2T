#!/usr/bin/env python3
"""UNBOUNDED loop equivalence by induction over the loop-carried state.

Bounded validation (loop_cfg_ir) only covers constant trip counts. This proves two structurally
matching loops equivalent for ALL trip counts -- including non-termination -- via induction, with no
unrolling. A natural loop is modeled as a transition system over its loop-carried state s (the header
phi values): an `init` (entry values), a `guard(s)` (the header branch condition), a `step(s)` (the
next state computed by the body), and a `result(s)` (the value returned on exit). Two loops B and A
are equivalent if, relating their states positionally by equality R:

    INIT    : B.init == A.init                                  (states agree on entry)
    GUARD   : forall s, B.guard(s) == A.guard(s)                (they loop the same number of times)
    STEP    : forall s, B.guard(s) => B.step(s) == A.step(s)    (each iteration agrees)
    RESULT  : forall s, not B.guard(s) => B.result(s) == A.result(s)   (the exit value agrees)

If all four are valid (Z3), then by induction the loops return equal values for every input and
every trip count. A transform that changes the body's step or the exit value is refuted with a
concrete state witness. Supported: one natural loop with a single header (phis) and a single
body/latch block, integer state, scalar body ops + icmp; other shapes are declined `unsupported`.

STEP and RESULT are discharged as Alive2 REFINEMENT, not raw equality, so a body fold that ADDS a
poison-generating flag (nsw/nuw/exact/disjoint) or a div/rem-by-zero (UB) is refuted, while one that
only DROPS a flag still proves -- instcombine-in-loop is exactly such a flag-rewriting pass. State
placeholders are treated as defined each iteration (loop-carried poison state is out of scope: a
conservative bound, never a false proof).
"""

from __future__ import annotations

import re
import subprocess

from o2t.formal_ir import smt_and, smt_or
from o2t.validate.scalar_ir import _BIN, _ICMP, _const, _function_body, _own_poison, _own_ub
from o2t.validate.mem2reg_ir import _blocks, _params, Unsupported


def _resolve(env, tok, width):
    """An operand -> (term, sort, poison). Env entries seeded as 2-tuples (params) are padded with a
    defined poison so this validator's poison-aware path and the sibling loop modules share `_eval`."""
    tok = tok.strip().rstrip(",")
    if tok in env:
        v = env[tok]
        return v if len(v) == 3 else (v[0], v[1], "false")
    if re.fullmatch(r"-?\d+", tok):
        return _const(int(tok), width), f"bv{width}", "false"
    if tok in ("true", "false"):
        return tok, "bool", "false"
    raise Unsupported(f"operand {tok!r}")


def _eval(env, line, ub=None):
    """Evaluate one non-phi, non-terminator instruction into env as (term, sort, poison). When `ub`
    is a list, any undefined-behaviour condition the instruction introduces (div/rem by zero, signed
    INT_MIN/-1, poison divisor) is appended to it; sibling callers that pass no `ub` keep the prior
    value-only behaviour. Poison is always tracked so a body fold that adds a poison-generating flag
    is caught by the refinement obligation."""
    m = re.fullmatch(r"(%[\w.]+)\s*=\s*(.+)", line)
    if not m:
        raise Unsupported(line)
    dst, rhs = m.group(1), m.group(2)
    im = re.fullmatch(r"icmp\s+(\w+)\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if im and im.group(1) in _ICMP:
        w = int(im.group(2))
        at, _, ap = _resolve(env, im.group(3), w)
        bt, _, bp = _resolve(env, im.group(4), w)
        env[dst] = (_ICMP[im.group(1)].format(a=at, b=bt), "bool", smt_or([ap, bp]))
        return
    sm = re.fullmatch(r"select\s+i1\s+(\S+),\s+i(\d+)\s+(\S+),\s+i\d+\s+(\S+)", rhs)
    if sm:
        w = int(sm.group(2))
        ct, cs, cp = _resolve(env, sm.group(1), 1)
        cterm = ct if cs == "bool" else f"(= {ct} {_const(1, 1)})"
        tt, _, tp = _resolve(env, sm.group(3), w)
        ft, _, fp = _resolve(env, sm.group(4), w)
        arm = tp if tp == fp else f"(ite {cterm} {tp} {fp})"
        env[dst] = (f"(ite {cterm} {tt} {ft})", f"bv{w}", smt_or([cp, arm]))
        return
    bm = re.fullmatch(r"(\w+)((?:\s+(?:nsw|nuw|exact|disjoint))*)\s+i(\d+)\s+(\S+),\s+(\S+)", rhs)
    if bm and bm.group(1) in _BIN:
        name, flags, w = bm.group(1), re.findall(r"nsw|nuw|exact|disjoint", bm.group(2)), int(bm.group(3))
        at, _, ap = _resolve(env, bm.group(4), w)
        bt, _, bp = _resolve(env, bm.group(5), w)
        op = _BIN[name]
        env[dst] = (f"({op} {at} {bt})", f"bv{w}", smt_or([ap, bp, _own_poison(name, op, flags, at, bt, w)]))
        if ub is not None:
            ub.append(_own_ub(name, at, bt, w))
            if name in ("udiv", "sdiv", "urem", "srem"):
                ub.append(bp)               # a poison divisor is UB
        return
    raise Unsupported(rhs)


def extract_loop(ll_text, func, prefix="s", header=None, free=None):
    """Model one natural loop as (state_widths, init, guard, step, result), every expression over
    canonical state placeholders {prefix}0..{prefix}{k-1} and the function parameters. `header`
    picks which loop to extract (default: the first phi block); `free` adds extra symbolic inputs
    (name -> width) treated as parameters -- e.g. an enclosing loop's variables for a nested loop."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    blocks = _blocks(body)
    order = {lab: i for i, (lab, _, _) in enumerate(blocks)}
    bmap = {lab: (lines, term) for lab, lines, term in blocks}
    if header is None:
        header = next((lab for lab, lines, _ in blocks
                       if any(re.search(r"=\s*phi\b", ln) for ln in lines)), None)
    if header is None or header not in bmap:
        raise Unsupported("no loop header (phi)")
    hlines, hterm = bmap[header]
    htargets = re.findall(r"label\s+%([\w.]+)", hterm)
    gm = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%([\w.]+),\s+label\s+%([\w.]+)", hterm)
    if not gm:
        raise Unsupported("header is not a conditional branch")
    cont, exit_lbl = gm.group(2), gm.group(3)
    # the continue target must branch back to the header (single body/latch block).
    if cont not in bmap or header not in re.findall(r"label\s+%([\w.]+)", bmap[cont][1]):
        raise Unsupported("not a single-body natural loop")

    params = dict(_params(ll_text, func))
    params.update(free or {})                    # extra symbolic inputs (e.g. enclosing-loop vars)
    penv = {n: (n.lstrip("%").replace(".", "_"), "bool" if w == 1 else f"bv{w}")
            for n, w in params.items()}

    phis, widths, inits, nexts = [], [], [], []
    for ln in hlines:
        pm = re.fullmatch(r"(%[\w.]+)\s*=\s*phi\s+i(\d+)\s+(.+)", ln)
        if not pm:
            continue
        arms = re.findall(r"\[\s*([^][,]+?)\s*,\s*%([\w.]+)\s*\]", pm.group(3))
        if len(arms) != 2:
            raise Unsupported("header phi without exactly two incomings")
        (v0, b0), (v1, b1) = arms
        init_v, next_v = (v0, v1) if order[b0] < order[b1] else (v1, v0)
        phis.append(pm.group(1)); widths.append(int(pm.group(2)))
        inits.append((init_v, int(pm.group(2)))); nexts.append(next_v)

    # state placeholders s0..s{k-1}; init expressions over params only.
    senv = dict(penv)
    for i, name in enumerate(phis):
        senv[name] = (f"{prefix}{i}", f"bv{widths[i]}")
    init = [_resolve(penv, v, w)[0] for v, w in inits]

    # guard: evaluate the header's non-phi instructions over the state. Header ops execute every
    # iteration, so their UB folds into the step obligation.
    step_ub = []
    genv = dict(senv)
    for ln in hlines:
        if re.search(r"=\s*phi\b", ln):
            continue
        _eval(genv, ln, step_ub)
    guard = _resolve(genv, gm.group(1), 1)
    guard_t = guard[0] if guard[1] == "bool" else f"(= {guard[0]} {_const(1, 1)})"

    # step: evaluate the body/latch; next state = the latch incoming expressions, with the poison of
    # each next-state expression and the iteration's UB tracked for the refinement obligation.
    benv = dict(senv)
    for ln in bmap[cont][0]:
        _eval(benv, ln, step_ub)
    resolved_step = [_resolve(benv, nv, widths[i]) for i, nv in enumerate(nexts)]
    step = [r[0] for r in resolved_step]
    step_poison = [r[2] for r in resolved_step]

    # result: the value returned in the exit block, over the state (resolve a single-incoming
    # lcssa phi to the state value).
    result_ub = []
    eenv = dict(senv)
    elines, eterm = bmap[exit_lbl]
    for ln in elines:
        pm = re.fullmatch(r"(%[\w.]+)\s*=\s*phi\s+i(\d+)\s+\[\s*(\S+),\s*%[\w.]+\s*\]", ln)
        if pm:
            eenv[pm.group(1)] = _resolve(eenv, pm.group(3), int(pm.group(2)))
        else:
            _eval(eenv, ln, result_ub)
    rm = re.fullmatch(r"ret\s+i(\d+)\s+(\S+)", eterm)
    if not rm:
        raise Unsupported("exit block does not return a scalar")
    rterm, _, result_poison = _resolve(eenv, rm.group(2), int(rm.group(1)))

    return {"widths": widths, "params": params, "init": init,
            "guard": guard_t, "step": step, "result": rterm,
            "step_poison": step_poison, "step_ub": smt_or(step_ub),
            "result_poison": result_poison, "result_ub": smt_or(result_ub),
            "state": [f"{prefix}{i}" for i in range(len(widths))]}


def _check(z3_bin, decls, goal):
    smt = "\n".join(["(set-logic QF_BV)", *decls,
                     f"(assert (not {goal}))", "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "unsat":
        return "proved", ""
    if head == "sat":
        return "refuted", out
    return "error", head


def prove_loop_equiv(z3_bin, before, after):
    """Prove two loop models equivalent by induction (init/guard/step/result). Returns a verdict."""
    if before["widths"] != after["widths"] or before["params"] != after["params"]:
        return {"status": "unsupported", "reason": "loop state/signature mismatch (non-isomorphic)"}
    pdecls = [f"(declare-const {n.lstrip('%').replace('.', '_')} "
              f"{'Bool' if w == 1 else f'(_ BitVec {w})'})" for n, w in before["params"].items()]
    sdecls = [f"(declare-const s{i} (_ BitVec {w}))" for i, w in enumerate(before["widths"])]

    def _refine(sp, tp, sv, tv):
        # src_poison OR (NOT tgt_poison AND src == tgt): the target value refines the source.
        return smt_or([sp, smt_and([f"(not {tp})", f"(= {sv} {tv})"])])

    # STEP/RESULT use Alive2 refinement, not raw equality, so a body fold that ADDS a poison flag
    # (nsw/nuw/exact/disjoint) or a div/rem-by-zero is refuted, while one that only DROPS a flag still
    # proves. (State placeholders are treated as defined each iteration: loop-carried poison state is
    # out of scope -- a conservative bound, never a false proof, documented as a limitation.)
    step_refine = smt_and([_refine(sp, tp, b, a) for sp, tp, b, a
                           in zip(before["step_poison"], after["step_poison"],
                                  before["step"], after["step"])])
    step_goal = f"(=> {before['guard']} " + \
        smt_and([f"(=> (not {before['step_ub']}) " +
                 smt_and([f"(not {after['step_ub']})", step_refine]) + ")"]) + ")"
    result_goal = f"(=> (not {before['guard']}) " + \
        smt_and([f"(=> (not {before['result_ub']}) " +
                 smt_and([f"(not {after['result_ub']})",
                          _refine(before["result_poison"], after["result_poison"],
                                  before["result"], after["result"])]) + ")"]) + ")"

    obligations = {
        "init": (pdecls, "(and " + " ".join(f"(= {b} {a})"
                 for b, a in zip(before["init"], after["init"])) + ")"),
        "guard": (pdecls + sdecls, f"(= {before['guard']} {after['guard']})"),
        "step": (pdecls + sdecls, step_goal),
        "result": (pdecls + sdecls, result_goal),
    }
    parts = {}
    for name, (decls, goal) in obligations.items():
        status, model = _check(z3_bin, decls, goal)
        parts[name] = status
        if status == "refuted":
            return {"status": "refuted", "failed": name, "witness": model, "parts": parts}
        if status == "error":
            return {"status": "error", "failed": name, "parts": parts}
    return {"status": "proved", "parts": parts}


def validate_loop_equiv(z3_bin, before_ll, after_ll, func):
    try:
        b = extract_loop(before_ll, func)
        a = extract_loop(after_ll, func)
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    out = prove_loop_equiv(z3_bin, b, a)
    out["function"] = func
    return out
