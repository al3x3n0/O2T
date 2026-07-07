#!/usr/bin/env python3
"""Pass IR (phase 2 core): compositional recovery of a fold's before/after from source.

The legacy source-intent path keys formal IR off a flat (operation, identity, rewrite) triple, so it
can only express single-op identities (`X + 0`, `X & X`) and declines compound folds. This module
recovers the fold STRUCTURALLY instead: it parses the `PatternMatch` matcher tree of the guard
(`match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))`) into the `before` expression, and the
rewrite value (`replaceInstUsesWith(I, <expr>)`, incl. `Builder.Create*` DFG subtrees) into the
`after` expression, and lowers both to the shared formal-IR node DSL. So arbitrarily nested matcher
algebra and multi-step rewrites become a provable obligation -- and anything unmodeled is declined
(`None`), never mis-modeled.

The produced formal dict is proved by the existing prover (`mini_alive.prove`), so it inherits the
premise-SAT anti-vacuity gate, the teeth, and the second-solver cross-check.
"""

from __future__ import annotations

import re
from itertools import product

from o2t import mini_alive as ma
from o2t.facts.value_tracking import fact_to_assumptions

# PatternMatch binary matchers -> formal-IR bitvector op (mirrors constraints/llvm_idioms.json).
MATCHER_BINOP = {
    "m_Add": "bvadd", "m_c_Add": "bvadd", "m_Sub": "bvsub", "m_Mul": "bvmul", "m_c_Mul": "bvmul",
    "m_And": "bvand", "m_c_And": "bvand", "m_Or": "bvor", "m_c_Or": "bvor",
    "m_Xor": "bvxor", "m_c_Xor": "bvxor", "m_Shl": "bvshl", "m_LShr": "bvlshr", "m_AShr": "bvashr",
    "m_UDiv": "bvudiv", "m_SDiv": "bvsdiv", "m_URem": "bvurem", "m_SRem": "bvsrem",
}
# Constant matchers -> concrete 32-bit value.
MATCHER_CONST = {"m_Zero": 0, "m_One": 1, "m_AllOnes": 0xFFFFFFFF}
# Value binders: bind a name to a symbolic operand.
MATCHER_VALUE = {"m_Value", "m_Specific", "m_Deferred"}
# IRBuilder emission calls -> formal-IR op (the `after`/DFG side).
BUILDER_BINOP = {
    "CreateAdd": "bvadd",
    "CreateSub": "bvsub", "CreateMul": "bvmul", "CreateAnd": "bvand", "CreateOr": "bvor",
    "CreateXor": "bvxor", "CreateShl": "bvshl", "CreateLShr": "bvlshr", "CreateAShr": "bvashr",
    "CreateUDiv": "bvudiv", "CreateSDiv": "bvsdiv", "CreateURem": "bvurem", "CreateSRem": "bvsrem",
}
# Poison-generating no-wrap flags. A flag makes the result poison when its no-overflow precondition is
# violated, so DROPPING a flag is a sound refinement (fewer poison inputs) while ADDING one is not --
# exactly what the refinement check (phase 12) discharges. `matcher/builder name -> (op, flag)`.
MATCHER_FLAG_BINOP = {
    "m_NSWAdd": ("bvadd", "nsw"), "m_NUWAdd": ("bvadd", "nuw"),
    "m_NSWSub": ("bvsub", "nsw"), "m_NUWSub": ("bvsub", "nuw"),
    "m_NSWMul": ("bvmul", "nsw"), "m_NUWMul": ("bvmul", "nuw"),
    "m_DisjointOr": ("bvor", "disjoint"),
}
BUILDER_FLAG_BINOP = {
    "CreateNSWAdd": ("bvadd", "nsw"), "CreateNUWAdd": ("bvadd", "nuw"),
    "CreateNSWSub": ("bvsub", "nsw"), "CreateNUWSub": ("bvsub", "nuw"),
    "CreateNSWMul": ("bvmul", "nsw"), "CreateNUWMul": ("bvmul", "nuw"),
    "CreateExactLShr": ("bvlshr", "exact"), "CreateExactAShr": ("bvashr", "exact"),
    "CreateDisjointOr": ("bvor", "disjoint"),
}
# `exact` (on lshr/ashr) is poison when a shifted-out bit is nonzero; like nsw/nuw, DROPPING it is a
# sound refinement and ADDING it is not. `m_Exact(SUB)` is a WRAPPER, tagging its shift operand exact.
_EXACT_OPS = {"bvlshr", "bvashr"}
# Width-changing casts -> formal-IR op. The matcher tree carries no bit widths, so we assign fixed
# REPRESENTATIVE widths (narrow<->wide) and only recover cast folds licensed by an explicit
# width-equality guard (see `recover_pair`), so a width-dependent fold can never become a false proof.
MATCHER_CAST = {"m_Trunc": "trunc", "m_ZExt": "zext", "m_SExt": "sext"}
BUILDER_CAST = {"CreateTrunc": "trunc", "CreateZExt": "zext", "CreateSExt": "sext"}
# ICmp predicates -> formal-IR comparison op. An icmp yields i1, modeled as a 0/1 bitvector (exact,
# since the result is always 0 or 1) via `ite(pred, 1, 0)` -- so it stays in the shared bv domain and
# the concrete engine can evaluate it. `m_SpecificICmp(PRED, ...)` carries the predicate as a literal;
# `m_ICmp(Pred, ...)` binds it, fixed by a `Pred == ICmpInst::ICMP_*` guard (see `recover_pair`).
_ICMP_PRED = {
    "ICMP_EQ": "eq", "ICMP_NE": "ne",
    "ICMP_SLT": "bvslt", "ICMP_SLE": "bvsle", "ICMP_SGT": "bvsgt", "ICMP_SGE": "bvsge",
    "ICMP_ULT": "bvult", "ICMP_ULE": "bvule", "ICMP_UGT": "bvugt", "ICMP_UGE": "bvuge",
}
BUILDER_ICMP = {
    "CreateICmpEQ": "eq", "CreateICmpNE": "ne",
    "CreateICmpSLT": "bvslt", "CreateICmpSLE": "bvsle", "CreateICmpSGT": "bvsgt", "CreateICmpSGE": "bvsge",
    "CreateICmpULT": "bvult", "CreateICmpULE": "bvule", "CreateICmpUGT": "bvugt", "CreateICmpUGE": "bvuge",
}
# min/max intrinsics: each is `pred(x,y) ? x : y` for the pred that keeps the extremum in x (smin
# keeps the smaller -> x<y; smax the larger -> x>y). Modeled as that ite, so a select/icmp min-select
# canonicalizes into the intrinsic and proves by construction. `kind -> keep-x-when predicate`.
_MINMAX_PRED = {"smin": "bvslt", "smax": "bvsgt", "umin": "bvult", "umax": "bvugt"}
MATCHER_MINMAX = {"m_SMin": "smin", "m_SMax": "smax", "m_UMin": "umin", "m_UMax": "umax"}
BUILDER_MINMAX = {"CreateSMin": "smin", "CreateSMax": "smax", "CreateUMin": "umin", "CreateUMax": "umax"}
_WIDTH = 32
_W_NARROW = 8


class Unsupported(Exception):
    """A construct outside the modeled fragment -- the fold is declined, never mis-modeled."""


_TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\(|\)|,|-?\d+|::|&|~|<|>")


def _tokenize(text: str) -> list[str]:
    """Tokenize, REJECTING any non-whitespace the token set doesn't cover (e.g. an infix `+`, `-`,
    `?:`, or `==`). Dropping such characters silently would let a rewrite like `X + Y` misparse to
    `X` and a wrong model prove; refusing them turns every misparse into a sound decline instead."""
    toks: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if text[pos:m.start()].strip():
            raise Unsupported(f"unrecognized token near {text[pos:m.start()].strip()!r}")
        toks.append(m.group())
        pos = m.end()
    if text[pos:].strip():
        raise Unsupported(f"unrecognized token near {text[pos:].strip()!r}")
    return toks


class _Parser:
    """Recursive-descent parser over `Name(args)` / identifiers / integers."""

    def __init__(self, tokens: list[str]):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def eat(self, tok=None):
        cur = self.peek()
        if tok is not None and cur != tok:
            raise Unsupported(f"expected {tok!r}, got {cur!r}")
        self.i += 1
        return cur

    def parse_call(self) -> dict:
        """A callish expression: NAME '(' args ')' , a bare NAME, or an integer literal."""
        cur = self.peek()
        if cur is None:
            raise Unsupported("unexpected end of expression")
        if re.fullmatch(r"-?\d+", cur):
            self.eat()
            return {"kind": "int", "value": int(cur)}
        name = self.eat()
        # skip C++ qualifier / method chains we don't model structurally (e.g. Builder.CreateAdd,
        # ConstantInt::getNullValue) -- keep the LAST identifier as the operation name.
        while self.peek() in (".", "::") or (self.peek() == ":" and True):
            self.eat()
            name = self.eat()
        template = None
        if self.peek() == "<":                               # a template arg, e.g. m_Intrinsic<Intrinsic::abs>
            self.eat("<")
            depth = 1
            while depth > 0 and self.peek() is not None:
                tok = self.eat()
                if tok == "<":
                    depth += 1
                elif tok == ">":
                    depth -= 1
                elif re.fullmatch(r"[A-Za-z_]\w*", tok):
                    template = tok                           # keep the LAST id (the intrinsic name)
        if self.peek() == "(":
            self.eat("(")
            args = []
            if self.peek() != ")":
                args.append(self.parse_call())
                while self.peek() == ",":
                    self.eat(",")
                    args.append(self.parse_call())
            self.eat(")")
            return {"kind": "call", "name": name, "template": template, "args": args}
        return {"kind": "name", "name": name}


