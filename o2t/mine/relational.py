#!/usr/bin/env python3
"""End-to-end: mine a loop TRANSFORM (before/after) from source, prove it for all n.

Connects the shape miner to the two-loop relational synthesizer. For each
`<base>_before` / `<base>_after` pair it:

  * parses both loop bodies (cv-mine-shapes), extracting each loop's MULTI-accumulator
    recurrence with SEQUENTIAL body semantics, plus the pre-loop inits and the ordered
    LIVE-OUTS (a `return`, or post-loop `slot = var;` assigns for multi-output);
  * prefixes A's locals `A_` and B's locals `B_` (function params and the index `i`
    stay shared), so the two loops form one product system;
  * synthesizes B's non-output IVs as aux invariants, then DISCOVERS the bijection
    A-outputs <-> B-outputs by proving each pairing's equality inductive under them.

So `strengthReduce` is proved via the discovered { k == c*i, A_acc == B_acc }, and a
multi-output `multiSR` via { k1==a*i, k2==b*i, acc1==acc1, acc2==acc2 } -- real loop
optimizations, from source, for every n.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




from o2t.mine import shapes
from o2t.synth import relational
coupled = relational.coupled
poly = relational.poly
c, v, op = poly.c, poly.v, poly.op

FOR_IDX_RE = re.compile(r"for\s*\(\s*int\s+(\w+)")


def subst_env(node, env):
    if isinstance(node, dict):
        if node.get("op") == "var" and node["name"] in env:
            return env[node["name"]]
        if "args" in node:
            return {**node, "args": [subst_env(a, env) for a in node["args"]]}
    return node


def rename(node, mapping):
    if isinstance(node, dict):
        if node.get("op") == "var" and node["name"] in mapping:
            return {"op": "var", "name": mapping[node["name"]]}
        if "args" in node:
            return {**node, "args": [rename(a, mapping) for a in node["args"]]}
    return node


def extract_delta(expr, name):
    """`var = var + DELTA` -> DELTA, else None."""
    if not isinstance(expr, dict) or expr.get("op") != "bvadd":
        return None
    x, y = expr["args"]

    def is_v(n):
        return isinstance(n, dict) and n.get("op") == "var" and n.get("name") == name
    return y if is_v(x) else (x if is_v(y) else None)


def extract_loop(body_text):
    """-> (ordered_accumulators[(name, init, delta)], output_name, index_name) or None."""
    try:
        stmts = shapes.Parser(shapes.tokenize(body_text)).program()
    except shapes.ShapeError:
        return None
    env, accs = {}, None
    post_outputs, ret_output, seen_for = [], None, False
    for s in stmts:
        if s[0] == "assign":
            if not seen_for:
                env[s[1]] = subst_env(s[2], env)  # pre-loop init
            else:
                # POST-loop `slot = var;` designates an ordered live-out (multi-output).
                rhs = s[2]
                if isinstance(rhs, dict) and rhs.get("op") == "var":
                    post_outputs.append(rhs["name"])
        elif s[0] == "for":
            seen_for, accs, body_env = True, [], {}
            # SEQUENTIAL body semantics: thread `body_env` so a later assignment that
            # reads an earlier-updated variable uses its NEW value (e.g. `k += c;
            # acc += k;` makes acc see k+c, not the old k). The extracted delta is the
            # sequenced one; the synth then applies it to the old state correctly.
            for x in s[2]:
                if x[0] != "assign":
                    return None
                name, expr = x[1], x[2]
                seq_rhs = subst_env(expr, body_env)
                delta = extract_delta(seq_rhs, name)
                init = env.get(name)
                if delta is None or init is None:
                    return None
                accs.append((name, init, delta))
                body_env[name] = seq_rhs  # name's new value, for later assigns this iter
        elif s[0] == "ret":
            r = s[1]
            if isinstance(r, dict) and r.get("op") == "var":
                ret_output = r["name"]
    outputs = post_outputs if post_outputs else ([ret_output] if ret_output else None)
    idx = FOR_IDX_RE.search(body_text)
    if accs is None or not outputs:
        return None
    return accs, outputs, (idx.group(1) if idx else "i")


def build_model(before, after, consts):
    """Combine the two mined loops into a product system with prefixed locals.
    Returns accumulators, A's/B's ordered live-outs, and all B-accumulator names."""
    a_accs, a_outs, a_idx = before
    b_accs, b_outs, b_idx = after
    a_map = {n: "A_" + n for n, _, _ in a_accs}
    a_map[a_idx] = "i"
    b_map = {n: "B_" + n for n, _, _ in b_accs}
    b_map[b_idx] = "i"
    accumulators = []
    for name, init, delta in a_accs:
        accumulators.append(dict(name="A_" + name, init=rename(init, a_map), delta=rename(delta, a_map)))
    for name, init, delta in b_accs:
        accumulators.append(dict(name="B_" + name, init=rename(init, b_map), delta=rename(delta, b_map)))
    return dict(consts=consts, accumulators=accumulators,
                a_outputs=["A_" + o for o in a_outs], b_outputs=["B_" + o for o in b_outs],
                b_accs=["B_" + n for n, _, _ in b_accs])


def prove_mined(z3_bin, model):
    """Prove a (possibly multi-output) loop transform: synthesize B's non-output IVs as
    aux invariants, then DISCOVER the bijection A-outputs <-> B-outputs by proving each
    pairing's equality inductive under the relation. Returns pairing + relation, or refuted."""
    accs, consts = model["accumulators"], model["consts"]
    all_vars = [a["name"] for a in accs] + ["i"] + consts
    a_outs, b_outs = model["a_outputs"], model["b_outputs"]
    if len(a_outs) != len(b_outs):
        return {"status": "output-count-mismatch"}

    def acc(name):
        return next(x for x in accs if x["name"] == name)

    # aux IVs = B's accumulators that are not live-outs (e.g. the running k of an SR loop)
    relation = []
    for auxname in [b for b in model["b_accs"] if b not in b_outs]:
        a = acc(auxname)
        found = coupled.synth_one(z3_bin, auxname, a["init"], a["delta"], consts, all_vars, relation)
        if found is None:
            return {"status": "no-aux-invariant", "var": auxname}
        relation.append(found["inv"])

    pairing, used = {}, set()
    for oa in a_outs:
        A, partner = acc(oa), None
        for ob in b_outs:
            if ob in used:
                continue
            B = acc(ob)
            out_eq = op("eq", v(oa), v(ob))
            base = poly.valid(z3_bin, all_vars, relation, A["init"], B["init"])
            step = poly.valid(z3_bin, all_vars, relation + [out_eq],
                              op("bvadd", v(oa), A["delta"]), op("bvadd", v(ob), B["delta"]))
            if base and step:
                partner = ob
                break
        if partner is None:
            return {"status": "output-not-preserved", "output": oa}
        used.add(partner)
        pairing[oa] = partner
        relation.append(op("eq", v(oa), v(partner)))
    return {"status": "proved", "pairing": pairing,
            "relation": [relational.render_rel(r) for r in relation]}


