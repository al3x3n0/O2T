#!/usr/bin/env python3
"""Bounded synthesis for observed formal gaps; every result remains advisory."""
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from o2t.agent.actions import ActionSpec, _truncate
from o2t.agent.check_runner import equal_values, validate_inputs, validate_coverage
from o2t.agent.staging import _SAFE_NAME

RUNNER = Path(__file__).with_name('check_runner.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def execute(argv, cwd, timeout, env=None):
    import os
    import signal
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   env=env or {'PATH': '/usr/bin:/bin', 'HOME': str(cwd)},
                                   start_new_session=(os.name == 'posix'))
        try:
            process.wait(timeout=timeout)
        except BaseException:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            raise
        out.seek(0); err.seek(0)
        return process.returncode, out.read(1000000).decode(errors='replace'), err.read(4000).decode(errors='replace')


def validate_candidate(record, paths, abi, minimum=256, timeout=30, coverage=None):
    """Measure checker calls independently, replay witnesses and break its fixture.

    These checks catch implementation mistakes, not actively hostile Python code.
    They do not confer formal proof or automatic promotion authority.
    """
    tool, fixture = Path(record['path']), Path(record['fixture'])
    originals = [tool, fixture, *paths.values()]
    hashes = {str(p): digest(p) for p in originals}
    coverage = [] if coverage is None else coverage
    controls = {k: p for k, p in paths.items() if k not in ('before', 'after')}
    evidence = {'status': 'rejected', 'trust': 'advisory-validated-native', 'runs': {},
                'required_coverage': coverage, 'controls': list(controls)}
    try:
        validate_coverage(coverage, abi)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            copies = {}
            for mode, source_after in [('identity', paths['before']), ('baseline', paths['after']), *controls.items()]:
                work = Path(tempfile.mkdtemp(dir=root))
                before, after = work / 'a.dylib', work / 'b.dylib'
                shutil.copy2(paths['before'], before); shutil.copy2(source_after, after)
                copies[mode] = (before, after)
                trace_path = work / 'trace.json'
                argv = [sys.executable, '-I', str(RUNNER), '--checker', str(tool),
                        '--before', str(before), '--after', str(after), '--abi', json.dumps(abi),
                        '--trace', str(trace_path), '--coverage', json.dumps(coverage)]
                code, stdout, stderr = execute(argv, work, timeout)
                measured = json.loads(trace_path.read_text()) if trace_path.exists() else {}
                run = {'exit_code': code, 'trace': measured, 'stderr': stderr}
                evidence['runs'][mode] = run
                try:
                    reported = json.loads(stdout)
                except ValueError:
                    raise ValueError(f'{mode}: checker must print one JSON object; {stderr[-500:]}')
                run['reported'] = reported
                if not isinstance(reported, dict) or not measured or measured.get('unpaired_calls'):
                    raise ValueError(f'{mode}: no matched native call trace or unpaired inputs')
                checked = reported.get('checked')
                if type(checked) is not int or checked != measured['checked'] or measured['unique_inputs'] < minimum:
                    raise ValueError(f'{mode}: checked must match traced call pairs and at least {minimum} distinct inputs are required')
                mismatches = measured['mismatches']
                expected = 'disagree' if mismatches else 'agree'
                if reported.get('status') != expected or reported.get('mismatches') != mismatches or code != bool(mismatches):
                    raise ValueError(f'{mode}: reported status/count/exit does not match independently observed outputs')
                missing = {r['id']: measured.get('coverage', {}).get(r['id']) for r in coverage
                           if not measured.get('coverage', {}).get(r['id'], {}).get('satisfied')}
                if missing:
                    evidence['missing_coverage'] = missing
                    raise ValueError(f'{mode}: input coverage requirements not met: ' + ', '.join(missing))
                if not mismatches and reported.get('witness') is not None:
                    raise ValueError(f'{mode}: agreeing run must have null witness')
                if mismatches:
                    witness = reported.get('witness')
                    if not isinstance(witness, dict):
                        raise ValueError(f'{mode}: mismatches require a concrete witness')
                    inputs = witness.get('inputs')
                    validate_inputs(inputs, abi)
                    rc, output, error = execute([sys.executable, '-I', str(RUNNER),
                        '--before', str(before), '--after', str(after), '--abi', json.dumps(abi),
                        '--replay', json.dumps(inputs)], work, timeout)
                    if rc != 0:
                        raise ValueError(f'{mode}: native witness replay failed: {error[-300:]}')
                    replay = json.loads(output)
                    actual_before, actual_after = replay['before'], replay['after']
                    if equal_values(actual_before, actual_after, abi):
                        raise ValueError(f'{mode}: reported witness does not reproduce')
                    if not equal_values(actual_before, witness.get('before', []), abi) or not equal_values(actual_after, witness.get('after', []), abi):
                        raise ValueError(f'{mode}: witness outputs differ from native replay')
                    run['witness_replayed'] = True
                if mode == 'identity' and mismatches:
                    raise ValueError('identity control must agree')
                if mode in controls and not mismatches:
                    raise ValueError(f'{mode}: planted native defect not detected; broaden the chosen inputs')
                if digest(before) != hashes[str(paths['before'])] or digest(after) != hashes[str(source_after)]:
                    raise ValueError('checker changed a native input artifact')
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(root),
                   'O2T_CHECK_BEFORE': str(copies['baseline'][0]),
                   'O2T_CHECK_AFTER': str(copies['baseline'][1]),
                   'O2T_CHECK_NEGATIVE': str(copies['negative'][1]), 'O2T_CHECK_ABI': json.dumps(abi)}
            evidence['fixtures'] = {}
            for mode in controls:
                env['O2T_CHECK_NEGATIVE'] = str(copies[mode][1])
                code, out, err = execute([sys.executable, '-I', str(fixture)], root, timeout, env)
                evidence['fixtures'][mode] = {'exit_code': code, 'stdout': out[-1000:], 'stderr': err}
                if mode == 'negative':
                    evidence['fixture'] = evidence['fixtures'][mode]
                if code != 0:
                    raise ValueError(f'{mode}: generated fixture failed: ' + err[-500:])
            broken = root / 'broken'
            broken.mkdir()
            (broken / tool.name).write_text('raise SystemExit(42)\n')
            shutil.copy2(fixture, broken / fixture.name)
            code, out, err = execute([sys.executable, '-I', str(broken / fixture.name)], root, timeout, env)
            evidence['broken_checker_fixture'] = {'exit_code': code, 'stdout': out[-1000:], 'stderr': err}
            if code == 0:
                raise ValueError('fixture passed with a broken sibling checker; it may test an embedded copy or ignore failures')
            for mode, (before, after) in copies.items():
                expected_after = paths['before'] if mode == 'identity' else paths['after'] if mode == 'baseline' else controls[mode]
                if digest(before) != hashes[str(paths['before'])] or digest(after) != hashes[str(expected_after)]:
                    raise ValueError('fixture changed a native input artifact')
        if any(digest(p) != sha for p, sha in hashes.items()):
            raise ValueError('candidate or original artifacts changed during validation')
        evidence['status'] = 'accepted'
    except (ValueError, OSError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        evidence['reason'] = str(exc)
    evidence['artifact_sha256'] = hashes
    return evidence


class GapChecks:
    def __init__(self, campaign, spec):
        self.campaign, self.spec = campaign, spec
        self.attempts = []
        self.selected = None
        if not isinstance(spec, dict) or set(spec) - {'report_job', 'cases', 'max_attempts', 'min_unique', 'required'}:
            raise ValueError('invalid gap_checks configuration')
        if spec.get('report_job') not in campaign.jobs:
            raise ValueError('gap_checks needs a report_job')
        if not campaign.jobs[spec['report_job']].get('result'):
            raise ValueError('gap report job needs a JSON result')
        for name, default, low, high in [('max_attempts', 3, 1, 10), ('min_unique', 256, 16, 100000)]:
            value = spec.get(name, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('invalid gap_checks ' + name)
        if type(spec.get('required', True)) is not bool:
            raise ValueError('gap_checks required must be boolean')
        cases = spec.get('cases')
        if not isinstance(cases, list) or not cases:
            raise ValueError('gap_checks requires native case bindings')
        self.cases = {}
        declared = {p for j in campaign.jobs.values() for p in j.get('outputs', [])}
        for case in cases:
            if not isinstance(case, dict) or set(case) - {'id', 'before', 'after', 'negative', 'abi', 'controls', 'coverage'} or not {'id', 'before', 'after', 'negative', 'abi'} <= set(case):
                raise ValueError('gap case requires id, before, after, negative, abi')
            if not isinstance(case['id'], str) or case['id'] in self.cases:
                raise ValueError('invalid or duplicate gap case id')
            if any(case[k] not in declared for k in ('before', 'after', 'negative')):
                raise ValueError('gap native libraries must be declared job outputs')
            abi = case['abi']
            if not isinstance(abi, dict) or set(abi) - {'kind', 'type', 'width', 'symbol', 'min', 'max', 'max_ulp'}:
                raise ValueError('invalid gap ABI')
            if abi.get('kind') not in ('lanes', 'memory') or abi.get('type') not in ('uint32', 'float32'):
                raise ValueError('unsupported gap ABI kind/type')
            if type(abi.get('width')) is not int or not 1 <= abi['width'] <= 64 or not isinstance(abi.get('symbol'), str):
                raise ValueError('invalid gap ABI width/symbol')
            if abi['type'] == 'float32':
                import math
                if any(type(abi.get(k)) not in (int, float) or not math.isfinite(abi[k]) for k in ('min', 'max')) or abi['min'] > abi['max']:
                    raise ValueError('float ABI requires finite min/max input bounds')
            if type(abi.get('max_ulp', 0)) is not int or not 0 <= abi.get('max_ulp', 0) <= 1:
                raise ValueError('invalid max_ulp')
            validate_coverage(case.get('coverage', []), abi)
            controls = case.get('controls', [])
            if not isinstance(controls, list) or len(controls) > 8:
                raise ValueError('controls must contain at most 8 additional native defects')
            ids = set()
            for control in controls:
                if not isinstance(control, dict) or set(control) != {'id', 'artifact'}:
                    raise ValueError('control requires id and artifact')
                if not isinstance(control['id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', control['id']) or control['id'] in ids:
                    raise ValueError('invalid or duplicate control id')
                ids.add(control['id'])
                if not isinstance(control['artifact'], str) or control['artifact'] not in declared:
                    raise ValueError('control library must be a declared output')
            self.cases[case['id']] = case

    def native_paths(self, case):
        return {**{k: case[k] for k in ('before', 'after', 'negative')},
                **{'control-' + c['id']: c['artifact'] for c in case.get('controls', [])}}

    def gaps(self):
        c = self.campaign
        record = c.records.get(self.spec['report_job'], {})
        if record.get('status') != 'completed':
            return None
        result = record.get('result')
        if not isinstance(result, list) or any(not isinstance(r, dict) or not isinstance(r.get('name'), str) or not isinstance(r.get('o2t'), dict) or not isinstance(r['o2t'].get('status'), str) for r in result):
            raise ValueError('gap report must be a list of {name, o2t:{status,reason}} records')
        return [{'id': r['name'], 'status': r['o2t']['status'], 'reason': r['o2t'].get('reason', ''),
                 'available': r['name'] in self.cases and all(
                     any(c.status(k) == 'completed' and path in j.get('outputs', []) for k, j in c.jobs.items())
                     for path in self.native_paths(self.cases[r['name']]).values())}
                for r in result if r['o2t'].get('status') in ('unsupported', 'unknown', 'timeout', 'inconclusive')]

    def snapshot(self):
        gaps = self.gaps()
        accepted = [a for a in self.attempts if a['validation']['status'] == 'accepted']
        return {'status': 'waiting' if gaps is None else ('not-applicable' if not gaps else
                ('accepted' if accepted else ('unavailable' if not any(g['available'] for g in gaps) else ('exhausted' if len(self.attempts) >= self.spec.get('max_attempts', 3) else 'open')))),
                'gaps': gaps, 'selected': self.selected, 'binding': self.cases.get(self.selected), 'attempts': self.attempts,
                'remaining_attempts': max(0, self.spec.get('max_attempts', 3) - len(self.attempts)),
                'trust': 'supplemental native evidence; formal unsupported statuses remain unchanged'}

    def restore(self, old):
        if not isinstance(old, dict):
            raise ValueError('invalid gap checkpoint')
        attempts = old.get('attempts', [])
        if not isinstance(attempts, list) or len(attempts) > self.spec.get('max_attempts', 3):
            raise ValueError('invalid gap checkpoint attempts')
        for attempt in attempts:
            if not isinstance(attempt, dict) or attempt.get('gap') not in self.cases or attempt.get('validator_sha256') != digest(Path(__file__)) + digest(RUNNER):
                raise ValueError('gap checkpoint validator changed')
            for path, sha in attempt['validation']['artifact_sha256'].items():
                if not Path(path).resolve().is_relative_to(self.campaign.out) or digest(path) != sha:
                    raise ValueError('gap checkpoint artifact changed')
        self.attempts = attempts
        self.selected = old.get('selected')
        if self.selected is not None and self.selected not in self.cases:
            raise ValueError('invalid selected gap in checkpoint')

    def complete(self):
        return not self.spec.get('required', True) or self.snapshot()['status'] in ('accepted', 'not-applicable')

    def must_continue(self):
        return self.spec.get('required', True) and self.snapshot()['status'] == 'open'

    def select(self, state, args, ctx, services):
        gap = next((g for g in self.gaps() or [] if g['id'] == args['id'] and g['available']), None)
        if gap is None:
            return {'error': 'gap-not-available'}
        self.selected = gap['id']
        self.campaign.checkpoint()
        return {**gap, 'native_binding': self.cases[self.selected], 'contract': self.contract()}

    def contract(self):
        return {'checker_cli': '--before LIB --after LIB; stdout one JSON object',
                'native_abi': 'lanes: void symbol(T *x, T *y, T *out), three pointers to arrays of exactly width elements. memory: void symbol(T *array), one pointer to exactly width elements, modified in place. There is no length argument. T is ctypes.c_uint32 or ctypes.c_float. Use the selected native_binding ABI as constants in the standalone checker; direct checker runs have no O2T_CHECK_ABI environment variable. Inputs and witness.inputs are lists of arrays (one array for memory, two for lanes). Only finite float inputs inside min/max are allowed.',
                'staging_names': 'The name argument MUST match ' + _SAFE_NAME.pattern + '. For name cv-agent-example the checker filename is cv-agent-example.py and its fixture is a sibling. The fixture must invoke that exact filename. Use properly escaped JSON strings in one action object.',
                'coverage_and_controls': 'Every configured native control must be detected. The fixture is rerun with each control supplied as O2T_CHECK_NEGATIVE. Every input coverage requirement must hold in every run: array is zero-based; each requires the predicate in every lane, any in at least one lane; minimum counts distinct paired inputs. These are input coverage predicates, not inferred branch coverage. At max_ulp=0 compare float32 bits exactly, except paired NaNs agree; positive and negative zero must remain distinct even with a ULP tolerance.',
                'output': {'status': 'agree|disagree', 'checked': 'number of paired native calls',
                           'mismatches': 'number of call pairs with differing output arrays',
                           'witness': 'null or {inputs:[input arrays], before:[values], after:[values]}'},
                'minimum_distinct_inputs': self.spec.get('min_unique', 256),
                'exit_codes': '0 agree, 1 disagree',
                'implementation': 'Standalone Python standard library ctypes; load supplied library arguments and call the declared symbol with explicit argtypes/restype. Do not import other checkers. Choose your own meaningful input strategy for the selected gap. Pair calls with identical input arrays. Floats use declared bounds and max_ulp; paired NaNs agree.',
                'fixture': 'Execute the actual sibling checker using Path(__file__).parent and sys.executable. Libraries are provided by O2T_CHECK_BEFORE, O2T_CHECK_AFTER, O2T_CHECK_NEGATIVE; O2T_CHECK_ABI is JSON. Assert exit codes, results and negative-control detection. Do not embed checker code or hardcode staging paths.',
                'validation': 'Independent native invocation trace, distinct input minimum, identity/baseline/negative runs under blinded library filenames, native witness replay, and broken-sibling fixture sensitivity. Results remain advisory; no full LLVM proof.'}

    def synthesize(self, state, args, ctx, services):
        if not self.selected or self.snapshot()['remaining_attempts'] <= 0:
            return {'error': 'select an available gap first; repairs are bounded by remaining_attempts'}
        staging = services.get('staging')
        if staging is None:
            return {'error': 'gap synthesis requires --enable-synthesis'}
        # Version every attempt; never overwrite the rejected checker needed for review.
        from o2t.agent.staging import StagingArea
        area = StagingArea(staging.root / ('gap-attempt-' + str(len(self.attempts) + 1)))
        record = area.stage_tool(args['name'], args['purpose'], args['tool_source'], args['fixture_source'])
        if 'error' in record:
            return record
        case = self.cases[self.selected]
        paths = {k: self.campaign.artifact(path) for k, path in self.native_paths(case).items()}
        validation = validate_candidate(record, paths, case['abi'], self.spec.get('min_unique', 256),
                                        min(60, services.get('action_timeout', 30)), case.get('coverage', []))
        attempt = {'gap': self.selected, 'candidate': record, 'validation': validation,
                   'validator_sha256': digest(Path(__file__)) + digest(RUNNER)}
        self.attempts.append(attempt)
        state.staged.append({**record, 'gap': self.selected, 'validation': validation})
        self.campaign.checkpoint()
        return _truncate(attempt, max_chars=2000, max_records=6)

    def registry(self):
        return {
            'select-gap': ActionSpec('select-gap', 'Select an observed formal gap for supplemental check synthesis.',
                {'id': {'type': 'string', 'required': True, 'enum': list(self.cases)}}, 'evidence', self.select),
            'synthesize-gap-check': ActionSpec('synthesize-gap-check',
                'Stage a checker and fixture for the selected gap; independently validate them. Reuse this action to repair a rejected candidate.',
                {**{k: {'type': 'string', 'required': True} for k in ('purpose', 'tool_source', 'fixture_source')},
                 'name': {'type': 'string', 'required': True, 'pattern': _SAFE_NAME.pattern}},
                'synthesis', self.synthesize)}