def _parse(text: str) -> dict:
    # normalise `A.b` / `A::b` chains so tokenizer keeps the method name.
    text = text.replace(".", "::")
    parser = _Parser(_tokenize(text))
    node = parser.parse_call()
    if parser.peek() is not None:                            # leftover tokens => an infix/ternary form
        raise Unsupported(f"trailing tokens after expression: {parser.toks[parser.i:]!r}")
    return node


def _var(name: str) -> dict:
    return {"op": "var", "name": name.lower()}


def _const(value: int, bits: int = _WIDTH) -> dict:
    return {"op": "bvconst", "bits": bits, "value": value & ((1 << bits) - 1)}


def _assign_width(widths: dict[str, int], name: str, bits: int) -> None:
    """Pin a bound value's representative width; a conflicting width is unmodeled (declines)."""
    key = name.lower()
    if widths.get(key, bits) != bits:
        raise Unsupported(f"conflicting widths for {name!r}")
    widths[key] = bits


def _lower_cast(castop: str, args: list, rec) -> dict:
    """Lower a width-changing cast. Representative widths: zext/sext widen NARROW->WIDE, trunc narrows
    WIDE->NARROW. `rec(argnode, hint_bits)` lowers the single operand at the required width."""
    if len(args) != 1:
        raise Unsupported(f"{castop} needs one operand")
    if castop in ("zext", "sext"):
        return {"op": castop, "args": [rec(args[0], _W_NARROW)], "bits": _WIDTH}
    return {"op": "trunc", "args": [rec(args[0], _WIDTH)], "bits": _W_NARROW}


def _contains_cast(node: dict) -> bool:
    if node.get("op") in ("zext", "sext", "trunc"):
        return True
    return any(_contains_cast(a) for a in node.get("args", []))


def _icmp(pred: str, a: dict, b: dict, bits: int = _WIDTH) -> dict:
    """An icmp result as a 0/1 bitvector: `pred(a, b) ? 1 : 0` at the result width `bits`."""
    return {"op": "ite", "args": [{"op": pred, "args": [a, b]}, _const(1, bits), _const(0, bits)]}


def _minmax(kind: str, a: dict, b: dict) -> dict:
    """A min/max intrinsic as `keep-a-when(a, b) ? a : b` (e.g. smin -> a<b ? a : b)."""
    return {"op": "ite", "args": [{"op": _MINMAX_PRED[kind], "args": [a, b]}, a, b]}


def _flag_binop(op: str, flag: str, a: dict, b: dict) -> dict:
    return {"op": op, "args": [a, b], "flags": [flag]}


def _abs(x: dict) -> dict:
    """Signed absolute value `x <s 0 ? -x : x`. The `@llvm.abs` int-min-poison flag is IGNORED
    (wrapping semantics); doing so only ever under-approximates poison, a sound (never false-proof)
    conservatism -- abs(INT_MIN) is modeled as INT_MIN rather than poison."""
    return {"op": "ite", "args": [{"op": "bvslt", "args": [x, _const(0)]}, {"op": "bvneg", "args": [x]}, x]}


def _bswap(x: dict) -> dict:
    """`@llvm.bswap.i32` -- reverse the 4 bytes -- modeled EXACTLY in existing ops (mask/shift/or) at
    the 32-bit domain width, so every engine handles it and no new prover node is needed."""
    def band(mask):
        return {"op": "bvand", "args": [x, _const(mask)]}
    lo_hi = {"op": "bvshl", "args": [band(0x000000FF), _const(24)]}
    mid_lo = {"op": "bvshl", "args": [band(0x0000FF00), _const(8)]}
    mid_hi = {"op": "bvlshr", "args": [band(0x00FF0000), _const(8)]}
    hi_lo = {"op": "bvlshr", "args": [band(0xFF000000), _const(24)]}
    return {"op": "bvor", "args": [{"op": "bvor", "args": [lo_hi, mid_lo]},
                                   {"op": "bvor", "args": [mid_hi, hi_lo]}]}


def _bitreverse(x: dict) -> dict:
    """`@llvm.bitreverse.i32` -- reverse the bit order -- as the classic 5-step parallel swap network
    (swap adjacent bits, then pairs, nibbles, bytes, halfwords), all in existing mask/shift/or ops."""
    node = x
    for mask, sh in ((0x55555555, 1), (0x33333333, 2), (0x0F0F0F0F, 4), (0x00FF00FF, 8), (0x0000FFFF, 16)):
        low = {"op": "bvshl", "args": [{"op": "bvand", "args": [node, _const(mask)]}, _const(sh)]}
        high = {"op": "bvlshr", "args": [{"op": "bvand", "args": [node, _const((~mask) & 0xFFFFFFFF)]}, _const(sh)]}
        node = {"op": "bvor", "args": [low, high]}
    return node


def _funnel(kind: str, a: dict, b: dict, c: dict) -> dict:
    """Funnel shift `@llvm.fshl`/`fshr(A, B, C)`: concatenate A:B, shift by `C mod 32`, take the top
    (fshl) / bottom (fshr) 32 bits -- i.e. `(A << sh) | (B >> (32-sh))` (fshl). The `sh == 0` case is an
    explicit branch (returning A / B) so no shift-by-width appears, keeping z3 and the masking concrete
    evaluator in agreement."""
    sh = {"op": "bvand", "args": [c, _const(31)]}
    inv = {"op": "bvsub", "args": [_const(32), sh]}
    is_zero = {"op": "eq", "args": [sh, _const(0)]}
    if kind == "fshl":
        shifted = {"op": "bvor", "args": [{"op": "bvshl", "args": [a, sh]}, {"op": "bvlshr", "args": [b, inv]}]}
        return {"op": "ite", "args": [is_zero, a, shifted]}
    shifted = {"op": "bvor", "args": [{"op": "bvshl", "args": [a, inv]}, {"op": "bvlshr", "args": [b, sh]}]}
    return {"op": "ite", "args": [is_zero, b, shifted]}


def _contains_flags(node: dict) -> bool:
    if node.get("flags"):
        return True
    return any(_contains_flags(a) for a in node.get("args", []))


def _poison_relevant_vars(node: dict, under_freeze: bool = False) -> set[str]:
    """Vars that appear under a `freeze` -- they must be declared poison so freeze is meaningful
    (else `freeze(X)` collapses to `X` and an unguarded `freeze(X) -> X` would falsely prove)."""
    acc: set[str] = set()
    if node.get("op") == "var" and under_freeze:
        acc.add(node["name"])
    deeper = under_freeze or node.get("op") == "freeze"
    for a in node.get("args", []):
        acc |= _poison_relevant_vars(a, deeper)
    return acc


def _bare_vars(node: dict, under_freeze: bool = False) -> set[str]:
    """Vars used OUTSIDE any `freeze`. A var frozen in `before` but used bare in `after` has its freeze
    DROPPED -- which is sound only if the value is a definite (non-undef) value."""
    acc: set[str] = set()
    if node.get("op") == "var" and not under_freeze:
        acc.add(node["name"])
    deeper = under_freeze or node.get("op") == "freeze"
    for a in node.get("args", []):
        acc |= _bare_vars(a, deeper)
    return acc


# Nodes that already produce a boolean (an ite condition may use them directly); everything else is a
# bitvector and is coerced with `!= 0`, matching LLVM's i1 select condition (true iff nonzero).
_BOOL_RESULT_OPS = {"eq", "ne", "bvslt", "bvsle", "bvsgt", "bvsge", "bvult", "bvule", "bvugt", "bvuge"}


def _as_bool(node: dict) -> dict:
    if node.get("op") in _BOOL_RESULT_OPS:
        return node
    return {"op": "ne", "args": [node, _const(0)]}


def _select(cond: dict, then: dict, els: dict) -> dict:
    """A `select`/ite formal-IR node: the condition is lowered to a boolean, the arms stay in the
    shared scalar domain, so `select C, X, Y` proves exactly like `C != 0 ? X : Y`."""
    return {"op": "ite", "args": [_as_bool(cond), then, els]}


