#!/usr/bin/env python3
"""SCEV-backed LLVM-IR loop frontend -- replaces regex PHI-chasing with LLVM's own parser.

The fragile part of the .ll miners was reconstructing loop recurrences from raw IR with
regex + brace counting (rotation, latch detection, temporary chains, GEP all broke it).
This instead asks LLVM itself: `opt -passes='print<scalar-evolution>'` runs the authoritative
ScalarEvolution analysis and prints every loop value as an add-recurrence `{start,+,step}<L>`.
We parse THAT -- a small, fixed, well-defined printer grammar -- not arbitrary IR.

A chained AddRec is Newton's forward difference: `{a0,+,a1,+,a2}` evaluated at iteration i is
  f(i) = a0*C(i,0) + a1*C(i,1) + a2*C(i,2)
so the per-iteration state is recovered exactly:
  init  = f(0)            = a0
  delta = f(i+1) - f(i)   = a1 + a2*i          (the tail AddRec {a1,+,a2} at i)
For len<=3 (affine/quadratic value => affine delta) this needs no division. Length-4+
(cubic value => quadratic delta with a /2) is DECLINED -- honest skip, matching the
"affine/quadratic deltas only" scope. The result is the same (name, init, delta) tuple the
relational prover consumes, so this is a drop-in, parser-agnostic-prover frontend.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_FALLBACK_OPT = "/opt/homebrew/opt/llvm@18/bin/opt"


def find_opt(opt_bin="opt"):
    return shutil.which(opt_bin) or (_FALLBACK_OPT if Path(_FALLBACK_OPT).exists() else None)


def run_scev(ll_text, opt_bin="opt"):
    opt = find_opt(opt_bin)
    if opt is None:
        return None
    proc = subprocess.run([opt, "-passes=print<scalar-evolution>", "-disable-output", "-"],
                          input=ll_text, capture_output=True, text=True)
    # The SCEV printer writes to stderr.
    return proc.stderr + proc.stdout


def sanitize(tok):
    """`%i.next` / `%1` -> a valid identifier; digit-led SSA names get a 'v' prefix."""
    name = tok.lstrip("%").replace(".", "_")
    return "v" + name if name[:1].isdigit() else name


# --- SCEV expression parser (a small recursive-descent over the printer's grammar) ------
_TOK = re.compile(r"\s*(\{|\}|\(|\)|,\+,|,|\+|\*|/u|smax|smin|umax|umin|"
                  r"trunc|zext|sext|to|<[^>]*>|i\d+|%[\w.]+|-?\d+)")


def _tokenize(s):
    toks, i = [], 0
    while i < len(s):
        m = _TOK.match(s, i)
        if not m:
            break
        toks.append(m.group(1))
        i = m.end()
    return toks


def c(value):
    return {"op": "bvconst", "value": value}


def v(name):
    return {"op": "var", "name": name}


def op(o, *a):
    return {"op": o, "args": list(a)}


class _Decline(Exception):
    pass


class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expr(self):
        tok = self.peek()
        if tok == "{":
            return self.addrec()
        if tok == "(":
            return self.paren()
        return self.atom()

    def atom(self):
        tok = self.next()
        if re.fullmatch(r"-?\d+", tok):
            return c(int(tok))
        if tok.startswith("%"):
            return v(sanitize(tok))
        raise _Decline(tok)  # smax/trunc/zext/type/... -- not modeled

    def addrec(self):
        self.next()  # '{'
        ops = [self.expr()]
        while self.peek() == ",+,":
            self.next()
            ops.append(self.expr())
        if self.peek() != "}":
            raise _Decline("addrec")
        self.next()  # '}'
        while self.peek() and self.peek().startswith("<"):  # flags + <%loop>
            self.next()
        return ("addrec", ops)

    def paren(self):
        self.next()  # '('
        node = self.expr()
        while self.peek() in ("+", "*"):
            o = self.next()
            rhs = self.expr()
            node = op("bvadd" if o == "+" else "bvmul", node, rhs)
        if self.peek() != ")":
            raise _Decline("paren")
        self.next()
        return node


def parse_scev(expr_text):
    """-> ('addrec', [operand_nodes]) | node | None (declined / not an addrec we model)."""
    try:
        p = _P(_tokenize(expr_text.strip()))
        node = p.expr()
        return node if p.i == len(p.t) else None
    except (_Decline, IndexError):
        return None


def recurrence_of(addrec_ops):
    """AddRec operand list -> (init, delta) via Newton forward difference, or None if
    any operand is non-atomic or the chain is too long (cubic+ delta needs division)."""
    if not (2 <= len(addrec_ops) <= 3):
        return None  # length 1 = invariant (no recurrence); 4+ = declined
    if any(isinstance(o, tuple) for o in addrec_ops):  # nested AddRec operand -> decline
        return None
    init = addrec_ops[0]
    if len(addrec_ops) == 2:
        delta = addrec_ops[1]                                   # f' = a1
    else:
        delta = op("bvadd", addrec_ops[1], op("bvmul", addrec_ops[2], v("i")))  # a1 + a2*i
    return init, delta


_SECTION_RE = re.compile(r"Classifying expressions for:\s*@(\w+)")
_PHI_RE = re.compile(r"^\s*(%[\w.]+)\s*=\s*phi\b")
_SCEV_RE = re.compile(r"^\s*-->\s+(.*?)\s+U:\s")
_RET_RE = re.compile(r"\bret\s+\S+\s+(%[\w.]+)")


def scev_recurrences(ll_text, func, opt_bin="opt"):
    """-> {phi_name: (init, delta)} for loop-carried phis of `func`, via LLVM SCEV. The
    canonical trip counter {0,+,1} is dropped (it is the shared index `i`)."""
    out = run_scev(ll_text, opt_bin)
    if out is None:
        return None
    lines = out.splitlines()
    # Slice this function's section.
    start = None
    for idx, ln in enumerate(lines):
        m = _SECTION_RE.search(ln)
        if m and m.group(1) == func:
            start = idx
            break
    if start is None:
        return None
    accs, pending = {}, None
    for ln in lines[start + 1:]:
        if _SECTION_RE.search(ln) or ln.startswith("Determining loop"):
            if _SECTION_RE.search(ln):
                break
            continue
        phi = _PHI_RE.match(ln)
        if phi:
            pending = sanitize(phi.group(1))
            continue
        sc = _SCEV_RE.match(ln)
        if sc and pending is not None:
            node = parse_scev(sc.group(1))
            if isinstance(node, tuple) and node[0] == "addrec":
                rec = recurrence_of(node[1])
                if rec is not None and rec != (c(0), c(1)):  # drop the trip counter
                    accs[pending] = rec
            pending = None
    return accs


# SCEV operator tokens OUTSIDE the integer-ring discharge (§2): width-changing
# (zext/sext/trunc) and non-polynomial (udiv, smax/smin/umax/umin) ops. Their
# presence in a closed-form exit value is exactly WHY a loop->closed-form transform
# (e.g. indvars) lands at `loop-eliminated` -- the ring homomorphism does not cover
# them. Surfaced as a diagnostic so the boundary is named, never silent (§9).
_BOUNDARY_OPS = {"/u": "udiv", "trunc": "trunc", "zext": "zext", "sext": "sext",
                 "smax": "smax", "smin": "smin", "umax": "umax", "umin": "umin"}


def closed_form_boundary_ops(ll_text, func, opt_bin="opt"):
    """The non-ring SCEV ops in `func`'s returned closed-form value -- i.e. the precise
    reason the integer discharge (§2) cannot validate it. Sorted list, or [] when the
    SCEV/return value is unavailable or already inside the ring. Diagnostic only:
    NEVER affects a proof verdict."""
    body = _function_body(ll_text, func)
    ret = _RET_RE.search(body or "")
    if ret is None:
        return []
    ret_name = ret.group(1)  # e.g. %add.lcssa
    out = run_scev(ll_text, opt_bin)
    if out is None:
        return []
    lines = out.splitlines()
    start = next((idx for idx, ln in enumerate(lines)
                  if (m := _SECTION_RE.search(ln)) and m.group(1) == func), None)
    if start is None:
        return []
    found, cur_is_ret = set(), False
    for ln in lines[start + 1:]:
        if _SECTION_RE.search(ln):
            break
        sc = _SCEV_RE.match(ln)
        if sc:
            if cur_is_ret:
                found.update(_BOUNDARY_OPS[t] for t in _tokenize(sc.group(1)) if t in _BOUNDARY_OPS)
            cur_is_ret = False
            continue
        # A value-definition line; remember whether it defines the returned value, so
        # the next `-->` SCEV line is attributed to the return's closed form.
        cur_is_ret = bool(re.match(r"\s*" + re.escape(ret_name) + r"\b", ln))
    return sorted(found)


def scev_loop_tuple(ll_text, func, opt_bin="opt"):
    """(accumulators[(name, init, delta)], [output], 'i') for cv-mine-relational, or None.
    Recurrences come from SCEV; only the (robust, leaf) return operand is read from IR."""
    accs = scev_recurrences(ll_text, func, opt_bin)
    if not accs:
        return None
    body = _function_body(ll_text, func)
    ret = _RET_RE.search(body or "")
    if ret is None:
        return None
    out_name = sanitize(ret.group(1))
    if out_name not in accs:  # rotated exit-phi: %x.lcssa / %x.next -> strip suffix to the phi
        base = re.sub(r"_(lcssa|next)$", "", out_name)
        out_name = base if base in accs else out_name
    if out_name not in accs:
        return None
    acc_list = [(name, init, delta) for name, (init, delta) in accs.items()]
    return acc_list, [out_name], "i"


_DEF_RE = re.compile(r"define\b[^@]*@(\w+)\s*\([^)]*\)[^{]*\{")


def _function_body(ll_text, func):
    for m in _DEF_RE.finditer(ll_text):
        if m.group(1) != func:
            continue
        depth, j = 1, m.end()
        while j < len(ll_text) and depth:
            depth += {"{": 1, "}": -1}.get(ll_text[j], 0)
            j += 1
        return ll_text[m.end():j - 1]
    return None


def function_names(ll_text):
    return [m.group(1) for m in _DEF_RE.finditer(ll_text)]
