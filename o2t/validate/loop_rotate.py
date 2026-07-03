#!/usr/bin/env python3
"""UNBOUNDED validation of loop-rotate (guard-motion) -- the case lockstep simulation can't reach.

Loop-rotate turns a guard-at-top `while` loop into a rotated do-while: a PRE-GUARD at entry decides
whether to enter, the body's guard moves to the BOTTOM (checking the NEXT state), and the result
flows through an lcssa phi. The loops check the same guards on the same states but with a structural
off-by-one, so a plain `(init,guard,step,result)` lockstep proof cannot express it.

We reconstruct a CANONICAL guard-on-current loop model from the rotated CFG and SELF-VERIFY it
against the actual emitted instructions: the BOTTOM guard must equal the canonical guard at the
STEPPED state (`g(step(s))`), and the lcssa LOOP value must equal the result at the stepped state.
Those z3 checks tie the reconstruction to the real bottom-guard and lcssa, so a miscompiled rotation
fails a check and is declined -- never falsely proved. The verified canonical model is then proved
equivalent to the original loop by the simulation machinery with automatic relation inference (which
handles the permuted/reshaped state). Single natural loop, acyclic body; other shapes are declined.
"""

from __future__ import annotations

import re

from o2t.validate.loop_induction import _eval, _resolve
from o2t.validate.mem2reg_ir import _blocks, _params, _function_body, Unsupported
from o2t.validate import loop_simulation as sim
from o2t.validate.loop_induction import extract_loop


def _phi(line):
    pm = re.fullmatch(r"(%[\w.]+)\s*=\s*phi\s+i(\d+)\s+(.+)", line)
    if not pm:
        return None
    arms = re.findall(r"\[\s*([^][,]+?)\s*,\s*%([\w.]+)\s*\]", pm.group(3))
    return pm.group(1), int(pm.group(2)), arms


def _bool(term, sort):
    return term if sort == "bool" else f"(= {term} (_ bv1 1))"


def _subst(expr, frm, to):
    return re.sub(r"(?<![\w.])" + re.escape(frm) + r"(?![\w.])", to, expr)


