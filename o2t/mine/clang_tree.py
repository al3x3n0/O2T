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


def _intrinsic_template(call_node: dict, source) -> str | None:
    """The non-type template argument of `m_Intrinsic<Intrinsic::ID>` -- the ONE datum clang's typed
    AST elides (it resolves the call structure but prints only `IntrinsicID_match`, never the ID). The
    compiler DOES pin the exact source span of the template-id (the callee DeclRefExpr's range), so we
    read that single token at the coordinate the compiler itself provides -- not a structural parse.
    Returns the trailing `::`-segment (`ctpop`) or None (declining, e.g. stub mode with no source)."""
    if source is None or not cp.inner(call_node):
        return None
    callee = cp.strip_casts(cp.inner(call_node)[0])
    rng = callee.get("range") or {}
    beg = (rng.get("begin") or {}).get("offset")
    end = rng.get("end") or {}
    eoff, tok = end.get("offset"), end.get("tokLen", 0)
    if beg is None or eoff is None:
        return None
    span = source[beg:eoff + tok]
    if isinstance(span, (bytes, bytearray)):
        span = span.decode("utf-8", "replace")
    lt, gt = span.find("<"), span.rfind(">")
    if lt < 0 or gt <= lt:
        return None
    return (span[lt + 1:gt].strip().split("::")[-1].strip() or None)


def _project_getoperand(call_node: dict, ctx: dict) -> dict | None:
    """A rewrite-side `CmpK->getOperand(J)` -> a synthetic projection NAME node, registering the
    matched operand subtree in `ctx['projections']` (mirrors pass_graph._two_icmp_arm._project). J
    projects m_ICmp arg[J+1] (args = [pred, op0, op1]); an unprojectable shape returns None (decline)."""
    inr = cp.inner(call_node)
    if not inr or inr[0].get("kind") != "MemberExpr" or not cp.inner(inr[0]):
        return None
    base = cp.strip_casts(cp.inner(inr[0])[0])
    bname = (base.get("referencedDecl") or {}).get("name") if base.get("kind") == "DeclRefExpr" else None
    cmp_names = ctx.get("cmp_names") or {}
    if bname not in cmp_names:
        return None
    args = cp.call_args(call_node)
    if len(args) != 1 or cp.strip_casts(args[0]).get("kind") != "IntegerLiteral":
        return None
    j = int(cp.strip_casts(args[0]).get("value", "0"))
    tree = (ctx.get("cmp_trees") or {}).get(bname)
    if not tree or tree.get("name") not in ("m_ICmp", "m_c_ICmp") or len(tree.get("args", [])) != 3 \
            or j not in (0, 1):
        return None
    pname = f"__proj_{cmp_names[bname]}_{j}"
    ctx["projections"][pname] = tree["args"][j + 1]
    return {"kind": "name", "name": pname}


