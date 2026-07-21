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

import json
import subprocess
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
        # Skip clang-materialized DEFAULT arguments (a Builder emitter's defaulted `Twine` name),
        # so the verbatim call's operand arity matches the source's -- the value operands only.
        args = [a for a in cp.call_args(n)
                if cp.strip_casts(a).get("kind") != "CXXDefaultArgExpr"]
        return {"kind": "call", "name": name, "template": None,
                "args": [_to_tree(a, lets) for a in args]}
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
    """In-memory (stub-mode) wrapper: dump `source` and extract the RIUW/guarded trees."""
    ast = _dump(source, clang_bin)
    return _extract_riuw(ast) if ast is not None else None


def _extract_riuw(ast: dict) -> tuple[dict, dict, str] | None:
    """From a fold's AST, extract (matcher_tree, rewrite_tree, guard_source), or None. The matcher
    is the 2nd arg of the single `match(&I, <pattern>)`; the rewrite is the 2nd arg of
    `replaceInstUsesWith(I, <value>)`; guard_source is the `&&`-joined reconstruction of the
    fold-condition's non-match conjuncts (empty for unguarded). Declines on absence, multiplicity,
    an unmapped node, more than one guarded `if`, or any guard that is not a flat reconstructible
    call -- and NEVER silently drops a guard. AST-based, so it serves both stub and source-file
    modes."""
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


_IR_MUTATORS = frozenset({
    "setOperand", "swapOperands", "replaceOperand", "replaceUsesOfWith", "setHasNoSignedWrap",
    "setHasNoUnsignedWrap", "setIsExact", "setPredicate", "dropPoisonGeneratingFlags",
    "mutateType", "copyIRFlags", "andIRFlags", "setFastMathFlags"})


def _has_ir_mutation(node: dict) -> bool:
    """An in-place IR mutation (`I.setOperand(...)`, `BOp->setHasNoSignedWrap()`) -- the replaced
    value would no longer be the matched shape, so the fold declines (AST-based mutation screen)."""
    if node.get("kind") in ("CallExpr", "CXXMemberCallExpr") and cp.callee_name(node) in _IR_MUTATORS:
        return True
    return any(_has_ir_mutation(ch) for ch in cp.inner(node))


def _has_nonvocab_call(node: dict) -> bool:
    """Any call outside {match, m_*, Create*} -- a guard/analysis-query/const-emitter. The
    return-form cut is UNGUARDED and pure-builder only, so its presence declines (never drops)."""
    k = node.get("kind")
    if k in ("CallExpr", "CXXMemberCallExpr"):
        name = cp.callee_name(node)
        if not (name and (name == "match" or name.startswith("m_") or name.startswith("Create"))):
            return True
    return any(_has_nonvocab_call(ch) for ch in cp.inner(node))


_INSTR_TYPES = ("BinaryOperator", "Instruction", "UnaryOperator", "ICmpInst", "CmpInst",
                "SelectInst", "PHINode", "GetElementPtrInst", "CastInst", "Operator")


def _instr_params_from_ast(fn_decl: dict) -> set:
    """Instruction-typed parameter names of a FunctionDecl. The AST's `qualType` is the resolved
    TYPE only (`const llvm::BinaryOperator &`) with no parameter name, so match the type keyword
    directly rather than the source-form `Type &name` regex."""
    out: set = set()
    for p in cp.inner(fn_decl):
        if p.get("kind") == "ParmVarDecl" and p.get("name"):
            qt = (p.get("type") or {}).get("qualType", "")
            if any(t in qt for t in _INSTR_TYPES):
                out.add(p["name"])
    return out


def _return_form_trees(ast: dict, fn_name: str, instr_params: set) -> tuple[dict, dict, str] | None:
    """Return-form recovery: a fold-named helper that RETURNS the replacement value (no RIUW).
    Scoped to the unguarded, positive-guard, pure-builder shape -- fold-name gated, single match on
    an instruction-typed parameter (`instr_params`), Builder.Create* lets inlined, non-bail return
    tree-ified. Guards, const emitters, and in-place mutation decline (this cut, never a dropped
    premise). AST-based; the caller supplies fn_name + instr_params from source or the AST."""
    from o2t.intent.pass_graph import _FOLD_CONTRACT_RE
    if not fn_name or not _FOLD_CONTRACT_RE.search(fn_name):
        return None                                   # not a fold-contract name -> decline
    if _has_ir_mutation(ast):
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
    if subj_name is None or subj_name not in instr_params:
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


