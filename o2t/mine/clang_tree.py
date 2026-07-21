#!/usr/bin/env python3
"""Clang-AST structured-tree front-end: recover a fold WITHOUT O2T's regex hand-parser.

pass_graph.recover_pair accepts pre-parsed matcher/rewrite TREES (the {kind,name,args,template}
form its `_parse` produces); supplying them bypasses the tokenizer + hand-parser entirely, so a
misparse becomes impossible on a tree. Until now only fixtures hand-authored those trees. This
module PRODUCES them from real source: it shells out to `clang -Xclang -ast-dump=json` (the C++
COMPILER's own parser), walks the CallExpr AST of the `match(...)` pattern and the
`replaceInstUsesWith(I, <rewrite>)` value, and maps each AST node to the tree dialect. The
compiler builds the call structure; O2T only relabels it -- removing the regex parser from the
trusted base (the #1 maturity item in docs/maturity.md).

Scope: matcher tree + RIUW rewrite tree, for a single guarded `if (match(&I, ...) && <guards>)
return replaceInstUsesWith(...)`. The guard conjuncts are reconstructed from the AST and become the
recovered PRECONDITION (a guard that cannot be reconstructed declines, never drops). Bailout
cascades (`if (!match) return null; ...`), the return-form anchor, and templated matchers
(`m_Intrinsic<...>`) still route through the string path; widening the AST producer to them
retires more of the parser over time. Anything the AST cannot cleanly map DECLINES -- never a
mis-mapping, never a dropped premise.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from o2t.intent import pass_graph as pg
from o2t.mine import clang_pass as cp

ROOT = Path(__file__).resolve().parents[2]
STUB = ROOT / "tests" / "fixtures" / "instcombine_pass_api.h"


class _Unmappable(Exception):
    """An AST node outside the mapped fragment -- the fold declines rather than mis-map."""


def _to_tree(node: dict) -> dict:
    """Map one clang AST expression node to pass_graph's {kind,name,args,template} tree."""
    n = cp.strip_casts(node)
    k = n.get("kind")
    if k in ("CallExpr", "CXXMemberCallExpr"):
        name = cp.callee_name(n)
        if not name:
            raise _Unmappable("call with no resolvable callee")
        return {"kind": "call", "name": name, "template": None,
                "args": [_to_tree(a) for a in cp.call_args(n)]}
    if k == "DeclRefExpr":
        ref = (n.get("referencedDecl") or {}).get("name")
        if not ref:
            raise _Unmappable("unresolved reference")
        return {"kind": "name", "name": ref}
    if k == "IntegerLiteral":
        return {"kind": "int", "value": int(n.get("value", "0"))}
    if k == "UnaryOperator" and n.get("opcode") == "-":
        inner = cp.strip_casts(cp.inner(n)[0]) if cp.inner(n) else {}
        if inner.get("kind") == "IntegerLiteral":
            return {"kind": "int", "value": -int(inner.get("value", "0"))}
    raise _Unmappable(f"unmapped AST node {k!r}")


def _dump(source: str, clang_bin: str = "clang") -> dict | None:
    with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False) as tf:
        tf.write(source)
        tmp = tf.name
    try:
        return cp.dump_ast(tmp, STUB, clang_bin)
    finally:
        Path(tmp).unlink(missing_ok=True)


# Callees the front-end may see in a fold path: the match predicate, the rewrite sink, matchers
# (m_*), builder emitters (Create*). A GUARD conjunct is any OTHER call (`isKnownNonNegative`); it
# is not dropped -- it is reconstructed into a precondition (below), and `recover_pair`'s fact
# vocabulary decides whether it is modeled. A guard that is not a flat name-argument call, or a
# logical `||`, cannot be reconstructed and DECLINES (never a dropped premise).
def _flatten_and(node: dict) -> list[dict]:
    """Flatten a left-associated `&&` tree into its leaf conjuncts."""
    n = cp.strip_casts(node)
    if n.get("kind") == "BinaryOperator" and n.get("opcode") == "&&":
        out: list[dict] = []
        for ch in cp.inner(n):
            out += _flatten_and(ch)
        return out
    return [n]


def _if_conditions(node: dict, out: list[dict]) -> None:
    if node.get("kind") == "IfStmt":
        kids = cp.inner(node)
        if kids:
            out.append(kids[0])                       # IfStmt inner = [cond, then, (else)]
    for ch in cp.inner(node):
        _if_conditions(ch, out)


