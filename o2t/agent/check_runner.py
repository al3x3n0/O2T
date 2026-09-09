#!/usr/bin/env python3
"""Instrument a generated ctypes checker. Advisory measurement, not a security sandbox."""
import argparse
from collections import defaultdict, deque
import ctypes
import json
import math
from pathlib import Path
import runpy
import struct
import sys


def equal_values(a, b, abi):
    if abi['type'] == 'uint32':
        return a == b
    def ordered(x):
        bits = struct.unpack('<I', struct.pack('<f', x))[0]
        return (~bits & 0xffffffff) if bits >> 31 else bits | 0x80000000
    return all((math.isnan(x) and math.isnan(y)) or
               (not math.isnan(x) and not math.isnan(y) and
                (x != 0 or y != 0 or math.copysign(1, x) == math.copysign(1, y)) and
                abs(ordered(x) - ordered(y)) <= abi.get('max_ulp', 0))
               for x, y in zip(a, b)) and len(a) == len(b)


def validate_inputs(inputs, abi):
    lengths = [abi['width']] if abi['kind'] == 'memory' else [abi['width'], abi['width']]
    if not isinstance(inputs, list) or len(inputs) != len(lengths):
        raise ValueError('wrong number of input arrays')
    for values, length in zip(inputs, lengths):
        if not isinstance(values, list) or len(values) != length:
            raise ValueError('wrong input array width')
        for value in values:
            if abi['type'] == 'uint32':
                if type(value) is not int or not 0 <= value <= 0xffffffff:
                    raise ValueError('input outside uint32 domain')
            elif type(value) not in (float, int) or not math.isfinite(value) or not abi['min'] <= value <= abi['max']:
                raise ValueError('input outside declared finite float domain')


def matches_predicate(value, predicate):
    if predicate == 'zero':
        return value == 0
    if predicate == 'nonzero':
        return value != 0
    if predicate == 'positive_zero':
        return value == 0 and math.copysign(1, value) > 0
    if predicate == 'negative_zero':
        return value == 0 and math.copysign(1, value) < 0
    if predicate == 'negative':
        return value < 0
    if predicate == 'positive':
        return value > 0
    raise ValueError('unknown coverage predicate')