def _recover_from_ast(ast: dict, fn_name: str, instr_params: set, marker: str) -> dict | None:
    """Shared tail: RIUW/guarded first, else the return-form anchor; drive recover_pair."""
    trees = _extract_riuw(ast)
    if trees is None:
        trees = _return_form_trees(ast, fn_name, instr_params)
    if trees is None:
        return None
    matcher_tree, rewrite_tree, guard_source = trees
    return pg.recover_pair(guard_source, "", marker,
                           matcher_tree=matcher_tree, rewrite_tree=rewrite_tree)


def recover_from_clang(source: str, marker: str = "probe.recovered.fold",
                       clang_bin: str = "clang") -> dict | None:
    """STUB-MODE: recover a fold obligation from in-memory C++ `source` parsed against the minimal
    API stub -- the regex parser is NOT in the loop. Returns recover_pair's formal dict or None.
    Reach is limited to stub-compatible source; see recover_from_source_file for verbatim reach."""
    ast = _dump(source, clang_bin)
    if ast is None:
        return None
    from o2t.intent.pass_graph import _FOLD_NAME_RE, _INSTR_PARAM_RE
    fn = _FOLD_NAME_RE.search(source)
    fn_name = fn.group(1) if fn else ""
    sig = source[:source.find("{")] if "{" in source else source
    instr_params = {m.group(1) for m in _INSTR_PARAM_RE.finditer(sig)}
    return _recover_from_ast(ast, fn_name, instr_params, marker)


def _dump_source_file(cpp_path: str, fn_name: str, includes: list[str],
                      clang_bin: str = "clang", timeout: int = 300) -> dict | None:
    """SOURCE-FILE MODE: parse a whole upstream `.cpp` against its REAL compile context and
    `-ast-dump-filter` to just `fn_name` (keeps the AST tractable -- KBs, not the GB of a full TU).
    Returns the single FunctionDecl node, or None. Each `includes` dir is passed as `-I`."""
    clang = cp.find_clang(clang_bin)
    if clang is None:
        return None
    argv = [clang, "-Xclang", "-ast-dump=json", "-Xclang", f"-ast-dump-filter={fn_name}",
            "-fsyntax-only", "-std=c++17"]
    for inc in includes:
        argv += ["-I", inc]
    argv.append(cpp_path)
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    try:
        return json.JSONDecoder().raw_decode(out)[0]     # first (matching) decl
    except (json.JSONDecodeError, ValueError):
        return None


def recover_from_source_file(cpp_path: str, fn_name: str, includes: list[str],
                             marker: str = "probe.recovered.fold",
                             clang_bin: str = "clang") -> dict | None:
    """VERBATIM reach: recover a fold obligation from UNMODIFIED upstream `.cpp` source, parsed in
    its real compile context (`includes` = the LLVM public headers + the pass's lib-internal
    header). The compiler's own parser builds the tree from real code -- the regex parser fully out
    of the loop, no stub approximation. Returns recover_pair's formal dict, or None on any decline.
    The fold name/instruction-params are read from the AST FunctionDecl."""
    ast = _dump_source_file(cpp_path, fn_name, includes, clang_bin)
    if ast is None or ast.get("kind") != "FunctionDecl":
        return None
    return _recover_from_ast(ast, ast.get("name") or fn_name, _instr_params_from_ast(ast), marker)


def _body_compound(fn_decl: dict) -> dict | None:
    for ch in cp.inner(fn_decl):
        if ch.get("kind") == "CompoundStmt":
            return ch
    return None


