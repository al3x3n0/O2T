#!/usr/bin/env python3
"""Local mini-Alive2: SMT translation validation of a real before/after .ll pair.

This is the toolless substitute for ``alive-tv`` (which is not installable here).
It parses a single-basic-block scalar-integer subset of LLVM IR into the
``cv_formal_ir`` DSL -- the exact inverse of ``cv-check-negative-intents.lower_ll``
-- then proves that the *after* function refines (here: equals the value of) the
*before* function for all inputs, using the existing ``equivalence_smt`` encoder
and Z3.  When Z3 is absent (``--no-z3``) it brute-forces the equivalence over a
small bit width with the scalar evaluator.

Supported subset (anything else is reported ``unsupported``, never crashes):
  define iN @name(iN %a, iN %b, ...) {
  entry:
    %r = add|sub|mul|and|or|xor|shl|lshr|ashr [nsw|nuw|exact ...] iN %x, %y
    %r = trunc|zext|sext iN %x to iM
    %r = icmp <pred> iN %x, %y          ; -> i1
    %r = select i1 %c, iN %x, iN %y
    ret iN <ssa-or-const>
  }

Poison-producing flags (nsw/nuw/exact) are now KEPT and lowered into the DSL, and
the proof runs in refinement mode: the *after* function must be poison wherever the
*before* is, and equal in value where the *before* is well-defined. Dropping a flag
is therefore a sound refinement; adding one is caught as unsound. (Unmodeled flags
like `or disjoint` are still dropped, narrowing that op to value-equivalence.)

Modes:
  --before A.ll --after B.ll   prove one real pair
  --selftest                   round-trip every scalar intent + extended identity
                               through lower_ll -> parse -> prove (no .ll corpus
                               needed; validates the parser against the registry)
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from o2t.formal_ir import FormalIrError, VALID_FLAGS, equivalence_smt, pair_instances_for_formal, premise_smt

# Match only define-funs whose body is a bare hex literal (the input vars);
# z3 4.16 puts the value on the next line, hence the \s* spanning the newline.
DEF_RE = re.compile(r"\(define-fun (\w+) \(\) \(_ BitVec \d+\)\s*#x([0-9a-fA-F]+)\)")

# binary integer opcode -> DSL op
BIN_OPS = {
    "add": "bvadd", "sub": "bvsub", "mul": "bvmul",
    "and": "bvand", "or": "bvor", "xor": "bvxor",
    "shl": "bvshl", "lshr": "bvlshr", "ashr": "bvashr",
}
ICMP_OPS = {
    "eq": "eq", "ne": "ne",
    "slt": "bvslt", "sle": "bvsle", "sgt": "bvsgt", "sge": "bvsge",
    "ult": "bvult", "ule": "bvule", "ugt": "bvugt", "uge": "bvuge",
}
FLAG_WORDS = {"nsw", "nuw", "exact", "disjoint"}


class Unsupported(Exception):
    """Raised when the IR is outside the validated subset."""


# --------------------------------------------------------------------------- #
# .ll -> DSL parser (the inverse of lower_ll)
# --------------------------------------------------------------------------- #

TYPE_RE = re.compile(r"^i(\d+)$")
DEFINE_RE = re.compile(r"define\s+(?:[\w\s]*?\s)?i(\d+)\s+@[\w.$]+\s*\(([^)]*)\)")


def _width(ty: str) -> int:
    m = TYPE_RE.match(ty)
    if not m:
        raise Unsupported(f"non-integer type {ty!r}")
    return int(m.group(1))


def _operand(tok: str, width: int, env: dict, var_bits: dict) -> dict:
    """Turn an operand token (%name or integer literal) into a DSL node."""
    tok = tok.strip().rstrip(",")
    if tok.startswith("%"):
        name = tok[1:]
        if name in env:
            return env[name]
        # a parameter referenced before any record: treat as a fresh variable
        var_bits.setdefault(name, width)
        return {"op": "var", "name": name}
    # integer literal
    try:
        value = int(tok, 0)
    except ValueError as exc:
        raise Unsupported(f"non-literal operand {tok!r}") from exc
    mask = (1 << width) - 1
    return {"op": "bvconst", "bits": width, "value": value & mask}


def parse_function(text: str) -> dict:
    """Parse a single-BB scalar function into a DSL ``formal``-style fragment.

    Returns ``{"result": <node>, "variables": [...], "variable_bits": {...},
    "result_bits": N}``.
    """
    m = DEFINE_RE.search(text)
    if not m:
        raise Unsupported("no parseable 'define iN @name(...)' header")
    result_bits = int(m.group(1))
    params = m.group(2).strip()

    env: dict[str, dict] = {}
    var_bits: dict[str, int] = {}
    variables: list[str] = []
    if params:
        for raw in params.split(","):
            parts = raw.split()
            # last token is %name; first iN token is the type
            ptoks = [p for p in parts if p.startswith("%")]
            ttoks = [p for p in parts if TYPE_RE.match(p)]
            if not ptoks or not ttoks:
                raise Unsupported(f"unparseable parameter {raw!r}")
            name = ptoks[-1][1:]
            width = _width(ttoks[0])
            env[name] = {"op": "var", "name": name}
            var_bits[name] = width
            variables.append(name)

    body = text[m.end():]
    # body up to the closing brace
    brace = body.find("{")
    if brace != -1:
        body = body[brace + 1:]
    end = body.rfind("}")
    if end != -1:
        body = body[:end]

    result_node: dict | None = None
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.endswith(":"):
            continue  # blank, comment, or label
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("ret "):
            rest = line[4:].strip()
            rtoks = rest.split(None, 1)
            if not rtoks:
                raise Unsupported("malformed ret")
            rty = rtoks[0]
            if rty == "void":
                raise Unsupported("void return")
            width = _width(rty)
            result_node = _operand(rtoks[1] if len(rtoks) > 1 else "", width, env, var_bits)
            result_bits = width
            break
        if line.startswith("br ") or line.startswith("switch"):
            raise Unsupported("control flow (multi-block)")
        result_name, node = parse_instruction(line, env, var_bits)
        env[result_name] = node

    if result_node is None:
        raise Unsupported("no ret instruction")
    return {
        "result": result_node,
        "variables": variables,
        "variable_bits": var_bits,
        "result_bits": result_bits,
    }


def parse_instruction(line: str, env: dict, var_bits: dict) -> tuple[str, dict]:
    if "=" not in line:
        raise Unsupported(f"unsupported instruction {line!r}")
    lhs, rhs = line.split("=", 1)
    name = lhs.strip().lstrip("%").strip()
    toks = rhs.split()
    if not toks:
        raise Unsupported(f"empty instruction {line!r}")
    opcode = toks[0]

    if opcode in BIN_OPS:
        present_flags = [t for t in toks[1:] if t in FLAG_WORDS]
        rest = [t for t in toks[1:] if t not in FLAG_WORDS]
        # rest = [iN, op1, op2]  (op1 has trailing comma)
        if len(rest) < 3:
            raise Unsupported(f"malformed {opcode}: {line!r}")
        width = _width(rest[0])
        a = _operand(rest[1], width, env, var_bits)
        b = _operand(rest[2], width, env, var_bits)
        dsl_op = BIN_OPS[opcode]
        node = {"op": dsl_op, "args": [a, b]}
        # Keep the poison-generating flags the formal IR models (nsw/nuw/exact);
        # they make the proof a real poison-REFINEMENT rather than value-equality.
        # Unmodeled flags (e.g. `or disjoint`) are dropped, narrowing to value-equiv
        # for that op -- still sound as a refinement check.
        kept = [f for f in present_flags if f in VALID_FLAGS.get(dsl_op, set())]
        if kept:
            node["flags"] = kept
        return name, node

    if opcode in ("trunc", "zext", "sext"):
        # zext iN %x to iM
        if "to" not in toks:
            raise Unsupported(f"malformed cast: {line!r}")
        ti = toks.index("to")
        src_ty = toks[1]
        dst_ty = toks[ti + 1]
        src_w = _width(src_ty)
        dst_w = _width(dst_ty)
        operand = _operand(toks[2], src_w, env, var_bits)
        return name, {"op": opcode, "bits": dst_w, "args": [operand]}

    if opcode == "icmp":
        # icmp pred iN %x, %y
        pred = toks[1]
        if pred not in ICMP_OPS:
            raise Unsupported(f"unsupported icmp predicate {pred!r}")
        width = _width(toks[2])
        a = _operand(toks[3], width, env, var_bits)
        b = _operand(toks[4], width, env, var_bits)
        return name, {"op": ICMP_OPS[pred], "args": [a, b]}

    if opcode == "select":
        # select i1 %c, iN %x, iN %y
        rest = " ".join(toks[1:])
        parts = [p.strip() for p in rest.split(",")]
        if len(parts) != 3:
            raise Unsupported(f"malformed select: {line!r}")
        cond_tok = parts[0].split()[-1]
        cond = env.get(cond_tok.lstrip("%"))
        if cond is None:
            raise Unsupported("select condition is not a recorded i1 value")
        then_parts = parts[1].split()
        else_parts = parts[2].split()
        tw = _width(then_parts[0])
        then = _operand(then_parts[1], tw, env, var_bits)
        els = _operand(else_parts[-1], tw, env, var_bits)
        return name, {"op": "ite", "args": [cond, then, els]}

    raise Unsupported(f"unsupported opcode {opcode!r}")


# --------------------------------------------------------------------------- #
# Proving
# --------------------------------------------------------------------------- #

def build_formal(before: dict, after: dict, marker: str) -> dict:
    """Combine two parsed functions into a scalar-bv32 formal record."""
    variables = list(dict.fromkeys(before["variables"] + after["variables"]))
    if not variables:
        raise Unsupported("function has no integer parameters to quantify over")
    var_bits = {}
    for src in (before, after):
        for name, bits in src["variable_bits"].items():
            if name in var_bits and var_bits[name] != bits:
                raise Unsupported(f"variable {name!r} has conflicting widths")
            var_bits[name] = bits
    if before["result_bits"] != after["result_bits"]:
        raise Unsupported("before/after return widths differ")
    # variable_bits is only needed when not all-32 (engine defaults to 32)
    vb = {k: v for k, v in var_bits.items() if v != 32}
    formal = {
        "marker": marker,
        "domain": "scalar-bv32",
        "equivalence": "result",
        "variables": variables,
        "before": before["result"],
        "after": after["result"],
        # Refinement (not bare equality): after must be poison wherever before is,
        # and equal in value where before is well-defined. For flag-free pairs this
        # coincides with value-equivalence; with flags it checks poison-refinement.
        "refinement": "refinement",
    }
    if vb:
        formal["variable_bits"] = vb
    return formal


# ---- scalar evaluator (toolless brute force + counterexample verification) -- #

EVAL = {
    "bvadd": lambda a, b, m: (a + b) & m,
    "bvsub": lambda a, b, m: (a - b) & m,
    "bvmul": lambda a, b, m: (a * b) & m,
    "bvand": lambda a, b, m: a & b,
    "bvor": lambda a, b, m: a | b,
    "bvxor": lambda a, b, m: a ^ b,
    "bvshl": lambda a, b, m: (a << (b & (m.bit_length() - 1))) & m,
    "bvlshr": lambda a, b, m: a >> (b & (m.bit_length() - 1)),
}


def _to_signed(v: int, w: int) -> int:
    return v - (1 << w) if v >> (w - 1) else v


def evaluate(node, env: dict, w: int):
    """Scalar evaluator over width ``w`` (all variables share width here).

    Returns an int, a bool (for comparisons), or None if the op is outside the
    toolless evaluator (e.g. mixed-width casts)."""
    mask = (1 << w) - 1
    op = node["op"]
    if op == "var":
        return env[node["name"]] & mask
    if op == "bvconst":
        return int(node["value"]) & mask
    if op in EVAL:
        a = evaluate(node["args"][0], env, w)
        b = evaluate(node["args"][1], env, w)
        if a is None or b is None:
            return None
        return EVAL[op](int(a), int(b), mask)
    if op == "bvashr":
        a = evaluate(node["args"][0], env, w)
        b = evaluate(node["args"][1], env, w)
        if a is None or b is None:
            return None
        sh = int(b) & (w - 1)
        return (_to_signed(int(a), w) >> sh) & mask
    if op in ("eq", "ne", "bvslt", "bvsle", "bvsgt", "bvsge",
              "bvult", "bvule", "bvugt", "bvuge"):
        a = evaluate(node["args"][0], env, w)
        b = evaluate(node["args"][1], env, w)
        if a is None or b is None:
            return None
        a, b = int(a), int(b)
        if op in ("eq", "ne"):
            r = (a == b)
            return r if op == "eq" else (not r)
        sa, sb = _to_signed(a, w), _to_signed(b, w)
        return {
            "bvslt": sa < sb, "bvsle": sa <= sb, "bvsgt": sa > sb, "bvsge": sa >= sb,
            "bvult": a < b, "bvule": a <= b, "bvugt": a > b, "bvuge": a >= b,
        }[op]
    if op == "ite":
        c = evaluate(node["args"][0], env, w)
        if c is None:
            return None
        return evaluate(node["args"][1 if c else 2], env, w)
    return None  # trunc/zext/sext (width-changing) not modelled toolless


def brute_force(formal: dict, w: int = 8):
    # Toolless approximation: evaluate at width ``w`` (i8) regardless of the
    # declared width, exactly like cv-check-negative-intents. The evaluator
    # returns None on width-changing casts, which surfaces below as a skip
    # (those genuinely need Z3); Z3 remains the authoritative backend.
    variables = formal["variables"]
    if len(variables) > 3:
        return ("skip", None)
    for combo in itertools.product(range(1 << w), repeat=len(variables)):
        env = dict(zip(variables, combo))
        b = evaluate(formal["before"], env, w)
        a = evaluate(formal["after"], env, w)
        if a is None or b is None:
            return ("skip", None)  # op outside the scalar evaluator
        if a != b:
            return ("refuted", {"width": w, "inputs": env, "before": b, "after": a,
                                "method": f"bruteforce-i{w}"})
    return ("proved", None)


def z3_prove(z3_bin: str, formal: dict):
    pairs = pair_instances_for_formal(formal)
    for _, pair in pairs:
        smt = equivalence_smt(formal["marker"], "mini-alive", pair)
        res = subprocess.run([z3_bin, "-in"], input=smt + "\n(get-model)",
                             capture_output=True, text=True)
        out = res.stdout.strip()
        head = out.splitlines()[0] if out else "error"
        if head == "unsat":
            # Anti-vacuity: an `unsat` proves the rewrite only if the premises are jointly
            # satisfiable; contradictory premises make `(and assumptions (not goal))` trivially
            # unsat and the "proof" vacuous. Confirm the premises admit a model first.
            premise = premise_smt(formal["marker"], "mini-alive", pair)
            if premise is not None:
                pres = subprocess.run([z3_bin, "-in"], input=premise,
                                      capture_output=True, text=True).stdout.strip()
                phead = pres.splitlines()[0] if pres else "error"
                if phead != "sat":
                    return ("unsupported", {"reason": "premises jointly unsatisfiable (vacuous proof)",
                                            "premise_result": phead})
            return ("proved", None)
        if head == "sat":
            model = {k: int(v, 16) for k, v in DEF_RE.findall(out)}
            env = {n: model.get(n, 0) for n in formal["variables"]}
            cex = {"width": 32, "inputs": env, "method": "z3"}
            b = evaluate(formal["before"], env, 32)
            a = evaluate(formal["after"], env, 32)
            if a is not None and b is not None:
                cex["before"], cex["after"] = b, a
            return ("refuted", cex)
        return ("error", {"z3": out[:200]})
    return ("error", None)


def prove(formal: dict, z3_bin: str | None):
    if z3_bin:
        return z3_prove(z3_bin, formal)
    status, cex = brute_force(formal)
    if status == "skip":
        return ("unsupported", {"reason": "toolless evaluator cannot cover this op set; needs Z3"})
    return (status, cex)


# --------------------------------------------------------------------------- #
# Self-test: round-trip the registry through lower_ll -> parse -> prove
# --------------------------------------------------------------------------- #

LLOP = {"bvadd": "add", "bvsub": "sub", "bvmul": "mul", "bvand": "and",
        "bvor": "or", "bvxor": "xor", "bvshl": "shl", "bvlshr": "lshr",
        "bvashr": "ashr"}


def lower_simple(node, width: int, lines: list, counter: list) -> str:
    """Minimal DSL->.ll lowerer for the round-trip self-test (arith/shift only)."""
    op = node["op"]
    if op == "var":
        return f"%{node['name']}"
    if op == "bvconst":
        return str(int(node["value"]) & ((1 << width) - 1))
    if op in LLOP:
        a = lower_simple(node["args"][0], width, lines, counter)
        b = lower_simple(node["args"][1], width, lines, counter)
        nm = f"%t{counter[0]}"
        counter[0] += 1
        lines.append(f"  {nm} = {LLOP[op]} i{width} {a}, {b}")
        return nm
    raise Unsupported(f"self-test lowerer cannot emit {op}")


def lower_pair_ll(formal: dict, width: int) -> tuple[str, str]:
    variables = formal["variables"]
    params = ", ".join(f"i{width} %{v}" for v in variables)
    out = {}
    for key in ("before", "after"):
        lines: list[str] = []
        res = lower_simple(formal[key], width, lines, [0])
        body = "\n".join(lines)
        out[key] = (f"define i{width} @{key}({params}) {{\nentry:\n{body}\n"
                    f"  ret i{width} {res}\n}}\n")
    return out["before"], out["after"]


# Real .ll pairs exercising poison-refinement under flags. These are meaningful
# ONLY under Z3: the toolless evaluator has no poison model, so a poison-only
# unsoundness (e.g. adding nsw) is value-equal and would look "proved". Run them
# solely when Z3 is available; expected is the refinement verdict.
def _fn(name, params, body, ret_var, w=32):
    return (f"define i{w} @{name}({params}) {{\nentry:\n{body}\n"
            f"  ret i{w} %{ret_var}\n}}\n")


FLAG_LL_CASES = [
    # dropping nsw is a sound refinement
    ("drop-nsw-sound",
     _fn("before", "i32 %x, i32 %y", "  %r = add nsw i32 %x, %y", "r"),
     _fn("after", "i32 %x, i32 %y", "  %r = add i32 %x, %y", "r"), "proved"),
    # adding nsw is unsound (poison where source was defined)
    ("add-nsw-unsound",
     _fn("before", "i32 %x, i32 %y", "  %r = add i32 %x, %y", "r"),
     _fn("after", "i32 %x, i32 %y", "  %r = add nsw i32 %x, %y", "r"), "refuted"),
    # (x <<nuw k) >>l k == x : sound only because nuw forbids losing high bits
    ("shl-nuw-lshr-roundtrip-sound",
     _fn("before", "i32 %x, i32 %k", "  %t = shl nuw i32 %x, %k\n  %r = lshr i32 %t, %k", "r"),
     _fn("after", "i32 %x, i32 %k", "  %r = add i32 %x, 0", "r"), "proved"),
    # same rewrite without nuw is unsound (value differs when bits are lost)
    ("shl-lshr-roundtrip-unsound",
     _fn("before", "i32 %x, i32 %k", "  %t = shl i32 %x, %k\n  %r = lshr i32 %t, %k", "r"),
     _fn("after", "i32 %x, i32 %k", "  %r = add i32 %x, 0", "r"), "refuted"),
]


def flag_selftest(z3_bin: str) -> tuple[int, int, list]:
    proved = failed = 0
    fails = []
    for label, before_ll, after_ll, expected in FLAG_LL_CASES:
        b = parse_function(before_ll)
        a = parse_function(after_ll)
        formal = build_formal(b, a, label)
        status, cex = prove(formal, z3_bin)
        if status == expected:
            proved += 1
        else:
            failed += 1
            fails.append({"case": label, "expected": expected, "status": status, "cex": cex})
    return proved, failed, fails


def selftest(z3_bin: str | None) -> int:
    root = Path(__file__).resolve().parent.parent / "constraints"
    sources = [root / "optimization_intents.json", root / "extended_identities.json"]
    proved = skipped = failed = 0
    failures = []
    for src in sources:
        if not src.exists():
            continue
        records = json.loads(src.read_text())
        for rec in records:
            formal = rec.get("formal") if isinstance(rec, dict) else None
            if not formal or formal.get("domain") != "scalar-bv32":
                continue
            if any(v != 32 for v in formal.get("variable_bits", {}).values()):
                continue
            # only round-trip records the simple lowerer can emit
            try:
                before_ll, after_ll = lower_pair_ll(formal, 32)
            except Unsupported:
                skipped += 1
                continue
            try:
                b = parse_function(before_ll)
                a = parse_function(after_ll)
                reparsed = build_formal(b, a, formal.get("marker", rec.get("marker", "selftest")))
                status, cex = prove(reparsed, z3_bin)
            except (Unsupported, FormalIrError) as exc:
                failed += 1
                failures.append({"marker": rec.get("marker"), "error": str(exc)})
                continue
            if status == "proved":
                proved += 1
            elif status == "unsupported":
                skipped += 1
            else:
                failed += 1
                failures.append({"marker": rec.get("marker"), "status": status, "cex": cex})
    backend = "z3" if z3_bin else "bruteforce"
    flag = {"proved": 0, "failed": 0, "skipped": len(FLAG_LL_CASES)}
    flag_failures: list = []
    if z3_bin:
        fp, ff, flag_failures = flag_selftest(z3_bin)
        flag = {"proved": fp, "failed": ff, "skipped": 0}
        failed += ff
        failures.extend(flag_failures)
    summary = {"selftest": {"proved": proved, "skipped": skipped, "failed": failed},
               "flag_refinement": flag, "backend": backend, "failures": failures[:10]}
    print(json.dumps(summary, sort_keys=True))
    print(f"mini-alive self-test: {proved} round-trip proved, {skipped} skipped, "
          f"{failed} failed [{backend}]; flag-refinement {flag['proved']} proved "
          f"{flag['failed']} failed", file=sys.stderr)
    return 0 if failed == 0 and proved > 0 else 1


# --------------------------------------------------------------------------- #

def resolve_z3(no_z3: bool, z3_bin: str) -> str | None:
    if no_z3:
        return None
    return shutil.which(z3_bin)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, help="before-optimization .ll")
    ap.add_argument("--after", type=Path, help="after-optimization .ll")
    ap.add_argument("--marker", default="mini-alive", help="label for the SMT query")
    ap.add_argument("--selftest", action="store_true",
                    help="round-trip the scalar registry through lower->parse->prove")
    ap.add_argument("--no-z3", action="store_true", help="force toolless brute force")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--out", type=Path, help="write JSON result here")
    args = ap.parse_args()

    z3_bin = resolve_z3(args.no_z3, args.z3_bin)

    if args.selftest:
        return selftest(z3_bin)

    if not args.before or not args.after:
        ap.error("provide --before and --after (or --selftest)")

    result: dict = {"status": "not-run", "marker": args.marker,
                    "backend": "z3" if z3_bin else "bruteforce"}
    try:
        before = parse_function(args.before.read_text())
        after = parse_function(args.after.read_text())
        formal = build_formal(before, after, args.marker)
        status, detail = prove(formal, z3_bin)
        result["status"] = status
        if detail is not None:
            result["detail"] = detail
    except Unsupported as exc:
        result["status"] = "unsupported"
        result["detail"] = {"reason": str(exc)}
    except FormalIrError as exc:
        result["status"] = "error"
        result["detail"] = {"reason": str(exc)}

    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    # exit 0 only when we positively proved refinement
    return 0 if result["status"] == "proved" else 1


if __name__ == "__main__":
    sys.exit(main())
