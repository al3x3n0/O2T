#!/usr/bin/env python3
"""Formal validation of loop->closed-form transforms (indvars), over the integers.

When `indvars` deletes a loop and returns a closed-form exit value, the loop-relational
prover reports `loop-eliminated`: the closed form uses smax/zext/trunc/udiv, outside the
integer-ring discharge, so it falls to the semi-formal differential. This module closes
that gap FORMALLY (all trip counts) for a recognized loop class.

  * SOURCE side -- a canonical counted do-while: an induction phi `i` = {0,+,1}, an
    accumulator phi `acc` with entry value A0 and per-iteration delta d (constant or
    affine in i), an exit guard `icmp slt %i.next, %n`, and `ret`urning the acc phi.
    The loop runs T = smax(n, 1) times (derived STRUCTURALLY from the guard, NOT from
    SCEV's answer), and the returned pre-increment phi is acc_closed(T - 1), where
    acc_closed(k) = A0 + sum_{j<k} d(j) -- affine d gives a degree<=2 polynomial.
  * OPTIMIZED side -- the return value's closed-form SCEV expression, parsed and lowered.

It discharges `source_exit(n) == optimized(n)` for ALL n over the integers (§2 ring
homomorphism Z -> Z/2^n carries the verdict to every width), modeling smax/smin/umax/umin
as `ite` branches -- still division-free, so Z3 settles them instantly. A mismatch is a
concrete miscompiling n (teeth); success upgrades the differential to a FORMAL verdict.

Scope is honest: a loop whose shape, accumulator, guard, or closed-form ops are not
recognized is DECLINED (`unsupported`), never silently passed -- the caller keeps the
semi-formal differential for it. zext/trunc (range-guarded identity) and udiv-by-constant
(Euclidean witness) are the next milestones; their ops currently decline.
"""

from __future__ import annotations

import re
import subprocess

from o2t.frontend import scev_loop as scev

class _Unsupported(Exception):
    pass


# --- integer lowering (extends synth/poly.lower_int with min/max branches) --------------
_INT_BINOP = {"add": "+", "sub": "-", "mul": "*"}
_MINMAX = {"smax": ("ite", ">="), "smin": ("ite", "<="),
           "umax": ("ite", ">="), "umin": ("ite", "<=")}  # over Int, signed/unsigned alias


def _hexint(value):
    return f"(- {-value})" if value < 0 else str(value)


def _divprod_name(node):
    """Deterministic Int var for an abstract (∏factors)/k term, from its canonical factors."""
    key = f"{node['k']}_" + "_".join(lower_int(a) for a in node["factors"])
    return "dp_" + re.sub(r"[^0-9a-zA-Z]", "_", key)


def _polyquot(d, num):
    """Abstract exact quotient num/d of an integer polynomial num by a constant d -- the source
    Faulhaber closed form `acc₀ + Σ_e c_e·(Σ_{j<m} j^e)` with denominators cleared to a single d."""
    return {"op": "polyquot", "d": d, "num": num}


def _polyquot_name(node):
    return "pq_" + re.sub(r"[^0-9a-zA-Z]", "_", f"{node['d']}_{lower_int(node['num'])}")


def lower_int(expr):
    """Lower the closed-form DSL to an SMT-LIB INTEGER term for the OUTER §2 identity:
    var/const/add/sub/mul/smax, plus an abstract `half` (an exact (x*y)/2, lowered to a
    fresh Int var shared by the source's triangular term and the optimized widening). The
    width ops zext/trunc/udiv are NOT lowered here -- they must be abstracted away first."""
    o = expr["op"]
    if o == "var":
        return expr["name"]
    if o == "const":
        return _hexint(expr["value"])
    if o == "divprod":
        return expr.get("_hname") or _divprod_name(expr)
    if o == "polyquot":
        return expr.get("_hname") or _polyquot_name(expr)
    if o in _INT_BINOP:
        return "(" + _INT_BINOP[o] + " " + " ".join(lower_int(a) for a in expr["args"]) + ")"
    if o in _MINMAX:
        # smax/smin are commutative; sort operands so `smax(n,1)` (source) and the printer's
        # `smax(1,n)` (optimized) canonicalize to the SAME term -> matching `half` symbols.
        a, b = sorted(lower_int(x) for x in expr["args"])
        return f"(ite ({_MINMAX[o][1]} {a} {b}) {a} {b})"
    raise _Unsupported(o)