def validate_coverage(requirements, abi):
    import re
    if not isinstance(requirements, list) or len(requirements) > 16:
        raise ValueError('coverage must contain at most 16 input requirements')
    ids = set()
    for r in requirements:
        if not isinstance(r, dict) or set(r) != {'id', 'array', 'predicate', 'lanes', 'minimum'}:
            raise ValueError('coverage requires id, array, predicate, lanes, minimum')
        if not isinstance(r['id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', r['id']) or r['id'] in ids:
            raise ValueError('invalid or duplicate coverage id')
        ids.add(r['id'])
        if type(r['array']) is not int or not 0 <= r['array'] < (1 if abi['kind'] == 'memory' else 2):
            raise ValueError('invalid coverage input array')
        if r['predicate'] not in ('zero', 'nonzero', 'positive_zero', 'negative_zero', 'negative', 'positive'):
            raise ValueError('unknown coverage predicate')
        if abi['type'] == 'uint32' and r['predicate'] in ('negative', 'negative_zero'):
            raise ValueError('coverage predicate cannot occur for uint32')
        if r['lanes'] not in ('each', 'any') or type(r['minimum']) is not int or not 1 <= r['minimum'] <= 100000:
            raise ValueError('invalid coverage lane policy or minimum')


def invoke(library, inputs, abi):
    validate_inputs(inputs, abi)
    scalar = ctypes.c_uint32 if abi['type'] == 'uint32' else ctypes.c_float
    array = scalar * abi['width']
    function = getattr(ctypes.CDLL(str(library)), abi['symbol'])
    function.restype = None
    pointer = ctypes.POINTER(scalar)
    if abi['kind'] == 'memory':
        function.argtypes = [pointer]
        a = array(*inputs[0])
        function(a)
        return list(a)
    function.argtypes = [pointer, pointer, pointer]
    x, y, out = array(*inputs[0]), array(*inputs[1]), array()
    function(x, y, out)
    return list(out)


class Trace:
    def __init__(self, paths, abi, coverage=None):
        self.paths = {str(Path(p).resolve()): side for side, p in paths.items()}
        self.abi = abi
        self.pending = defaultdict(lambda: {'before': deque(), 'after': deque()})
        self.calls = {'before': 0, 'after': 0}
        self.pairs = self.mismatches = 0
        self.unique = set()
        self.witness = None
        self.loader = ctypes.CDLL
        self.requirements = coverage or []
        self.coverage = {r['id']: [0] * (abi['width'] if r['lanes'] == 'each' else 1)
                         for r in self.requirements}

    def load(self, path, *args, **kwargs):
        key = str(Path(path).resolve())
        if key not in self.paths:
            raise ValueError('checker must load only the supplied --before and --after libraries')
        return Library(self.loader(key, *args, **kwargs), self.paths[key], self)

    def call(self, side, function, args):
        abi = self.abi
        expected = 1 if abi['kind'] == 'memory' else 3
        if len(args) != expected:
            raise ValueError('native call does not match declared ABI')
        if sum(self.calls.values()) >= 400000:
            raise ValueError('native call cap exceeded')
        scalar = ctypes.c_uint32 if abi['type'] == 'uint32' else ctypes.c_float
        pointers = [ctypes.cast(a, ctypes.POINTER(scalar)) for a in args]
        inputs = [list(p[:abi['width']]) for p in pointers[:1 if expected == 1 else 2]]
        validate_inputs(inputs, abi)
        function(*args)
        output = list(pointers[-1][:abi['width']])
        self.calls[side] += 1
        key = json.dumps(inputs, allow_nan=False)
        self.pending[key][side].append(output)
        pair = self.pending[key]
        if pair['before'] and pair['after']:
            before, after = pair['before'].popleft(), pair['after'].popleft()
            self.pairs += 1
            if key not in self.unique:
                for requirement in self.requirements:
                    matches = [matches_predicate(v, requirement['predicate'])
                               for v in inputs[requirement['array']]]
                    hits = matches if requirement['lanes'] == 'each' else [any(matches)]
                    counts = self.coverage[requirement['id']]
                    for index, hit in enumerate(hits):
                        counts[index] += int(hit)
            self.unique.add(key)
            if not equal_values(before, after, abi):
                self.mismatches += 1
                if self.witness is None:
                    self.witness = {'inputs': inputs, 'before': before, 'after': after}
        if not pair['before'] and not pair['after']:
            del self.pending[key]

    def report(self):
        return {'calls': self.calls, 'checked': self.pairs, 'unique_inputs': len(self.unique),
                'unpaired_calls': sum(len(q) for sides in self.pending.values() for q in sides.values()),
                'mismatches': self.mismatches, 'witness': self.witness,
                'coverage': {r['id']: {'counts': self.coverage[r['id']], 'minimum': r['minimum'],
                    'satisfied': all(n >= r['minimum'] for n in self.coverage[r['id']])}
                    for r in self.requirements},
                'scope': 'observed native invocations on supplied artifacts; finite execution only'}


class Function:
    def __init__(self, function, side, trace):
        object.__setattr__(self, 'function', function)
        object.__setattr__(self, 'side', side)
        object.__setattr__(self, 'trace', trace)

    def __setattr__(self, key, value):
        setattr(self.function, key, value)

    def __getattr__(self, key):
        return getattr(self.function, key)

    def __call__(self, *args):
        return self.trace.call(self.side, self.function, args)


class Library:
    def __init__(self, library, side, trace):
        self.library, self.side, self.trace = library, side, trace
        self.function = Function(getattr(library, trace.abi['symbol']), side, trace)

    def __getattr__(self, name):
        if name != self.trace.abi['symbol']:
            raise ValueError('checker must call the declared native symbol')
        return self.function

    __getitem__ = __getattr__


def main():
    ap = argparse.ArgumentParser()
    for name in ('before', 'after', 'abi'):
        ap.add_argument('--' + name, required=True)
    ap.add_argument('--checker')
    ap.add_argument('--trace')
    ap.add_argument('--replay')
    ap.add_argument('--coverage', default='[]')
    args = ap.parse_args()
    if args.replay:
        inputs, abi = json.loads(args.replay), json.loads(args.abi)
        print(json.dumps({'before': invoke(args.before, inputs, abi), 'after': invoke(args.after, inputs, abi)}))
        return
    if not args.checker or not args.trace:
        ap.error('--checker and --trace are required unless replaying')
    abi, coverage = json.loads(args.abi), json.loads(args.coverage)
    validate_coverage(coverage, abi)
    trace = Trace({'before': args.before, 'after': args.after}, abi, coverage)
    ctypes.CDLL = trace.load
    ctypes.cdll = ctypes.LibraryLoader(trace.load)
    sys.argv = [args.checker, '--before', args.before, '--after', args.after]
    try:
        runpy.run_path(args.checker, run_name='__main__')
    finally:
        Path(args.trace).write_text(json.dumps(trace.report()) + '\n')


if __name__ == '__main__':
    main()
