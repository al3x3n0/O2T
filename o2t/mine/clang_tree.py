#!/usr/bin/env python3
"""Clang-AST structured-tree front-end: recover a fold WITHOUT O2T's regex hand-parser.

pass_graph.recover_pair accepts pre-parsed matcher/rewrite TREES (the {kind,name,args,template}
form its `_parse` produces); supplying them bypasses the tokenizer + hand-parser entirely, so a
misparse becomes impossible on a tree. Until now only fixtures hand-authored those trees. This
module PRODUCES them: it shells out to `clang -Xclang -ast-dump=json` (the C++ COMPILER's own
parser), walks the CallExpr AST of the `match(...)` pattern and the rewrite, and maps each AST
node to the tree dialect. The compiler builds the call structure; O2T only relabels it -- removing
the regex parser from the trusted base (the #1 maturity item in docs/maturity.md).

STUB MODE (this implementation): clang parses the fold against a MINIMAL API stub
(tests/fixtures/instcombine_pass_api.h), so it works only on stub-compatible source. This proves
the parser-free PRINCIPLE byte-for-byte against the regex path, but its reach on VERBATIM upstream
is 0 -- real folds reference API the stub does not declare, and clang emits RecoveryExprs that
decline. Production reach needs the real LLVM headers + full InstCombiner compile context (the
matcher tree DOES parse cleanly on verbatim source with `-I <llvm-include> -ast-dump-filter`;
Builder/guard resolution needs the class context). See docs/maturity.md roadmap #1.

Scope: (1) a single guarded `if (match(&I, ...) && <guards>) return replaceInstUsesWith(...)` --
the guard conjuncts are reconstructed from the AST into the recovered PRECONDITION; and (2) the
RETURN-form anchor (upstream's dominant idiom) -- a fold-named helper that returns the replacement
value directly, with Builder.Create* lets inlined at the AST level, unguarded/pure-builder only.
Bailout cascades, guarded return-form, const emitters, and templated matchers (`m_Intrinsic<...>`)
still route through the string path; widening the AST producer to them retires more of the parser
over time. Anything the AST cannot cleanly map DECLINES -- never a mis-mapping, never a dropped
premise.
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


def _to_tree(node: dict, lets: dict | None = None) -> dict:
    """Map one clang AST expression node to pass_graph's {kind,name,args,template} tree. A
    DeclRefExpr to a local single-assignment `let` (a `Value *T = Builder.Create*(...)` binding) is
    inlined by recursing into its initializer -- the AST-level equivalent of the string path's
    let-inlining, so a return-form rewrite that names intermediates recovers compositionally."""
    lets = lets or {}
    n = cp.strip_casts(node)
    k = n.get("kind")
    if k in ("CallExpr", "CXXMemberCallExpr"):
        name = cp.callee_name(n)
        if not name:
            raise _Unmappable("call with no resolvable callee")
        return {"kind": "call", "name": name, "template": None,
                "args": [_to_tree(a, lets) for a in cp.call_args(n)]}
    if k == "DeclRefExpr":
        ref = (n.get("referencedDecl") or {}).get("name")
        if not ref:
            raise _Unmappable("unresolved reference")
        if ref in lets:
            return _to_tree(lets[ref], lets)             # inline the SSA let
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


# --- return-form anchor via the AST (upstream's dominant "return the replacement" idiom) --------
_BAIL_VALUES = {"nullptr", "false", "0"}


def _collect_lets(node: dict, out: dict) -> None:
    """SSA `let` bindings: a VarDecl `Value *T = Builder.Create*(...)`; name -> the init node.
    Uninitialized decls (matcher-bound operands) and non-Create* inits are not lets."""
    if node.get("kind") == "VarDecl":
        kids = cp.inner(node)
        if kids:
            init = cp.strip_casts(kids[-1])
            if init.get("kind") in ("CallExpr", "CXXMemberCallExpr") \
                    and (cp.callee_name(init) or "").startswith("Create"):
                out[node.get("name")] = init
    for ch in cp.inner(node):
        _collect_lets(ch, out)


def _nonbail_returns(node: dict, out: list) -> None:
    if node.get("kind") == "ReturnStmt":
        kids = cp.inner(node)
        if kids:
            v = cp.strip_casts(kids[0])
            if v.get("kind") == "CXXNullPtrLiteralExpr":
                return
            if v.get("kind") == "DeclRefExpr" and \
                    (v.get("referencedDecl") or {}).get("name") in _BAIL_VALUES:
                return
            out.append(v)
    for ch in cp.inner(node):
        _nonbail_returns(ch, out)


def _has_nonvocab_call(node: dict) -> bool:
    """Any call outside {match, m_*, Create*} -- a guard/analysis-query/const-emitter. The
    return-form cut is UNGUARDED and pure-builder only, so its presence declines (never drops)."""
    k = node.get("kind")
    if k in ("CallExpr", "CXXMemberCallExpr"):
        name = cp.callee_name(node)
        if not (name and (name == "match" or name.startswith("m_") or name.startswith("Create"))):
            return True
    return any(_has_nonvocab_call(ch) for ch in cp.inner(node))


def _return_form_trees(ast: dict, source: str) -> tuple[dict, dict, str] | None:
    """Return-form recovery: a fold-named helper that RETURNS the replacement value (no RIUW).
    Scoped to the unguarded, positive-guard, pure-builder shape -- fold-name gated, single match on
    the instruction parameter, Builder.Create* lets inlined, non-bail return tree-ified. Guards,
    const emitters, and in-place mutation decline (this cut, never a dropped premise)."""
    from o2t.intent.pass_graph import (_FOLD_CONTRACT_RE, _FOLD_NAME_RE, _INSTR_PARAM_RE,
                                       _MUTATES_IR_RE)
    fn = _FOLD_NAME_RE.search(source)
    if fn is None or not _FOLD_CONTRACT_RE.search(fn.group(1)):
        return None                                   # not a fold-contract name -> decline
    if _MUTATES_IR_RE.search(source):
        return None                                   # in-place mutation -> decline
    if _has_nonvocab_call(ast):
        return None                                   # a guard/const-emitter -> this cut declines
    matches = []
    cp.find_member_call(ast, "match", matches)
    if len(matches) != 1:
        return None
    m_args = cp.call_args(matches[0])
    if len(m_args) != 2:
        return None
    # subject gate: the match must inspect the function's instruction-typed parameter.
    subj = cp.strip_casts(m_args[0])
    if subj.get("kind") == "UnaryOperator":               # &I
        subj = cp.strip_casts(cp.inner(subj)[0]) if cp.inner(subj) else {}
    subj_name = (subj.get("referencedDecl") or {}).get("name") if subj.get("kind") == "DeclRefExpr" else None
    signature = source[:source.find("{")] if "{" in source else source
    if subj_name is None or subj_name not in {m.group(1) for m in _INSTR_PARAM_RE.finditer(signature)}:
        return None
    lets: dict = {}
    _collect_lets(ast, lets)
    rets: list = []
    _nonbail_returns(ast, rets)
    if len(rets) != 1:
        return None                                   # 0 or many replacement returns -> decline
    try:
        return _to_tree(m_args[1]), _to_tree(rets[0], lets), ""
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
        # phase-36 idiom: a fold-named helper that RETURNS the replacement (no replaceInstUsesWith).
        ast = _dump(source, clang_bin)
        trees = _return_form_trees(ast, source) if ast is not None else None
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
