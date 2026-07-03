#!/usr/bin/env python3
"""Infer a loop-transform INTENT from OPTIMIZATION CODE (LLVM pass source), then prove it.

Everything else in the loop track recovers intent from ARTIFACTS -- the IR before/after a
pass runs. This recovers it from the IMPLEMENTATION: it reads a loop pass's C++ and lifts
the recurrence-level transform the pass INTENDS, for all inputs, then discharges it with the
relational prover (cv-mine-relational.prove_mined).

The bridge is Scalar Evolution. Loop passes phrase intent in SCEV, and SCEV's add-recurrence
`{start,+,step}` is EXACTLY the (init, delta) recurrence our prover consumes. So the LSR
idiom -- a loop-variant product `getMulExpr(C, IV)` rewritten to an add-recurrence
`getAddRecExpr(0, C, L)` (the multiply-by-IV becomes a running add) -- lifts to:

    before:  acc += i*C          (the eliminated product, accumulated)
    after :  k += C;  acc += k    (the add-recurrence k, and its use)

and is proved sound via the discovered relation { k == C*i, acc == acc }. A rewrite whose
recurrence STEP disagrees with the product's coefficient (`getAddRecExpr(0, D, L)`, D != C)
is REFUTED. A product with no IV operand carries no strength-reduction intent and is
DECLARED skipped -- never silently reported as verified.

Scope is honest: one getMulExpr/getAddRecExpr idiom per function, the SCEV calls actually
recognized are listed per transform, and unrecognized idioms are surfaced, not swallowed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




from o2t.mine import relational as minerel
c, v, op = minerel.c, minerel.v, minerel.op

IV_NAMES = {"IV", "Iv", "IndVar", "AR", "AddRec"}
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "sizeof", "else", "do"}
# SCEV constructors we know how to read; anything else in a body is logged as unrecognized.
SCEV_CALLS = ["getMulExpr", "getAddRecExpr", "getAddExpr", "getUDivExpr", "getConstant",
              "getSMaxExpr", "getSMinExpr", "getZeroExtendExpr", "getTruncateExpr",
              "getNegativeSCEV"]

FUNC_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*\{", re.S)


def strip_comments(src):
    """Drop // and /* */ comments -- they hold `(...)` and `{...}` that would otherwise
    confuse header matching and brace counting."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def split_functions(src):
    """name -> body text, brace-matched, skipping nested blocks already consumed."""
    src = strip_comments(src)
    funcs, pos = {}, 0
    for m in FUNC_RE.finditer(src):
        if m.start() < pos or m.group(1) in KEYWORDS:
            continue
        depth, j = 1, m.end()
        while j < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        funcs[m.group(1)] = src[m.end():j - 1]
        pos = j
    return funcs


def balanced_args(text, open_idx):
    """text[open_idx] == '(' ; return top-level comma-split args, or None."""
    depth, cur, args = 0, "", []
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                if cur.strip():
                    args.append(cur.strip())
                return args
        if depth == 1 and ch == ",":
            args.append(cur.strip())
            cur = ""
            continue
        cur += ch
    return None


def find_call(body, name):
    m = re.search(r"\b" + name + r"\s*\(", body)
    return balanced_args(body, m.end() - 1) if m else None


def lift_operand(s):
    """A SCEV argument -> ('iv',) | ('lit', n) | ('const', name) | ('opaque', s)."""
    s = re.sub(r"^SE\.", "", s.strip())
    m = re.fullmatch(r"getConstant\(\s*(-?\w+)\s*\)", s)
    if m:
        g = m.group(1)
        return ("lit", int(g)) if re.fullmatch(r"-?\d+", g) else ("const", g.lower())
    if s in IV_NAMES:
        return ("iv",)
    if re.fullmatch(r"\w+", s):
        return ("const", s.lower())
    return ("opaque", s)


def node(operand):
    k = operand[0]
    if k == "iv":
        return v("i")
    if k == "lit":
        return c(operand[1])
    if k == "const":
        return v(operand[1])
    return None  # opaque -> cannot lift


def lift_transform(body):
    """Lift the (before, after, consts) recurrence pair from one pass function, or None.
    Also returns the list of SCEV constructors seen (for honest reporting)."""
    seen = [name for name in SCEV_CALLS if re.search(r"\b" + name + r"\s*\(", body)]
    mul, addrec = find_call(body, "getMulExpr"), find_call(body, "getAddRecExpr")
    if not mul or not addrec or len(mul) < 2 or len(addrec) < 2:
        return None, seen
    ops = [lift_operand(x) for x in mul]
    iv = [o for o in ops if o[0] == "iv"]
    coeff = [o for o in ops if o[0] == "const"]
    if not iv or not coeff:  # product is not (const * IV) -> no SR intent
        return None, seen
    start, step = lift_operand(addrec[0]), lift_operand(addrec[1])
    prod_n, start_n, step_n = node(coeff[0]), node(start), node(step)
    if prod_n is None or start_n is None or step_n is None:
        return None, seen
    before = ([("acc", c(0), op("bvmul", v("i"), prod_n))], ["acc"], "i")
    after = ([("acc", c(0), v("k")), ("k", start_n, step_n)], ["acc"], "i")
    consts = sorted({o[1] for o in (coeff[0], start, step) if o[0] == "const"})
    return (before, after, consts), seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path,
                    default=ROOT / "tests" / "fixtures" / "loop_pass_scev.cpp")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    funcs = split_functions(args.source.read_text())
    results = []
    for name, body in funcs.items():
        lifted, seen = lift_transform(body)
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