def _to_tree(node: dict, lets: dict | None = None, ctx: dict | None = None) -> dict:
    """Map one clang AST expression node to pass_graph's {kind,name,args,template} tree. A
    DeclRefExpr to a local single-assignment `let` (a `Value *T = Builder.Create*(...)` binding) is
    inlined by recursing into its initializer -- the AST-level equivalent of the string path's
    let-inlining, so a return-form rewrite that names intermediates recovers compositionally.

    `ctx` (optional) carries the two-icmp contract's rewrite context: `source` (bytes, for the
    m_Intrinsic template-id read), and in `rewrite` mode `cmp_names`/`cmp_trees`/`projections` (for
    `CmpK->getOperand(J)` projection) and `getop_lets` (a `Value *CtPop = CmpK->getOperand(J)` binding,
    inlined like a Create-let). In rewrite mode a `->getType()` chain lowers to the opaque `Ty` name,
    exactly as the string path's _normalize_rewrite does."""
    lets = lets or {}
    ctx = ctx or {}
    n = cp.strip_casts(node)
    k = n.get("kind")
    if k in ("CallExpr", "CXXMemberCallExpr"):
        name = cp.callee_name(n)
        if not name:
            raise _Unmappable("call with no resolvable callee")
        if ctx.get("rewrite"):
            if name == "getType":
                return {"kind": "name", "name": "Ty"}    # `X->getType()` -> opaque type token
            if name == "getOperand":
                proj = _project_getoperand(n, ctx)
                if proj is None:
                    raise _Unmappable("unprojectable getOperand in rewrite")
                return proj
        # Skip clang-materialized DEFAULT arguments (a Builder emitter's defaulted `Twine` name),
        # so the verbatim call's operand arity matches the source's -- the value operands only.
        args = [a for a in cp.call_args(n)
                if cp.strip_casts(a).get("kind") != "CXXDefaultArgExpr"]
        template = _intrinsic_template(n, ctx.get("source")) if name == "m_Intrinsic" else None
        return {"kind": "call", "name": name, "template": template,
                "args": [_to_tree(a, lets, ctx) for a in args]}
    if k == "DeclRefExpr":
        ref = (n.get("referencedDecl") or {}).get("name")
        if not ref:
            raise _Unmappable("unresolved reference")
        if ref in lets:
            return _to_tree(lets[ref], lets, ctx)        # inline the SSA let
        if ref in (ctx.get("getop_lets") or {}):
            return _to_tree(ctx["getop_lets"][ref], lets, ctx)   # inline the getOperand let
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
    """Shared tail (SINGLE-obligation view): RIUW/guarded first, else the return-form anchor; drive
    recover_pair -- None for a predicate-SET fold (multiple cases can't collapse to one, exactly as
    pass_graph.recover_from_function refuses)."""
    trees = _extract_riuw(ast)
    if trees is None:
        trees = _return_form_trees(ast, fn_name, instr_params)
    if trees is None:
        return None
    matcher_tree, rewrite_tree, guard_source = trees
    return pg.recover_pair(guard_source, "", marker,
                           matcher_tree=matcher_tree, rewrite_tree=rewrite_tree)