def lower_int_mod(expr, width):
    """FAITHFUL modular Int lowering for the widening lemma, at ambient bit `width`:
    signed leaves (nsw, no wrap), smax as ite, zext as unsigned reinterpretation
    (`x + 2^from` when negative), mul wrapping `mod 2^width`, udiv as floor `div`, and trunc
    as the low `to` bits `mod 2^to`. The ambient width threads down (trunc/zext reset it)."""
    o = expr["op"]
    if o == "var":
        return expr["name"]
    if o == "const":
        return _hexint(expr["value"])
    if o in ("add", "sub"):
        sym = "+" if o == "add" else "-"
        return f"({sym} " + " ".join(lower_int_mod(a, width) for a in expr["args"]) + ")"
    if o in _MINMAX:
        a, b = sorted(lower_int_mod(x, width) for x in expr["args"])
        return f"(ite ({_MINMAX[o][1]} {a} {b}) {a} {b})"
    if o == "zext":
        x = lower_int_mod(expr["args"][0], expr["from"])
        return f"(ite (>= {x} 0) {x} (+ {x} {1 << expr['from']}))"
    if o == "mul":
        a, b = (lower_int_mod(x, width) for x in expr["args"])
        return f"(mod (* {a} {b}) {1 << width})"
    if o == "udiv":
        a, b = (lower_int_mod(x, width) for x in expr["args"])
        return f"(div {a} {b})"
    if o == "trunc":
        return f"(mod {lower_int_mod(expr['args'][0], expr['from'])} {1 << expr['to']})"
    raise _Unsupported(o)


def _var(name):
    return {"op": "var", "name": name}


def _const(value):
    return {"op": "const", "value": value}


def _op(o, *args):
    return {"op": o, "args": list(args)}


# --- closed-form SCEV expression parser (smax/min, +, *, parens, consts, vars) -----------
class _ScevParser:
    """Recursive descent over the SCEV printer's expression grammar for a closed form.
    Produces the closed-form DSL, or raises _Unsupported on a construct we do not model
    (addrec {..}, /u, trunc/zext/sext -- future milestones)."""

    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.i != len(self.t):
            raise _Unsupported("trailing:" + " ".join(self.t[self.i:]))
        return node

    def expr(self):
        node = self.term()
        # left-assoc infix: + and the min/max keywords (SCEV prints `a smax b`).
        while self.peek() in ("+", "smax", "smin", "umax", "umin"):
            tok = self.next()
            rhs = self.term()
            node = _op("add", node, rhs) if tok == "+" else _op(tok, node, rhs)
        return node

    def term(self):
        node = self.factor()
        while self.peek() in ("*", "/u"):
            tok = self.next()
            node = _op("mul" if tok == "*" else "udiv", node, self.factor())
        return node

    def factor(self):
        tok = self.peek()
        if tok == "(":
            self.next()
            node = self.expr()
            if self.peek() != ")":
                raise _Unsupported("unbalanced")
            self.next()
            self._skip_flags()
            return node
        if tok == "{":
            raise _Unsupported("addrec")          # a surviving recurrence, not a closed form
        if tok in ("trunc", "zext", "sext"):
            # KW iFROM <operand> to iTO  (e.g. `zext i32 (..) to i33`, `trunc i33 (..) to i32`).
            self.next()
            wfrom = self._width(self.next())
            operand = self.factor()
            if self.peek() != "to":
                raise _Unsupported(f"{tok}-no-to")
            self.next()
            wto = self._width(self.next())
            return {"op": tok, "from": wfrom, "to": wto, "args": [operand]}
        self.next()
        if re.fullmatch(r"-?\d+", tok):
            node = _const(int(tok))
        elif tok.startswith("%"):
            node = _var(scev.sanitize(tok))
        else:
            raise _Unsupported(tok)
        self._skip_flags()
        return node

    def _skip_flags(self):
        while self.peek() and self.peek().startswith("<"):
            self.next()

    @staticmethod
    def _width(tok):
        if not (isinstance(tok, str) and re.fullmatch(r"i\d+", tok)):
            raise _Unsupported(f"width:{tok}")
        return int(tok[1:])