def lower_matcher(node: dict, binds: set[str], widths: dict[str, int],
                  pred_binds: dict[str, str] | None = None, hint: int = _WIDTH) -> dict:
    """Lower a parsed matcher tree to a formal-IR `before` node, collecting bound variable names and
    their representative widths. `hint` is the width leaves/consts take when unconstrained by a cast;
    `pred_binds` maps a bound icmp-predicate name to its guard-fixed comparison op."""
    pred_binds = pred_binds or {}
    if node["kind"] == "int":
        return _const(node["value"], hint)
    if node["kind"] == "name":
        raise Unsupported(f"bare operand {node['name']!r} in matcher")
    name, args = node["name"], node["args"]
    if name in MATCHER_CONST:
        return _const(MATCHER_CONST[name], hint)
    if name == "m_SpecificInt":
        if len(args) != 1 or args[0]["kind"] != "int":
            raise Unsupported("m_SpecificInt needs an integer")
        return _const(args[0]["value"], hint)
    if name in MATCHER_VALUE:
        if len(args) != 1 or args[0]["kind"] != "name":
            raise Unsupported(f"{name} needs a bound name")
        binds.add(args[0]["name"].lower())
        _assign_width(widths, args[0]["name"], hint)
        return _var(args[0]["name"])
    if name in MATCHER_BINOP:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        return {"op": MATCHER_BINOP[name], "args": [lower_matcher(args[0], binds, widths, pred_binds, hint),
                                                    lower_matcher(args[1], binds, widths, pred_binds, hint)]}
    if name in MATCHER_FLAG_BINOP:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        op, flag = MATCHER_FLAG_BINOP[name]
        return _flag_binop(op, flag, lower_matcher(args[0], binds, widths, pred_binds, hint),
                           lower_matcher(args[1], binds, widths, pred_binds, hint))
    if name == "m_Exact":                                    # wrapper: tag a shift operand `exact`
        if len(args) != 1:
            raise Unsupported("m_Exact needs one operand")
        inner = lower_matcher(args[0], binds, widths, pred_binds, hint)
        if inner.get("op") not in _EXACT_OPS:
            raise Unsupported("m_Exact only models lshr/ashr")
        return {**inner, "flags": inner.get("flags", []) + ["exact"]}
    if name in ("m_ICmp", "m_c_ICmp", "m_SpecificICmp"):
        if len(args) != 3 or args[0]["kind"] != "name":
            raise Unsupported(f"{name} needs a predicate and two operands")
        pred = _ICMP_PRED.get(args[0]["name"]) or pred_binds.get(args[0]["name"].lower())
        if pred is None:                                 # unbound / unmodeled predicate -> decline
            raise Unsupported(f"unresolved icmp predicate {args[0]['name']!r}")
        return _icmp(pred, lower_matcher(args[1], binds, widths, pred_binds, _WIDTH),
                     lower_matcher(args[2], binds, widths, pred_binds, _WIDTH), hint)
    if name in MATCHER_MINMAX:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        return _minmax(MATCHER_MINMAX[name], lower_matcher(args[0], binds, widths, pred_binds, hint),
                       lower_matcher(args[1], binds, widths, pred_binds, hint))
    if name == "m_Freeze":
        if len(args) != 1:
            raise Unsupported("m_Freeze needs one operand")
        return {"op": "freeze", "args": [lower_matcher(args[0], binds, widths, pred_binds, hint)]}
    if name == "m_Intrinsic":                                # generic intrinsic: m_Intrinsic<Intrinsic::ID>
        tmpl = node.get("template")
        if tmpl in _MINMAX_PRED:
            if len(args) != 2:
                raise Unsupported("min/max intrinsic needs two operands")
            return _minmax(tmpl, lower_matcher(args[0], binds, widths, pred_binds, hint),
                           lower_matcher(args[1], binds, widths, pred_binds, hint))
        if tmpl == "abs":                                    # abs(X[, int_min_poison]) -- flag ignored
            if not args:
                raise Unsupported("abs intrinsic needs an operand")
            return _abs(lower_matcher(args[0], binds, widths, pred_binds, hint))
        if tmpl in ("bswap", "bitreverse"):
            if len(args) != 1:
                raise Unsupported(f"{tmpl} intrinsic needs one operand")
            fold = _bswap if tmpl == "bswap" else _bitreverse
            return fold(lower_matcher(args[0], binds, widths, pred_binds, hint))
        if tmpl in ("fshl", "fshr"):
            if len(args) != 3:
                raise Unsupported(f"{tmpl} intrinsic needs three operands")
            return _funnel(tmpl, *(lower_matcher(a, binds, widths, pred_binds, hint) for a in args))
        raise Unsupported(f"unmodeled intrinsic {tmpl!r}")
    if name in MATCHER_CAST:
        return _lower_cast(MATCHER_CAST[name], args,
                           lambda a, h: lower_matcher(a, binds, widths, pred_binds, h))
    if name == "m_Select":
        if len(args) != 3:
            raise Unsupported("m_Select needs three operands")
        return _select(lower_matcher(args[0], binds, widths, pred_binds, hint),
                       lower_matcher(args[1], binds, widths, pred_binds, hint),
                       lower_matcher(args[2], binds, widths, pred_binds, hint))
    raise Unsupported(f"unmodeled matcher {name!r}")


def lower_rewrite(node: dict, binds: set[str], widths: dict[str, int], hint: int = _WIDTH) -> dict:
    """Lower a rewrite value expression to a formal-IR `after` node (bound var, Builder.Create*
    DFG subtree, or a null/zero constant). References must resolve to matcher-bound names; a bound
    value keeps the width the matcher pinned for it."""
    if node["kind"] == "int":
        return _const(node["value"], hint)
    if node["kind"] == "name":
        nm = node["name"].lower()
        if nm not in binds:
            raise Unsupported(f"rewrite references unbound value {node['name']!r}")
        _assign_width(widths, nm, widths.get(nm, hint))       # keep the matcher-pinned width
        return _var(node["name"])
    name, args = node["name"], node["args"]
    if name in ("getNullValue", "getZero", "getFalse"):
        return _const(0, hint)
    if name in ("getAllOnesValue",):
        return _const(0xFFFFFFFF, hint)
    if name in ("getTrue",):                              # i1 true, modeled as a 0/1 bitvector
        return _const(1, hint)
    if name == "get" and len(args) == 2 and args[-1]["kind"] == "int":
        return _const(args[-1]["value"], hint)            # ConstantInt::get(Ty, N)
    if name in BUILDER_BINOP:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        return {"op": BUILDER_BINOP[name], "args": [lower_rewrite(args[0], binds, widths, hint),
                                                    lower_rewrite(args[1], binds, widths, hint)]}
    if name in BUILDER_FLAG_BINOP:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        op, flag = BUILDER_FLAG_BINOP[name]
        return _flag_binop(op, flag, lower_rewrite(args[0], binds, widths, hint),
                           lower_rewrite(args[1], binds, widths, hint))
    if name in BUILDER_ICMP:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        return _icmp(BUILDER_ICMP[name], lower_rewrite(args[0], binds, widths, _WIDTH),
                     lower_rewrite(args[1], binds, widths, _WIDTH), hint)
    if name in BUILDER_MINMAX:
        if len(args) != 2:
            raise Unsupported(f"{name} needs two operands")
        return _minmax(BUILDER_MINMAX[name], lower_rewrite(args[0], binds, widths, hint),
                       lower_rewrite(args[1], binds, widths, hint))
    if name == "CreateFreeze":
        if len(args) != 1:
            raise Unsupported("CreateFreeze needs one operand")
        return {"op": "freeze", "args": [lower_rewrite(args[0], binds, widths, hint)]}
    if name == "CreateBinaryIntrinsic":                             # first arg is the Intrinsic:: id
        if len(args) < 2 or args[0]["kind"] != "name":
            raise Unsupported("CreateBinaryIntrinsic needs an intrinsic id and operands")
        iid = args[0]["name"]
        if iid in _MINMAX_PRED:
            if len(args) != 3:
                raise Unsupported("min/max intrinsic needs two operands")
            return _minmax(iid, lower_rewrite(args[1], binds, widths, hint),
                           lower_rewrite(args[2], binds, widths, hint))
        if iid == "abs":                                            # abs(X[, int_min_poison]) -- flag ignored
            return _abs(lower_rewrite(args[1], binds, widths, hint))
        raise Unsupported(f"unmodeled binary intrinsic {iid!r}")
    if name == "CreateUnaryIntrinsic":                              # CreateUnaryIntrinsic(Intrinsic::ID, X)
        if len(args) != 2 or args[0]["kind"] != "name" or args[0]["name"] not in ("bswap", "bitreverse"):
            raise Unsupported("CreateUnaryIntrinsic only models bswap/bitreverse")
        fold = _bswap if args[0]["name"] == "bswap" else _bitreverse
        return fold(lower_rewrite(args[1], binds, widths, hint))
    if name in BUILDER_CAST:
        return _lower_cast(BUILDER_CAST[name], args,
                           lambda a, h: lower_rewrite(a, binds, widths, h))
    if name == "CreateSelect":
        if len(args) != 3:
            raise Unsupported("CreateSelect needs three operands")
        return _select(lower_rewrite(args[0], binds, widths, hint),
                       lower_rewrite(args[1], binds, widths, hint),
                       lower_rewrite(args[2], binds, widths, hint))
    raise Unsupported(f"unmodeled rewrite emitter {name!r}")


_MATCH_RE = re.compile(r"\bmatch\s*\([^,]+,\s*(m_\w+\s*(?:<[^>]*>)?\s*\(.*\))\s*\)\s*$")
_RIUW_RE = re.compile(r"\breplaceInstUsesWith\s*\(\s*[^,]+,\s*(.+?)\s*\)\s*;?\s*$")
# Guards that constrain legality/profitability but NOT the value semantics -- safe to drop from a
# value-equivalence obligation (they gate *whether* to fold, not *what* the fold computes).
_VALUE_IRRELEVANT = re.compile(
    r"\b(?:hasOneUse|hasNUses|hasNUsesOrMore|hasPoisonGeneratingFlags|use_empty|user_empty|"
    r"one[_-]?use)\b")
# Poison/undef are a TWO-LEVEL lattice (Lee et al. PLDI'17): poison is strictly stronger than undef,
# and LLVM has two distinct freedom guards. `isGuaranteedNotToBeUndefOrPoison(X)` means X is a DEFINITE
# value (neither undef nor poison); `isGuaranteedNotToBePoison(X)` rules out poison ONLY -- X may still
# be undef. O2T's single poison bit has no `undef`, so its `not-poison` assumption models a definite
# value; both guards therefore emit it, but only the DEFINITE guard may license dropping a `freeze`
# (freeze exists precisely to collapse undef's use-multiplicity, so a poison-only guard is not enough).
_NOTUNDEFPOISON_RE = re.compile(r"\bisGuaranteedNotToBeUndefOrPoison\s*\(\s*&?(\w+)")
_NOTPOISON_ONLY_RE = re.compile(r"\bisGuaranteedNotToBePoison\s*\(\s*&?(\w+)")
# A type-equality guard (`X->getType() == I.getType()`) fixes the result width to a bound value's
# width -- it licenses a cast round-trip's representative widths but carries no value SMT itself.
_TYPE_EQ_RE = re.compile(r"getType\s*\(\s*\)\s*==\s*[^&|]*?getType\s*\(\s*\)")
# A predicate-binding guard (`Pred == ICmpInst::ICMP_EQ`) fixes an `m_ICmp`-bound predicate to a
# concrete comparison; it constrains WHICH icmp, not a value, so it carries no assumption SMT.
_PRED_GUARD_RE = re.compile(r"^\s*(\w+)\s*==\s*(?:\w+::)?(ICMP_\w+)\s*$")


