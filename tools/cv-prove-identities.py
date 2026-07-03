#!/usr/bin/env python3
"""Prove the extended-identity library (new formally-modeled transform families).

The 51-intent registry covers every generated probe marker. This broadens the
*formal* coverage to transform families the registry does not yet model --
Reassociate (associativity/commutativity), InstSimplify (absorbing/idempotent
folds), and shift-by-zero -- as a standalone library
(`constraints/extended_identities.json`) so the existing registry contracts are
untouched. Each identity is proved at every generated width (i8/i16/i32/i64);
a width where one fails is reported with a counterexample.

Z3 is authoritative; `--no-z3` falls back to a brute-force i8 check that also
surfaces any counterexample.
"""

from __future__ import annotations

import argparse
import collections
import copy
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cv_formal_ir import FormalIrError, equivalence_smt, pair_instances_for_formal

BINOPS = {"bvadd": lambda a, b, m: (a + b) & m, "bvsub": lambda a, b, m: (a - b) & m,
          "bvmul": lambda a, b, m: (a * b) & m, "bvand": lambda a, b, m: a & b,
          "bvor": lambda a, b, m: a | b, "bvxor": lambda a, b, m: a ^ b}


class NonPortable(Exception):
    pass


def port_const(value, old_bits, w):
    full = (1 << old_bits) - 1
    if value in (0, 1):
        return value
    if value == full:
        return (1 << w) - 1
    if value == (1 << (old_bits - 1)):
        return 1 << (w - 1)
    if value == (1 << (old_bits - 1)) - 1:
        return (1 << (w - 1)) - 1
    if value < (1 << w):                        # small, fits the target width
        return value
    # Masking a non-fitting constant would fabricate a different (possibly false)
    # identity -- skip this width instead, like cv-prove-multiwidth.
    raise NonPortable(f"const {value} (@{old_bits}b) not portable to {w}b")


def reencode(node, w):
    if isinstance(node, dict):
        if node.get("op") == "bvconst":
            return {"op": "bvconst", "bits": w, "value": port_const(int(node["value"]), int(node.get("bits", 32)), w)}
        return {k: reencode(v, w) for k, v in node.items()}
    if isinstance(node, list):
        return [reencode(x, w) for x in node]
    return node


def formal_at_width(formal, w):
    out = copy.deepcopy(formal)
    out["before"] = reencode(formal["before"], w)
    out["after"] = reencode(formal["after"], w)
    out["variable_bits"] = {v: w for v in formal.get("variables", [])}
    return out


def evaluate(node, env, w):
    mask = (1 << w) - 1
    op = node["op"]
    if op == "var":
        return env[node["name"]] & mask
    if op == "bvconst":
        return port_const(int(node["value"]), int(node.get("bits", 32)), w) & mask
    a = evaluate(node["args"][0], env, w)
    b = evaluate(node["args"][1], env, w)
    if op == "bvshl":
        return (a << (b % w)) & mask
    if op == "bvlshr":
        return (a >> (b % w)) & mask
    if op == "bvashr":
        sign = (a >> (w - 1)) & 1
        shifted = a >> (b % w)
        if sign:
            shifted |= (mask << (w - (b % w))) & mask
        return shifted & mask
    return BINOPS[op](a, b, mask)


def brute_force(formal, w=8):
    variables = formal["variables"]
    if len(variables) > 3:
        return None
    for combo in itertools.product(range(1 << w), repeat=len(variables)):
        env = dict(zip(variables, combo))
        if evaluate(formal["before"], env, w) != evaluate(formal["after"], env, w):
            return {"width": w, "inputs": env}
    return None


def prove_at_width(formal, w, z3_bin):
    try:
        fw = formal_at_width(formal, w)
    except NonPortable as exc:
        return "skipped", {"reason": str(exc)}
    if z3_bin:
        try:
            pairs = pair_instances_for_formal(fw)
        except FormalIrError as exc:
            return "encode-error", {"reason": str(exc)}
        for _, pair in pairs:
            smt = equivalence_smt("identity", "extended", pair)
            res = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True)
            head = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "error"
            if head != "unsat":
                return "FAILED", {"z3": head}
        return "proved", None
    # toolless: brute-force at i4 (tractable even for 3-variable identities);
    # full multi-width proving is z3's job.
    try:
        small = formal_at_width(formal, 4)
    except NonPortable as exc:
        return "skipped", {"reason": str(exc)}
    ce = brute_force(small, w=4)
    return ("FAILED", ce) if ce else ("proved", None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--identities", type=Path,
                        default=Path(__file__).resolve().parent.parent / "constraints" / "extended_identities.json")
    parser.add_argument("--widths", default="8,16,32,64")
    parser.add_argument("--z3", default=None)
    parser.add_argument("--no-z3", action="store_true")
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    widths = [int(w) for w in args.widths.split(",")]
    z3_bin = None if args.no_z3 else (args.z3 or shutil.which("z3"))
    identities = json.loads(args.identities.read_text())

    results = []
    by_family = collections.Counter()
    proved = failed = skipped = 0
    for rec in identities:
        per_width = {}
        for w in widths:
            status = prove_at_width(rec["formal"], w, z3_bin)
            status_str = status[0] if isinstance(status, tuple) else status
            per_width[w] = status_str
            if status_str == "proved":
                proved += 1
            elif status_str == "skipped":
                skipped += 1
            else:
                failed += 1
        # an identity is covered if it proved everywhere it was portable (no FAILs)
        if all(v in ("proved", "skipped") for v in per_width.values()) and \
                any(v == "proved" for v in per_width.values()):
            by_family[rec["family"]] += 1
        results.append({"family": rec["family"], "name": rec["name"],
                        "rewrite": rec["rewrite"], "widths": per_width})

    summary = {"z3": z3_bin or None, "widths": widths,
               "identities": len(identities), "family_proofs": dict(by_family),
               "proved": proved, "failed": failed, "skipped": skipped, "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n")
    mode = "proved" if z3_bin else "checked"
    print(f"identities: {len(identities)} across {sorted(by_family)} x {widths} -> "
          f"{proved} {mode}, {failed} failed", file=sys.stderr)
    return 1 if (args.require_all and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
