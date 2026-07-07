#!/usr/bin/env python3
"""Deterministic poison-aware stand-in for Vellvm's LLVM IR interpreter (no real Vellvm in-tree).

Parses the LLVM IR TEXT emitted by pass_graph.to_llvm_ir (a single-block SSA function) and evaluates
it over concrete integer arguments, tracking a poison bit -- so it is an INDEPENDENT oracle: it reads
the emitted text, not O2T's DSL. Protocol: `fake-llvm-interp.py <module.ll> <fn> <arg0> ...` prints
the unsigned result, or `poison` when the result is poison. Models nsw/nuw overflow, freeze (collapses
poison to a defined 0), icmp (i1 0/1), select, and trunc/zext/sext.
"""
import re
import sys

CMP = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
       "slt": None, "sle": None, "sgt": None, "sge": None,
       "ult": lambda a, b: a < b, "ule": lambda a, b: a <= b, "ugt": lambda a, b: a > b, "uge": lambda a, b: a >= b}


def to_signed(v, w):
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def overflow(op, flag, a, b, w):
    lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    sa, sb = to_signed(a, w), to_signed(b, w)
    m = (1 << w) - 1
    if flag == "nsw":
        return {"add": sa + sb, "sub": sa - sb, "mul": sa * sb, "shl": sa << (b & (w - 1))}.get(op, 0) not in range(lo, hi + 1)
    if flag == "nuw":
        return {"add": a + b, "sub": a - b, "mul": a * b, "shl": a << (b & (w - 1))}.get(op, 0) & ~m != 0 or (op == "sub" and a - b < 0)
    if flag == "exact" and op in ("lshr", "ashr"):
        if b >= w:
            return True
        shifted = (a >> b) if op == "lshr" else (sa >> b)
        return ((shifted << b) & m) != a
    return False


def main():
    text = open(sys.argv[1]).read()
    fn, args = sys.argv[2], [int(x) for x in sys.argv[3:]]
    m = re.search(r"define\s+i(\d+)\s+@" + re.escape(fn) + r"\s*\(([^)]*)\)\s*\{(.*)\}", text, re.S)
    if not m:
        print("poison"); return
    env = {}  # name -> (value, poison, width)
    for i, p in enumerate([p for p in m.group(2).split(",") if p.strip()]):
        w = int(re.match(r"i(\d+)\s+%(\w+)", p.strip()).group(1))
        name = re.match(r"i(\d+)\s+%(\w+)", p.strip()).group(2)
        env[name] = (args[i] & ((1 << w) - 1), False, w)

    def operand(tok, w):
        tok = tok.strip()
        if tok.startswith("%"):
            v, p, _ = env[tok[1:]]
            return v & ((1 << w) - 1), p
        return int(tok) & ((1 << w) - 1), False

    result = None
    for line in m.group(3).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("ret"):
            rm = re.match(r"ret i(\d+) (\S+)", line)
            result = operand(rm.group(2), int(rm.group(1)))
            break
        dst, rhs = [s.strip() for s in line.split("=", 1)]
        dst = dst[1:]
        mask = lambda w: (1 << w) - 1  # noqa: E731
        mm = re.match(r"(add|sub|mul|and|or|xor|shl|lshr|ashr|udiv|sdiv|urem|srem)((?: nsw| nuw| exact)*) i(\d+) (\S+), (\S+)", rhs)
        if mm:
            op, flags, w = mm.group(1), mm.group(2).split(), int(mm.group(3))
            a, pa = operand(mm.group(4), w)
            b, pb = operand(mm.group(5), w)
            sh = b & (w - 1)
            val = {"add": a + b, "sub": a - b, "mul": a * b, "and": a & b, "or": a | b, "xor": a ^ b,
                   "shl": a << sh, "lshr": a >> sh, "ashr": to_signed(a, w) >> sh,
                   "udiv": a // b if b else 0, "sdiv": int(to_signed(a, w) / to_signed(b, w)) if b else 0,
                   "urem": a % b if b else 0, "srem": a % b if b else 0}[op] & mask(w)
            pois = pa or pb or any(overflow(op, f, a, b, w) for f in flags)
            env[dst] = (val, pois, w)
            continue
        im = re.match(r"icmp (\w+) i(\d+) (\S+), (\S+)", rhs)
        if im:
            pred, w = im.group(1), int(im.group(2))
            a, pa = operand(im.group(3), w)
            b, pb = operand(im.group(4), w)
            f = CMP[pred] or (lambda a, b: to_signed(a, w) < to_signed(b, w) if pred == "slt"
                              else to_signed(a, w) <= to_signed(b, w) if pred == "sle"
                              else to_signed(a, w) > to_signed(b, w) if pred == "sgt"
                              else to_signed(a, w) >= to_signed(b, w))
            env[dst] = (1 if f(a, b) else 0, pa or pb, 1)
            continue
        sm = re.match(r"select i1 (\S+), i(\d+) (\S+), i(\d+) (\S+)", rhs)
        if sm:
            c, pc = operand(sm.group(1), 1)
            w = int(sm.group(2))
            t, pt = operand(sm.group(3), w)
            e, pe = operand(sm.group(5), w)
            env[dst] = (t if c else e, pc or (pt if c else pe), w)
            continue
        fm = re.match(r"freeze i(\d+) (\S+)", rhs)
        if fm:
            w = int(fm.group(1))
            v, p = operand(fm.group(2), w)
            env[dst] = (0 if p else v, False, w)             # freeze collapses poison to a defined value
            continue
        cm = re.match(r"(zext|sext|trunc) i(\d+) (\S+) to i(\d+)", rhs)
        if cm:
            op, sw, tw = cm.group(1), int(cm.group(2)), int(cm.group(4))
            v, p = operand(cm.group(3), sw)
            out = (to_signed(v, sw) & mask(tw)) if op == "sext" else (v & mask(tw))
            env[dst] = (out, p, tw)
            continue
        print("poison"); return                              # unmodeled instruction
    if result is None or result[1]:
        print("poison")
    else:
        print(result[0])


if __name__ == "__main__":
    main()