def _reconstruct_guard(call: dict) -> str | None:
    """A guard CallExpr -> its source form `name(A, B)` (args must be plain references), or None
    when it is not a flat name-argument call -- in which case the fold declines rather than drop
    the guard."""
    name = cp.callee_name(call)
    if not name:
        return None
    args = []
    for a in cp.call_args(call):
        aa = cp.strip_casts(a)
        if aa.get("kind") != "DeclRefExpr":
            return None
        ref = (aa.get("referencedDecl") or {}).get("name")
        if not ref:
            return None
        args.append(ref)
    return f"{name}({', '.join(args)})"


def extract_trees(source: str,
                  clang_bin: str = "clang") -> tuple[dict, dict, str] | None:
    """From a fold's C++ source, extract (matcher_tree, rewrite_tree, guard_source) via the clang
    AST, or None. The matcher is the 2nd arg of the single `match(&I, <pattern>)`; the rewrite is
    the 2nd arg of `replaceInstUsesWith(I, <value>)`; guard_source is the `&&`-joined
    reconstruction of the fold-condition's non-match conjuncts (empty for an unguarded fold).
    Declines on absence, multiplicity, an unmapped node, more than one guarded `if`, or any guard
    that is not a flat reconstructible call -- and NEVER silently drops a guard."""
    ast = _dump(source, clang_bin)
    if ast is None:
        return None
    matches, riuws = [], []
    cp.find_member_call(ast, "match", matches)
    cp.find_member_call(ast, "replaceInstUsesWith", riuws)
    if len(matches) != 1 or len(riuws) != 1:
        return None                                   # multi-match / no rewrite: out of this cut
    conds: list[dict] = []
    _if_conditions(ast, conds)
    if len(conds) != 1:
        return None                                   # bailout cascades / no if: string path only
    conjuncts = _flatten_and(conds[0])
    is_match = [c for c in conjuncts
                if c.get("kind") in ("CallExpr", "CXXMemberCallExpr") and cp.callee_name(c) == "match"]
    if len(is_match) != 1:
        return None                                   # match not a top-level && conjunct
    guard_srcs = []
    for c in conjuncts:
        if c is is_match[0]:
            continue
        if c.get("kind") not in ("CallExpr", "CXXMemberCallExpr"):
            return None                               # e.g. a `Pred == ICMP_EQ` compare -> decline
        rs = _reconstruct_guard(c)
        if rs is None:
            return None                               # unreconstructible guard -> decline, not drop
        guard_srcs.append(rs)
    m_args, r_args = cp.call_args(is_match[0]), cp.call_args(riuws[0])
    if len(m_args) != 2 or len(r_args) != 2:
        return None
    try:
        return _to_tree(m_args[1]), _to_tree(r_args[1]), " && ".join(guard_srcs)
    except (_Unmappable, IndexError):
        return None


def recover_from_clang(source: str, marker: str = "probe.recovered.fold",
                       clang_bin: str = "clang") -> dict | None:
    """Recover a fold obligation via the Clang-AST front-end -- the regex parser is NOT in the
    loop. Returns the same formal dict recover_pair produces (provable by mini_alive.prove), or
    None on any decline. The trees drive recover_pair directly; guards route through the string
    path and so are absent here (unguarded folds only, this cut)."""
    trees = extract_trees(source, clang_bin)
    if trees is None:
        return None
    matcher_tree, rewrite_tree, guard_source = trees
    return pg.recover_pair(guard_source, "", marker,
                           matcher_tree=matcher_tree, rewrite_tree=rewrite_tree)


def available(clang_bin: str = "clang") -> bool:
    return cp.find_clang(clang_bin) is not None


def main(argv=None) -> int:
    import argparse
    import json
    import shutil
    import sys
    ap = argparse.ArgumentParser(
        description="Recover a fold obligation from C++ source via the Clang AST (no regex parser)")
    ap.add_argument("source", type=Path, help="a fold's C++ source file")
    ap.add_argument("--clang-bin", default="clang")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    if not available(args.clang_bin):
        print("cv-mine-clang-tree: clang required", file=sys.stderr)
        return 2
    pair = recover_from_clang(args.source.read_text(), clang_bin=args.clang_bin)
    if pair is None:
        print("declined: outside the AST front-end's mapped fragment")
        return 0
    out = {"recovered": True, "obligation": pair}
    z3 = shutil.which(args.z3_bin)
    if z3:
        from o2t import mini_alive as ma
        out["verdict"] = ma.prove(pair, z3)[0]
    if args.report:
        args.report.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in out if k != "obligation"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
