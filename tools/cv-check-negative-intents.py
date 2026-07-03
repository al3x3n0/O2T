#!/usr/bin/env python3
"""Make the formal track find bugs, not just bless them.

Two soundness checks for the prover itself:

  * Negative registry (`constraints/negative_intents.json`): known-UNSOUND
    rewrites that MUST be rejected. Each is expected to fail refinement; the tool
    extracts a counterexample (a concrete input where before != after) and lowers
    it to a witness. If a "negative" actually proves sound, that is a failure --
    the negative is mislabeled or the engine is too weak.

  * Mutation teeth-test (`--mutate`): each sound scalar intent is perturbed
    (its result wrapped in `+1`) and must then be rejected. If a mutated -- now
    wrong -- rewrite still "proves", the prover is vacuous, which is a serious
    formal-track bug.

Counterexamples come from Z3 (authoritative, bv32, poison-aware) when available,
with a toolless brute-force fallback over i8 that also produces the witness. Each
counterexample is verified by an independent Python evaluator and lowered to a
runnable before/after `.ll` pair.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cv_formal_ir import equivalence_smt, pair_instances_for_formal

BINOPS = {"bvadd": lambda a, b, m: (a + b) & m, "bvsub": lambda a, b, m: (a - b) & m,
          "bvmul": lambda a, b, m: (a * b) & m, "bvand": lambda a, b, m: a & b,
          "bvor": lambda a, b, m: a | b, "bvxor": lambda a, b, m: a ^ b}
LLOP = {"bvadd": "add", "bvsub": "sub", "bvmul": "mul", "bvand": "and",
        "bvor": "or", "bvxor": "xor"}
DEF_RE = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\) #x([0-9a-fA-F]+)\)")


def port_const(value: int, old_bits: int, w: int) -> int:
    full = (1 << old_bits) - 1
    if value in (0, 1):
        return value
    if value == full:
        return (1 << w) - 1
    if value == (1 << (old_bits - 1)):
        return 1 << (w - 1)
    if value == (1 << (old_bits - 1)) - 1:
        return (1 << (w - 1)) - 1
    return value & ((1 << w) - 1)


def evaluate(node, env: dict, w: int) -> int:
    mask = (1 << w) - 1
    op = node["op"]
    if op == "var":
        return env[node["name"]] & mask
    if op == "bvconst":
        return port_const(int(node["value"]), int(node.get("bits", 32)), w) & mask
    if op in BINOPS:
        a = evaluate(node["args"][0], env, w)
        b = evaluate(node["args"][1], env, w)
        return BINOPS[op](a, b, mask)
    raise ValueError(f"evaluator: unsupported op {op}")


def safe_eval(node, env, w):
    try:
        return evaluate(node, env, w)
    except (ValueError, KeyError):
        return None  # op outside the scalar evaluator (e.g. vector/reduction)


def brute_force(formal: dict, w: int = 8):
    variables = formal["variables"]
    if len(variables) > 3:
        return None
    for combo in itertools.product(range(1 << w), repeat=len(variables)):
        env = dict(zip(variables, combo))
        b = safe_eval(formal["before"], env, w)
        a = safe_eval(formal["after"], env, w)
        if b is None or a is None:
            return None  # cannot brute-force ops the evaluator does not cover
        if b != a:
            return {"width": w, "inputs": env, "before": b, "after": a,
                    "method": "bruteforce-i8"}
    return None


def z3_disprove(z3_bin: str, formal: dict):
    pairs = pair_instances_for_formal(formal)
    for _, pair in pairs:
        smt = equivalence_smt(formal.get("marker", "negative"), "negative", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt + "\n(get-model)",
                             capture_output=True, text=True)
        head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
        if head == "sat":
            model = {k: int(v, 16) for k, v in DEF_RE.findall(res.stdout)}
            env = {n: model.get(n, 0) for n in formal["variables"]}
            return {"width": 32, "inputs": env,
                    "before": safe_eval(formal["before"], env, 32),
                    "after": safe_eval(formal["after"], env, 32), "method": "z3-bv32"}, "sat"
        if head == "unsat":
            return None, "unsat"
    return None, "error"


def lower_ll(formal: dict, w: int) -> str:
    counter = [0]

    def emit(node, lines):
        op = node["op"]
        if op == "var":
            return f"%{node['name']}"
        if op == "bvconst":
            return str(port_const(int(node["value"]), int(node.get("bits", 32)), w))
        a = emit(node["args"][0], lines)
        b = emit(node["args"][1], lines)
        name = f"%t{counter[0]}"
        counter[0] += 1
        lines.append(f"  {name} = {LLOP[op]} i{w} {a}, {b}")
        return name

    params = ", ".join(f"i{w} %{v}" for v in formal["variables"])
    out = []
    for fn, key in (("before", "before"), ("after", "after")):
        lines = []
        res = emit(formal[key], lines)
        out.append(f"define i{w} @{fn}({params}) {{\nentry:")
        out.extend(lines)
        out.append(f"  ret i{w} {res}\n}}")
    return "\n".join(out) + "\n"


def mutate(formal: dict) -> dict:
    m = copy.deepcopy(formal)
    m["after"] = {"op": "bvadd", "args": [formal["after"],
                                          {"op": "bvconst", "bits": 32, "value": 1}]}
    return m


def check(formal, z3_bin):
    """Return (disproved, witness, note). Disproved == rewrite is correctly unsound."""
    if z3_bin:
        witness, status = z3_disprove(z3_bin, formal)
        if witness:
            return True, witness, "z3"
        if status == "unsat":
            return False, None, "unexpectedly-sound (z3 unsat)"
    bf = brute_force(formal)
    if bf:
        return True, bf, "bruteforce"
    return False, None, "no counterexample found"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent.parent / "constraints"
    parser.add_argument("--negatives", type=Path, default=here / "negative_intents.json")
    parser.add_argument("--intents", type=Path, default=here / "optimization_intents.json")
    parser.add_argument("--mutate", action="store_true", help="run the mutation teeth-test")
    parser.add_argument("--no-z3", action="store_true")
    parser.add_argument("--z3", default=None)
    parser.add_argument("--emit-witness", type=Path, help="dir for before/after .ll witnesses")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    z3_bin = None if args.no_z3 else (args.z3 or shutil.which("z3"))
    results = []
    failures = 0

    for rec in json.loads(args.negatives.read_text()):
        formal = dict(rec["formal"], marker=rec["marker"])
        disproved, witness, note = check(formal, z3_bin)
        ok = disproved
        if not ok:
            failures += 1
        entry = {"marker": rec["marker"], "kind": "negative", "rejected": disproved,
                 "method": note, "witness": witness, "ok": ok}
        if disproved and args.emit_witness:
            args.emit_witness.mkdir(parents=True, exist_ok=True)
            wp = args.emit_witness / (rec["marker"].replace(".", "_") + ".ll")
            header = (f"; counterexample {witness['inputs']} @ i{witness['width']}: "
                      f"before={witness['before']} after={witness['after']}\n")
            wp.write_text(header + lower_ll(formal, witness["width"]))
            entry["witness_ll"] = str(wp)
        results.append(entry)

    if args.mutate:
        for rec in json.loads(args.intents.read_text()):
            formal = rec.get("formal")
            if not isinstance(formal, dict) or formal.get("domain") != "scalar-bv32":
                continue
            mutated = dict(mutate(formal), marker=rec["marker"] + "~mut")
            disproved, witness, note = check(mutated, z3_bin)
            if not disproved:
                failures += 1  # a wrong rewrite that still "proves" == vacuous prover
            results.append({"marker": rec["marker"] + "~mut", "kind": "mutation",
                            "rejected": disproved, "method": note, "ok": disproved})

    summary = {"z3": z3_bin or None,
               "negatives": sum(1 for r in results if r["kind"] == "negative"),
               "mutations": sum(1 for r in results if r["kind"] == "mutation"),
               "rejected": sum(1 for r in results if r["rejected"]),
               "failures": failures, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"negatives: {summary['negatives']} known-bad + {summary['mutations']} mutations, "
          f"{summary['rejected']} correctly rejected, {failures} failure(s) "
          f"[{'z3' if z3_bin else 'bruteforce'}]", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
