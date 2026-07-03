"""Shared matcher/builder lift core (M1) -- importable.

Parses LLVM PatternMatch matcher expressions into the before-tree and IRBuilder
rewrite expressions into the after-tree, driven by the unified vocabulary
(llvm_idioms.json matchers + OPERATION_FOR_BUILDER_CALL). Used by cv-lift-matcher.py
(the prove-all tool), cv-lift-finding.py (autonomous whole-transform lift from real
findings), and downstream autonomous-verification tools.

    m_Add/.../m_Shl  -> bvadd/.../bvshl     m_Zero/m_One/m_AllOnes -> bvconst
    m_Value(X)       -> var X (binds)        m_Specific/m_Deferred(X) -> var X (ref)
    Builder.CreateXxx / BinaryOperator::CreateXxx -> bvop
    replaceInstUsesWith(I, EXPR) -> EXPR     getNullValue/getAllOnesValue/get -> const
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from o2t.registry.optimization_registry import BV_OP_FOR_OPERATION, OPERATION_FOR_BUILDER_CALL

ROOT = Path(__file__).resolve().parents[2]
MASK = (1 << 32) - 1


def load_matcher_vocab():
    idioms = json.loads((ROOT / "constraints" / "llvm_idioms.json").read_text())
    op_matchers = {m: BV_OP_FOR_OPERATION[o["operation"]]
                   for o in idioms["operations"] for m in o["matchers"]
                   if o["operation"] in BV_OP_FOR_OPERATION}
    const_matchers = {m: int(c["formal_value"]) & MASK
                      for c in idioms["constants"] for m in c["matchers"]
                      if c.get("formal_value") is not None}
    ref_matchers = {m for c in idioms["constants"] for m in c["matchers"]
                    if c.get("formal_value") is None}
    return op_matchers, const_matchers, ref_matchers


OP_MATCHERS, CONST_MATCHERS, REF_MATCHERS = load_matcher_vocab()
VALUE_MATCHERS = {"m_Value", "m_Op"}
REPLACE_CALLS = {"replaceInstUsesWith", "ReplaceInstWithValue"}
CONST_ZERO_BUILDERS = {"getNullValue"}
CONST_ALLONES_BUILDERS = {"getAllOnesValue"}
# Non-local: a select materializes control flow as a value. m_Select / CreateSelect
# (Cond, T, F) lift to ite -- so if-conversion and select-folds mine like any other
# pattern. An i1 condition value C is "taken iff C != 0".
SELECT_MATCHERS = {"m_Select"}
SELECT_BUILDERS = {"CreateSelect", "SelectInst::Create"}
BOOL_OPS = {"eq", "ne", "and", "or", "not", "bvslt", "bvsle", "bvsgt", "bvsge",
            "bvult", "bvule", "bvugt", "bvuge"}
# A comparison condition: m_ICmp(PRED, lhs, rhs) / m_SpecificICmp(PRED, lhs, rhs)
# lift to the boolean op named by PRED (an ICmpInst::ICMP_* literal).
ICMP_MATCHERS = {"m_ICmp", "m_SpecificICmp", "m_c_ICmp", "m_SpecificCmp"}
ICMP_PRED_OP = {
    "ICMP_EQ": "eq", "ICMP_NE": "ne",
    "ICMP_SLT": "bvslt", "ICMP_SLE": "bvsle", "ICMP_SGT": "bvsgt", "ICMP_SGE": "bvsge",
    "ICMP_ULT": "bvult", "ICMP_ULE": "bvule", "ICMP_UGT": "bvugt", "ICMP_UGE": "bvuge",
}


def icmp_pred_op(node):
    """Map an ICmpInst::ICMP_* predicate literal to its boolean DSL op."""
    if "bare" not in node:
        raise MatcherError("icmp predicate must be a literal ICMP_* token")
    base = node["bare"].split("::")[-1].split(".")[-1]
    if base not in ICMP_PRED_OP:
        raise MatcherError(f"unsupported or symbolic icmp predicate {node['bare']!r}")
    return ICMP_PRED_OP[base]


def cond_to_bool(node):
    """A select/branch condition is an i1; model it as `node != 0` unless the lift
    already produced a boolean (a comparison)."""
    if isinstance(node, dict) and node.get("op") in BOOL_OPS:
        return node
    return {"op": "ne", "args": [node, {"op": "bvconst", "bits": 32, "value": 0}]}


class MatcherError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# parse:  expr := IDENT ('(' expr (',' expr)* ')')?   (qualified idents + numbers)
# --------------------------------------------------------------------------- #

TOKEN_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*|\d+|\(|\)|,)")


def tokenize(text: str):
    pos, tokens = 0, []
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise MatcherError(f"unexpected character at {pos}: {text[pos:pos+12]!r}")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            raise MatcherError(f"trailing tokens: {self.toks[self.i:]}")
        return node

    def expr(self):
        name = self.next()
        if name is None:
            raise MatcherError("expected token")
        if name.isdigit():
            return {"num": int(name)}
        if not re.match(r"[A-Za-z_]", name):
            raise MatcherError(f"expected identifier, got {name!r}")
        if self.peek() == "(":
            self.next()
            args = []
            if self.peek() != ")":
                args.append(self.expr())
                while self.peek() == ",":
                    self.next()
                    args.append(self.expr())
            if self.next() != ")":
                raise MatcherError("expected ')'")
            return {"call": name, "args": args}
        return {"bare": name}


def parse_expr(text: str):
    return Parser(tokenize(text)).parse()


# --------------------------------------------------------------------------- #
# lift parsed matcher -> DSL before-tree
# --------------------------------------------------------------------------- #

def lift(node, bound: set[str]):
    if "bare" in node:
        bound.add(node["bare"])
        return {"op": "var", "name": node["bare"]}
    if "num" in node:
        return {"op": "bvconst", "bits": 32, "value": node["num"] & MASK}
    name = node["call"]
    args = node["args"]
    if name in OP_MATCHERS:
        if len(args) != 2:
            raise MatcherError(f"{name} expects 2 args, got {len(args)}")
        return {"op": OP_MATCHERS[name], "args": [lift(args[0], bound), lift(args[1], bound)]}
    if name in SELECT_MATCHERS:
        if len(args) != 3:
            raise MatcherError(f"{name} expects 3 args (cond, true, false)")
        return {"op": "ite", "args": [cond_to_bool(lift(args[0], bound)),
                                      lift(args[1], bound), lift(args[2], bound)]}
    if name in ICMP_MATCHERS:
        if len(args) != 3:
            raise MatcherError(f"{name} expects 3 args (pred, lhs, rhs)")
        return {"op": icmp_pred_op(args[0]),
                "args": [lift(args[1], bound), lift(args[2], bound)]}
    if name in CONST_MATCHERS:
        return {"op": "bvconst", "bits": 32, "value": CONST_MATCHERS[name]}
    if name in VALUE_MATCHERS or name in REF_MATCHERS:
        if len(args) != 1 or "bare" not in args[0]:
            raise MatcherError(f"{name} expects a single bound name")
        bound.add(args[0]["bare"])
        return {"op": "var", "name": args[0]["bare"]}
    raise MatcherError(f"unsupported matcher {name!r}")


def lift_matcher(text: str):
    bound: set[str] = set()
    before = lift(parse_expr(text), bound)
    return before, sorted(bound)


# --------------------------------------------------------------------------- #
# Builder side: lift the rewrite expression (the AFTER-tree).
# --------------------------------------------------------------------------- #

def _basename(qualified: str) -> str:
    return qualified.split("::")[-1].split(".")[-1]


def lift_builder(node):
    if "num" in node:
        return {"op": "bvconst", "bits": 32, "value": node["num"] & MASK}
    if "bare" in node:
        return {"op": "var", "name": node["bare"]}  # a matched value reference
    name = _basename(node["call"])
    args = node["args"]
    if name in REPLACE_CALLS:
        return lift_builder(args[-1])  # replaceInstUsesWith(I, EXPR) -> EXPR
    if name in SELECT_BUILDERS:
        if len(args) != 3:
            raise MatcherError(f"{name} expects 3 args (cond, true, false)")
        return {"op": "ite", "args": [cond_to_bool(lift_builder(args[0])),
                                      lift_builder(args[1]), lift_builder(args[2])]}
    op = OPERATION_FOR_BUILDER_CALL.get(name)
    if op:
        bvop = BV_OP_FOR_OPERATION.get(op)
        if not bvop or len(args) < 2:
            raise MatcherError(f"builder {name} needs 2 operands")
        return {"op": bvop, "args": [lift_builder(args[0]), lift_builder(args[1])]}
    if name in CONST_ZERO_BUILDERS:
        return {"op": "bvconst", "bits": 32, "value": 0}
    if name in CONST_ALLONES_BUILDERS:
        return {"op": "bvconst", "bits": 32, "value": MASK}
    if name == "get":  # ConstantInt::get(Ty, N) -> N (the last numeric arg)
        for arg in reversed(args):
            if "num" in arg:
                return {"op": "bvconst", "bits": 32, "value": arg["num"] & MASK}
        raise MatcherError("ConstantInt::get without a numeric value")
    raise MatcherError(f"unsupported builder {name!r}")


def lift_builder_expr(text: str):
    return lift_builder(parse_expr(text))


def collect_vars(node, out: set[str]):
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["name"])
        for arg in node.get("args", []) or []:
            collect_vars(arg, out)


def lift_transform(matcher_text: str, builder_text: str):
    """Lift a WHOLE transform: before from the matcher, after from the rewrite."""
    before, _ = lift_matcher(matcher_text)
    after = lift_builder_expr(builder_text)
    variables: set[str] = set()
    collect_vars(before, variables)
    collect_vars(after, variables)
    return before, after, sorted(variables)
