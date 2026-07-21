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

Scope of this first cut: UNGUARDED structural folds (matcher tree + RIUW rewrite tree). Guards,
the return-form anchor, and templated matchers (`m_Intrinsic<...>`) still route through the string
path; widening the AST producer to them retires more of the parser over time. Anything the AST
cannot cleanly map DECLINES (None) -- never a mis-mapping.
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


# The only callees this cut may see: the match predicate, the rewrite sink, matchers (m_*), and
# builder emitters (Create*). ANY other call -- an analysis-query GUARD (`isKnownNonNegative`), a
# type query, a helper -- means a precondition or construct this cut does not model. Extracting
# only the match+rewrite would SILENTLY DROP that guard (a value-relevant precondition), so the
# fold must decline instead. Likewise a logical `&&`/`||` combines the match with other conditions.
def _allowed_callee(name: str | None) -> bool:
    return bool(name) and (name in ("match", "replaceInstUsesWith")
                           or name.startswith("m_") or name.startswith("Create"))


def _has_guard(node: dict) -> bool:
    """True if the AST contains any call outside the allowed vocabulary, or a logical connective --
    either means a guard/precondition this cut cannot model (decline, never drop it)."""
    k = node.get("kind")
    if k in ("CallExpr", "CXXMemberCallExpr") and not _allowed_callee(cp.callee_name(node)):
        return True
    if k == "BinaryOperator" and node.get("opcode") in ("&&", "||"):
        return True
    return any(_has_guard(ch) for ch in cp.inner(node))


def extract_trees(source: str, clang_bin: str = "clang") -> tuple[dict, dict] | None:
    """From a fold's C++ source, extract (matcher_tree, rewrite_tree) via the clang AST, or None.
    The matcher is the 2nd arg of the (single) `match(&I, <pattern>)`; the rewrite is the 2nd arg
    of `replaceInstUsesWith(I, <value>)`. Declines on absence, multiplicity, an unmapped node, or
    ANY guard/precondition (a non-vocabulary call or a logical connective) -- this cut recovers
    UNGUARDED folds only, and never silently drops a guard."""
    ast = _dump(source, clang_bin)
    if ast is None:
        return None
    matches, riuws = [], []
    cp.find_member_call(ast, "match", matches)
    cp.find_member_call(ast, "replaceInstUsesWith", riuws)
    if len(matches) != 1 or len(riuws) != 1:
        return None                                   # multi-match / no rewrite: out of this cut
    if _has_guard(ast):
        return None                                   # a guard would be silently dropped -> decline
    m_args, r_args = cp.call_args(matches[0]), cp.call_args(riuws[0])
    if len(m_args) != 2 or len(r_args) != 2:
        return None
    try:
        return _to_tree(m_args[1]), _to_tree(r_args[1])
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
    matcher_tree, rewrite_tree = trees
    return pg.recover_pair("", "", marker, matcher_tree=matcher_tree, rewrite_tree=rewrite_tree)


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