def _split_and(text: str) -> list[str]:
    """Split a boolean guard on top-level `&&` (respecting parentheses)."""
    parts, depth, cur, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text[i:i + 2] == "&&":
            parts.append(cur)
            cur, i = "", i + 2
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def recover_pair(predicate_source: str, rewrite_source: str,
                 marker: str = "probe.recovered.fold") -> dict | None:
    """Recover a compositional formal obligation from a fold's guard conjunction and its
    `replaceInstUsesWith(I, <expr>)` rewrite. The guard's `match(...)` conjunct becomes `before`; its
    analysis-query conjuncts (`isKnownNonZero`/`isKnownNonNegative`/...) become the PRECONDITION under
    which the equivalence must hold. Returns a formal dict provable by mini_alive.prove, or None on
    any unmodeled construct -- including an UNRECOGNISED guard, since dropping a value-relevant
    precondition could turn an unsound fold into a false `proved` (a sound decline)."""
    rm = _RIUW_RE.search(rewrite_source.strip())
    if not rm:
        return None
    matcher_src: str | None = None
    facts: list[dict] = []
    pred_binds: dict[str, str] = {}
    poison_free: set[str] = set()
    definite: set[str] = set()
    has_type_eq = False
    for conjunct in _split_and(predicate_source.strip()):
        if "match(" in conjunct:
            mm = _MATCH_RE.search(conjunct)
            if not mm or matcher_src is not None:
                return None
            matcher_src = mm.group(1)
        elif _VALUE_IRRELEVANT.search(conjunct):
            continue                                     # legality/profitability, no value effect
        elif _TYPE_EQ_RE.search(conjunct):
            has_type_eq = True                           # licenses a cast round-trip's width equality
        elif _NOTUNDEFPOISON_RE.search(conjunct):        # X is a DEFINITE value (not undef, not poison)
            v = _NOTUNDEFPOISON_RE.search(conjunct).group(1).lower()
            poison_free.add(v)
            definite.add(v)
        elif _NOTPOISON_ONLY_RE.search(conjunct):        # X is not poison, but may still be undef
            poison_free.add(_NOTPOISON_ONLY_RE.search(conjunct).group(1).lower())
        elif _PRED_GUARD_RE.match(conjunct):
            ident, pred_name = _PRED_GUARD_RE.match(conjunct).groups()
            if pred_name not in _ICMP_PRED:
                return None                              # unmodeled predicate -> decline
            pred_binds[ident.lower()] = _ICMP_PRED[pred_name]
        else:
            recovered = fact_to_assumptions(conjunct)
            if recovered is None:
                return None                              # unmodeled precondition -> decline
            facts.extend(recovered)
    if matcher_src is None:
        return None
    try:
        binds: set[str] = set()
        widths: dict[str, int] = {}
        before = lower_matcher(_parse(matcher_src), binds, widths, pred_binds)
        after = lower_rewrite(_parse(_unwrap(rm.group(1))), binds, widths)  # unwrap inlined-helper parens
    except Unsupported:
        return None
    if not binds:
        return None
    # A cast changes width, so `replaceInstUsesWith(I, X)` is well-typed only when the result width
    # equals X's -- expressed in an explicit `X->getType() == I.getType()` guard, not the matcher tree.
    # Without that guard we cannot license the representative widths, so decline (a sound bound).
    if (_contains_cast(before) or _contains_cast(after)) and not has_type_eq:
        return None
    # Poison: a value under a `freeze` must be poison-declared (else freeze is a no-op and an unguarded
    # `freeze(X) -> X` would falsely prove); a `not-poison` guard is asserted over its bound value. Both
    # kinds of poison-relevant value must be matcher-bound.
    poison_vars = _poison_relevant_vars(before) | _poison_relevant_vars(after) | poison_free
    if not poison_vars <= binds:
        return None                                      # freeze/poison guard on an unbound value
    # Two-level lattice: dropping a `freeze` (frozen in `before`, used bare in `after`) is sound only
    # if the value is DEFINITE. A poison-only `not-poison` guard rules out poison but NOT undef, and
    # O2T's model has no undef -- so it would falsely prove. Require the definite guard, else decline.
    freeze_dropped = _poison_relevant_vars(before) & _bare_vars(after)
    if freeze_dropped & (poison_free - definite):
        return None
    facts = facts + [{"op": "not-poison", "name": v} for v in poison_free]
    assumptions = []
    for fact in facts:
        fact = dict(fact)
        if fact.get("op") == "mask-pair":                # two-operand disjointness (X & Y) == 0
            fact["left"] = str(fact.get("left", "")).lower()
            fact["right"] = str(fact.get("right", "")).lower()
            if fact["left"] not in binds or fact["right"] not in binds:
                return None                              # guard on a value the matcher never bound
        else:
            fact["name"] = str(fact.get("name", "")).lower()
            if fact["name"] not in binds:                # guard on a value the matcher never bound
                return None
        assumptions.append(fact)
    result = {
        "domain": "scalar-bv32",
        "marker": marker,
        "variables": sorted(binds),
        "before": before,
        "after": after,
        "equivalence": "result",
        "assumptions": assumptions,
    }
    # Non-uniform widths (from casts) declared explicitly; a uniform-32 fold omits this and is
    # byte-identical to before this phase.
    non_uniform = {v: w for v, w in widths.items() if w != _WIDTH}
    if non_uniform:
        result["variable_bits"] = non_uniform
    if poison_vars:
        result["poison_variables"] = sorted(poison_vars)
    # Refinement is the true soundness criterion for `before -> after` (any behaviour of `after` is
    # allowed for `before`); we used value-equality as a conservative proxy. It coincides with equality
    # on poison-free folds, but a poison-relevant rewrite may legitimately be MORE defined: introducing
    # a `freeze` or DROPPING a no-wrap flag is sound yet value-unequal. Those are discharged as a
    # refinement (which still refutes adding a flag or a poison-unsound freeze).
    if poison_vars or _contains_flags(before) or _contains_flags(after):
        result["refinement"] = "refinement"
    # Safety net: never emit a malformed obligation (e.g. an inconsistent-width cast mix). A formal
    # that the IR builder rejects is declined here rather than raised later at prove time.
    from o2t.formal_ir import FormalIrError, pair_for_formal
    try:
        pair_for_formal(result)
    except FormalIrError:
        return None
    return result


# --- phase 15: bridge the AST miner's operand-level finding schema to recover_pair --------------
# The real `cv-mine-pass-source-ast` miner emits a fold as (opcode, operand-level predicate_source,
# rewrite_source) -- e.g. opcode "add" + `match(Op1, m_Zero())` -- NOT a whole-instruction matcher
# tree. This bridge reconstructs `match(&I, m_<Opcode>(slot0, slot1))` from the operand predicates so
# a genuine miner finding flows through the same structural recovery.
_OPCODE_MATCHER = {
    "add": "m_Add", "sub": "m_Sub", "mul": "m_Mul", "and": "m_And", "or": "m_Or",
    "xor": "m_Xor", "shl": "m_Shl", "lshr": "m_LShr", "ashr": "m_AShr",
    "udiv": "m_UDiv", "sdiv": "m_SDiv", "urem": "m_URem", "srem": "m_SRem",
}
_OPERAND_MATCH_RE = re.compile(r"^match\s*\(\s*(Op\d+)\s*,\s*(.+)\)\s*$")
_OPERAND_EQ_RE = re.compile(r"^(Op\d+)\s*==\s*(Op\d+)$")


def finding_to_predicate(opcode: str, predicate_source: str) -> str | None:
    """Rebuild a whole-instruction matcher predicate from a miner finding's opcode + operand-level
    guard. `match(OpK, SUB)` fills operand slot K with SUB; `OpA == OpB` aliases the operands (a
    deferred match); anything else is passed through as a value-fact conjunct. None if unmodeled."""
    matcher_name = _OPCODE_MATCHER.get(opcode)
    if matcher_name is None:
        return None
    slots = {0: "m_Value(Op0)", 1: "m_Value(Op1)"}
    facts: list[str] = []
    for conjunct in _split_and(predicate_source.strip()):
        mm = _OPERAND_MATCH_RE.match(conjunct)
        if mm and mm.group(1) in ("Op0", "Op1"):
            slots[int(mm.group(1)[2:])] = mm.group(2).strip()
            continue
        eq = _OPERAND_EQ_RE.match(conjunct)
        if eq and {eq.group(1), eq.group(2)} == {"Op0", "Op1"}:
            slots[0], slots[1] = "m_Value(Op0)", "m_Deferred(Op0)"   # operands are the same value
            continue
        facts.append(conjunct)                                       # a value fact -> recover_pair owns it
    predicate = f"match(&I, {matcher_name}(" + slots[0] + ", " + slots[1] + "))"
    return " && ".join([predicate, *facts])


def recover_from_finding(finding: dict) -> dict | None:
    """Recover a formal obligation directly from an AST-miner finding dict (the real miner schema:
    `opcode`, operand-level `predicate_source`, `rewrite_source`). The rewrite is normalized to the
    `replaceInstUsesWith(I, <value>)` form whether the source returns the value directly or via
    `replaceInstUsesWith`. Returns a formal dict provable by mini_alive.prove, or None if unmodeled."""
    predicate = finding_to_predicate(str(finding.get("opcode") or ""),
                                     str(finding.get("predicate_source") or ""))
    if predicate is None:
        return None
    value = re.sub(r"^\s*return\s+", "", str(finding.get("rewrite_source") or "")).rstrip(";").strip()
    if not value:
        return None
    if not value.startswith("replaceInstUsesWith"):
        value = f"replaceInstUsesWith(I, {value})"
    return recover_pair(predicate, "return " + value + ";",
                        marker=str(finding.get("marker") or "probe.recovered.fold"))


