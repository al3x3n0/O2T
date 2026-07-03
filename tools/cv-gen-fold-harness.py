#!/usr/bin/env python3
"""Generate a symbolic-IR fold harness FROM A REAL MINED FOLD (closes B's last gap).

cv-extract-pass-model turns miner findings into a per-function pass model -- an
ordered cascade of branches, each {guard, output} over a known opcode. This tool
LOWERS that model to a compilable C++ harness against the mock LLVM IR: every
branch's guard becomes a C++ predicate, its output a C++ value expression, and a
soundness check compares the fold's replacement against the instruction's true
value (the opcode applied to the operands).

The result is exactly the artifact cv-klee-fold runs, but synthesized from mined
source instead of hand-written:

  * Under O2T_WITH_KLEE: klee_make_symbolic operands + klee_assert ->
    KLEE explores every operand value (the true KLEE-on-bitcode path).
  * Natively (here, KLEE absent): concrete enumeration over [-8,8]^2 -- the
    KleeCompat fallback -- which already includes any small-coefficient miscompile
    trigger.

Teeth: a fold mined with an unsound branch (e.g. add x,x -> x) must be caught when
its generated harness runs; a clean fold must pass. This cross-checks the symbolic
symexec verdict (cv-symexec-pass) against actual EXECUTION of the same mined model.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INCLUDE = ROOT / "include"




sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.intent import extract_pass_model as _epm

# --- DSL -> C++ lowering ---------------------------------------------------- //
# Mirrors cv_formal_ir.expr_to_smt (values) and cv-symexec-pass.guard_to_smt
# (guards), but emits int32 C++ instead of SMT-LIB. uint32 casts give two's-
# complement wraparound matching bit-vector semantics.
SCMP = {"eq": "==", "ne": "!=", "slt": "<", "sle": "<=", "sgt": ">", "sge": ">="}
UCMP = {"ult": "<", "ule": "<=", "ugt": ">", "uge": ">="}
BINOP = {"bvadd": "+", "bvsub": "-", "bvmul": "*", "bvand": "&", "bvor": "|", "bvxor": "^"}
SHIFT = {"bvshl": "<<", "bvlshr": ">>", "bvashr": ">>"}


class LowerError(Exception):
    pass


def val_cpp(n: dict, varmap: dict | None = None) -> str:
    op = n.get("op")
    if op == "var":
        name = n["name"]
        return varmap[name] if varmap and name in varmap else f"{name}->val"
    if op == "bvconst":
        v = int(n["value"]) & 0xFFFFFFFF
        signed = v - (1 << 32) if v >= (1 << 31) else v
        return f"(int32_t){signed}"
    if op == "bvneg":
        return f"(int32_t)(0u - (uint32_t)({val_cpp(n['args'][0], varmap)}))"
    if op in BINOP:
        a, b = val_cpp(n["args"][0], varmap), val_cpp(n["args"][1], varmap)
        return f"(int32_t)((uint32_t)({a}) {BINOP[op]} (uint32_t)({b}))"
    if op == "bvashr":  # arithmetic (signed) right shift
        a, b = val_cpp(n["args"][0], varmap), val_cpp(n["args"][1], varmap)
        return f"(int32_t)((int32_t)({a}) >> ((uint32_t)({b}) & 31u))"
    if op in SHIFT:  # bvshl / bvlshr -- unsigned
        a, b = val_cpp(n["args"][0], varmap), val_cpp(n["args"][1], varmap)
        return f"(int32_t)((uint32_t)({a}) {SHIFT[op]} ((uint32_t)({b}) & 31u))"
    if op == "ite":
        c = guard_cpp(n["args"][0], varmap)
        t, e = val_cpp(n["args"][1], varmap), val_cpp(n["args"][2], varmap)
        return f"(({c}) ? ({t}) : ({e}))"
    raise LowerError(f"unsupported value op: {op}")


def guard_cpp(g: dict, varmap: dict | None = None) -> str:
    op = g.get("op")
    if op in SCMP:
        a, b = val_cpp(g["args"][0], varmap), val_cpp(g["args"][1], varmap)
        return f"((int32_t)({a}) {SCMP[op]} (int32_t)({b}))"
    if op in UCMP:
        a, b = val_cpp(g["args"][0], varmap), val_cpp(g["args"][1], varmap)
        return f"((uint32_t)({a}) {UCMP[op]} (uint32_t)({b}))"
    if op == "not":
        return f"(!{guard_cpp(g['args'][0], varmap)})"
    if op in ("and", "or"):
        sep = " && " if op == "and" else " || "
        return "(" + sep.join(guard_cpp(a, varmap) for a in g["args"]) + ")"
    raise LowerError(f"unsupported guard op: {op}")


HEADER = """// GENERATED from a mined fold by cv-gen-fold-harness.py -- DO NOT EDIT.
// Source function: {function}  (opcode {opcode}, {nbranch} branch(es))
#include "o2t/KleeCompat.h"
#include <cstdint>
#include <cstdio>

