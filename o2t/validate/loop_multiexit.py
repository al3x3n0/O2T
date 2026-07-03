#!/usr/bin/env python3
"""UNBOUNDED equivalence for MULTI-EXIT loops (a loop with several exit edges / in-body breaks).

The induction provers so far assume one guard at the header. A multi-exit loop leaves through
several edges -- a header guard plus in-body `break`s -- each with its own condition and returned
value. We model it as an ORDERED list of exits `(fire(s), result(s))` plus a continue-`step(s)`
applied when no exit fires, recovered by symbolically walking the loop region from the header and
accumulating the path condition to each exit edge and to the latch.

Two such loops are proved equivalent for ALL trip counts by induction with positional relation R:

    INIT          : init_B == init_A
    DECISION      : forall s. R(s) => fire_k_B(s) == fire_k_A(s)            for every exit k
    RESULT        : forall s. R(s) /\\ fire_k_B(s) => result_k_B == result_k_A   for every exit k
    STEP          : forall s. R(s) /\\ (no exit fires) => step_B(s) == step_A(s)

If all hold, both loops take the same exit at the same iteration and return the same value -- so a
transform that drops a break, swaps an exit value, or changes the body is refuted with a witness.
Linear loop body with exit branches (no internal merges); other shapes are declined.
"""

from __future__ import annotations

import re

from o2t.validate.loop_induction import _eval, _resolve
from o2t.validate.mem2reg_ir import _blocks, _params, _function_body, Unsupported


def _phi(line):
    pm = re.fullmatch(r"(%[\w.]+)\s*=\s*phi\s+i(\d+)\s+(.+)", line)
    if not pm:
        return None
    arms = re.findall(r"\[\s*([^][,]+?)\s*,\s*%([\w.]+)\s*\]", pm.group(3))
    return pm.group(1), int(pm.group(2)), arms


def _bool(t):
    return t[0] if t[1] == "bool" else f"(= {t[0]} (_ bv1 1))"