# --- phase 1+: reconstruct the path condition from a fold FUNCTION's control flow --------------
_BAIL_RETURNS = ("nullptr", "false", "{}", "None", "std::nullopt", "0")


def _balanced(text: str, open_idx: int) -> tuple[str, int]:
    """Given text[open_idx] == '(', return (inner, index-after-matching-')')."""
    depth, i = 0, open_idx
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i + 1
        i += 1
    raise Unsupported("unbalanced parentheses")


def _iter_if_returns(body: str):
    """Yield (condition, return_value) for each `if (<cond>) return <value>;` in program order."""
    for m in re.finditer(r"\bif\s*\(", body):
        try:
            cond, after = _balanced(body, m.end() - 1)
        except Unsupported:
            continue
        tail = body[after:].lstrip()
        rm = re.match(r"return\s+(.+?)\s*;", tail, re.S)
        if rm:
            yield cond.strip(), rm.group(1).strip()


def _unwrap(s: str) -> str:
    """Strip one layer of fully-enclosing parentheses (`(A && B)` -> `A && B`), leaving calls like
    `match(...)` intact."""
    s = s.strip()
    if s.startswith("("):
        try:
            inner, end = _balanced(s, 0)
            if end == len(s):
                return inner.strip()
        except Unsupported:
            pass
    return s


def _bail_atoms(cond: str) -> list[str] | None:
    """Path contribution of an early-return-to-bail guard `if (COND) return bail;` -- i.e. NOT COND.
    Handles the real idiom `!A || !B || ...` (De Morgan -> A && B && ...); each disjunct must be a
    negated atom, else we cannot model the precondition and decline (None)."""
    atoms = []
    for disjunct in _split_top(_unwrap(cond), "||"):
        disjunct = _unwrap(disjunct.strip())
        if disjunct.startswith("!"):
            # `!A` -> A ; an inlined helper guard `!(A && B)` -> A, B (each conjunct a positive fact).
            atoms.extend(_split_top(_unwrap(disjunct[1:].strip()), "&&"))
        else:
            return None                       # a positive disjunct in a bail -> unmodeled
    return atoms


def _split_top(text: str, sep: str) -> list[str]:
    parts, depth, cur, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text[i:i + len(sep)] == sep:
            parts.append(cur)
            cur, i = "", i + len(sep)
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return [p for p in (p.strip() for p in parts) if p]


def _balanced_brace(text: str, open_idx: int) -> tuple[str, int]:
    depth, i = 0, open_idx
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i + 1
        i += 1
    raise Unsupported("unbalanced braces")


def _positive_atoms(cond: str) -> list[str]:
    """Atoms of a positive descent guard `if (COND) { ... }` -- COND must be a `&&`-conjunction."""
    return _split_top(_unwrap(cond), "&&")


_KW_RE = re.compile(r"\b(if|return|for|while|replaceInstUsesWith)\b")
_RIUW_STMT_RE = re.compile(r"(replaceInstUsesWith\s*\(.+?\)\s*;)", re.S)


def _find_fold_path(body: str, path: list[str]) -> tuple[list[str], str] | None:
    """Walk a block's statements in order, threading the accumulated path condition, and return
    (path_atoms, fold_rewrite) at the `return replaceInstUsesWith(...)`. Handles nested `if (G){..}`
    blocks (descend under G), early-return bailouts (`if (B) return null;` -> path gains NOT B for
    later siblings), and positive `if (G) return fold;`. Declines (None) on unmodeled shapes."""
    i = 0
    while True:
        kw = _KW_RE.search(body, i)
        if not kw:
            return None
        if kw.group(1) == "return":
            rm = re.match(r"return\s+(.+?)\s*;", body[kw.start():], re.S)
            if rm and "replaceInstUsesWith" in rm.group(1):
                return path, "return " + rm.group(1).strip() + ";"
            i = kw.end()
            continue
        if kw.group(1) == "replaceInstUsesWith":              # a bare (unguarded) statement rewrite
            sm = _RIUW_STMT_RE.match(body[kw.start():])
            if sm:
                return path, "return " + sm.group(1).rstrip(";").strip() + ";"
            i = kw.end()
            continue
        if kw.group(1) in ("for", "while"):
            # phase 5: a loop over IR (`for (Instruction &I : BB)`) is a universal quantifier over
            # instructions -- each iteration is an independent per-instruction obligation, so the loop
            # header adds NO value precondition. Skip it and recover the body fold.
            paren = body.find("(", kw.end())
            if paren < 0:
                return None
            try:
                _, after = _balanced(body, paren)
            except Unsupported:
                return None
            i = after                                          # scan the body statements transparently
            continue
        # an `if`: parse the balanced condition, then dispatch on what follows.
        paren = body.find("(", kw.end())
        if paren < 0:
            return None
        try:
            cond, after = _balanced(body, paren)
        except Unsupported:
            return None
        rest = body[after:]
        lead = len(rest) - len(rest.lstrip())
        rest = rest.lstrip()
        if rest.startswith("{"):                                  # nested block: descend under COND
            block, blk_end = _balanced_brace(body, after + lead)
            sub = _find_fold_path(block, path + _positive_atoms(cond))
            if sub is not None:
                return sub
            i = blk_end                                            # fold not inside; keep scanning
            continue
        sm = _RIUW_STMT_RE.match(rest)                             # `if (COND) replaceInstUsesWith(...);`
        if sm:
            return path + _positive_atoms(cond), "return " + sm.group(1).rstrip(";").strip() + ";"
        cb = re.match(r"(?:continue|break)\s*;", rest)             # per-iteration bailout in a loop
        if cb:
            bail = _bail_atoms(cond)
            if bail is None:
                return None
            path = path + bail
            i = after + lead + cb.end()
            continue
        rm = re.match(r"return\s+(.+?)\s*;", rest, re.S)
        if not rm:
            return None
        retval = rm.group(1).strip()
        if "replaceInstUsesWith" in retval:                        # positive guard returning the fold
            return path + _positive_atoms(cond), "return " + retval + ";"
        if retval.rstrip(";").strip() in _BAIL_RETURNS:            # bailout: add NOT(cond) for siblings
            bail = _bail_atoms(cond)
            if bail is None:
                return None
            path = path + bail
            i = after + lead + rm.end()
            continue
        return None                                                # non-bail, non-fold return


# --- phase 4: interprocedural helper inlining ---------------------------------------------------
_NON_CALL = {"if", "for", "while", "switch", "return", "sizeof", "match", "replaceInstUsesWith"}


def _fold_body(source: str) -> str:
    """The body of the function that performs the rewrite (contains `replaceInstUsesWith`), so a
    helper defined BEFORE the fold in the same source is not mistaken for the fold body."""
    i = 0
    while i < len(source):
        if source[i] == "{":
            block, end = _balanced_brace(source, i)
            if "replaceInstUsesWith" in block:
                return block
            i = end
        else:
            i += 1
    return source


def _parse_helpers(source: str) -> dict[str, tuple[list[str], str]]:
    """Collect single-return helper definitions `TYPE name(params) { return EXPR; }` -> name ->
    (param_names, return_expr). Multi-statement functions (incl. the fold itself) are not helpers."""
    helpers: dict[str, tuple[list[str], str]] = {}
    for m in re.finditer(r"\b(\w+)\s*\(", source):
        name = m.group(1)
        if name in _NON_CALL:
            continue
        try:
            params_str, after = _balanced(source, m.end() - 1)
        except Unsupported:
            continue
        rest = source[after:]
        lead = len(rest) - len(rest.lstrip())
        if not rest.lstrip().startswith("{"):
            continue
        try:
            body, _ = _balanced_brace(source, after + lead)
        except Unsupported:
            continue
        bm = re.fullmatch(r"\s*return\s+(.+?)\s*;\s*", body, re.S)
        if not bm:
            continue
        params = [p.strip().split()[-1].lstrip("*&") for p in params_str.split(",") if p.strip()]
        if all(re.fullmatch(r"\w+", p) for p in params):
            helpers[name] = (params, bm.group(1).strip())
    return helpers


def _inline_calls(text: str, helpers: dict[str, tuple[list[str], str]], depth: int = 4) -> str:
    """Replace `helper(args)` with its return expression (parenthesised), binding params to args, to
    a bounded recursion depth. Retires the 'blocked helper slice': a guard/value in a called helper
    is resolved into the fold before recovery."""
    if depth <= 0:
        return text
    for name, (params, expr) in helpers.items():
        out, i, changed = text, 0, False
        while True:
            m = re.search(r"\b" + re.escape(name) + r"\s*\(", out[i:])
            if not m:
                break
            popen = i + m.end() - 1
            try:
                args_str, after = _balanced(out, popen)
            except Unsupported:
                i = i + m.end()
                continue
            args = _split_top(args_str, ",")
            if len(args) != len(params):
                i = after
                continue
            sub = expr
            for p, a in zip(params, args):
                sub = re.sub(r"\b" + re.escape(p) + r"\b", a.strip().replace("\\", r"\\"), sub)
            out = out[:i + m.start()] + "(" + sub + ")" + out[after:]
            changed = True
            i = i + m.start() + len(sub) + 2
        if changed:
            return _inline_calls(out, helpers, depth - 1)
    return text


def recover_from_function(source: str, marker: str = "probe.recovered.fold",
                          helpers_source: str = "") -> dict | None:
    """Reconstruct a fold's obligation from its FUNCTION source by walking the control flow to the
    `return replaceInstUsesWith(I, <expr>)` and collecting the full path condition -- early-return
    bailouts (negated, De Morgan) and enclosing positive `if` guards, at arbitrary nesting. Single-
    return helper calls in guards/rewrites are inlined first (interprocedural). Declines on any
    guard/return shape outside the modeled fragment (a sound bound)."""
    helpers = _parse_helpers(source + "\n" + helpers_source)
    body = _fold_body(source)                          # the function body that performs the rewrite
    if helpers:
        body = _inline_calls(body, helpers)
    try:
        found = _find_fold_path(body, [])
    except Unsupported:
        return None
    if found is None:
        return None
    atoms, fold_rewrite = found
    match_atoms = [a for a in atoms if a.startswith("match")]
    if len(match_atoms) != 1:
        return None
    predicate = " && ".join([match_atoms[0]] + [a for a in atoms if not a.startswith("match")])
    return recover_pair(predicate, fold_rewrite, marker)