namespace {{
struct Value {{ int32_t val; }};
struct Instruction {{ Value *Op0; Value *Op1; }};
Value *replaceInstUsesWith(Instruction &, Value *V) {{ return V; }}
Value POOL[64];
int POOLN = 0;
Value *mk(int32_t v) {{ POOL[POOLN % 64].val = v; return &POOL[POOLN++ % 64]; }}
"""


def generate(model: dict) -> str:
    operands = model.get("operands", ["Op0", "Op1"])
    body = [HEADER.format(function=model.get("function", "?"),
                          opcode=model.get("opcode", "?"), nbranch=len(model["branches"]))]
    # the fold cascade -- one `if (guard) return output;` per mined branch
    body.append(f"Value *fold(Value *{operands[0]}, Value *{operands[1]}, Instruction &I) {{")
    for br in model["branches"]:
        g = guard_cpp(br["guard"])
        out = val_cpp(br["output"])
        body.append(f"  if ({g}) return replaceInstUsesWith(I, mk({out}));  // {br.get('name', '')}")
    body.append("  return nullptr;")
    body.append("}")
    # reference: the opcode applied to the operands, evaluated on the local ints
    ref_node = {"op": model["opcode"], "args": [{"op": "var", "name": o} for o in operands]}
    ref = val_cpp(ref_node, varmap={operands[0]: "a", operands[1]: "b"})
    body.append("""
int checkFold(int32_t a, int32_t b) {{
  Value V0{{a}}, V1{{b}};
  Instruction I{{&V0, &V1}};
  Value *R = fold(&V0, &V1, I);
  if (R == nullptr) return 0;
  int32_t Reference = {ref};
  if (R->val != Reference) {{
#if !(defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
    std::printf("MISCOMPILE: {fn} replaced op(%d,%d) with %d (true value %d)\\n",
                a, b, R->val, Reference);
#endif
    return 1;
  }}
  return 0;
}}
}}  // namespace

