#!/usr/bin/env python3
"""Build generic native output defects from this run's fresh vector IR."""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from common import context
from rv_cases import EXTRA_MUTATIONS

ctx = context()


def one(case):
    directory = ctx.out / case['name']
    wrapper = (directory / 'wrapper-after.cpp').read_text()
    if case['kind'] == 'memory':
        old = 'void run_batch(float* a) { verify(a); }'
        new = 'void run_batch(float* a) { verify(a); a[0] += 1.0f; }'
    else:
        old = 'out[0]=r[0];out[1]=r[1];out[2]=r[2];out[3]=r[3];'
        new = old + (' out[0] ^= 1u;' if case['kind'] == 'integer' else ' out[0] += 1.0f;')
    if wrapper.count(old) != 1:
        raise ValueError('unexpected wrapper for ' + case['name'])
    mutations = {'negative': new, **{'negative-' + name: old + code
                 for name, code in EXTRA_MUTATIONS.get(case['name'], {}).items()}}
    for name, replacement in mutations.items():
        source = directory / (name + '-wrapper.cpp')
        source.write_text(wrapper.replace(old, replacement))
        command = [str(ctx.llvm18 / 'bin/clang++'), '-std=c++17', '-O0', '-ffp-contract=off',
                   '-dynamiclib', str(directory / 'vector.ll'), str(source),
                   '-o', str(directory / (name + '.dylib'))]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (directory / (name + '-build.log')).write_text(result.stdout + result.stderr)
        result.check_returncode()
    return {'name': case['name'], 'status': 'built', 'controls': list(mutations),
            'scope': 'planted output defects, not RV bugs'}


with ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(one, json.loads((ctx.out / 'cases.json').read_text())))
(ctx.out / 'native-controls.json').write_text(json.dumps(results, indent=2) + '\n')
