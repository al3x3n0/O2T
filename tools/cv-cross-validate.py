#!/usr/bin/env python3
"""Cross-validate a proven transform against the REAL optimizer (autonomous-verify #2).

A deductive proof says the lifted before/after are equivalent. This independently
CONFIRMS it against reality: lower the lifted before-tree to .ll, run the real opt,
and translation-validate (cv-mini-alive) that the optimizer's output refines the
input. A transform is VERIFIED only when proof AND TV agree -- so a lifter bug
can't fake it (TV runs the real opt and re-derives semantics from the actual .ll),
and a TV refutation is a REAL MISCOMPILE witness.

  verdict per transform:
    verified   -- opt fired the simplification AND mini-alive proved it sound
    no-trigger -- opt left the IR unchanged (couldn't exercise the transform)
    bug        -- mini-alive REFUTED the opt's output (real miscompile) -> witness
    tv-error   -- translation validation could not run

Needs `opt` (CV_LLVM_BIN, default /opt/homebrew/opt/llvm@18/bin) and z3.
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
MINI_ALIVE = HERE / "cv-mini-alive.py"

LLOP = {"bvadd": "add", "bvsub": "sub", "bvmul": "mul", "bvand": "and", "bvor": "or",
        "bvxor": "xor", "bvshl": "shl", "bvlshr": "lshr", "bvashr": "ashr"}


def default_opt() -> str | None:
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    cand = base / "opt"
    return str(cand) if cand.exists() else shutil.which("opt")


def lower_node(node, lines, ctr):
    op = node["op"]
    if op == "var":
        return f"%{node['name']}"
    if op == "bvconst":
        return str(int(node["value"]) & ((1 << 32) - 1))
    if op == "bvneg":
        a = lower_node(node["args"][0], lines, ctr)
        nm = f"%t{ctr[0]}"
        ctr[0] += 1
        lines.append(f"  {nm} = sub i32 0, {a}")
        return nm
    if op not in LLOP:
        raise ValueError(f"cannot lower op {op}")
    a = lower_node(node["args"][0], lines, ctr)
    b = lower_node(node["args"][1], lines, ctr)
    nm = f"%t{ctr[0]}"
    ctr[0] += 1
    lines.append(f"  {nm} = {LLOP[op]} i32 {a}, {b}")
    return nm


def to_module(before, variables) -> str:
    params = ", ".join(f"i32 %{v}" for v in variables) or ""
    lines: list[str] = []
    res = lower_node(before, lines, [0])
    body = "\n".join(lines)
    return f"define i32 @t({params}) {{\nentry:\n{body}\n  ret i32 {res}\n}}\n"


def run_opt(opt: str, before_ll: str, passes: str) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        bp, ap = Path(d) / "before.ll", Path(d) / "after.ll"
        bp.write_text(before_ll)
        proc = subprocess.run([opt, f"-passes={passes}", "-S", str(bp), "-o", str(ap)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        return ap.read_text()


def translation_validate(before_ll: str, after_ll: str, z3_bin: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        bp, ap = Path(d) / "b.ll", Path(d) / "a.ll"
        bp.write_text(before_ll)
        ap.write_text(after_ll)
        proc = subprocess.run([sys.executable, str(MINI_ALIVE), "--before", str(bp),
                               "--after", str(ap), "--z3-bin", z3_bin],
                              capture_output=True, text=True)
        try:
            return json.loads(proc.stdout)["status"]
        except (json.JSONDecodeError, KeyError):
            return "tv-error"


def root_mnemonic_count(text: str, mnem: str) -> int:
    return sum(1 for line in text.splitlines() if f"= {mnem} " in line)


def cross_validate(before, after, variables, opt: str, z3_bin: str, passes: str) -> dict:
    before_ll = to_module(before, variables)
    after_ll = run_opt(opt, before_ll, passes)
    if after_ll is None:
        return {"verdict": "opt-error"}
    tv = translation_validate(before_ll, after_ll, z3_bin)
    if tv == "refuted":
        return {"verdict": "bug", "tv": tv, "witness": before_ll}
    if tv != "proved":
        return {"verdict": "tv-error", "tv": tv}
    mnem = LLOP.get(before.get("op"), "")
    fired = root_mnemonic_count(after_ll, mnem) < root_mnemonic_count(before_ll, mnem)
    return {"verdict": "verified" if fired else "no-trigger", "tv": tv}


# --------------------------------------------------------------------------- #

def v(name):
    return {"op": "var", "name": name}


def bvc(value):
    return {"op": "bvconst", "bits": 32, "value": value}


def builtin_transforms():
    return [
        dict(marker="add-zero", before={"op": "bvadd", "args": [v("a"), bvc(0)]},
             after=v("a"), variables=["a"]),
        dict(marker="mul-one", before={"op": "bvmul", "args": [v("a"), bvc(1)]},
             after=v("a"), variables=["a"]),
        dict(marker="xor-self", before={"op": "bvxor", "args": [v("a"), v("a")]},
             after=bvc(0), variables=["a"]),
    ]


def bug_demo(z3_bin: str) -> dict:
    """Detect a miscompile without a real buggy opt: feed a deliberately-wrong
    'optimized' output and confirm TV refutes it (the bug-detection path)."""
    before_ll = to_module({"op": "bvadd", "args": [v("a"), bvc(0)]}, ["a"])
    wrong_after = "define i32 @t(i32 %a) {\nentry:\n  ret i32 0\n}\n"  # add a,0 != 0
    tv = translation_validate(before_ll, wrong_after, z3_bin)
    return {"verdict": "bug" if tv == "refuted" else f"MISSED({tv})", "tv": tv}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transforms", type=Path, help="cv-lift-finding report json (results[])")
    ap.add_argument("--selftest", action="store_true", help="built-in transforms via real opt")
    ap.add_argument("--opt", default=None, help="opt binary (default CV_LLVM_BIN/opt)")
    ap.add_argument("--passes", default="instcombine")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    opt = args.opt or default_opt()
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    # Bug-detection path needs only z3 (no opt) -- always exercised.
    bug = bug_demo(z3_bin)

    results = []
    if args.selftest or args.transforms:
        if opt is None:
            print(json.dumps({"status": "skipped", "reason": "opt not found", "bug_demo": bug}))
            return 0 if bug["verdict"] == "bug" else 1
        if args.transforms:
            data = json.loads(args.transforms.read_text())
            transforms = [{"marker": r["marker"], "before": r["before"],
                           "after": r["after"], "variables": r["variables"]}
                          for r in data.get("results", []) if "before" in r]
        else:
            transforms = builtin_transforms()
        for t in transforms:
            try:
                cv = cross_validate(t["before"], t["after"], t["variables"], opt, z3_bin, args.passes)
            except ValueError as exc:
                cv = {"verdict": "unlowerable", "reason": str(exc)}
            results.append({"marker": t["marker"], **{k: v for k, v in cv.items() if k != "witness"}})

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    summary = {"opt": opt, "transforms": len(results), "verdicts": counts,
               "bug_demo": bug, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    for r in results:
        print(f"  [{r['verdict']}] {r['marker']} tv={r.get('tv')}", file=sys.stderr)
    print(f"  bug-detection demo: {bug['verdict']}", file=sys.stderr)
    bad = counts.get("bug", 0) + counts.get("tv-error", 0) + counts.get("opt-error", 0)
    ok = bug["verdict"] == "bug" and (not results or counts.get("verified", 0) > 0)
    return 0 if ok and bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
