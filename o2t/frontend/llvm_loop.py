#!/usr/bin/env python3
"""Parse REAL LLVM IR loops and synthesize their closed-form invariant for all n.

This consumes actual `.ll` -- the form LLVM `opt`/`clang` emit -- rather than a
pseudo-C++ fixture. From a counted loop it extracts the PHI-based recurrence (a mini
scalar evolution):

    %x = phi [ INIT, %preheader ], [ %x.next, %latch ]   ;  %x.next = add %x, DELTA

DELTA is resolved through the loop's temporaries (e.g. `%t = mul %i, %c` -> i*c). The
PHI whose step is `+1` is the induction variable i; the others are accumulators. Each
accumulator's recurrence is handed to cv-synth-invariant-poly, which discharges the
closed form over Z (sound for every bitvector width).

So `@triangular` (`acc += i`) is proved `2*acc == i*i - i`, `@scaled_triangular`
(`acc += i*c`) `2*acc == c*i*i - c*i`, `@sum_const` (`acc += a`) `acc == a*i` -- all
mined from real LLVM IR, for every trip count.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




from o2t.synth import poly
c, v, op = poly.c, poly.v, poly.op
MASK = (1 << 32) - 1
BVOP = {"add": "bvadd", "sub": "bvsub", "mul": "bvmul", "shl": "bvshl"}

# Tolerant of real opt/clang output: `define dso_local i32 @f(i32 noundef %0) #0 {`,
# numbered SSA values, attribute groups.
DEF_RE = re.compile(r"define\b[^@]*@(\w+)\s*\(([^)]*)\)[^{]*\{")
LABEL_RE = re.compile(r"^([\w.]+):")
PHI_RE = re.compile(r"(%[\w.]+)\s*=\s*phi\s+\S+\s+(.+)")
PAIR_RE = re.compile(r"\[\s*(\S+?)\s*,\s*(%[\w.]+)\s*\]")
BIN_RE = re.compile(r"(%[\w.]+)\s*=\s*(add|sub|mul|shl)\s+(?:nsw\s+|nuw\s+|exact\s+|nneg\s+)*"
                    r"\S+\s+(\S+?),\s*(\S+)")


def sanitize(tok):
    name = tok.lstrip("%").replace(".", "_")
    return ("v" + name) if name[:1].isdigit() else name  # SMT symbols can't start with a digit


def split_functions(text):
    out = {}
    for m in DEF_RE.finditer(text):
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        out[m.group(1)] = (m.group(2), text[m.end():j - 1])
    return out


def parse_loop(body):
    """-> (loop_label, {phi_var: [(val, label), ...]}, {def_var: (op, a, b)})."""
    phis, defs, cur = {}, {}, None
    loop_label = None
    for raw in body.splitlines():
        line = raw.strip()
        lab = LABEL_RE.match(line)
        if lab:
            cur = lab.group(1)
            continue
        m = PHI_RE.match(line)
        if m:
            phis[m.group(1)] = PAIR_RE.findall(m.group(2))
            loop_label = cur  # PHIs live in the loop header
            continue
        b = BIN_RE.match(line)
        if b:
            defs[b.group(1)] = (b.group(2), b.group(3), b.group(4))
    return loop_label, phis, defs


def resolve(tok, defs, iv, seen=()):
    if re.fullmatch(r"-?\d+", tok):
        return c(int(tok) & MASK)
    if tok in defs and tok not in seen:
        o, a, b = defs[tok]
        return op(BVOP[o], resolve(a, defs, iv, seen + (tok,)), resolve(b, defs, iv, seen + (tok,)))
    if tok == iv:
        return v("i")
    return v(sanitize(tok))  # a parameter (loop-invariant)


def recurrences(body):
    loop_label, phis, defs = parse_loop(body)
    if loop_label is None:
        return None
    rec = {}  # phi_var -> (init_tok, delta_tok)
    for var, pairs in phis.items():
        # The recurrence incoming is the one COMPUTED in the loop (a value in `defs`);
        # the other is the init from the preheader. This is rotation-agnostic -- it
        # works whether the back-edge is the header itself (canonical) or a separate
        # latch block (clang -O1 rotated loops).
        init_tok = recur_tok = None
        for val, label in pairs:
            (init_tok, recur_tok) = (init_tok, val) if val in defs else (val, recur_tok)
        if recur_tok is None or recur_tok not in defs:
            return None
        o, a, b = defs[recur_tok]
        if o != "add":
            return None
        delta_tok = b if a == var else (a if b == var else None)
        if delta_tok is None:
            return None
        rec[var] = (init_tok, delta_tok)
    iv = next((var for var, (_, d) in rec.items() if re.fullmatch(r"-?\d+", d) and int(d) == 1), None)
    if iv is None:
        return None
    accs = {var: (i0, d) for var, (i0, d) in rec.items() if var != iv}
    return iv, accs, defs


def analyze(z3_bin, name, params, body):
    res = recurrences(body)
    if res is None:
        return {"function": name, "status": "no-recurrence"}
    iv, accs, defs = res
    consts = [sanitize(p.split()[-1]) for p in params.split(",") if p.strip()]
    out = {"function": name, "accumulators": []}
    for var, (init_tok, delta_tok) in accs.items():
        init = resolve(init_tok, defs, iv)
        delta = resolve(delta_tok, defs, iv)
        found = poly.synthesize(z3_bin, consts, init, delta)
        out["accumulators"].append({"var": sanitize(var),
                                    "invariant": found["invariant"] if found else None})
    out["status"] = "proved" if all(a["invariant"] for a in out["accumulators"]) else "no-invariant"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path, default=ROOT / "tests" / "fixtures" / "llvm_loops.ll")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    results = [analyze(z3_bin, name, params, body)
               for name, (params, body) in split_functions(args.source.read_text()).items()]
    proved = [r for r in results if r["status"] == "proved"]
    ok = bool(proved) and all(r["status"] == "proved" for r in results)
    report = {"functions": len(results), "proved": len(proved), "results": results, "ok": ok}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"functions": len(results), "proved": len(proved), "ok": ok}, sort_keys=True))
    for r in results:
        inv = "; ".join(a["invariant"] or f"{a['var']}=?" for a in r.get("accumulators", [])) or r["status"]
        print(f"  [{r['status']}] {r['function']}: {inv}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
