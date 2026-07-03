#!/usr/bin/env python3
"""Mine branch SHAPES from pass source and prove the CFG transform they encode.

Beyond matchers: this reads a fold function's C++ CONTROL FLOW (nested if/else/return)
and lifts the value it computes into a nested ite-tree -- a real branch shape, not a
PatternMatch idiom. Functions are paired by name:

    <base>_before          the original branch shape
    <base>_after           the SimplifyCFG-transformed shape (must be EQUIVALENT)
    <base>_unsound_after   a deliberately-wrong transform (must DIFFER)

Each pair is proved with Z3 (before == after => UNSAT of the inequality => sound;
before vs unsound_after => SAT). `isTrue(C)` models a branch on `C != 0`.

Grammar (tight, fold-function subset): statements are `return EXPR;` and
`if (COND) BLOCK [else BLOCK]`; COND is isTrue()/comparison/&&/||/!; EXPR is
identifiers, integer literals, and +/-/*; BLOCK is `{...}` or a single statement.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from o2t.formal_ir import FormalIrError, equivalence_smt, pair_for_formal  # noqa: E402

MASK = (1 << 32) - 1
ZERO = {"op": "bvconst", "bits": 32, "value": 0}
CMP = {"==": "eq", "!=": "ne", "<": "bvslt", "<=": "bvsle", ">": "bvsgt", ">=": "bvsge"}
TOK_RE = re.compile(r"\s*(&&|\|\||==|!=|<=|>=|[(){};,!<>+\-*=]|[A-Za-z_]\w*|\d+)")
FUNC_RE = re.compile(r"Value\s*\*\s*(\w+)\s*\(([^)]*)\)\s*\{")


class ShapeError(ValueError):
    pass


def tokenize(text: str):
    out, pos = [], 0
    while pos < len(text):
        m = TOK_RE.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ShapeError(f"bad token at {text[pos:pos + 10]!r}")
        out.append(m.group(1))
        pos = m.end()
    return out


class Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def eat(self, expect=None):
        tok = self.peek()
        if expect is not None and tok != expect:
            raise ShapeError(f"expected {expect!r}, got {tok!r}")
        self.i += 1
        return tok

    # --- statements parsed to a small imperative AST ------------------------- #
    def program(self):
        stmts = []
        while self.peek() not in ("}", None):
            stmts.append(self.statement())
        return stmts

    def block_stmts(self):
        if self.peek() == "{":
            self.eat("{")
            stmts = []
            while self.peek() not in ("}", None):
                stmts.append(self.statement())
            self.eat("}")
            return stmts
        return [self.statement()]

    def statement(self):
        tok = self.peek()
        if tok == "return":
            self.eat("return")
            val = self.expr()
            self.eat(";")
            return ("ret", val)
        if tok == "if":
            self.eat("if")
            self.eat("(")
            cond = self.cond()
            self.eat(")")
            then = self.block_stmts()
            els = None
            if self.peek() == "else":
                self.eat("else")
                els = self.block_stmts()
            return ("if", cond, then, els)
        if tok == "for":
            self.eat("for")
            self.eat("(")
            count = None
            while self.peek() not in (")", None):
                t = self.eat()
                if t == "<" and self.peek() is not None and self.peek().isdigit():
                    count = int(self.peek())
            self.eat(")")
            body = self.block_stmts()
            if count is None:
                raise ShapeError("for-loop bound is not an integer literal")
            return ("for", count, body)
        if tok == "Value":  # local declaration: Value * NAME = EXPR ;
            self.eat("Value")
            if self.peek() == "*":
                self.eat("*")
            name = self.eat()
            self.eat("=")
            val = self.expr()
            self.eat(";")
            return ("assign", name, val)
        # assignment: NAME = EXPR ;
        name = self.eat()
        self.eat("=")
        val = self.expr()
        self.eat(";")
        return ("assign", name, val)

    # --- conditions ---------------------------------------------------------- #
    def cond(self):
        node = self.and_()
        args = [node]
        while self.peek() == "||":
            self.eat("||")
            args.append(self.and_())
        return node if len(args) == 1 else {"op": "or", "args": args}

    def and_(self):
        node = self.not_()
        args = [node]
        while self.peek() == "&&":
            self.eat("&&")
            args.append(self.not_())
        return node if len(args) == 1 else {"op": "and", "args": args}

    def not_(self):
        if self.peek() == "!":
            self.eat("!")
            return {"op": "not", "args": [self.not_()]}
        return self.cmp()

    def cmp(self):
        if self.peek() == "isTrue":
            self.eat("isTrue")
            self.eat("(")
            val = self.expr()
            self.eat(")")
            return {"op": "ne", "args": [val, ZERO]}
        if self.peek() == "(":
            self.eat("(")
            inner = self.cond()
            self.eat(")")
            return inner
        left = self.expr()
        if self.peek() in CMP:
            o = self.eat()
            return {"op": CMP[o], "args": [left, self.expr()]}
        return {"op": "ne", "args": [left, ZERO]}  # bare value used as a bool

    # --- value expressions --------------------------------------------------- #
    def expr(self):
        node = self.term()
        while self.peek() in ("+", "-"):
            o = self.eat()
            node = {"op": "bvadd" if o == "+" else "bvsub", "args": [node, self.term()]}
        return node

    def term(self):
        node = self.factor()
        while self.peek() == "*":
            self.eat("*")
            node = {"op": "bvmul", "args": [node, self.factor()]}
        return node

    def factor(self):
        tok = self.peek()
        if tok == "(":
            self.eat("(")
            inner = self.expr()
            self.eat(")")
            return inner
        if tok is not None and tok.isdigit():
            self.eat()
            return {"op": "bvconst", "bits": 32, "value": int(tok) & MASK}
        if tok is not None and re.match(r"[A-Za-z_]", tok):
            self.eat()
            return {"op": "var", "name": tok}
        raise ShapeError(f"unexpected value token {tok!r}")


def subst(node, env):
    """Replace variable references by their current value-tree in `env`."""
    if not isinstance(node, dict):
        return node
    if node.get("op") == "var":
        return env.get(node["name"], node)
    if "args" in node:
        return {**node, "args": [subst(a, env) for a in node["args"]]}
    return node


def interpret(stmts, env):
    """Execute the statement AST, returning the value-tree of the first reached
    `return` (loops are unrolled, assignments thread an environment, an `if` whose
    arms return becomes an ite)."""
    for idx, s in enumerate(stmts):
        kind = s[0]
        if kind == "assign":
            env[s[1]] = subst(s[2], env)
        elif kind == "for":
            _, count, body = s
            for _ in range(count):
                interpret(body, env)  # body assigns into the shared env
        elif kind == "ret":
            return subst(s[1], env)
        elif kind == "if":
            _, cond, then, els = s
            cond_tree = subst(cond, env)
            then_val = interpret(then, dict(env))
            if then_val is not None:  # then-arm returns -> the function value is an ite
                rest = els if els is not None else stmts[idx + 1:]
                else_val = interpret(rest, dict(env))
                return {"op": "ite", "args": [cond_tree, then_val, else_val]}
    return None


def parse_functions(source: str) -> dict[str, dict]:
    """name -> value-tree (ite for branches, unrolled fold for loops)."""
    out = {}
    for m in FUNC_RE.finditer(source):
        name = m.group(1)
        depth, j = 1, m.end()
        while j < len(source) and depth:
            depth += {"{": 1, "}": -1}.get(source[j], 0)
            j += 1
        body = source[m.end():j - 1]
        try:
            value = interpret(Parser(tokenize(body)).program(), {})
        except ShapeError:
            continue
        if value is not None:
            out[name] = value
    return out


def collect_vars(node, out):
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["name"])
        for a in node.get("args", []) or []:
            collect_vars(a, out)


def prove_equiv(before, after, z3_bin):
    variables = set()
    collect_vars(before, variables)
    collect_vars(after, variables)
    formal = {"domain": "scalar-bv32", "equivalence": "result", "variables": sorted(variables),
              "poison_variables": [], "refinement": "refinement", "before": before, "after": after}
    smt = equivalence_smt("shape", "mine-shapes", pair_for_formal(formal))
    proc = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
    return proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path,
                    default=ROOT / "tests" / "fixtures" / "branch_shapes.cpp")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    fns = parse_functions(args.source.read_text())
    bases = sorted({n[: -len("_before")] for n in fns if n.endswith("_before")})
    results = []
    for base in bases:
        before = fns.get(base + "_before")
        for suffix, expect in (("_after", "unsat"), ("_unsound_after", "sat")):
            after = fns.get(base + suffix)
            if before is None or after is None:
                continue
            try:
                verdict = prove_equiv(before, after, z3_bin)
            except (ShapeError, FormalIrError) as exc:
                results.append({"transform": base + suffix, "status": "error", "error": str(exc)})
                continue
            results.append({"transform": base + suffix, "expected": expect, "verdict": verdict,
                            "kind": "sound" if suffix == "_after" else "teeth",
                            "ok": verdict == expect})

    failed = [r for r in results if not r.get("ok")]
    report = {"source": str(args.source), "functions_parsed": len(fns),
              "transforms_checked": len(results), "failed": len(failed),
              "results": results, "ok": bool(results) and not failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"functions_parsed": len(fns), "transforms_checked": len(results),
                      "failed": len(failed), "ok": report["ok"]}, sort_keys=True))
    for r in results:
        print(f"  [{'ok' if r.get('ok') else 'FAIL'}] {r['transform']}: "
              f"expected {r.get('expected')}, got {r.get('verdict', r.get('error'))}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