# --- phase 3: reconcile the recovered obligation across two independent engines ----------------
def _to_signed(value: int, width: int) -> int:
    return value - (1 << width) if value >> (width - 1) else value


def _assumption_holds(assumption: dict, env: dict, width: int) -> bool:
    """Concretely evaluate a recovered precondition dict over `env` at `width` bits."""
    mask = (1 << width) - 1
    op = assumption["op"]
    if op == "mask-pair":                                 # (X & Y) == 0 -- operands share no set bits
        return ((env[assumption["left"]] & env[assumption["right"]]) & mask) == 0
    v = env[assumption["name"]] & mask
    if op == "not-eq":
        return v != (int(assumption.get("value", 0)) & mask)
    if op == "power-of-two":
        return v != 0 and (v & (v - 1)) == 0
    if op == "cmp":
        target = int(assumption.get("value", 0))
        pred = assumption["predicate"]
        if pred[0] == "s":
            lhs = _to_signed(v, width)
        else:
            lhs = v
        return {"sge": lhs >= target, "sgt": lhs > target, "sle": lhs <= target, "slt": lhs < target,
                "uge": lhs >= (target & mask), "ugt": lhs > (target & mask),
                "ule": lhs <= (target & mask), "ult": lhs < (target & mask),
                "eq": lhs == target, "ne": lhs != target}.get(pred, True)
    return True                                       # unmodeled assumption -> don't constrain


def reconcile(pair: dict, z3_bin: str, width: int = 8) -> dict:
    """Cross-check a recovered obligation across two independent engines: the symbolic z3 proof
    (bv32, in `mini_alive.prove`) and an exhaustive CONCRETE enumeration over `width`-bit inputs that
    satisfy the recovered precondition. A sound value identity holds at every width, so the two must
    AGREE (both find the fold sound, or both find a counterexample); a divergence means the recovered
    obligation is not trustworthy (e.g. a width-non-uniform or mis-recovered fold) and must not be
    trusted on a `proved`. Returns {z3, concrete, agree, checked}; concrete is `skipped` when the
    obligation uses an op the toolless evaluator cannot cover."""
    from o2t import mini_alive as ma
    z3_status, _ = ma.prove(pair, z3_bin)
    if pair.get("refinement") == "refinement":
        # The toolless engine checks value-equality and models neither poison nor no-wrap flags, so it
        # is not a faithful oracle for a refinement obligation -- abstain rather than (dis)agree.
        return {"z3": z3_status, "concrete": "skipped", "agree": True, "checked": 0}
    variables = pair["variables"]
    assumptions = pair.get("assumptions", [])
    counterexample = None
    checked = 0
    for combo in product(range(1 << width), repeat=len(variables)):
        env = dict(zip(variables, combo))
        if not all(_assumption_holds(a, env, width) for a in assumptions):
            continue
        b = ma.evaluate(pair["before"], env, width)
        a = ma.evaluate(pair["after"], env, width)
        if b is None or a is None:
            return {"z3": z3_status, "concrete": "skipped", "agree": True, "checked": checked}
        checked += 1
        if b != a:
            counterexample = env
            break
    concrete = "proved" if counterexample is None else "refuted"
    agree = ((z3_status == "proved" and concrete == "proved") or
             (z3_status in ("refuted",) and concrete == "refuted"))
    return {"z3": z3_status, "concrete": concrete, "agree": agree, "checked": checked,
            "counterexample": counterexample}


# --- phase 16: cross-WIDTH reconciliation for width-changing cast folds -------------------------
def _rescale(node: dict, nmap: dict) -> dict:
    """Copy a formal-IR node remapping every bit width through `nmap` (old bits -> new bits). A
    bvconst's value is re-masked to its new width so `all-ones`/`0`/`1` stay themselves."""
    if node.get("op") == "var":
        return dict(node)
    if node.get("op") == "bvconst":
        bits = nmap.get(node["bits"], node["bits"])
        return {"op": "bvconst", "bits": bits, "value": node["value"] & ((1 << bits) - 1)}
    out = {k: v for k, v in node.items() if k != "args"}
    if isinstance(out.get("bits"), int):
        out["bits"] = nmap.get(out["bits"], out["bits"])
    out["args"] = [_rescale(a, nmap) for a in node.get("args", [])]
    return out


def reconcile_widths(pair: dict, z3_bin: str,
                     width_pairs: tuple = ((8, 32), (4, 16), (16, 32))) -> dict:
    """Cross-WIDTH check for a width-changing cast fold. Phase 8 recovers casts at ONE representative
    (narrow, wide) pair = (8, 32), and the toolless/compiled engines cannot evaluate a width change,
    so a cast fold otherwise rests on a single-width z3 proof. Re-prove it at several representative
    pairs (rescaling the recovered obligation): a width-UNIFORM identity -- the only sound kind --
    holds at every pair, so the verdicts must AGREE; a divergence means the single-width proof was a
    width-SPECIFIC coincidence and must not be trusted. Returns {applicable, verdicts, agree, status}.
    `applicable` is False for a fold with no cast (nothing width-parametric to cross-check)."""
    from o2t import mini_alive as ma
    if not (_contains_cast(pair.get("before", {})) or _contains_cast(pair.get("after", {}))):
        return {"applicable": False}
    # every variable's width, INCLUDING those that default to _WIDTH (absent from variable_bits) -- all
    # must be remapped or a rescaled variant would be width-inconsistent and error spuriously.
    base_widths = {v: pair.get("variable_bits", {}).get(v, _WIDTH) for v in pair["variables"]}
    verdicts: dict = {}
    for narrow, wide in width_pairs:
        nmap = {_W_NARROW: narrow, _WIDTH: wide}
        variant = dict(pair)
        variant["variable_bits"] = {v: nmap.get(b, b) for v, b in base_widths.items()}
        variant["before"] = _rescale(pair["before"], nmap)
        variant["after"] = _rescale(pair["after"], nmap)
        verdicts[(narrow, wide)] = ma.prove(variant, z3_bin)[0]
    statuses = set(verdicts.values())
    return {"applicable": True, "verdicts": verdicts, "agree": len(statuses) == 1,
            "status": next(iter(statuses)) if len(statuses) == 1 else "disagree"}


# --- phase 17: independent poison/flag-aware oracle for REFINEMENT obligations ------------------
def _flag_poison(op: str, flags: list, a: int, b: int, w: int) -> bool:
    """True iff `op a b` violates a no-wrap flag at width `w` (concrete mirror of flag_poison_smt)."""
    mask = (1 << w) - 1
    a, b = a & mask, b & mask
    sa, sb = _to_signed(a, w), _to_signed(b, w)
    lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    for fl in flags:
        if op == "bvadd" and fl == "nsw" and not (lo <= sa + sb <= hi):
            return True
        if op == "bvadd" and fl == "nuw" and a + b > mask:
            return True
        if op == "bvsub" and fl == "nsw" and not (lo <= sa - sb <= hi):
            return True
        if op == "bvsub" and fl == "nuw" and a - b < 0:
            return True
        if op == "bvmul" and fl == "nsw" and not (lo <= sa * sb <= hi):
            return True
        if op == "bvmul" and fl == "nuw" and a * b > mask:
            return True
        if op in ("bvlshr", "bvashr") and fl == "exact":     # a shifted-out bit was nonzero
            if b >= w:
                return True
            shifted = (a >> b) if op == "bvlshr" else (sa >> b)
            if ((shifted << b) & mask) != a:
                return True
        if op == "bvor" and fl == "disjoint" and (a & b) != 0:  # operands share a set bit
            return True
    return False


def _eval_poison(node: dict, env: dict, pois: dict, w: int):
    """Poison-aware concrete eval -> (value, poison, exact) or None for an op outside this oracle.
    `exact` is False when a freeze of a poison input made the value arbitrary (unknowable concretely).
    Covers the refinement fragment: var, const, freeze, and binops with no-wrap flags."""
    mask = (1 << w) - 1
    op = node.get("op")
    if op == "var":
        return env[node["name"]] & mask, bool(pois.get(node["name"], False)), True
    if op == "bvconst":
        return node["value"] & mask, False, True
    if op == "freeze":
        inner = _eval_poison(node["args"][0], env, pois, w)
        if inner is None:
            return None
        v, p, ex = inner
        return (0, False, False) if p else (v, False, ex)     # freeze(poison) = arbitrary but defined
    if op in ma.EVAL and len(node.get("args", [])) == 2:
        left = _eval_poison(node["args"][0], env, pois, w)
        right = _eval_poison(node["args"][1], env, pois, w)
        if left is None or right is None:
            return None
        value = ma.EVAL[op](int(left[0]), int(right[0]), mask)
        poison = left[1] or right[1] or _flag_poison(op, node.get("flags", []), left[0], right[0], w)
        return value, poison, left[2] and right[2]
    return None                                               # ite/comparison/cast -> caller skips