def extract_multiexit(ll_text, func, prefix="s"):
    """Walk the loop region and recover (state, init, exits=[(fire,result)], step)."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    blocks = _blocks(body)
    bmap = {lab: (lines, term) for lab, lines, term in blocks}
    is_ret = {lab: bool(re.fullmatch(r"ret\s+.*", term)) for lab, _l, term in blocks}

    header = next((lab for lab, lines, _t in blocks
                   if any(_phi(ln) for ln in lines)), None)
    if header is None:
        raise Unsupported("no loop header (phi)")
    hlines, _ht = bmap[header]

    params = _params(ll_text, func)
    penv = {n: (n.lstrip("%").replace(".", "_"), "bool" if w == 1 else f"bv{w}")
            for n, w in params.items()}
    phis, widths, init_tok, latch_tok, latch_blk = [], [], [], [], None
    for ln in hlines:
        a = _phi(ln)
        if not a:
            continue
        name, w, incs = a
        # the latch is the back-edge source -- reachable FROM the header; the other incoming is the
        # preheader (init), which the header does not reach.
        latch_inc = [(v, b) for v, b in incs if _reaches(bmap, is_ret, header, b)]
        pre_inc = [(v, b) for v, b in incs if (v, b) not in latch_inc]
        if not latch_inc or not pre_inc:
            raise Unsupported("header phi without preheader+latch")
        phis.append(name); widths.append(w)
        init_tok.append(pre_inc[0][0]); latch_tok.append(latch_inc[0][0])
        latch_blk = latch_inc[0][1]

    senv = dict(penv)
    for i, name in enumerate(phis):
        senv[name] = (f"{prefix}{i}", f"bv{widths[i]}")
    state = [f"{prefix}{i}" for i in range(len(phis))]
    init = [_resolve(penv, t, w)[0] for t, w in zip(init_tok, widths)]

    # symbolic walk from the header to the latch, collecting exit edges.
    env = dict(senv)
    exits = []
    pc = "true"
    cur = header
    seen = set()
    while True:
        if cur in seen:
            raise Unsupported("non-linear loop body (internal merge)")
        seen.add(cur)
        lines, term = bmap[cur]
        for ln in lines:
            if not _phi(ln):
                _eval(env, ln)
        if cur == latch_blk:
            step = [_resolve(env, t, widths[i])[0] for i, t in enumerate(latch_tok)]
            stay = pc
            break
        bm = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%([\w.]+),\s+label\s+%([\w.]+)", term)
        um = re.fullmatch(r"br\s+label\s+%([\w.]+)", term)
        if um:
            cur = um.group(1)
            continue
        if not bm:
            raise Unsupported(f"unsupported terminator {term!r}")
        cond = _bool(_resolve(env, bm.group(1).rstrip(","), 1))
        t_lab, e_lab = bm.group(2), bm.group(3)
        t_exit, e_exit = is_ret.get(t_lab, False), is_ret.get(e_lab, False)
        if t_exit == e_exit:
            raise Unsupported("branch is not a single loop exit edge")
        if t_exit:                                   # taken -> exit, fallthrough -> in-loop
            exits.append((f"(and {pc} {cond})", _exit_result(bmap, t_lab, env, senv)))
            pc, cur = f"(and {pc} (not {cond}))", e_lab
        else:                                        # fallthrough -> exit
            exits.append((f"(and {pc} (not {cond}))", _exit_result(bmap, e_lab, env, senv)))
            pc, cur = f"(and {pc} {cond})", t_lab

    return {"widths": widths, "params": params, "init": init, "exits": exits,
            "step": step, "stay": stay, "state": state}


def _exit_result(bmap, lab, env, senv):
    elines, eterm = bmap[lab]
    eenv = dict(env)
    for ln in elines:
        if not _phi(ln):
            _eval(eenv, ln)
    rm = re.fullmatch(r"ret\s+i(\d+)\s+(\S+)", eterm)
    if not rm:
        raise Unsupported("exit does not return a scalar")
    return _resolve(eenv, rm.group(2), int(rm.group(1)))[0]


def _reaches(bmap, is_ret, src, target):
    seen, stack = set(), [src]
    while stack:
        b = stack.pop()
        if b == target:
            return True
        if b in seen or is_ret.get(b, False) or b not in bmap:
            continue
        seen.add(b)
        for t in re.findall(r"label\s+%([\w.]+)", bmap[b][1]):
            stack.append(t)
    return False


def _decls(params, state, widths):
    out = [f"(declare-const {n.lstrip('%').replace('.', '_')} "
           f"{'Bool' if w == 1 else f'(_ BitVec {w})'})" for n, w in params.items()]
    out += [f"(declare-const {s} (_ BitVec {w}))" for s, w in zip(state, widths)]
    return out


def prove_multiexit_equiv(z3_bin, before, after):
    """Prove two multi-exit loops equivalent by induction over the ordered exits + the step."""
    from o2t.validate.loop_simulation import _check
    if before["widths"] != after["widths"] or before["params"] != after["params"] \
            or len(before["exits"]) != len(after["exits"]):
        return {"status": "unsupported", "reason": "loop shape mismatch"}
    state, widths, params = before["state"], before["widths"], before["params"]
    # both models use the same state placeholders (positional R = equality).
    after = _rename(after, before["state"])
    pd = _decls(params, [], [])
    sd = _decls(params, state, widths)

    parts = {}
    init_goal = "(and " + " ".join(f"(= {b} {a})"
                for b, a in zip(before["init"], after["init"])) + ")"
    parts["init"] = _check(z3_bin, pd, init_goal)[0]
    if parts["init"] != "proved":
        return {"status": "refuted", "failed": "init", "parts": parts}

    for k, ((fb, rb), (fa, ra)) in enumerate(zip(before["exits"], after["exits"])):
        d = _check(z3_bin, sd, f"(= {fb} {fa})")[0]
        parts[f"decision{k}"] = d
        if d != "proved":
            return {"status": "refuted", "failed": f"decision{k}", "parts": parts}
        r = _check(z3_bin, sd, f"(=> {fb} (= {rb} {ra}))")[0]
        parts[f"result{k}"] = r
        if r != "proved":
            return {"status": "refuted", "failed": f"result{k}", "parts": parts}

    step_goal = (f"(=> {before['stay']} (and "
                 + " ".join(f"(= {b} {a})" for b, a in zip(before["step"], after["step"])) + "))")
    parts["step"] = _check(z3_bin, sd, step_goal)[0]
    if parts["step"] != "proved":
        return {"status": "refuted", "failed": "step", "parts": parts}
    return {"status": "proved", "parts": parts}


def _rename(model, names):
    """Re-express a model's expressions over a different set of state placeholders (positional)."""
    out = dict(model)
    sub = {old: new for old, new in zip(model["state"], names)}

    def r(expr):
        for old, new in sub.items():
            expr = re.sub(r"(?<![\w.])" + re.escape(old) + r"(?![\w.])", new, expr)
        return expr
    out["init"] = [r(e) for e in model["init"]]
    out["step"] = [r(e) for e in model["step"]]
    out["stay"] = r(model["stay"])
    out["exits"] = [(r(f), r(res)) for f, res in model["exits"]]
    out["state"] = list(names)
    return out


def validate_multiexit(z3_bin, ll_before, func_before, ll_after, func_after):
    try:
        b = extract_multiexit(ll_before, func_before, prefix="s")
        a = extract_multiexit(ll_after, func_after, prefix="a")
    except Unsupported as exc:
        return {"status": "unsupported", "reason": str(exc)}
    out = prove_multiexit_equiv(z3_bin, b, a)
    out["before"], out["after"], out["exits"] = func_before, func_after, len(b["exits"])
    return out


def function_names(ll_text):
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)
