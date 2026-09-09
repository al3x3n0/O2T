#!/usr/bin/env python3
"""Prepare a full RV campaign manifest (macOS AArch64, LLVM 16 + 18).

The agent executes the manifest: fresh build, inputs, transformation, formal and
native verification, planted controls, then optional model-authored gap checks.
An existing clean pinned checkout and installed toolchains are prerequisites.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

HELPERS = Path(__file__).resolve().with_name('rv_campaign')
sys.path.insert(0, str(HELPERS.parent.parent))
sys.path.insert(0, str(HELPERS))
from rv_cases import CASES, TARGETED, gap_requirements


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rv-source', required=True, type=Path)
    parser.add_argument('--revision', default='44e0bdb78889da1261596a0cacfdf12693e19e4e')
    parser.add_argument('--llvm16', required=True, type=Path)
    parser.add_argument('--llvm18', required=True, type=Path)
    parser.add_argument('--z3', required=True, type=Path)
    parser.add_argument('--alive2', required=True, type=Path)
    parser.add_argument('--cmake', default=shutil.which('cmake'), type=Path)
    parser.add_argument('--directory', required=True, type=Path, help='new manifest/config directory')
    parser.add_argument('--case', action='append', dest='selected_cases',
                        help='select a supported case (repeatable; default: all)')
    parser.add_argument('--coverage', type=Path, help='additional per-case input requirements as JSON')
    args = parser.parse_args(argv)
    available = [(name, kind) for name, _, kind, _ in CASES] + [(n, 'integer') for n in TARGETED]
    selected = args.selected_cases or [n for n, _ in available]
    if len(set(selected)) != len(selected) or set(selected) - {n for n, _ in available}:
        parser.error('cases must be distinct supported case names')
    additions = json.loads(args.coverage.read_text()) if args.coverage else {}
    if not isinstance(additions, dict) or set(additions) - set(selected):
        parser.error('coverage must map selected case names to additional requirements')
    if args.cmake is None:
        parser.error('cmake is required')
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / n).exists() for n in ('campaign.json', 'config.json')):
        parser.error('use a fresh manifest directory')
    config = {k: str(getattr(args, k).resolve()) for k in ('llvm16', 'llvm18', 'z3', 'alive2')}
    config.update(rv=str(args.rv_source.resolve()), revision=args.revision, cases=selected)
    config_path = directory / 'config.json'
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    cases = [(n, k) for n, k in available if n in selected]
    jobs = []

    def job(name, requires, outputs, result=None, kind='execution', script=None, argv=None, timeout=300):
        command = argv or ['{python}', str(HELPERS / script), '--config', str(config_path),
                           '--out-dir', '{out_dir}']
        entry = dict(id=name, requires=requires, argv=command, outputs=outputs,
                     evidence_kind=kind, timeout=timeout)
        if result:
            entry['result'] = result
        jobs.append(entry)

    job('inspect-target', [], ['target.json'], 'target.json', script='inspect_target.py')
    llvm = args.llvm16.resolve()
    job('configure-rv', ['inspect-target'], ['rv-build/CMakeCache.txt'], argv=[
        str(args.cmake.resolve()), '-S', str(args.rv_source.resolve()), '-B', '{out_dir}/rv-build',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
        '-DLLVM_INSTALL_ROOT=' + str(llvm), '-DLLVM_DIR=' + str(llvm / 'lib/cmake/llvm'),
        '-DCMAKE_C_COMPILER=' + str(llvm / 'bin/clang'),
        '-DCMAKE_CXX_COMPILER=' + str(llvm / 'bin/clang++'),
        '-DCMAKE_PROJECT_RV_INCLUDE=' + str(HELPERS / 'llvm-setup.cmake'),
        '-DLLVM_BUILD_LLVM_DYLIB=ON', '-DLLVM_LINK_LLVM_DYLIB=ON',
        '-DRV_TARGETS_TO_BUILD=AArch64', '-DRV_ENABLE_PLUGIN=OFF'])
    job('build-rv', ['configure-rv'], ['rv-build/tools/rvTool'], argv=[
        str(args.cmake.resolve()), '--build', '{out_dir}/rv-build', '--target', 'rvTool', '-j4'], timeout=1200)
    job('prepare-inputs', ['inspect-target'], ['cases.json'] + [n + '/scalar.ll' for n, _ in cases],
        'cases.json', script='prepare_cases.py')
    transform_files = ['vector.ll', 'formal-before.ll', 'formal-after.ll', 'before.dylib',
                       'after.dylib', 'wrapper-before.cpp', 'wrapper-after.cpp']
    job('vectorize', ['build-rv', 'prepare-inputs'], ['transformations.json'] +
        [n + '/' + f for n, _ in cases for f in transform_files],
        'transformations.json', script='run_transformations.py')
    job('formal-checks', ['vectorize'], ['formal-summary.json'] +
        [n + '/' + f for n, _ in cases for f in ('o2t.json', 'alive2.log')],
        'formal-summary.json', 'formal', script='formal_checks.py')
    job('native-checks', ['vectorize'], ['native-summary.json'], 'native-summary.json',
        'native', script='native_all.py')
    job('negative-controls', ['formal-checks'], ['additional-results.json'], 'additional-results.json',
        'negative-control', script='additional_checks.py')
    job('prepare-gap-controls', ['vectorize'], ['native-controls.json'] +
        [n + '/negative.dylib' for n, _ in cases] +
        [control['artifact'] for n, _ in cases for control in gap_requirements(n)['controls']], 'native-controls.json',
        'negative-control', script='native_controls.py')
    bindings = []
    for name, kind in cases:
        abi = dict(kind='memory' if kind == 'memory' else 'lanes',
                   type='uint32' if kind == 'integer' else 'float32',
                   width=32 if kind == 'memory' else 4, symbol='run_batch')
        if kind != 'integer':
            abi.update(min=-128, max=128, max_ulp=1 if kind == 'float_uniform' else 0)
        requirements = gap_requirements(name)
        extra = additions.get(name, [])
        if not isinstance(extra, list):
            parser.error('additional coverage must be a list')
        requirements['coverage'] += extra
        from o2t.agent.check_runner import validate_coverage
        validate_coverage(requirements['coverage'], abi)
        bindings.append(dict(id=name, before=name + '/before.dylib', after=name + '/after.dylib',
                             negative=name + '/negative.dylib', abi=abi, **requirements))
    tracked = subprocess.check_output(['git', '-C', str(args.rv_source), 'ls-files', '-z']).decode().split('\0')
    inputs = [str(Path(__file__).resolve()), str(config_path)] + [str(p) for p in HELPERS.iterdir() if p.is_file()]
    if args.coverage:
        inputs.append(str(args.coverage.resolve()))
    inputs += [str((args.rv_source / p).resolve()) for p in tracked if p and (args.rv_source / p).is_file()]
    manifest = dict(version=1, name='RV-full-autonomous-verification',
        goal='Build the pinned RV target in a fresh directory and run every required job. Inspect fresh formal and native results, then choose an available unsupported case and write your own meaningful supplemental checker and fixture. Use validation feedback to repair within the attempt budget. Do not import existing checkers or infer formal proof from sampled agreement. Conclude with observed proofs, samples, planted defects and unresolved gaps.',
        inputs=inputs, jobs=jobs,
        gap_checks=dict(report_job='formal-checks', cases=bindings, required=True, max_attempts=3, min_unique=256))
    (directory / 'campaign.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(directory / 'campaign.json')


if __name__ == '__main__':
    main()