def reconcile_refinement(pair: dict, z3_bin: str, width: int = 4) -> dict:
    """Independent cross-check for a REFINEMENT obligation (phases 11-13): poison/freeze/flag folds
    that the value-equality reconcile abstains on, so they otherwise trust z3 alone. Enumerate value
    AND poison-state assignments at `width` bits with a poison-aware evaluator and verify the actual
    refinement condition -- `before defined => (after defined AND after == before)` -- then require the
    concrete verdict to match z3. Returns {z3, concrete, agree, checked}; concrete is `skipped` for an
    op outside the oracle (a cast, a comparison) or a freeze whose arbitrary value would be compared."""
    z3_status, _ = ma.prove(pair, z3_bin)
    if pair.get("refinement") != "refinement":
        return {"z3": z3_status, "concrete": "skipped", "agree": True, "checked": 0, "reason": "not-refinement"}
    variables = pair["variables"]
    poison_vars = list(pair.get("poison_variables", []))
    assumptions = pair.get("assumptions", [])
    value_facts = [a for a in assumptions if a.get("op") != "not-poison"]
    nonpoison = {a["name"] for a in assumptions if a.get("op") == "not-poison"}
    counterexample = None
    checked = 0
    for combo in product(range(1 << width), repeat=len(variables)):
        env = dict(zip(variables, combo))
        if not all(_assumption_holds(a, env, width) for a in value_facts):
            continue
        for pstate in product((False, True), repeat=len(poison_vars)):
            pois = dict(zip(poison_vars, pstate))
            if any(pois.get(n, False) for n in nonpoison):        # not-poison guard filters this state
                continue
            b = _eval_poison(pair["before"], env, pois, width)
            a = _eval_poison(pair["after"], env, pois, width)
            if b is None or a is None:
                return {"z3": z3_status, "concrete": "skipped", "agree": True, "checked": checked}
            b_val, b_pois, b_exact = b
            a_val, a_pois, a_exact = a
            if b_pois:
                continue                                          # before poison -> nothing to refine
            checked += 1
            if a_pois:                                            # before defined but after poison -> fails
                counterexample = {"env": env, "poison": pois}
                break
            if not (b_exact and a_exact):                         # after defined but arbitrary -> can't compare
                return {"z3": z3_status, "concrete": "skipped", "agree": True, "checked": checked}
            if a_val != b_val:
                counterexample = {"env": env, "poison": pois}
                break
        if counterexample:
            break
    concrete = "proved" if counterexample is None else "refuted"
    agree = ((z3_status == "proved" and concrete == "proved") or
             (z3_status == "refuted" and concrete == "refuted"))
    return {"z3": z3_status, "concrete": concrete, "agree": agree, "checked": checked,
            "counterexample": counterexample}


# --- phase 20: re-checkable certificates (an unverified validator only weakly increases confidence) --
def _holds_at(pair: dict, env: dict, pois: dict, width: int):
    """z3-free: does `before -> after` HOLD at this concrete point? True / False(violated) / None
    (an op the evaluator cannot cover, e.g. a width-changing cast). Poison-aware for refinement folds."""
    if pair.get("refinement") == "refinement":
        b = _eval_poison(pair["before"], env, pois, width)
        a = _eval_poison(pair["after"], env, pois, width)
        if b is None or a is None:
            return None
        (bv, bp, bex), (av, ap, aex) = b, a
        if bp:
            return True                                       # before poison -> refinement vacuously holds
        if ap:
            return False                                      # before defined, after poison -> violated
        if not (bex and aex):
            return None                                       # arbitrary frozen value -> cannot compare
        return av == bv
    b = ma.evaluate(pair["before"], env, width)
    a = ma.evaluate(pair["after"], env, width)
    if b is None or a is None:
        return None
    return b == a


def _find_violation(pair: dict, base_env: dict, width: int):
    """z3-free search for a concrete point that VIOLATES the obligation, drawing operand values from
    `base_env` and trying every poison state of the poison-declared variables. Returns the point or None
    (None also when the assumptions exclude it or the evaluator cannot cover the ops)."""
    variables = pair["variables"]
    poison_vars = list(pair.get("poison_variables", []))
    assumptions = pair.get("assumptions", [])
    value_facts = [a for a in assumptions if a.get("op") != "not-poison"]
    nonpoison = {a["name"] for a in assumptions if a.get("op") == "not-poison"}
    env = {v: int(base_env.get(v, 0)) & ((1 << width) - 1) for v in variables}
    if not all(_assumption_holds(a, env, width) for a in value_facts):
        return None
    for pstate in product((False, True), repeat=len(poison_vars)):
        pois = dict(zip(poison_vars, pstate))
        if any(pois.get(n, False) for n in nonpoison):
            continue
        if _holds_at(pair, env, pois, width) is False:
            return {"env": env, "poison": pois}
    return None


def certify(pair: dict, z3_bin: str) -> dict:
    """Emit a re-checkable certificate for the verdict. A `refuted` verdict carries the concrete
    counterexample -- a self-contained, z3-free proof of unsoundness. A `proved` verdict is attested by
    the solver and marked for independent re-checking by exhaustive small-width enumeration (see
    `check_certificate`). Turns 'z3 said so' into an artifact an independent checker can re-verify."""
    status, cex = ma.prove(pair, z3_bin)
    cert = {"marker": pair.get("marker", "?"), "verdict": status, "solver": "z3"}
    if status == "refuted" and cex:
        cert["counterexample"] = {k: int(v) for k, v in cex.get("inputs", {}).items()}
    return cert


def check_certificate(pair: dict, cert: dict, width: int = 4) -> str:
    """Independently re-verify a certificate WITHOUT invoking z3.
      * refuted -> `confirmed` if the counterexample really violates the obligation, else `invalid`
        (z3's model does not refute -> the verdict is untrustworthy).
      * proved  -> exhaustive z3-free enumeration at `width` bits: `confirmed` if no violation exists,
        `invalid` if a concrete counterexample is found (z3 was WRONG -> false proof caught), or
        `unchecked` when the ops are outside the toolless evaluator (a cast/div covered elsewhere)."""
    if cert["verdict"] == "refuted":
        return "confirmed" if _find_violation(pair, cert.get("counterexample", {}), 32) is not None else "invalid"
    if cert["verdict"] != "proved":
        return "unchecked"
    variables = pair["variables"]
    poison_vars = list(pair.get("poison_variables", []))
    assumptions = pair.get("assumptions", [])
    value_facts = [a for a in assumptions if a.get("op") != "not-poison"]
    nonpoison = {a["name"] for a in assumptions if a.get("op") == "not-poison"}
    checked = 0
    for combo in product(range(1 << width), repeat=len(variables)):
        env = dict(zip(variables, combo))
        if not all(_assumption_holds(a, env, width) for a in value_facts):
            continue
        for pstate in product((False, True), repeat=len(poison_vars)):
            pois = dict(zip(poison_vars, pstate))
            if any(pois.get(n, False) for n in nonpoison):
                continue
            holds = _holds_at(pair, env, pois, width)
            if holds is None:
                return "unchecked"                            # op outside the evaluator -> cannot re-check here
            if holds is False:
                return "invalid"                              # a false 'proved' caught independently
            checked += 1
    return "confirmed" if checked else "unchecked"


# --- phase 19: lower an obligation to real LLVM IR for a machine-checked interpreter oracle --------
# The recovered before/after is emitted as textual LLVM IR so an EXTERNAL LLVM-IR interpreter can serve
# as an independent oracle. Vellvm (github.com/vellvm/vellvm) ships an interpreter EXTRACTED FROM a
# Coq/Rocq-mechanized semantics -- the only oracle backed by a machine-checked spec -- and it evaluates
# the poison/undef/flag semantics that concrete CPU execution cannot observe. The SAME IR also runs
# through `lli` or clang for a value-level cross-check. Native `freeze`/`nsw`/`nuw`/`icmp`/`select`/
# `trunc|zext|sext` are emitted so the interpreter checks O2T's SMT encoding against LLVM's real IR.
_LLOP = {"bvadd": "add", "bvsub": "sub", "bvmul": "mul", "bvand": "and", "bvor": "or", "bvxor": "xor",
         "bvshl": "shl", "bvlshr": "lshr", "bvashr": "ashr", "bvudiv": "udiv", "bvsdiv": "sdiv",
         "bvurem": "urem", "bvsrem": "srem"}
_LLCMP = {"eq": "eq", "ne": "ne", "bvslt": "slt", "bvsle": "sle", "bvsgt": "sgt", "bvsge": "sge",
          "bvult": "ult", "bvule": "ule", "bvugt": "ugt", "bvuge": "uge"}


def _ll_lower(node: dict, widths: dict, lines: list, ctr: list) -> tuple:
    """Lower a DSL node to LLVM IR SSA text -> (operand, bit-width). Appends instructions to `lines`."""
    op = node.get("op")
    if op == "var":
        return "%" + node["name"], widths.get(node["name"], _WIDTH)
    if op == "bvconst":
        return str(_to_signed(node["value"] & ((1 << node["bits"]) - 1), node["bits"])), node["bits"]
    if op in _LLOP:
        a, wa = _ll_lower(node["args"][0], widths, lines, ctr)
        b, _ = _ll_lower(node["args"][1], widths, lines, ctr)
        flags = "".join(" " + f for f in node.get("flags", []))
        nm = f"%t{ctr[0]}"; ctr[0] += 1
        lines.append(f"  {nm} = {_LLOP[op]}{flags} i{wa} {a}, {b}")
        return nm, wa
    if op in _LLCMP:
        a, wa = _ll_lower(node["args"][0], widths, lines, ctr)
        b, _ = _ll_lower(node["args"][1], widths, lines, ctr)
        nm = f"%t{ctr[0]}"; ctr[0] += 1
        lines.append(f"  {nm} = icmp {_LLCMP[op]} i{wa} {a}, {b}")
        return nm, 1
    if op == "ite":
        c, wc = _ll_lower(node["args"][0], widths, lines, ctr)
        if wc != 1:
            raise Unsupported("ite condition must be i1")
        t, wt = _ll_lower(node["args"][1], widths, lines, ctr)
        e, _ = _ll_lower(node["args"][2], widths, lines, ctr)
        nm = f"%t{ctr[0]}"; ctr[0] += 1
        lines.append(f"  {nm} = select i1 {c}, i{wt} {t}, i{wt} {e}")
        return nm, wt
    if op in ("zext", "sext", "trunc"):
        v, wv = _ll_lower(node["args"][0], widths, lines, ctr)
        nm = f"%t{ctr[0]}"; ctr[0] += 1
        lines.append(f"  {nm} = {op} i{wv} {v} to i{node['bits']}")
        return nm, node["bits"]
    if op == "freeze":
        v, wv = _ll_lower(node["args"][0], widths, lines, ctr)
        nm = f"%t{ctr[0]}"; ctr[0] += 1
        lines.append(f"  {nm} = freeze i{wv} {v}")
        return nm, wv
    if op == "bvneg":
        v, wv = _ll_lower(node["args"][0], widths, lines, ctr)
        nm = f"%t{ctr[0]}"; ctr[0] += 1
        lines.append(f"  {nm} = sub i{wv} 0, {v}")
        return nm, wv
    raise Unsupported(f"no LLVM IR lowering for {op!r}")