def params_of(signature):
    names = []
    for part in signature.split(","):
        toks = re.findall(r"\w+", part)
        if toks:
            names.append(toks[-1])  # the parameter name
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path, default=ROOT / "tests" / "fixtures" / "loop_transforms.cpp")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    source = args.source.read_text()
    funcs = {}  # name -> (body_text, params)
    for m in shapes.FUNC_RE.finditer(source):
        depth, j = 1, m.end()
        while j < len(source) and depth:
            depth += {"{": 1, "}": -1}.get(source[j], 0)
            j += 1
        funcs[m.group(1)] = (source[m.end():j - 1], params_of(m.group(2)))

    bases = sorted({n[:-len("_before")] for n in funcs if n.endswith("_before")})
    results = []
    for base in bases:
        if base + "_after" not in funcs:
            continue
        before = extract_loop(funcs[base + "_before"][0])
        after = extract_loop(funcs[base + "_after"][0])
        if before is None or after is None:
            results.append({"transform": base, "status": "unparsed"})
            continue
        consts = funcs[base + "_before"][1]
        res = prove_mined(z3_bin, build_model(before, after, consts))
        results.append({"transform": base, "status": res["status"],
                        "pairing": res.get("pairing"), "relation": res.get("relation")})

    proved = [r for r in results if r["status"] == "proved"]
    # A definitive verdict (proved OR a sound refutation) is a successful run; the
    # per-transform expectation (which should prove, which must be refuted) is checked
    # by the gate fixture.
    definitive = {"proved", "output-not-preserved", "no-aux-invariant"}
    ok = bool(proved) and all(r["status"] in definitive for r in results)
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
