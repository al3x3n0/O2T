#!/usr/bin/env python3
"""UNBOUNDED loop equivalence by SIMULATION RELATION (for structurally-different loops).

`loop_induction` proves two loops equivalent when their loop-carried state matches positionally
(R = equality). Transforms that reshape the loop -- reindex the induction variable, rotate the
guard, strength-reduce -- produce a DIFFERENT state, so positional equality no longer holds at each
iteration. The general tool is a simulation relation R(s, t) between the two states, proved
inductive:

    INIT   : R(init_B, init_A)                                  (the entry states are related)
    GUARD  : forall s,t. R(s,t) => (guard_B(s) == guard_A(t))   (related states loop in lockstep)
    STEP   : forall s,t. R(s,t) /\\ guard_B(s) => R(step_B(s), step_A(t))   (R is preserved)
    RESULT : forall s,t. R(s,t) /\\ ~guard_B(s) => result_B(s) == result_A(t)  (exits agree)

If all four are valid, the two loops return equal values for every input and every trip count --
for loops whose iterations no longer line up positionally. R is supplied (a callable building the
SMT predicate from the two state-value lists), since inferring it is transform-specific; a wrong R
or a miscompiled loop fails one obligation with a witness. Positional equivalence (loop_induction)
is the special case R(s,t) = (s == t).
"""

from __future__ import annotations

import re
import subprocess

from o2t.validate.loop_induction import extract_loop
from o2t.validate.mem2reg_ir import Unsupported


def _decls(params, *state_sets):
    out = [f"(declare-const {n.lstrip('%').replace('.', '_')} "
           f"{'Bool' if w == 1 else f'(_ BitVec {w})'})" for n, w in params.items()]
    for names, widths in state_sets:
        out += [f"(declare-const {nm} (_ BitVec {w}))" for nm, w in zip(names, widths)]
    return out


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