def to_llvm_ir(pair: dict, side: str = "before", fn: str = "f") -> str | None:
    """Emit the `before` or `after` side of an obligation as a textual LLVM IR module `define @fn`.
    None if any op has no IR lowering. This is the input a machine-checked interpreter (Vellvm) or
    `lli`/clang consumes to serve as an independent oracle over LLVM's real semantics."""
    widths = {v: pair.get("variable_bits", {}).get(v, _WIDTH) for v in pair["variables"]}
    lines: list = []
    try:
        result, rbits = _ll_lower(pair[side], widths, lines, [0])
    except Unsupported:
        return None
    params = ", ".join(f"i{widths[v]} %{v}" for v in pair["variables"])
    body = "\n".join(lines + [f"  ret i{rbits} {result}"])
    return f"define i{rbits} @{fn}({params}) {{\n{body}\n}}\n"


# Small values fit an int32_t driver literal (clang path); the poison-aware interp path adds signed/
# unsigned boundary inputs so an nsw/nuw overflow (hence poison) is actually reachable in the sweep.
_CLANG_VALUES = (0, 1, 2, 3, 5, 8)
_INTERP_VALUES = (0, 1, 2, 3, (1 << 31) - 1, 1 << 31, (1 << 32) - 1)


def reconcile_vellvm(pair: dict, z3_bin: str, interp_bin=None, clang_bin: str = "clang",
                     values=None) -> dict:
    """4th, LLVM-IR-level oracle: emit before/after as real IR and execute both through an external
    interpreter over a value sweep, requiring agreement with z3. Prefers a machine-checked interpreter
    (`interp_bin`, e.g. Vellvm) that also models poison/undef; falls back to clang/CPU (value fragment,
    poison unobservable). Returns {z3, interp, agree, ...}; `interp` is `skipped` when the IR uses an
    op with no lowering, the signature is unsupported (mixed width), or no runner is available."""
    z3_status, _ = ma.prove(pair, z3_bin)
    before_ir = to_llvm_ir(pair, "before")
    after_ir = to_llvm_ir(pair, "after")
    if before_ir is None or after_ir is None:
        return {"z3": z3_status, "interp": "skipped", "agree": True, "reason": "no IR lowering"}
    if interp_bin is not None:
        from o2t.symexec import vellvm_interp as vi                    # poison-aware machine-checked backend
        verdict = vi.differential(before_ir, after_ir, "f", interp_bin, values or _INTERP_VALUES)
    else:
        if pair.get("refinement") == "refinement":
            # clang/CPU executes concretely and cannot observe poison (nsw is a no-op, freeze an
            # identity at -O0), so it is not a faithful oracle for a refinement fold -- abstain.
            return {"z3": z3_status, "interp": "skipped", "agree": True, "reason": "value-oracle vs poison"}
        from o2t.validate.differential import differential
        verdict = differential(before_ir, after_ir, "f", clang_bin, values or _CLANG_VALUES)
    status = verdict.get("status")
    if status in ("skipped", "unsupported-signature", "compile-failed", "inconclusive"):
        return {"z3": z3_status, "interp": "skipped", "agree": True, "reason": status}
    interp = "proved" if status.endswith("pass") else "refuted"
    return {"z3": z3_status, "interp": interp, "agree": z3_status == interp,
            "witness": verdict.get("witness")}


# --- phase 3b: reconcile against the COMPILED symbolic execution of a generated shim harness -----
_SHIM_BUILDER = {"bvadd": "CreateAdd", "bvsub": "CreateSub", "bvmul": "CreateMul", "bvand": "CreateAnd",
                 "bvor": "CreateOr", "bvxor": "CreateXor", "bvshl": "CreateShl", "bvlshr": "CreateLShr",
                 "bvashr": "CreateAShr", "bvudiv": "CreateUDiv", "bvsdiv": "CreateSDiv",
                 "bvurem": "CreateURem", "bvsrem": "CreateSRem"}


def _to_smt(node: dict) -> str:
    op = node["op"]
    if op == "var":
        return node["name"].upper()
    if op == "bvconst":
        return f"(_ bv{node['value']} 32)"
    if op in _SHIM_BUILDER:                                # bv binops lower to valid SMT-LIB directly
        return "(" + op + " " + " ".join(_to_smt(a) for a in node["args"]) + ")"
    raise Unsupported(f"no SMT lowering for {op!r}")      # e.g. ite/ne -> compiled path declines


def _to_shim_expr(node: dict) -> str:
    op = node["op"]
    if op == "var":
        return node["name"].upper()
    if op == "bvconst":
        return f'Value{{"(_ bv{node["value"]} 32)"}}'
    if op in _SHIM_BUILDER:
        return f"B.{_SHIM_BUILDER[op]}(" + ", ".join(_to_shim_expr(a) for a in node["args"]) + ")"
    raise Unsupported(f"no shim builder for {op!r}")


def _query_call(assumption: dict) -> str | None:
    op = assumption["op"]
    if op == "mask-pair":                                 # (X & Y) == 0 disjointness query
        return f"haveNoCommonBitsSet({assumption['left'].upper()}, {assumption['right'].upper()})"
    v = assumption["name"].upper()
    if op == "power-of-two":
        return f"isKnownToBeAPowerOfTwo({v})"
    if op == "not-eq" and int(assumption.get("value", 0)) == 0:
        return f"isKnownNonZero({v})"
    if op == "cmp" and int(assumption.get("value", 0)) == 0:
        return {"sge": f"isKnownNonNegative({v})", "slt": f"isKnownNegative({v})"}.get(assumption["predicate"])
    return None


def to_shim_harness(pair: dict) -> str | None:
    """Generate a self-contained `symbolic_llvm.h` harness realizing a recovered fold, or None if any
    part (a builder op or a guard) has no shim mapping."""
    if pair.get("refinement") == "refinement":
        return None                                      # shim drops no-wrap flags / cannot model poison
    variables = [v.upper() for v in pair["variables"]]
    try:
        after_expr = _to_shim_expr(pair["after"])
        before_smt = _to_smt(pair["before"])
    except Unsupported:
        return None
    queries = []
    for assumption in pair.get("assumptions", []):
        call = _query_call(assumption)
        if call is None:
            return None
        queries.append(call)
    guard = " && ".join(queries)
    body = (f"if ({guard}) return {after_expr};\n  return Value{{\"\"}};" if guard
            else f"return {after_expr};")
    decls = "; ".join(f'Value {v}{{"{v}"}}' for v in variables)
    params = ", ".join(f"Value {v}" for v in variables)
    return (f'#include "symbolic_llvm.h"\n#include <cstring>\n'
            f'static Value foldRecovered({params}, IRBuilder &B) {{\n  {body}\n}}\n'
            f'int main(int argc, char **argv) {{\n  cv_setup(argc, argv);\n'
            f'  {decls}; IRBuilder B;\n  std::string input = "{before_smt}";\n'
            f'  Value out = foldRecovered({", ".join(variables)}, B);\n'
            f'  cv_emit(input, out.t.empty() ? nullptr : &out);\n  return 0;\n}}\n')


def reconcile_compiled(pair: dict, z3_bin: str, clang: str = "clang++") -> dict:
    """Reconcile a recovered obligation against the COMPILED symbolic execution of a generated shim
    harness (`symexec/real_pass`): the recovered before/after/guard is realized as real C++, compiled,
    and symbolically executed through its actual branches, and the per-path verdict must match the
    direct z3 verdict. Catches lowering/prover divergence via an independent compiled oracle. Returns
    {z3, compiled, agree, ...}; `compiled` is `skipped` when there is no shim mapping or no compiler."""
    import shutil
    import tempfile
    from pathlib import Path as _Path
    from o2t import mini_alive as ma
    from o2t.symexec import real_pass as rp
    src = to_shim_harness(pair)
    if src is None:
        return {"compiled": "skipped", "reason": "no shim mapping"}
    if shutil.which(clang) is None and not _Path(clang).exists():
        return {"compiled": "skipped", "reason": "no compiler"}
    with tempfile.TemporaryDirectory() as d:
        cpp = _Path(d) / "recovered_fold.cpp"
        cpp.write_text(src)
        exe = rp.compile_harness(str(cpp), clang=clang)
        if exe is None:
            return {"compiled": "skipped", "reason": "compile failed"}
        v = rp.verify_fold(z3_bin, exe, "recovered")
    compiled = ("refuted" if v["refuted"] else "proved" if v["proved"] else "unsupported")
    z3_status, _ = ma.prove(pair, z3_bin)
    agree = ((z3_status == "proved" and compiled == "proved") or
             (z3_status == "refuted" and compiled == "refuted"))
    return {"z3": z3_status, "compiled": compiled, "agree": agree,
            "paths": v["paths"], "rewriting_paths": v["rewriting_paths"]}