def _recover_arm(if_stmt: dict, marker: str) -> dict | None:
    """One cascade arm `if (match(&I, PAT) && <guards>) <then returns REWRITE>` -> an obligation,
    or None. Guard/mutation/non-vocab checks are SCOPED TO THE ARM (the if-condition + then-branch),
    so a function-level prelude (`assert`, unused `getOperand` lets) does not block the arm. The
    then-branch's Builder.Create* lets are inlined; the rewrite is its single non-bail return."""
    kids = cp.inner(if_stmt)
    if len(kids) < 2:
        return None
    cond, then = kids[0], kids[1]
    conjuncts = _flatten_and(cond)
    is_match = [c for c in conjuncts
                if c.get("kind") in ("CallExpr", "CXXMemberCallExpr") and cp.callee_name(c) == "match"]
    if len(is_match) != 1:
        return None
    guard_srcs = []
    for c in conjuncts:
        if c is is_match[0]:
            continue
        if c.get("kind") not in ("CallExpr", "CXXMemberCallExpr"):
            return None
        rs = _reconstruct_guard(c)
        if rs is None:
            return None
        guard_srcs.append(rs)
    if _has_ir_mutation(then) or _has_nonvocab_call(then):
        return None                                   # mutation / const-emitter in the arm -> decline
    rets: list = []
    _nonbail_returns(then, rets)
    if len(rets) != 1:
        return None
    lets: dict = {}
    _collect_lets(then, lets)
    m_args = cp.call_args(is_match[0])
    if len(m_args) != 2:
        return None
    try:
        mt, rt = _to_tree(m_args[1]), _to_tree(rets[0], lets)
    except (_Unmappable, IndexError):
        return None
    return pg.recover_pair(" && ".join(guard_srcs), "", marker, matcher_tree=mt, rewrite_tree=rt)


def recover_folds_from_source_file(cpp_path: str, fn_name: str, includes: list[str],
                                   marker: str = "probe.recovered.fold",
                                   clang_bin: str = "clang") -> list[dict]:
    """CASCADE-aware verbatim recovery: every top-level `if (match(&I, ...)) return <rewrite>;` arm
    of a fold-named function becomes its own obligation, tagged `arm`. Reads the real-headers AST
    (parser-free); a function-level prelude (asserts, operand lets) is tolerated. Falls back to the
    single-obligation path (RIUW / return-form) when the body is not a cascade. Returns [] on
    decline. The refutation-standalone caveat of pass_graph's cascade slicing applies to arm > 0."""
    ast = _dump_source_file(cpp_path, fn_name, includes, clang_bin)
    if ast is None or ast.get("kind") != "FunctionDecl":
        return []
    from o2t.intent.pass_graph import _FOLD_CONTRACT_RE
    name = ast.get("name") or fn_name
    if not _FOLD_CONTRACT_RE.search(name):
        return []
    body = _body_compound(ast)
    arms: list[dict] = []
    if body is not None:
        for idx, stmt in enumerate(cp.inner(body)):
            if stmt.get("kind") != "IfStmt":
                continue
            matches: list = []
            cp.find_member_call(cp.inner(stmt)[0] if cp.inner(stmt) else {}, "match", matches)
            if not matches:
                continue
            pair = _recover_arm(stmt, marker)
            if pair is not None:
                arms.append({**pair, "arm": len(arms), "standalone": len(arms) > 0})
    if arms:
        return arms
    single = _recover_from_ast(ast, name, _instr_params_from_ast(ast), marker)
    return [{**single, "arm": 0, "standalone": False}] if single is not None else []


def available(clang_bin: str = "clang") -> bool:
    return cp.find_clang(clang_bin) is not None


def llvm_include_dir(clang_bin: str = "clang") -> str | None:
    """Locate the LLVM public header tree (`<prefix>/include` next to the clang binary) that has
    `llvm/IR/PatternMatch.h` -- the include dir source-file mode parses verbatim upstream against.
    Returns None if not found (source-file mode then skips)."""
    clang = cp.find_clang(clang_bin)
    if clang is None:
        return None
    inc = Path(clang).resolve().parent.parent / "include"
    return str(inc) if (inc / "llvm" / "IR" / "PatternMatch.h").exists() else None


def main(argv=None) -> int:
    import argparse
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