def prove_simulation(z3_bin, before, after, relation):
    """Prove `before` and `after` equivalent under simulation relation `relation`. `relation` is a
    callable `(b_state_values, a_state_values) -> smt_bool` -- it is invoked on the symbolic states
    (for GUARD/RESULT), on the INIT expressions, and on the STEP expressions, so no substitution is
    needed. Returns a verdict dict with the failed obligation (if any) and a witness."""
    if before["params"] != after["params"]:
        return {"status": "unsupported", "reason": "different signatures"}
    params = before["params"]
    bstate = (before["state"], before["widths"])
    astate = (after["state"], after["widths"])
    pdecls = _decls(params)
    sdecls = _decls(params, bstate, astate)

    R = relation
    obligations = {
        "init": (pdecls, R(before["init"], after["init"])),
        "guard": (sdecls, f"(=> {R(before['state'], after['state'])} "
                  f"(= {before['guard']} {after['guard']}))"),
        "step": (sdecls, f"(=> (and {R(before['state'], after['state'])} {before['guard']}) "
                 f"{R(before['step'], after['step'])})"),
        "result": (sdecls, f"(=> (and {R(before['state'], after['state'])} (not {before['guard']})) "
                   f"(= {before['result']} {after['result']}))"),
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


def validate_simulation(z3_bin, ll_before, func_before, ll_after, func_after, relation):
    try:
        b = extract_loop(ll_before, func_before, prefix="s")
        a = extract_loop(ll_after, func_after, prefix="t")
    except Unsupported as exc:
        return {"status": "unsupported", "reason": str(exc)}
    out = prove_simulation(z3_bin, b, a, relation)
    out["before"], out["after"] = func_before, func_after
    return out


def mapped_relation(pairs, extra=()):
    """Build a relation from index pairs: each `(b_index, a_index)` means `s[b_index] == t[a_index]`,
    plus optional `extra` `(b_index, a_index)` equalities (e.g. a duplicated/redundant state
    component on one side maps to the same source). Lets a simulation hold across loops whose state
    has a different shape -- as long as every related pair is exactly equal (overflow-safe)."""
    def R(svals, tvals):
        conj = [f"(= {svals[b]} {tvals[a]})" for b, a in pairs]
        conj += [f"(= {svals[b]} {tvals[a]})" for b, a in extra]
        return "(and " + " ".join(conj) + ")"
    return R


def equality_relation(n_states):
    """The positional-equality relation (recovers loop_induction's special case)."""
    def R(svals, tvals):
        return "(and " + " ".join(f"(= {s} {t})" for s, t in zip(svals, tvals)) + ")"
    return R


def _atom_smt(atom, svals, tvals):
    """One inferred atom over the given state-value lists. Equality atoms are `(b_index, a_index)`
    meaning `s[b]==t[a]`; affine atoms are `("affine", b_index, a_index, c, d)` meaning
    `t[a] == c*s[b] + d` (strength reduction: the A-side accumulator equals `c*` a B-side IV plus
    an offset)."""
    if len(atom) == 2:
        b, a = atom
        return f"(= {svals[b]} {tvals[a]})"
    _, b, a, c, d = atom
    return f"(= {tvals[a]} (bvadd (bvmul {c} {svals[b]}) {d}))"


def relation_from_atoms(atoms):
    """A relation built from inferred atoms (equality and/or affine)."""
    def R(svals, tvals):
        if not atoms:
            return "true"
        return "(and " + " ".join(_atom_smt(at, svals, tvals) for at in atoms) + ")"
    return R


def _split_app(expr):
    """`(op a b ...)` -> (op, [args]) respecting nested parens, or None."""
    if not (expr.startswith("(") and expr.endswith(")")):
        return None
    toks, depth, cur = [], 0, ""
    for ch in expr[1:-1]:
        if ch == " " and depth == 0:
            if cur:
                toks.append(cur); cur = ""
        else:
            depth += {"(": 1, ")": -1}.get(ch, 0)
            cur += ch
    if cur:
        toks.append(cur)
    return (toks[0], toks[1:]) if toks else None


_IS_CONST = re.compile(r"\(_ bv\d+ \d+\)|#x[0-9a-fA-F]+|#b[01]+")


def _const_stride(step_expr, var):
    """If `step_expr` is `var + CONST` (a constant-stride accumulator), return CONST, else None."""
    app = _split_app(step_expr)
    if not app or app[0] != "bvadd" or len(app[1]) != 2:
        return None
    a, b = app[1]
    if a == var and _IS_CONST.fullmatch(b):
        return b
    if b == var and _IS_CONST.fullmatch(a):
        return a
    return None


def _const_value(tok):
    """Numeric value of a bitvector constant token, or None."""
    m = re.fullmatch(r"\(_ bv(\d+) \d+\)", tok)
    if m:
        return int(m.group(1))
    if tok.startswith("#x"):
        return int(tok[2:], 16)
    if tok.startswith("#b"):
        return int(tok[2:], 2)
    return None


def _coefficient(K, S, width):
    """The constant `c` with `c*S == K (mod 2^width)`, so an accumulator striding by K tracks
    `c*` an induction variable striding by S. Exact division when S | K; otherwise the modular
    inverse when S is odd (invertible mod 2^width). None if no exact coefficient exists."""
    mod = 1 << width
    if S != 0 and K % S == 0:
        return (K // S) % mod
    if S % 2 == 1:
        return (K * pow(S % mod, -1, mod)) % mod
    return None


def _affine_candidates(before, after):
    """Strength-reduction atoms over CONSTANT-stride variables (any stride, not just unit): for an
    induction variable s_i striding by S in B and an accumulator t_j striding by K in A, propose
    `t_j == c*s_i + d` with `c` the coefficient making the strides line up (`c*S == K`) and
    `d = init_A[j] - c*init_B[i]`. Houdini verifies (inductive by construction) and keeps it only if
    it also holds at entry; unit-coefficient atoms are skipped (they restate an equality)."""
    bstride = {i: _const_value(s) for i in range(len(before["widths"]))
               if (s := _const_stride(before["step"][i], before["state"][i])) is not None}
    astride = {j: _const_value(k) for j in range(len(after["widths"]))
               if (k := _const_stride(after["step"][j], after["state"][j])) is not None}
    out = []
    for i, S in bstride.items():
        w = before["widths"][i]
        for j, K in astride.items():
            if after["widths"][j] != w or S is None or K is None:
                continue
            c = _coefficient(K, S, w)
            if c is None or c == 1:
                continue                     # unit coefficient is just an equality (already a candidate)
            ctok = f"(_ bv{c} {w})"
            d = f"(bvsub {after['init'][j]} (bvmul {ctok} {before['init'][i]}))"
            out.append(("affine", i, j, ctok, d))
    return out


def _valid(z3_bin, decls, goal):
    return _check(z3_bin, decls, goal)[0] == "proved"


def infer_relation(z3_bin, before, after):
    """INFER a simulation relation automatically (Houdini over component equalities). Candidate
    atoms are every equality `s[i] == t[j]` (matching width) that holds at entry; we then drop any
    atom the loop step does not preserve under the current conjunction, until a fixpoint. The
    survivors are the strongest inductive relation expressible as component equalities -- which
    covers identity, permutation, and redundant/duplicated induction-variable reshapes."""
    bw, aw = before["widths"], after["widths"]
    bstate, astate = before["state"], after["state"]
    pdecls = _decls(before["params"])
    sdecls = _decls(before["params"], (bstate, bw), (astate, aw))

    # candidate atoms: component equalities + strength-reduction affine atoms, kept only if they
    # hold at the loop entry.
    candidates = [(i, j) for i, wi in enumerate(bw) for j, wj in enumerate(aw) if wi == wj]
    candidates += _affine_candidates(before, after)
    atoms = [at for at in candidates
             if _valid(z3_bin, pdecls, _atom_smt(at, before["init"], after["init"]))]
    changed = True
    while changed:
        changed = False
        R = relation_from_atoms(atoms)(bstate, astate)
        survivors = []
        for at in atoms:
            goal = f"(=> (and {R} {before['guard']}) {_atom_smt(at, before['step'], after['step'])})"
            if _valid(z3_bin, sdecls, goal):
                survivors.append(at)
            else:
                changed = True
        atoms = survivors
    return atoms


def validate_simulation_auto(z3_bin, ll_before, func_before, ll_after, func_after):
    """Extract both loops, INFER the simulation relation, and discharge -- no hand-given R. Returns
    the verdict plus the inferred atoms."""
    try:
        b = extract_loop(ll_before, func_before, prefix="s")
        a = extract_loop(ll_after, func_after, prefix="t")
    except Unsupported as exc:
        return {"status": "unsupported", "reason": str(exc)}
    atoms = infer_relation(z3_bin, b, a)
    out = prove_simulation(z3_bin, b, a, relation_from_atoms(atoms))
    out["inferred_atoms"] = atoms
    out["before"], out["after"] = func_before, func_after
    return out
