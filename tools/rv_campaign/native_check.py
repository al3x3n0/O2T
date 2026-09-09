#!/usr/bin/env python3
import ctypes
import json
import math
from pathlib import Path
import random
import struct
import sys
import zlib

from common import context
ctx = context()
HERE = H = ctx.out
ROOT = ctx.root
RV = ctx.rv
LLVM = LLVM16 = ctx.llvm16 / 'bin'
LLVM18 = ctx.llvm18 / 'bin'
name = sys.argv[1]
case = next(c for c in json.loads((HERE / "cases.json").read_text()) if c["name"] == name)
d = HERE / name
libs = [ctypes.CDLL(str(d / f"{label}.dylib")) for label in ("before", "after")]
funcs = [lib.run_batch for lib in libs]
rng = random.Random(zlib.crc32(name.encode()))
isint = case["kind"] == "integer"
memory = case["kind"] == "memory"
T = ctypes.c_uint32 if isint else ctypes.c_float
batches = lanes = mismatch_count = nan_pairs = 0
witness = None
max_ulp = 0

def bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]

def ordered(x):
    b = bits(x)
    return (~b & 0xffffffff) if b & 0x80000000 else b | 0x80000000

def equal(a, b):
    global max_ulp, nan_pairs
    if isint:
        return a == b
    if math.isnan(a) and math.isnan(b):
        nan_pairs += 1
        return True
    if math.isnan(a) or math.isnan(b):
        return False
    ulp = abs(ordered(a) - ordered(b))
    max_ulp = max(max_ulp, ulp)
    # RV's configured math resolver allows 1 ULP for the sqrt math case.
    return ulp <= (1 if name == "upstream_ifelse" else 0)

def check(xs, ys=None):
    global batches, lanes, mismatch_count, witness
    batches += 1
    if memory:
        arrays = [(T * len(xs))(*xs) for _ in funcs]
        for f, a in zip(funcs, arrays):
            f(a)
        outputs = [list(a) for a in arrays]
    else:
        x, y = (T * 4)(*xs), (T * 4)(*ys)
        outputs = []
        for f in funcs:
            out = (T * 4)()
            f(x, y, out)
            outputs.append(list(out))
    for i, (a, b) in enumerate(zip(*outputs)):
        lanes += 1
        if not equal(a, b):
            mismatch_count += 1
            if witness is None:
                witness = {"x": xs, "y": ys, "lane_or_memory_index": i, "before": a, "after": b}

for f in funcs:
    f.restype = None
    f.argtypes = [ctypes.POINTER(T)] if memory else [ctypes.POINTER(T)] * 3

if memory:
    # 8 touched floats and 24 sentinels; lanes access disjoint pairs or strided slots.
    for mask in range(16):
        values = [float(i + 1000) for i in range(32)]
        for lane in range(4):
            values[2*lane:2*lane+2] = [3.0, 1.0] if mask & (1 << lane) else [-2.0, 4.0]
        check(values)
    for _ in range(4096):
        check([rng.randint(-65536, 65536) / 512.0 for _ in range(32)])
else:
    if isint:
        # Every uint8 operand pair is tested, packed into four simultaneous lanes.
        for offset in range(0, 65536, 4):
            check([(offset+i) >> 8 for i in range(4)], [(offset+i) & 255 for i in range(4)])
        edges = [0, 1, 2, 3, 7, 15, 31, 32, 127, 255, 0x7fffffff, 0x80000000, 0xffffffff]
        for i in range(len(edges)):
            for j in range(len(edges)):
                check([edges[(i+k) % len(edges)] for k in range(4)],
                      [edges[(j+3*k) % len(edges)] for k in range(4)])
    else:
        edges = [-128.0, -16.0, -1.0, -0.0, 0.0, 0.001, 0.5, 1.0, 2.0, 16.0, 128.0]
        for i in range(len(edges)):
            for j in range(len(edges)):
                check([edges[(i+k) % len(edges)] for k in range(4)],
                      [edges[(j+3*k) % len(edges)] for k in range(4)])
    for mask in range(16):
        check([1 if mask & (1 << k) else 2 for k in range(4)],
              [0 if mask & (1 << k) else 3 for k in range(4)])
    for _ in range(4096):
        if isint:
            check([rng.getrandbits(32) for _ in range(4)], [rng.getrandbits(32) for _ in range(4)])
        else:
            check([rng.randint(-65536,65536)/512.0 for _ in range(4)],
                  [rng.randint(-65536,65536)/512.0 for _ in range(4)])

result = {"name": name, "status": "disagree" if mismatch_count else "agree",
          "batches": batches, "values_compared": lanes, "mismatches": mismatch_count,
          "witness": witness, "nan_pairs": nan_pairs, "max_ulp_difference": max_ulp,
          "scope": "finite native execution; four-lane SIMD versus scalar reference",
          "allowed_math_ulp": 1 if name == "upstream_ifelse" else 0}
(d / "native-results.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
raise SystemExit(1 if mismatch_count else 0)