def parse_closed_form(expr_text):
    """SCEV expression string -> closed-form DSL. Raises _Unsupported when out of scope."""
    return _ScevParser(scev._tokenize(expr_text.strip())).parse()


# --- source-loop recognition (canonical counted do-while) -------------------------------
_DEF_RE = re.compile(r"define\b[^@]*@(\w+)\s*\(([^)]*)\)[^{]*\{")
_PHI_RE = re.compile(r"^\s*(%[\w.]+)\s*=\s*phi\s+\w+\s*\[\s*([^,]+),\s*%\w+\s*\],\s*\[\s*([^,]+),\s*%\w+\s*\]")
_ADD_RE = re.compile(r"^\s*(%[\w.]+)\s*=\s*add\b[^,]*\s+(%[\w.]+|\S+),\s*(\S+)\s*$")
_GUARD_RE = re.compile(r"icmp\s+slt\s+\w+\s+(%[\w.]+),\s*(%[\w.]+)")
_RET_RE = re.compile(r"\bret\s+\S+\s+(%[\w.]+)")


def _func_body(ll_text, func):
    for m in _DEF_RE.finditer(ll_text):
        if m.group(1) != func:
            continue
        depth, j = 1, m.end()
        while j < len(ll_text) and depth:
            depth += {"{": 1, "}": -1}.get(ll_text[j], 0)
            j += 1
        return ll_text[m.end():j - 1]
    return None


def _atom(tok):
    tok = tok.strip()
    if re.fullmatch(r"-?\d+", tok):
        return _const(int(tok))
    if tok.startswith("%"):
        return _var(scev.sanitize(tok))
    return None


_MUL_RE = re.compile(r"^\s*(%[\w.]+)\s*=\s*mul\b[^,]*\s+(%[\w.]+|-?\d+),\s*(%[\w.]+|-?\d+)\s*$")


def _resolve_affine(tok, muls, adds, defined, iv):
    """Resolve an SSA value to (coeff_i, const) with value = coeff_i*i + const, both DSL
    exprs, or None if not affine in the loop index `i`. `defined` is the set of all SSA names
    assigned inside the loop -- a name defined in-loop by anything OTHER than a recognized
    mul/add (e.g. an `shl`, a load) is i-DEPENDENT in an unmodeled way and is DECLINED, never
    mistaken for an i-free parameter (that would be unsound)."""
    if tok == iv:
        return {1: _const(1)}                              # i itself: coefficient of i^1 is 1
    if tok in muls:
        a, b = (_resolve_affine(x, muls, adds, defined, iv) for x in muls[tok])
        if a is None or b is None:
            return None
        out = {}                                           # polynomial product: degrees add
        for da, ca in a.items():
            for db, cb in b.items():
                if da + db > _MAX_DELTA_DEGREE:
                    return None                            # beyond the Faulhaber ceiling -> decline
                out[da + db] = _op("add", out[da + db], _op("mul", ca, cb)) if da + db in out \
                    else _op("mul", ca, cb)
        return out
    if tok in adds:
        a, b = (_resolve_affine(x, muls, adds, defined, iv) for x in adds[tok])
        if a is None or b is None:
            return None
        out = dict(a)                                      # polynomial sum: add coefficients
        for d, c in b.items():
            out[d] = _op("add", out[d], c) if d in out else c
        return out
    if tok.startswith("%") and tok in defined:
        return None                                        # in-loop value, unmodeled op -> decline
    atom = _atom(tok)
    return {0: atom} if atom is not None else None         # literal or i-free parameter (degree 0)


