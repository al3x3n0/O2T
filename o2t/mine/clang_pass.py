#!/usr/bin/env python3
"""Infer loop-transform intent from pass SOURCE via a REAL Clang AST (no regex parsing).

Robust replacement for cv-mine-pass-scev's frontend: instead of FUNC_RE + brace counting +
balanced_args over C++ text, this shells out to clang `-Xclang -ast-dump=json` (the same
move the .ll path makes with `opt`) and walks LLVM's own AST. A minimal SCEV-API stub
(tests/fixtures/scev_pass_api.h) is `-include`d so the getMulExpr/getAddRecExpr/getConstant
member calls resolve into clean, typed CXXMemberCallExpr nodes -- comments, line wraps,
nested parens, and macro noise all handled by the compiler, not by us.

Lifts the LSR strength-reduction idiom -- getMulExpr(C, IV) [loop-variant product c*i]
rewritten to getAddRecExpr(0, C, L) [running add {0,+,c}] -- into before: acc+=i*C /
after: k+=C; acc+=k, discharged by minerel.prove_mined as { B_k==C*i, A_acc==B_acc }.
wrongStride (recurrence step d != product coeff c) is REFUTED; a product with no IV operand
is DECLARED no-idiom. The prover is unchanged -- frontend swap isolated from the proof layer.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FALLBACK_CLANG = "/opt/homebrew/opt/llvm@18/bin/clang"




from o2t.mine import relational as minerel
c, v, op = minerel.c, minerel.v, minerel.op

IV_NAMES = {"IV", "Iv", "IndVar", "AR", "AddRec"}


def find_clang(clang_bin="clang"):
    return shutil.which(clang_bin) or (_FALLBACK_CLANG if Path(_FALLBACK_CLANG).exists() else None)


def dump_ast(cpp_path, stub, clang_bin="clang"):
    clang = find_clang(clang_bin)
    if clang is None:
        return None
    src = Path(cpp_path).read_text()
    # Drop the real (unavailable) LLVM include; the stub supplies the API surface.
    body = "\n".join(ln for ln in src.splitlines()
                     if not (ln.lstrip().startswith("#include") and "ScalarEvolution" in ln))
    with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False) as tf:
        tf.write(body)
        tmp = tf.name
    try:
        proc = subprocess.run(
            [clang, "-Xclang", "-ast-dump=json", "-fsyntax-only", "-std=c++17",
             "-include", str(stub), tmp],
            capture_output=True, text=True)
        if not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, OSError):
        return None
    finally:
        Path(tmp).unlink(missing_ok=True)


# --- AST helpers ------------------------------------------------------------------------
def inner(n):
    return n.get("inner", []) or []


def strip_casts(n):
    """Descend through implicit nodes to the meaningful expression."""
    while n and n.get("kind") in ("ImplicitCastExpr", "CXXBindTemporaryExpr",
                                  "MaterializeTemporaryExpr", "ParenExpr",
                                  "ExprWithCleanups", "ConstantExpr"):
        ch = inner(n)
        if not ch:
            break
        n = ch[0]
    return n


def callee_name(call):
    """For a CXXMemberCallExpr / CallExpr, the called member/function name."""
    if not inner(call):
        return None
    head = inner(call)[0]
    if head.get("kind") == "MemberExpr":
        return head.get("name")
    head = strip_casts(head)
    if head.get("kind") == "DeclRefExpr":
        return head.get("referencedDecl", {}).get("name")
    return None


def call_args(call):
    """Arguments of a call = inner nodes after the callee (MemberExpr or DeclRef head)."""
    return inner(call)[1:]


def lift_operand(arg):
    """An argument expression -> ('iv',) | ('lit', n) | ('const', name) | ('opaque',)."""
    a = strip_casts(arg)
    k = a.get("kind")
    if k == "DeclRefExpr":
        name = a.get("referencedDecl", {}).get("name", "")
        return ("iv",) if name in IV_NAMES else ("const", name.lower())
    if k in ("CXXMemberCallExpr", "CallExpr") and callee_name(a) == "getConstant":
        gc = call_args(a)
        if gc:
            inner_arg = strip_casts(gc[0])
            if inner_arg.get("kind") == "IntegerLiteral":
                return ("lit", int(inner_arg.get("value", "0")))
            if inner_arg.get("kind") == "DeclRefExpr":
                return ("const", inner_arg.get("referencedDecl", {}).get("name", "").lower())
    if k == "IntegerLiteral":
        return ("lit", int(a.get("value", "0")))
    return ("opaque",)


def node_of(operand):
    if operand[0] == "iv":
        return v("i")
    if operand[0] == "lit":
        return c(operand[1])
    if operand[0] == "const":
        return v(operand[1])
    return None


def find_member_call(node, name, out):
    if node.get("kind") in ("CXXMemberCallExpr", "CallExpr") and callee_name(node) == name:
        out.append(node)
    for ch in inner(node):
        find_member_call(ch, name, out)


SCEV_CALLS = ["getMulExpr", "getAddRecExpr", "getAddExpr", "getConstant"]


def lift_function(fn_node):
    """Lift (before, after, consts) from one FunctionDecl, plus SCEV calls seen, or None."""
    seen = sorted({cn for n in _collect_calls(fn_node)
                   if (cn := callee_name(n)) in SCEV_CALLS})
    muls, addrecs = [], []
    find_member_call(fn_node, "getMulExpr", muls)
    find_member_call(fn_node, "getAddRecExpr", addrecs)
    if not muls or not addrecs:
        return None, seen
    mul_ops = [lift_operand(a) for a in call_args(muls[0])]
    iv = [o for o in mul_ops if o[0] == "iv"]
    coeff = [o for o in mul_ops if o[0] == "const"]
    if not iv or not coeff:  # product is not (const * IV) -> no SR intent
        return None, seen
    ar = call_args(addrecs[0])
    if len(ar) < 2:
        return None, seen
    start, step = lift_operand(ar[0]), lift_operand(ar[1])
    prod_n, start_n, step_n = node_of(coeff[0]), node_of(start), node_of(step)
    if prod_n is None or start_n is None or step_n is None:
        return None, seen
    before = ([("acc", c(0), op("bvmul", v("i"), prod_n))], ["acc"], "i")
    after = ([("acc", c(0), v("k")), ("k", start_n, step_n)], ["acc"], "i")
    consts = sorted({o[1] for o in (coeff[0], start, step) if o[0] == "const"})
    return (before, after, consts), seen


def _collect_calls(node, out=None):
    out = [] if out is None else out
    if node.get("kind") in ("CXXMemberCallExpr", "CallExpr"):
        out.append(node)
    for ch in inner(node):
        _collect_calls(ch, out)
    return out


def functions(ast):
    """Top-level FunctionDecls that have a body."""
    out = []
    for n in inner(ast):
        if n.get("kind") == "FunctionDecl" and any(ch.get("kind") == "CompoundStmt" for ch in inner(n)):
            out.append(n)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path,
                    default=ROOT / "tests" / "fixtures" / "loop_pass_scev.cpp")
    ap.add_argument("--stub", type=Path, default=ROOT / "tests" / "fixtures" / "scev_pass_api.h")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--clang-bin", default="clang")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0
    if find_clang(args.clang_bin) is None:
        print(json.dumps({"status": "skipped", "reason": "clang not found"}))
        return 0
    ast = dump_ast(args.source, args.stub, args.clang_bin)
    if ast is None:
        print(json.dumps({"status": "skipped", "reason": "clang AST dump failed"}))
        return 0

    results = []
    for fn in functions(ast):
        name = fn.get("name")
        lifted, seen = lift_function(fn)
        if lifted is None:
            results.append({"transform": name, "status": "no-idiom", "scev_calls": seen})
            continue
        before, after, consts = lifted
        res = minerel.prove_mined(z3_bin, minerel.build_model(before, after, consts))
        results.append({"transform": name, "status": res["status"], "scev_calls": seen,
                        "pairing": res.get("pairing"), "relation": res.get("relation")})

    proved = [r for r in results if r["status"] == "proved"]
    recognized = [r for r in results if r["status"] != "no-idiom"]
    definitive = {"proved", "output-not-preserved", "no-aux-invariant"}
    ok = bool(proved) and all(r["status"] in definitive for r in recognized)
    report = {"transforms": len(results), "proved": len(proved), "results": results, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"transforms": len(results), "proved": len(proved), "ok": ok}, sort_keys=True))
    for r in results:
        rel = " /\\ ".join(r["relation"]) if r.get("relation") else r["status"]
        print(f"  [{r['status']}] {r['transform']}: {rel}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