int main() {{
#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
  int32_t a, b;
  klee_make_symbolic(&a, sizeof(a), "a");
  klee_make_symbolic(&b, sizeof(b), "b");
  klee_assert(checkFold(a, b) == 0);
  return 0;
#else
  for (int a = -8; a <= 8; ++a)
    for (int b = -8; b <= 8; ++b)
      if (checkFold(a, b)) return 1;
  std::printf("SOUND over enumerated domain [-8,8]^2\\n");
  return 0;
#endif
}}
""".format(ref=ref, fn=model.get("function", "?")))
    return "\n".join(body)


# --- compile + run ---------------------------------------------------------- //
def default_clang() -> str | None:
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    for name in ("clang++", "clang"):
        if (base / name).exists():
            return str(base / name)
    return shutil.which("clang++") or shutil.which("clang")


def compile_run(model: dict, clang: str) -> dict:
    try:
        src = generate(model)
    except LowerError as e:
        return {"function": model.get("function"), "status": "unlowerable", "reason": str(e)}
    with tempfile.TemporaryDirectory() as d:
        cpp, exe = Path(d) / "gen.cpp", Path(d) / "gen"
        cpp.write_text(src)
        cc = subprocess.run([clang, "-std=c++17", "-I", str(INCLUDE), str(cpp), "-o", str(exe)],
                            capture_output=True, text=True)
        if cc.returncode != 0:
            return {"function": model.get("function"), "status": "compile-error",
                    "stderr": cc.stderr[-400:]}
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        return {"function": model.get("function"), "opcode": model.get("opcode"),
                "branches": len(model["branches"]),
                "status": "sound" if run.returncode == 0 else "miscompile",
                "output": run.stdout.strip(), "backend": "native-enumeration"}


# clean control: a single add-zero branch (sound). Demonstrates no false positives.
SOUND_MODEL = {
    "function": "foldAddZeroOnly", "opcode": "bvadd", "operands": ["Op0", "Op1"],
    "branches": [{"name": "add-zero",
                  "guard": {"op": "eq", "args": [{"op": "var", "name": "Op1"},
                                                 {"op": "bvconst", "bits": 32, "value": 0}]},
                  "output": {"op": "var", "name": "Op0"}}]}


def mined_models() -> list[dict]:
    findings = [json.loads(l) for l in _epm.DEFAULT_FINDINGS.read_text().splitlines() if l.strip()]
    return _epm.build_models(findings, _epm.marker_opcode(_epm.DEFAULT_FACTS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--model", type=Path, help="a pass-model JSON (single object or list)")
    src.add_argument("--mine", type=Path, metavar="SNIPPET", help="run the real miner, then generate")
    src.add_argument("--emit", type=Path, metavar="MODEL", help="just print generated C++ for a model")
    ap.add_argument("--selftest", action="store_true",
                    help="clean fold -> sound; real mined unsound fold -> miscompile")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    if args.emit is not None:
        model = json.loads(args.emit.read_text())
        print(generate(model[0] if isinstance(model, list) else model))
        return 0

    clang = default_clang()
    if clang is None:
        print(json.dumps({"status": "skipped", "reason": "clang not found"}))
        return 0

    if args.selftest:
        sound = compile_run(SOUND_MODEL, clang)
        mined = mined_models()
        mined_runs = [compile_run(m, clang) for m in mined]
        any_miscompile = any(r["status"] == "miscompile" for r in mined_runs)
        report = {"clang": clang, "sound_control": sound,
                  "mined_models": len(mined), "mined_runs": mined_runs,
                  "ok": sound.get("status") == "sound" and any_miscompile}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"ok": report["ok"], "sound_control": sound.get("status"),
                          "mined_models": len(mined),
                          "mined_miscompiles": sum(r["status"] == "miscompile" for r in mined_runs)},
                         sort_keys=True))
        for r in mined_runs:
            print(f"  mined {r.get('function')}: {r.get('status')} ({r.get('output', '')})", file=sys.stderr)
        return 0 if report["ok"] else 1

    # one-shot: --model or --mine
    if args.model is not None:
        data = json.loads(args.model.read_text())
        models = data if isinstance(data, list) else [data]
    elif args.mine is not None:
        if not _epm.DEFAULT_MINER.exists():
            print(json.dumps({"status": "skipped", "reason": "miner not built"}))
            return 0
        findings = _epm.run_miner(args.mine, _epm.DEFAULT_MINER)
        models = _epm.build_models(findings, _epm.marker_opcode(_epm.DEFAULT_FACTS))
    else:
        ap.error("provide --model, --mine, --emit, or --selftest")

    runs = [compile_run(m, clang) for m in models]
    out = {"models": len(models), "runs": runs}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"models": len(models),
                      "miscompiles": sum(r["status"] == "miscompile" for r in runs)}, sort_keys=True))
    for r in runs:
        print(f"  {r.get('function')}: {r.get('status')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