def _recover_cases_from_ast(ast: dict, fn_name: str, instr_params: set, marker: str) -> list[dict]:
    """Cases-aware tail: like _recover_from_ast but returns ALL obligation cases -- a predicate-SET
    guard (`ICmpInst::isEquality(Pred)`, phase 39) expands to one case per member (eq/ne/...), each
    instantiated consistently through the matcher and the rewrite, and ALL must prove. Returns one
    entry for an ordinary single-case fold (byte-identical to _recover_from_ast), [] on decline."""
    trees = _extract_riuw(ast)
    if trees is None:
        trees = _return_form_trees(ast, fn_name, instr_params)
    if trees is None:
        return []
    matcher_tree, rewrite_tree, guard_source = trees
    return pg.recover_pair_cases(guard_source, "", marker,
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


# --- the two-icmp caller contract via the AST (pass_graph phase 40, parser-free) -----------------
# `foldX(ICmpInst *Cmp0, ICmpInst *Cmp1, bool IsAnd, ...)`: a negated-OR BAILOUT establishes both
# icmp matches, then per-IsAnd-case arms return the combined rewrite. The AST producer mirrors
# pass_graph._two_icmp_arm on the real-headers tree -- combining the two matched icmp trees under the
# arm's IsAnd-selected connective (m_And/m_Or), reconstructing the `PredK == ICMP_*` guards, and
# projecting `CmpK->getOperand(J)` (via the CtPop let) to the matched operand subtree. Anything
# outside this shape DECLINES.
def _twoicmp_params(fn_decl: dict) -> tuple[list[str], str | None]:
    """The fold's two `ICmpInst *` parameter names and its `bool IsAnd` selector name (or None)."""
    icmps, band = [], None
    for p in cp.inner(fn_decl):
        if p.get("kind") != "ParmVarDecl" or not p.get("name"):
            continue
        qt = (p.get("type") or {}).get("qualType", "")
        if "ICmpInst" in qt and "*" in qt:
            icmps.append(p["name"])
        elif qt in ("bool", "_Bool") and p["name"] == "IsAnd":
            band = p["name"]
    return icmps, band


def _getoperand_lets(body: dict) -> dict:
    """Top-level `Value *CtPop = CmpK->getOperand(J);` bindings -- name -> the getOperand init node."""
    out: dict = {}
    for stmt in cp.inner(body):
        if stmt.get("kind") != "DeclStmt":
            continue
        for vd in cp.inner(stmt):
            if vd.get("kind") != "VarDecl" or not cp.inner(vd):
                continue
            init = cp.strip_casts(cp.inner(vd)[-1])
            if init.get("kind") == "CXXMemberCallExpr" and cp.callee_name(init) == "getOperand":
                out[vd.get("name")] = init
    return out


def _bailout_matches(if_stmt: dict, icmp_params: list[str]) -> dict | None:
    """A `if (!match(Cmp0, PAT0) || !match(Cmp1, PAT1)) return nullptr;` bailout -> {subject: pattern}
    for both icmp params, or None. The then-branch must be a PURE bailout (only bail returns), so
    reaching past it means both matched -- the precondition every arm builds on."""
    kids = cp.inner(if_stmt)
    if len(kids) < 2:
        return None
    cond, then = kids[0], kids[1]
    nonbail: list = []
    _nonbail_returns(then, nonbail)
    if nonbail:
        return None                                    # not a pure bailout -> not the contract shape
    matches: list = []
    cp.find_member_call(cond, "match", matches)
    out: dict = {}
    for m in matches:
        margs = cp.call_args(m)
        if len(margs) != 2:
            return None
        subj = cp.strip_casts(margs[0])
        sname = (subj.get("referencedDecl") or {}).get("name") if subj.get("kind") == "DeclRefExpr" else None
        if sname not in icmp_params or sname in out:
            return None
        out[sname] = margs[1]
    return out if set(out) == set(icmp_params) else None


def _pred_guard_src(node: dict) -> str | None:
    """A `PredK == ICmpInst::ICMP_*` conjunct -> its `Pred == ICMP_*` source form (which
    recover_pair_cases' _PRED_GUARD_RE reads), or None for any other shape (decline)."""
    if node.get("kind") != "BinaryOperator" or node.get("opcode") != "==":
        return None
    kids = cp.inner(node)
    if len(kids) != 2:
        return None
    lhs, rhs = cp.strip_casts(kids[0]), cp.strip_casts(kids[1])
    ln = (lhs.get("referencedDecl") or {}).get("name") if lhs.get("kind") == "DeclRefExpr" else None
    rn = (rhs.get("referencedDecl") or {}).get("name") if rhs.get("kind") == "DeclRefExpr" else None
    if ln and rn and rn.startswith("ICMP_"):
        return f"{ln} == {rn}"
    if rn and ln and ln.startswith("ICMP_"):             # commuted `ICMP_NE == Pred`
        return f"{rn} == {ln}"
    return None


def _twoicmp_arm(if_stmt: dict, icmp_params: list[str], band: str, cmp_trees: dict,
                 getop_lets: dict, source, marker: str) -> list[dict]:
    """One two-icmp arm `if (IsAnd && PredK == ICMP_* ...) return Builder.Create...;` -> its
    obligation(s). The `IsAnd`/`!IsAnd` conjunct fixes the reachable case (m_And vs m_Or); the
    `PredK == ICMP_*` conjuncts become the precondition; the rewrite projects `CmpK->getOperand(J)`.
    [] for any statement outside the contract shape (never a mis-mapping)."""
    kids = cp.inner(if_stmt)
    if len(kids) < 2:
        return []
    cond, then = kids[0], kids[1]
    polarity = None
    guard_srcs: list[str] = []
    for c in _flatten_and(cond):
        if c.get("kind") == "DeclRefExpr" and (c.get("referencedDecl") or {}).get("name") == band:
            polarity = True                            # `IsAnd` conjunct -> the and-case is reachable
            continue
        if c.get("kind") == "UnaryOperator" and c.get("opcode") == "!":
            inner0 = cp.strip_casts(cp.inner(c)[0]) if cp.inner(c) else {}
            if (inner0.get("referencedDecl") or {}).get("name") == band:
                polarity = False                       # `!IsAnd` conjunct -> the or-case is reachable
                continue
            return []                                  # other negation -> outside the shape
        g = _pred_guard_src(c)
        if g is None:
            return []
        guard_srcs.append(g)
    if polarity is None:
        return []                                      # no IsAnd selector on this arm -> not an arm
    if _has_ir_mutation(then):
        return []
    rets: list = []
    _nonbail_returns(then, rets)
    if len(rets) != 1:
        return []
    projections: dict = {}
    ctx = {"rewrite": True, "source": source, "cmp_trees": cmp_trees, "getop_lets": getop_lets,
           "cmp_names": {icmp_params[0]: "0", icmp_params[1]: "1"}, "projections": projections}
    try:
        rewrite_tree = _to_tree(rets[0], None, ctx)
    except (_Unmappable, IndexError):
        return []
    combined = {"kind": "call", "name": "m_And" if polarity else "m_Or", "template": None,
                "args": [cmp_trees[icmp_params[0]], cmp_trees[icmp_params[1]]]}
    out: list[dict] = []
    for pair in pg.recover_pair_cases(" && ".join(guard_srcs), "", marker, matcher_tree=combined,
                                      rewrite_tree=rewrite_tree, projections=projections):
        pair.setdefault("case", {})[band] = polarity
        pair["marker"] = f"{pair['marker']}.{'and' if polarity else 'or'}"
        out.append(pair)
    return out


def _twoicmp_arms(ast: dict, source, marker: str) -> list[dict]:
    """Recover the two-icmp caller contract from a fold's real-headers AST: two `ICmpInst *` params +
    a `bool IsAnd` selector, a negated-OR bailout binding both matches, per-case arms. [] otherwise."""
    icmp_params, band = _twoicmp_params(ast)
    if len(icmp_params) != 2 or band is None:
        return []
    body = _body_compound(ast)
    if body is None:
        return []
    bail = None
    for stmt in cp.inner(body):
        if stmt.get("kind") == "IfStmt":
            m = _bailout_matches(stmt, icmp_params)
            if m is not None:
                bail = (stmt, m)
                break
    if bail is None:
        return []
    try:
        cmp_trees = {c: _to_tree(pat, None, {"source": source}) for c, pat in bail[1].items()}
    except (_Unmappable, IndexError):
        return []
    getop_lets = _getoperand_lets(body)
    arms: list[dict] = []
    for stmt in cp.inner(body):
        if stmt.get("kind") != "IfStmt" or stmt is bail[0]:
            continue
        for pair in _twoicmp_arm(stmt, icmp_params, band, cmp_trees, getop_lets, source, marker):
            arms.append({**pair, "arm": len(arms), "standalone": len(arms) > 0})
    return arms


# --- the simplifyXInst caller contract via the AST (pass_graph phase 37, parser-free) ------------
# `simplify<Op>Inst(Value *Op0, Value *Op1, ...)` DOCUMENTS its instruction in the NAME (`sub Op0,
# Op1`): the name licenses synthesizing the phantom `m_<Op>(m_Value(Op0), m_Value(Op1))` and
# splicing each arm's `match(OpK, PAT)` into slot K. The spliced operand name is retired; a rewrite
# that still references it lowers to an unbound value and DECLINES (self-enforcing, no unsound splice).
def _simplify_params(fn_decl: dict) -> tuple[str | None, list[str]]:
    """The opname the fold's `simplify<Op>Inst` name declares + its `Value *` parameter names."""
    from o2t.intent.pass_graph import _SIMPLIFY_CONTRACT_RE
    m = _SIMPLIFY_CONTRACT_RE.search((fn_decl.get("name") or "") + "(")
    if m is None:
        return None, []
    params = [p["name"] for p in cp.inner(fn_decl)
              if p.get("kind") == "ParmVarDecl" and p.get("name")
              and "Value" in (p.get("type") or {}).get("qualType", "")
              and "*" in (p.get("type") or {}).get("qualType", "")]
    return m.group(1), params


def _simplify_arm(if_stmt: dict, opname: str, op0: str, op1: str, source, marker: str) -> dict | None:
    """One simplifyXInst arm `if (match(OpK, PAT) [&& guards]) return <value>;` -> its obligation via
    the name-declared phantom instruction, or None. Operand matches splice into the phantom's slots;
    non-match conjuncts must be reconstructible guards (else decline)."""
    from o2t.intent.pass_graph import _OP_TO_MATCHER
    kids = cp.inner(if_stmt)
    if len(kids) < 2:
        return None
    cond, then = kids[0], kids[1]
    slot_of = {op0: 0, op1: 1}
    spliced: dict[int, dict] = {}
    guard_srcs: list[str] = []
    for c in _flatten_and(cond):
        if c.get("kind") in ("CallExpr", "CXXMemberCallExpr") and cp.callee_name(c) == "match":
            margs = cp.call_args(c)
            if len(margs) != 2:
                return None
            subj = cp.strip_casts(margs[0])
            sname = (subj.get("referencedDecl") or {}).get("name") if subj.get("kind") == "DeclRefExpr" else None
            if sname not in slot_of or slot_of[sname] in spliced:
                return None                            # foreign subject / duplicate operand -> decline
            spliced[slot_of[sname]] = margs[1]
        elif c.get("kind") in ("CallExpr", "CXXMemberCallExpr"):
            rs = _reconstruct_guard(c)
            if rs is None:
                return None
            guard_srcs.append(rs)
        else:
            return None                                # bare bool / isa / compare -> outside this cut
    if not spliced:
        return None                                    # no operand match -> not a contract arm
    if _has_ir_mutation(then):
        return None
    rets: list = []
    _nonbail_returns(then, rets)
    if len(rets) != 1:
        return None
    src_ctx = {"source": source}
    primary = {"kind": "call", "name": _OP_TO_MATCHER[opname], "template": None,
               "args": [{"kind": "call", "name": "m_Value", "template": None,
                         "args": [{"kind": "name", "name": op0}]},
                        {"kind": "call", "name": "m_Value", "template": None,
                         "args": [{"kind": "name", "name": op1}]}]}
    try:
        for k, pat in spliced.items():
            primary["args"][k] = _to_tree(pat, None, src_ctx)     # retire the operand: splice its shape
        rewrite_tree = _to_tree(rets[0], None, {"rewrite": True, "source": source})
    except (_Unmappable, IndexError):
        return None
    return pg.recover_pair(" && ".join(guard_srcs), "", marker,
                           matcher_tree=primary, rewrite_tree=rewrite_tree)


def _simplify_arms(ast: dict, source, marker: str) -> list[dict]:
    """Recover the simplifyXInst caller contract from a fold's real-headers AST: the name declares the
    instruction, each `if (match(OpK, ...)) return ...;` arm becomes an obligation. [] otherwise."""
    opname, params = _simplify_params(ast)
    if opname is None or len(params) < 2:
        return []
    body = _body_compound(ast)
    if body is None:
        return []
    arms: list[dict] = []
    for stmt in cp.inner(body):
        if stmt.get("kind") != "IfStmt":
            continue
        pair = _simplify_arm(stmt, opname, params[0], params[1], source, marker)
        if pair is not None:
            arms.append({**pair, "arm": len(arms), "standalone": len(arms) > 0})
    return arms


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
    # single fold, cases-aware: a predicate-SET guard (phase 39) yields multiple cases, all under
    # arm 0 (a refutation of any case refutes the fold); an ordinary fold yields exactly one.
    single_cases = _recover_cases_from_ast(ast, name, _instr_params_from_ast(ast), marker)
    if single_cases:
        return [{**c, "arm": 0, "standalone": False} for c in single_cases]
    # phase-40 shape: the two-icmp caller contract (needs the source bytes for the m_Intrinsic
    # template-id read the typed AST elides); then the phase-37 simplifyXInst name contract.
    try:
        source = Path(cpp_path).read_bytes()
    except OSError:
        return []
    two = _twoicmp_arms(ast, source, marker)
    return two if two else _simplify_arms(ast, source, marker)


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