_MAX_DELTA_DEGREE = 3  # delta up to i^3 -> quartic closed form (the Faulhaber ceiling here)


def recognize_source_loop(ll_text, func):
    """Recognize the canonical counted do-while and return its exit-value model, or None.

    Returns {"acc0", "c1", "c0", "bound", "iv"} where the per-iteration accumulator delta is
    the AFFINE function `c1*i + c0` (c1 = 0 for a constant delta), `acc0` is the entry value,
    and `bound` is the loop-bound n of `icmp slt %i.next, %n`. A non-affine delta -> None.
    """
    body = _func_body(ll_text, func)
    if body is None:
        return None
    phis, adds, muls, defined = {}, {}, {}, set()
    for ln in body.splitlines():
        if (m := re.match(r"^\s*(%[\w.]+)\s*=", ln)):
            defined.add(m.group(1))                        # every SSA value assigned in-loop
        if (m := _PHI_RE.match(ln)):
            phis[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
        if (m := _ADD_RE.match(ln)):
            adds[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
        if (m := _MUL_RE.match(ln)):
            muls[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
    guard = _GUARD_RE.search(body)
    ret = _RET_RE.search(body)
    if not guard or not ret:
        return None
    iv_next, bound = guard.group(1), guard.group(2)        # icmp slt %i.next, %n
    iv_phi = next((p for p, (e, lb) in phis.items() if lb == iv_next), None)
    if iv_phi is None or iv_next not in adds:
        return None
    base, step = adds[iv_next]
    if base != iv_phi or step != "1" or phis[iv_phi][0] != "0":
        return None                                        # not i = {0, +, 1}
    acc_phi = ret.group(1)
    if acc_phi not in phis:                                 # rotated exit phi -> decline
        return None
    acc_entry, acc_next = phis[acc_phi]
    if acc_next not in adds:
        return None
    lhs, rhs = adds[acc_next]
    if lhs != acc_phi:
        return None                                        # not acc = acc + delta
    acc0 = _atom(acc_entry)
    delta = _resolve_affine(rhs, muls, adds, defined, iv_phi)   # {degree: coeff}, degree <= 2
    if acc0 is None or delta is None:
        return None
    return {"acc0": acc0, "delta": delta, "bound": scev.sanitize(bound), "iv": iv_phi}


# Faulhaber sums Σ_{j=0}^{m-1} j^e as (denominator, numerator-in-m) over the falling factorials
# of m = T-1: e=0 → m; e=1 → m(m-1)/2; e=2 → m(m-1)(2m-1)/6. Cleared to a single denominator,
# the source closed form is an exact integer quotient (a `polyquot`).
_FAULHABER_DEN = {0: 1, 1: 2, 2: 6, 3: 4}


def _faulhaber_num(e, m, m1, m2):
    """The numerator of Σ_{j<m} j^e (denominator _FAULHABER_DEN[e]), in m=T-1, m1=T-2, m2=T-3."""
    if e == 0:
        return m                                           # m  / 1
    if e == 1:
        return _op("mul", m, m1)                           # m(m-1) / 2
    if e == 2:
        return _op("mul", _op("mul", m, m1), _op("sub", _op("mul", _const(2), m), _const(1)))  # m(m-1)(2m-1)/6
    # Σj³ = [m(m-1)/2]² = m²(m-1)² / 4
    return _op("mul", _op("mul", m, m), _op("mul", m1, m1))


def source_exit_value(model):
    """The returned pre-increment phi as a closed form in n. The loop runs T = smax(n,1) times,
    so the exit phi holds acc after T-1 increments: `acc0 + Σ_e c_e · (Σ_{j<T-1} j^e)`. The
    Faulhaber sums are cleared to a single denominator D = lcm of the per-degree denominators
    and emitted as an exact `polyquot(D, numerator)` (D·s == numerator). For a constant delta
    (D = 1) this is just the linear `acc0 + c0·(T-1)`."""
    delta = model["delta"]
    trip = _op("smax", _var(model["bound"]), _const(1))    # T = max(n, 1)
    m = _op("sub", trip, _const(1))                        # m = T-1
    m1 = _op("sub", trip, _const(2))                       # m-1 = T-2
    m2 = _op("sub", trip, _const(3))                       # m-2 = T-3
    d = 1
    for e in delta:
        d = d * _FAULHABER_DEN[e] // _gcd(d, _FAULHABER_DEN[e])   # lcm of denominators
    num = _op("mul", _const(d), model["acc0"])             # D·acc0 + Σ_e c_e · (D/den_e)·num_e
    for e, coeff in sorted(delta.items()):
        scale = d // _FAULHABER_DEN[e]
        term = _op("mul", coeff, _op("mul", _const(scale), _faulhaber_num(e, m, m1, m2)))
        num = _op("add", num, term)
    return _polyquot(d, num) if d != 1 else num


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def _half(x, y):
    """Abstract exact half-product (x*y)/2 -- canonical (operands sorted) so the source's
    triangular term and the optimized side's widening idiom map to the SAME symbol."""
    return _divprod([x, y], 2)


def _divprod(factors, k):
    """Abstract exact quotient (∏factors)/k -- canonical (factors sorted) so the source's
    Faulhaber term and the optimized side's widening idiom map to the SAME symbol. Subsumes
    the triangular half (k=2, two factors)."""
    return {"op": "divprod", "k": k, "factors": sorted(factors, key=lower_int)}


def _zext_factors(node):
    """Flatten a left-nested product of zext'd terms `zext(X1) * ... * zext(Xn)` into
    [X1, ..., Xn], or None if any leaf is not a zext."""
    if node.get("op") == "zext":
        return [node["args"][0]]
    if node.get("op") == "mul":
        out = []
        for a in node["args"]:
            sub = _zext_factors(a)
            if sub is None:
                return None
            out += sub
        return out
    return None


# --- widening idiom: trunc_iW( (zext X1 * ... * zext Xn) /u K ) == (∏Xi)/K  (mod 2^to) -----
def match_widening(node):
    """Match indvars' product-widening idiom of ANY arity: a truncation of an unsigned-divided
    product of zext'd factors, `trunc(udiv(mul(zext(X1), ..., zext(Xn)), K))`. Returns
    {"factors": [X1..Xn], "k": K, "to": to} or None -- triangular (n=2,K=2) is the special case."""
    if node.get("op") != "trunc":
        return None
    inner = node["args"][0]
    if inner.get("op") != "udiv":
        return None
    prod, k = inner["args"]
    if k.get("op") != "const":
        return None
    factors = _zext_factors(prod)
    if factors is None or len(factors) < 2:
        return None
    return {"factors": factors, "k": k["value"], "to": node["to"]}


def _i32_range(variables):
    return [f"(assert (and (<= (- 2147483648) {v}) (< {v} 2147483648)))" for v in sorted(variables)]


def prove_widening_lemma(z3_bin, node, match):
    """Prove `node == (∏Xi) div K  (mod 2^to)` for ALL i32 inputs (Z3 unsat of the negation):
    the i(to+ε)-widened product, unsigned-divided by K and truncated, equals the EXACT
    quotient's low `to` bits. (The widened width carries the guard bits the division needs; K
    divides the product of consecutive factors.) Returns True on proof."""
    variables = set()
    for f in match["factors"]:
        _free_vars(f, variables)
    lhs = lower_int_mod(node, match["to"])
    prod = "(* " + " ".join(lower_int(f) for f in match["factors"]) + ")" if len(match["factors"]) > 1 \
        else lower_int(match["factors"][0])
    rhs = f"(mod (div {prod} {match['k']}) {1 << match['to']})"
    decls = [f"(declare-const {v} Int)" for v in sorted(variables)]
    smt = "\n".join(["(set-logic ALL)", *decls, *_i32_range(variables),
                     f"(assert (not (= {lhs} {rhs})))", "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout.strip()
    return bool(out) and out.splitlines()[0].strip() == "unsat"


def abstract_widenings(z3_bin, expr):
    """Replace each lemma-proven widening sub-term with an abstract `divprod(factors, K)` node,
    so the surrounding affine algebra becomes a §2 polynomial identity. Returns (expr', lemmas)
    where lemmas is a list of bools (one per matched widening). A widening whose lemma FAILS is
    left in place (the outer lowering then declines it -> unsupported)."""
    lemmas = []

    def rec(node):
        if not isinstance(node, dict):
            return node
        match = match_widening(node)
        if match is not None:
            ok = prove_widening_lemma(z3_bin, node, match)
            lemmas.append(ok)
            if ok:
                return _divprod(match["factors"], match["k"])
            return node
        if "args" in node:
            return {**node, "args": [rec(a) for a in node["args"]]}
        return node

    return rec(expr), lemmas


# --- discharge --------------------------------------------------------------------------
def _free_vars(node, out):
    if node["op"] == "divprod":
        out.add(node.get("_hname") or _divprod_name(node))   # the quotient's own symbol...
        for f in node["factors"]:                            # ...plus inner vars (n) for its constraint
            _free_vars(f, out)
        return
    if node["op"] == "polyquot":
        out.add(node.get("_hname") or _polyquot_name(node))
        _free_vars(node["num"], out)
        return
    if node["op"] == "var":
        out.add(node["name"])
    for a in node.get("args", []):
        _free_vars(a, out)


def _assign_divprods(node, dps):
    """Give each abstract (∏factors)/k its own fresh var + record `k*v == ∏factors`. Distinct
    quotients over EQUAL products (e.g. T-1 written `T-1` vs `-1+T`) are forced equal by Z3, so
    the source's Faulhaber term and the optimized widening need not match syntactically."""
    if not isinstance(node, dict):
        return
    if node["op"] == "divprod":
        name = f"dpv{len(dps)}"
        node["_hname"] = name
        prod = "(* " + " ".join(lower_int(f) for f in node["factors"]) + ")" \
            if len(node["factors"]) > 1 else lower_int(node["factors"][0])
        dps.append((name, node["k"], prod))
        for f in node["factors"]:
            _assign_divprods(f, dps)
        return
    if node["op"] == "polyquot":
        name = f"pqv{len(dps)}"
        node["_hname"] = name
        dps.append((name, node["d"], lower_int(node["num"])))
        _assign_divprods(node["num"], dps)
        return
    for a in node.get("args", []):
        _assign_divprods(a, dps)


def prove_equal(z3_bin, source_expr, opt_expr, extra_constraints=()):
    """`source_expr == opt_expr` for ALL integer values of the free variables (Z3 unsat of
    the negation). Returns (status, model) with status in {proved, refuted, unsupported}.
    `extra_constraints` are additional SMT assertion bodies (e.g. exact-divisibility facts)."""
    dps = []
    _assign_divprods(source_expr, dps)
    _assign_divprods(opt_expr, dps)
    variables = set()
    _free_vars(source_expr, variables)
    _free_vars(opt_expr, variables)
    try:
        src_smt, opt_smt = lower_int(source_expr), lower_int(opt_expr)
    except _Unsupported as exc:
        return "unsupported", {"reason": str(exc)}
    decls = [f"(declare-const {name} Int)" for name in sorted(variables)]
    # d*v == numerator: each abstract quotient is EXACT (a widening's product of consecutive
    # integers is divisible by its divisor -- that is WHY the widening lemma holds -- and the
    # source Faulhaber numerator is divisible by D since a sum of powers is an integer). This
    # multiplicative form keeps the query LINEAR-ish (floor `div` makes Z3 diverge on the
    # nonlinear products). Quotients over equal numerators are forced equal, letting source and
    # optimized reconcile despite syntactic differences and the cubic's modular-inverse constant.
    cons = [f"(assert (= (* {d} {name}) {num}))" for name, d, num in dps]
    cons += [f"(assert {c})" for c in extra_constraints]
    # Compare the i32 results: equality mod 2^32 (the actual obligation). For affine forms this
    # is implied by the §2 over-ℤ identity; for closed forms with a modular-inverse constant
    # (e.g. the cubic `2·3⁻¹`) the equivalence ONLY holds mod 2^32, so we assert exactly that.
    body = ["(set-logic ALL)", *decls, *cons]
    smt = "\n".join([*body,
                     f"(assert (not (= (mod {src_smt} 4294967296) (mod {opt_smt} 4294967296))))",
                     "(check-sat)", "(get-model)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    if head == "sat":
        return "refuted", {"counterexample": _parse_int_model(out)}
    if head != "unsat":
        return "unsupported", {"reason": head}
    # Guard against a VACUOUS proof: if the exact-quotient constraints were themselves
    # unsatisfiable (a non-divisible numerator), `unsat` above would prove nothing. Confirm the
    # constraints admit a model before trusting the verdict (cheap: Z3 just picks one n).
    if cons:
        chk = subprocess.run([z3_bin, "-in"], input="\n".join([*body, "(check-sat)", ""]),
                             capture_output=True, text=True).stdout.strip()
        if not chk.startswith("sat"):
            return "unsupported", {"reason": "quotient-constraints-unsat"}
    return "proved", {}


_MODEL_RE = re.compile(r"\(define-fun (\w+) \(\) Int\s+(\(- \d+\)|-?\d+)\)")


def _parse_int_model(text):
    out = {}
    for name, val in _MODEL_RE.findall(text):
        out[name] = int(val.strip("()").replace(" ", ""))   # "(- 5)" -> "-5"; "5" -> "5"
    return out


def validate_closed_form(z3_bin, src_text, opt_text, func, opt_bin="opt"):
    """Formally validate a loop->closed-form transform for `func`. Returns a verdict dict:
    {"status": proved | refuted | unsupported, ...}. `unsupported` means the loop class or
    a closed-form op is out of scope -- the caller keeps its semi-formal differential."""
    model = recognize_source_loop(src_text, func)
    if model is None:
        return {"status": "unsupported", "reason": "source-loop-not-canonical"}
    expr_text = scev_return_expr(opt_text, func, opt_bin)
    if expr_text is None:
        return {"status": "unsupported", "reason": "no-closed-form-scev"}
    try:
        opt_expr = parse_closed_form(expr_text)
    except _Unsupported as exc:
        return {"status": "unsupported", "reason": f"closed-form-op:{exc}"}
    # Two-part proof: (A) prove each i33/udiv/trunc widening computes the exact half-product
    # mod 2^32 (modular lemma), abstracting it to `half`; then (B) discharge the surrounding
    # affine closed form as a §2 polynomial identity over the integers (homomorphism lifts it
    # to every width). A widening whose lemma fails stays concrete -> (B) declines it.
    opt_abstract, lemmas = abstract_widenings(z3_bin, opt_expr)
    if not all(lemmas):
        return {"status": "unsupported", "reason": "widening-lemma-failed",
                "bound": model["bound"]}
    source_expr = source_exit_value(model)
    status, info = prove_equal(z3_bin, source_expr, opt_abstract)
    return {"status": status, "bound": model["bound"], "widenings": len(lemmas), **info}


def scev_return_expr(ll_text, func, opt_bin="opt"):
    """The SCEV expression STRING for `func`'s returned value, or None."""
    body = _func_body(ll_text, func)
    ret = _RET_RE.search(body or "")
    if ret is None:
        return None
    ret_name = ret.group(1)
    out = scev.run_scev(ll_text, opt_bin)
    if out is None:
        return None
    lines = out.splitlines()
    start = next((idx for idx, ln in enumerate(lines)
                  if (m := scev._SECTION_RE.search(ln)) and m.group(1) == func), None)
    if start is None:
        return None
    cur_is_ret = False
    for ln in lines[start + 1:]:
        if scev._SECTION_RE.search(ln):
            break
        sc = scev._SCEV_RE.match(ln)
        if sc:
            if cur_is_ret:
                return sc.group(1)
            continue
        cur_is_ret = bool(re.match(r"\s*" + re.escape(ret_name) + r"\b", ln))
    return None