def extract_rotated_model(ll_text, func, prefix="t"):
    """Reconstruct a canonical (init, guard, step, result) model from rotated IR, plus self-check
    obligations (SMT goals that must be valid for the reconstruction to be faithful)."""
    body = _function_body(ll_text, func)
    if body is None:
        raise Unsupported(f"function {func} not found")
    blocks = _blocks(body)
    bmap = {lab: (lines, term) for lab, lines, term in blocks}
    entry_lab = blocks[0][0]

    header = None
    for lab, lines, _t in blocks:
        if any((a := _phi(ln)) and any(b == lab for _v, b in a[2]) for ln in lines):
            header = lab
            break
    if header is None:
        raise Unsupported("no rotated loop header (self-phi)")
    hlines, hterm = bmap[header]
    gm = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%[\w.]+,\s+label\s+%[\w.]+", hterm)
    if not gm:
        raise Unsupported("loop header not a conditional branch")

    params = _params(ll_text, func)
    penv = {n: (n.lstrip("%").replace(".", "_"), "bool" if w == 1 else f"bv{w}")
            for n, w in params.items()}

    phis, widths, init_tok, nexts = [], [], [], []
    for ln in hlines:
        a = _phi(ln)
        if not a:
            continue
        name, w, incs = a
        latch = next((v for v, b in incs if b == header), None)
        pre = next((v for v, b in incs if b != header), None)
        if latch is None or pre is None:
            raise Unsupported("header phi without preheader+latch")
        phis.append(name); widths.append(w); init_tok.append(pre); nexts.append(latch)

    senv = dict(penv)
    for i, name in enumerate(phis):
        senv[name] = (f"{prefix}{i}", f"bv{widths[i]}")
    state = [f"{prefix}{i}" for i in range(len(phis))]
    init = [_resolve(penv, t, w)[0] for t, w in zip(init_tok, widths)]

    benv = dict(senv)
    for ln in hlines:
        if not re.search(r"=\s*phi\b", ln):
            _eval(benv, ln)
    step = [_resolve(benv, nv, widths[i])[0] for i, nv in enumerate(nexts)]
    bg = _resolve(benv, gm.group(1).rstrip(","), 1)
    bottom_guard = _bool(bg[0], bg[1])          # = g(step(s)) as a function of current state

    # pre-guard: g at the entry state.
    eenv = dict(penv)
    for ln in bmap[entry_lab][0]:
        _eval(eenv, ln)
    em = re.fullmatch(r"br\s+i1\s+(\S+),\s+label\s+%[\w.]+,\s+label\s+%[\w.]+", bmap[entry_lab][1])
    if not em:
        raise Unsupported("entry not a pre-guard branch")
    pg = _resolve(eenv, em.group(1).rstrip(","), 1)
    pre_guard = _bool(pg[0], pg[1])

    # induction variable: the phi whose init value appears in the pre-guard; canonical guard is the
    # pre-guard with that init replaced by the IV's current placeholder.
    ivs = [i for i in range(len(phis)) if init[i] in pre_guard and init[i] not in ("", "true", "false")]
    if not ivs:
        raise Unsupported("could not locate the induction variable in the pre-guard")
    iv = ivs[0]
    guard = _subst(pre_guard, init[iv], state[iv])

    # exit/lcssa: skip value (entry edge) and loop value (loop-exit edge), resolved to the state.
    exit_lab = next((lab for lab, _l, term in blocks if re.fullmatch(r"ret\s+.*", term)), None)
    if exit_lab is None:
        raise Unsupported("no return block")
    # resolve names defined on the loop-exit critical path (single-incoming phis) down to header vals.
    resolve_env = dict(benv)
    for lab, lines, _t in blocks:
        if lab in (entry_lab, header):
            continue
        for ln in lines:
            a = _phi(ln)
            if a and len(a[2]) == 1:
                resolve_env[a[0]] = _resolve(resolve_env, a[2][0][0], a[1])
            elif "=" in ln and not a:
                _eval(resolve_env, ln)
    skip_val = loop_val = None
    for ln in bmap[exit_lab][0]:
        a = _phi(ln)
        if a and len(a[2]) == 2:
            for v, b in a[2]:
                if b == entry_lab:
                    skip_val = v
                else:
                    loop_val = v
    if skip_val is None or loop_val is None:
        raise Unsupported("no lcssa skip/loop incoming at exit")

    # result phi: the one whose preheader init equals the lcssa skip value.
    res = [i for i in range(len(phis)) if init_tok[i] == skip_val]
    if not res:
        raise Unsupported("could not match the lcssa skip value to a loop state")
    ridx = res[0]
    loop_val_expr = _resolve(resolve_env, loop_val, widths[ridx])[0]

    decls_s = [f"(declare-const {n.lstrip('%').replace('.', '_')} "
               f"{'Bool' if w == 1 else f'(_ BitVec {w})'})" for n, w in params.items()]
    decls_s += [f"(declare-const {s} (_ BitVec {w})) " for s, w in zip(state, widths)]
    guard_at_step = guard
    for i in range(len(state)):
        guard_at_step = _subst(guard_at_step, state[i], step[i])
    checks = [
        ("bottom-guard", decls_s, f"(= {bottom_guard} {guard_at_step})"),
        ("loop-result", decls_s, f"(= {loop_val_expr} {step[ridx]})"),
    ]
    model = {"widths": widths, "params": params, "init": init, "guard": guard,
             "step": step, "result": state[ridx], "state": state}
    return model, checks


def validate_rotate(z3_bin, original_ll, opt_text, func):
    """Validate one real loop-rotate: reconstruct + self-verify the rotated model, then prove it
    equivalent to the original loop (automatic relation inference). Returns a verdict dict."""
    try:
        rot, checks = extract_rotated_model(opt_text, func, prefix="t")
        orig = extract_loop(original_ll, func, prefix="s")
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    for name, decls, goal in checks:
        if sim._check(z3_bin, decls, goal)[0] != "proved":
            return {"status": "unsupported", "function": func,
                    "reason": f"rotated self-check failed: {name}"}
    atoms = sim.infer_relation(z3_bin, orig, rot)
    out = sim.prove_simulation(z3_bin, orig, rot, sim.relation_from_atoms(atoms))
    out["function"], out["inferred_atoms"], out["self_checked"] = func, atoms, [c[0] for c in checks]
    return out


def run_rotate(src_text, opt_bin="opt"):
    from o2t.validate.scalar_ir import run_passes
    return run_passes(src_text, "loop-rotate", opt_bin)


def function_names(ll_text):
    return re.findall(r"define\b[^@]*@(\w+)\s*\(", ll_text)
