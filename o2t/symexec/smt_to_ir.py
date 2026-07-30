#!/usr/bin/env python3
"""Render the symbolic shim's SMT terms back into LLVM IR, so an EXTERNAL oracle can check them.

The symexec track proves `output == input` where BOTH terms are built by the shim itself. That is
self-referential: a systematically wrong encoding -- a matcher that binds the wrong operand, an
opcode mapped to the wrong SMT operator -- would produce a wrong input AND a matching wrong output,
and z3 would happily prove them equal. Every proof would look fine.

Translating both terms back to IR and asking reference Alive2 whether src refines tgt breaks that
circle: Alive2 never sees the shim, only two IR functions, and it knows what LLVM's operators mean.

The term language is small and closed (the shim emits nothing else), so an unknown head is a HARD
ERROR rather than a guess -- silently rendering an unrecognised operator as something plausible is
exactly the failure this module exists to catch.
"""

from __future__ import annotations

# SMT head -> LLVM binary opcode
_BIN = {
    "bvadd": "add", "bvsub": "sub", "bvmul": "mul",
    "bvand": "and", "bvor": "or", "bvxor": "xor",
    "bvshl": "shl", "bvlshr": "lshr", "bvashr": "ashr",
    "bvudiv": "udiv", "bvsdiv": "sdiv", "bvurem": "urem", "bvsrem": "srem",
}


class UntranslatableTerm(Exception):
    """A term the renderer does not model. Never guessed at -- see the module docstring."""


def _tokens(s: str):
    return s.replace("(", " ( ").replace(")", " ) ").split()


def _parse(toks, i=0):
    """S-expression -> nested lists."""
    if toks[i] != "(":
        return toks[i], i + 1
    out, i = [], i + 1
    while toks[i] != ")":
        node, i = _parse(toks, i)
        out.append(node)
    return out, i + 1


def parse_term(term: str):
    node, i = _parse(_tokens(term))
    if i != len(_tokens(term)):
        raise UntranslatableTerm(f"trailing tokens in {term!r}")
    return node


_CMP = {"=": "eq", "bvult": "ult", "bvule": "ule", "bvugt": "ugt", "bvuge": "uge",
        "bvslt": "slt", "bvsle": "sle", "bvsgt": "sgt", "bvsge": "sge"}


class _Emitter:
    """Emits IR and tracks each value's WIDTH.

    Width is not bookkeeping: once icmp is modelled, terms mix i1 and i32, and an emitter that
    assumed one width would render `and i1 %c1, %c2` as an i32 `and` -- valid-looking IR denoting a
    different program, which is precisely the class of error this renderer exists to detect.
    """

    def __init__(self, default_width: int):
        self.default_width = default_width
        self.lines: list[str] = []
        self.n = 0
        self.vars: set[str] = set()

    def _fresh(self) -> str:
        self.n += 1
        return f"%t{self.n}"

    def emit(self, node) -> tuple[str, int]:
        """Emit instructions for `node`; return (operand, width)."""
        if isinstance(node, str):
            if node.startswith("#b"):
                return str(int(node[2:], 2)), 1
            self.vars.add(node)
            return f"%{node}", self.default_width
        if not node:
            raise UntranslatableTerm("empty term")
        head = node[0]
        if head == "_" and len(node) == 3 and str(node[1]).startswith("bv"):
            return str(int(str(node[1])[2:])), int(node[2])          # (_ bvN W)
        if head in _BIN and len(node) == 3:
            (a, wa), (b, wb) = self.emit(node[1]), self.emit(node[2])
            if wa != wb:
                raise UntranslatableTerm(f"width mismatch in {head}: i{wa} vs i{wb}")
            r = self._fresh()
            self.lines.append(f"  {r} = {_BIN[head]} i{wa} {a}, {b}")
            return r, wa
        if head in _CMP and len(node) == 3:
            (a, wa), (b, wb) = self.emit(node[1]), self.emit(node[2])
            if wa != wb:
                raise UntranslatableTerm(f"width mismatch in {head}: i{wa} vs i{wb}")
            r = self._fresh()
            self.lines.append(f"  {r} = icmp {_CMP[head]} i{wa} {a}, {b}")
            return r, 1                                              # a comparison yields i1
        if head == "bvnot" and len(node) == 2:
            a, w = self.emit(node[1])
            r = self._fresh()
            self.lines.append(f"  {r} = xor i{w} {a}, -1")
            return r, w
        if head == "bvneg" and len(node) == 2:
            a, w = self.emit(node[1])
            r = self._fresh()
            self.lines.append(f"  {r} = sub i{w} 0, {a}")
            return r, w
        if head == "not" and len(node) == 2:                         # SMT Bool negation
            a, w = self.emit(node[1])
            if w != 1:
                raise UntranslatableTerm("`not` applied to a non-i1 term")
            r = self._fresh()
            self.lines.append(f"  {r} = xor i1 {a}, true")
            return r, 1
        if head == "ite" and len(node) == 4:
            c, wc = self.emit(node[1])
            (x, wx), (y, wy) = self.emit(node[2]), self.emit(node[3])
            if wc != 1:
                raise UntranslatableTerm(f"select condition is i{wc}, not i1")
            if wx != wy:
                raise UntranslatableTerm(f"select arms differ: i{wx} vs i{wy}")
            r = self._fresh()
            self.lines.append(f"  {r} = select i1 {c}, i{wx} {x}, i{wx} {y}")
            return r, wx
        raise UntranslatableTerm(f"unmodelled term head {head!r}")


def render_pair(src_term: str, tgt_term: str, width: int = 32, fname: str = "f"):
    """Both terms as IR functions over the SAME parameter list, ready for alive-tv.

    The shared signature matters: Alive2 compares src and tgt argument-for-argument, so a parameter
    appearing in only one of them must still be declared in both. The RETURN type comes from the
    terms themselves -- an icmp-rooted fold returns i1, not i32.
    """
    a, b = _Emitter(width), _Emitter(width)
    (ra, wa), (rb, wb) = a.emit(parse_term(src_term)), b.emit(parse_term(tgt_term))
    if wa != wb:
        raise UntranslatableTerm(f"src returns i{wa} but tgt returns i{wb}")
    params = sorted(a.vars | b.vars)
    ty = f"i{width}"
    # `noundef` is NOT a convenience here, it is what the shim actually models: a symbolic Value is
    # one definite bit-vector, never `undef`. Rendering without it asks Alive2 a DIFFERENT question
    # than the one z3 was asked, and Alive2 answers it by quantifying over every use of a
    # multiply-used argument -- which times out even on `(A&B)^(A|B) -> A^B`, reported as a
    # "failed-to-prove" that an unwary caller reads as agreement.
    #
    # The limitation this leaves is real and deliberate: the cross-check cannot catch undef-related
    # unsoundness, because neither side models undef. It checks the ENCODING -- that the shim's SMT
    # terms mean what LLVM's operators mean -- which is the circle worth breaking.
    sig = ", ".join(f"{ty} noundef %{p}" for p in params)
    rty = f"i{wa}"

    def fn(em, ret):
        body = "\n".join(em.lines)
        return (f"define {rty} @{fname}({sig}) {{\n" + (body + "\n" if body else "") +
                f"  ret {rty} {ret}\n}}\n")

    return fn(a, ra), fn(b, rb)
