#!/usr/bin/env python3
"""Configured compiled-target campaigns. Commands are operator data, never model output.

Job success means execution completed, not that the target is correct. Even JSON
labelled `formal` remains observed evidence and cannot enter the agent proof gate.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from o2t.agent.actions import ActionSpec, build_registry
from o2t.agent.llm import LLMClient
from o2t.agent.loop import run_pass_agent
from o2t.agent.staging import StagingArea


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


class Campaign:
    def __init__(self, manifest: Path, out_dir: Path, timeout: int = 120):
        self.manifest = manifest.resolve()
        self.root = self.manifest.parent
        self.out = out_dir.resolve()
        if type(timeout) is not int or timeout <= 0:
            raise ValueError('action timeout must be a positive integer')
        self.timeout = timeout
        self.spec = json.loads(self.manifest.read_text())
        self.records = {}
        self._validate()
        self.gap_checks = None
        if 'gap_checks' in self.spec:
            from o2t.agent.gap_checks import GapChecks
            self.gap_checks = GapChecks(self, self.spec['gap_checks'])
        inputs = {str((self.root / p).resolve()): _hash((self.root / p).resolve())
                  for p in self.spec.get('inputs', [])}
        self.fingerprint = hashlib.sha256(json.dumps(
            [self.spec, inputs, str(self.root), str(self.out)], sort_keys=True).encode()).hexdigest()
        self.out.mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex

    def _validate(self):
        s = self.spec
        if not isinstance(s, dict) or set(s) - {'version', 'name', 'goal', 'inputs', 'jobs', 'gap_checks'}:
            raise ValueError('invalid campaign object or unknown fields')
        if type(s.get('version')) is not int or s.get('version') != 1 or not isinstance(s.get('name'), str):
            raise ValueError('campaign requires version 1 and a name')
        if not isinstance(s.get('goal', ''), str):
            raise ValueError('goal must be a string')
        inputs = s.get('inputs', [])
        if not isinstance(inputs, list) or not all(isinstance(p, str) for p in inputs):
            raise ValueError('inputs must list files to fingerprint')
        jobs = s.get('jobs')
        if not isinstance(jobs, list) or not jobs or len(jobs) > 100:
            raise ValueError('campaign requires 1..100 jobs')
        self.jobs = {}
        for j in jobs:
            if not isinstance(j, dict) or set(j) - {
                    'id', 'description', 'argv', 'requires', 'required', 'timeout',
                    'outputs', 'result', 'evidence_kind', 'success_exit_codes'}:
                raise ValueError('invalid job or unknown fields')
            name = j.get('id', '')
            if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
                raise ValueError('invalid job id')
            if name in self.jobs:
                raise ValueError('duplicate job id')
            argv = j.get('argv')
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
                raise ValueError('argv must be a nonempty string array')
            codes = j.get('success_exit_codes', [0])
            if not isinstance(codes, list) or not codes or not all(type(v) is int and 0 <= v <= 255 for v in codes):
                raise ValueError('success_exit_codes must be a nonempty list of exit codes')
            for field in ('requires', 'outputs'):
                value = j.get(field, [])
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError(f'{field} must be a string array')
            if type(j.get('required', True)) is not bool:
                raise ValueError('required must be boolean')
            if type(j.get('timeout', self.timeout)) is not int or j.get('timeout', self.timeout) <= 0:
                raise ValueError('timeout must be a positive integer')
            if j.get('evidence_kind', 'execution') not in ('execution', 'formal', 'native', 'negative-control'):
                raise ValueError('unknown evidence_kind')
            if not isinstance(j.get('description', ''), str):
                raise ValueError('description must be a string')
            for path in j.get('outputs', []):
                self.artifact(path)
            if 'result' in j and j['result'] not in j.get('outputs', []):
                raise ValueError('result must be a declared output')
            self.jobs[name] = j
        if not any(j.get('required', True) for j in jobs):
            raise ValueError('campaign must have a required job')
        owners = [str(self.artifact(p)) for j in jobs for p in j.get('outputs', [])]
        if len(set(owners)) != len(owners):
            raise ValueError('outputs must have a single producer')
        visiting, visited = set(), set()

        def visit(name):
            if name not in self.jobs:
                raise ValueError('unknown dependency ' + name)
            if name in visiting:
                raise ValueError('cyclic campaign dependencies')
            if name in visited:
                return
            visiting.add(name)
            for dep in self.jobs[name].get('requires', []):
                visit(dep)
            visiting.remove(name)
            visited.add(name)
        for name in self.jobs:
            visit(name)

    def artifact(self, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('artifact must be an output-relative path')
        path = (self.out / relative).resolve()
        # Reserve internal state and logs from job outputs / model artifact reads.
        if not path.is_relative_to(self.out) or path.relative_to(self.out).parts[0:1] == ('.campaign',):
            raise ValueError('artifact escapes output root or uses reserved .campaign directory')
        if path == self.out:
            raise ValueError('artifact must name a file')
        return path

    def status(self, name):
        if name in self.records:
            return self.records[name]['status']
        dependencies = [self.status(d) for d in self.jobs[name].get('requires', [])]
        if any(s in ('failed', 'blocked') for s in dependencies):
            return 'blocked'
        return 'ready' if all(s == 'completed' for s in dependencies) else 'pending'

    def required(self):
        needed = set()
        def add(name):
            if name in needed:
                return
            needed.add(name)
            for dep in self.jobs[name].get('requires', []):
                add(dep)
        for name, job in self.jobs.items():
            if job.get('required', True):
                add(name)
        return needed

    def snapshot(self):
        statuses = {k: self.status(k) for k in self.jobs}
        required = self.required()
        result = {'name': self.spec['name'], 'fingerprint': self.fingerprint,
                'manifest': str(self.manifest), 'out_dir': str(self.out),
                'status': ('complete' if all(statuses[k] == 'completed' for k in required)
                           else 'incomplete'),
                'jobs': self.records, 'statuses': statuses,
                'trust': 'observed-tool-output; execution completion is not a correctness verdict'}
        if self.gap_checks:
            result['gap_checks'] = self.gap_checks.snapshot()
            if not self.gap_checks.complete():
                result['status'] = 'incomplete'
        return result

    def resume(self, path):
        document = json.loads(Path(path).read_text())
        old = document.get('campaign') if isinstance(document, dict) else None
        if not isinstance(old, dict) or not isinstance(old.get('jobs'), dict):
            raise ValueError('invalid campaign checkpoint')
        if old.get('fingerprint') != self.fingerprint:
            raise ValueError('resume rejected: manifest, declared inputs or output directory changed')
        for name, record in old['jobs'].items():
            if not isinstance(record, dict) or name not in self.jobs or record.get('status') not in ('completed', 'failed'):
                raise ValueError('invalid resume job record')
            if record['status'] == 'completed':
                expected = self.jobs[name].get('outputs', [])
                if not isinstance(record.get('output_sha256'), dict) or set(record['output_sha256']) != set(expected):
                    raise ValueError('resume rejected: missing output hashes')
                for rel, digest in record['output_sha256'].items():
                    if _hash(self.artifact(rel)) != digest:
                        raise ValueError('resume rejected: output changed: ' + rel)
        for name, record in old['jobs'].items():
            if record['status'] == 'completed' and any(
                    old['jobs'].get(dep, {}).get('status') != 'completed'
                    for dep in self.jobs[name].get('requires', [])):
                raise ValueError('resume rejected: completed job has incomplete dependencies')
        self.records = old['jobs']
        if self.gap_checks:
            self.gap_checks.restore(old.get('gap_checks', {}))

    def checkpoint(self):
        _write(self.out / '.campaign/checkpoint.json', {'campaign': self.snapshot()})

    def step(self, state, args, ctx, services):
        name = args['id']
        status = self.status(name)
        if status != 'ready':
            return {'error': 'job-not-ready', 'id': name, 'status': status}
        job = self.jobs[name]
        substitutions = {'{python}': sys.executable, '{manifest_dir}': str(self.root),
                         '{out_dir}': str(self.out)}
        argv = job['argv'][:]
        for token, value in substitutions.items():
            argv = [a.replace(token, value) for a in argv]
        logdir = self.out / '.campaign' / self.session
        logdir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        record = {'status': 'failed', 'argv': argv, 'evidence_kind': job.get('evidence_kind', 'execution'),
                  'trust': 'observed-tool-output', 'exit_code': None}
        # Commands receive neither provider credentials nor arbitrary inherited API tokens.
        env = {k: v for k, v in os.environ.items()
               if k in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SDKROOT', 'DEVELOPER_DIR')}
        try:
            with tempfile.TemporaryDirectory() as home, \
                    (logdir / (name + '.stdout')).open('w') as stdout, \
                    (logdir / (name + '.stderr')).open('w') as stderr:
                env['HOME'] = home
                proc = subprocess.Popen(argv, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                        stdout=stdout, stderr=stderr,
                                        start_new_session=(os.name == 'posix'))
                try:
                    proc.wait(timeout=min(self.timeout, job.get('timeout', self.timeout)))
                except BaseException:
                    if os.name == 'posix':
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        proc.kill()
                    proc.wait()
                    raise
            record['exit_code'] = proc.returncode
            if proc.returncode in job.get('success_exit_codes', [0]):
                record['output_sha256'] = {p: _hash(self.artifact(p)) for p in job.get('outputs', [])}
                if job.get('result'):
                    path = self.artifact(job['result'])
                    if path.stat().st_size > 1_000_000:
                        raise ValueError('JSON result exceeds 1MB')
                    record['result'] = json.loads(path.read_text())
                record['status'] = 'completed'
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            record['error'] = str(exc)
        record['elapsed_s'] = round(time.monotonic() - started, 3)
        record['logs'] = str(logdir / name)
        self.records[name] = record
        self.checkpoint()
        return record

    def read(self, state, args, ctx, services):
        rel = args['path']
        allowed = {p for k, j in self.jobs.items() if self.status(k) == 'completed'
                   for p in j.get('outputs', [])}
        if rel not in allowed:
            return {'error': 'not a completed declared output'}
        try:
            offset = int(args.get('offset', '0'))
            if offset < 0:
                raise ValueError('offset must be nonnegative')
            with self.artifact(rel).open('rb') as fh:
                fh.seek(offset)
                data = fh.read(10001)
            return {'path': rel, 'text': data[:10000].decode('utf-8', errors='replace'),
                    'next_offset': offset + min(len(data), 10000), 'more': len(data) > 10000}
        except (OSError, ValueError) as exc:
            return {'error': str(exc)}

    def registry(self, synthesis):
        from o2t.agent.actions import _truncate
        available = build_registry(enable_synthesis=synthesis)
        registry = {k: v for k, v in available.items() if k in ('conclude', 'synthesize-tool')}
        if self.gap_checks:
            registry.pop('synthesize-tool', None)
            if synthesis:
                registry.update(self.gap_checks.registry())
        original = registry['conclude']
        def conclude(state, args, ctx, services):
            remaining = [k for k in self.required() if self.status(k) in ('ready', 'pending')]
            if remaining:
                return {'error': 'campaign-incomplete', 'remaining_required': sorted(remaining)}
            if self.gap_checks and self.gap_checks.must_continue():
                return {'error': 'gap-check-required', 'gap_checks': _truncate(self.gap_checks.snapshot())}
            return original.handler(state, args, ctx, services)
        registry['conclude'] = replace(original, handler=conclude)
        registry['campaign-step'] = ActionSpec('campaign-step',
            'Execute one ready configured job. Completed jobs cannot be rerun. Exit zero is not a proof.',
            {'id': {'type': 'string', 'required': True, 'enum': list(self.jobs)}}, 'evidence', self.step)
        registry['read-artifact'] = ActionSpec('read-artifact',
            'Read a completed declared output, up to 10000 bytes; offset is a decimal byte offset.',
            {'path': {'type': 'string', 'required': True}, 'offset': {'type': 'string'}},
            'evidence', self.read)
        return registry


class CampaignClient(LLMClient):
    def __init__(self, campaign, registry, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.campaign, self.registry = campaign, registry
        self.path = campaign.out / '.campaign' / campaign.session / 'transcript.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def call(self, request):
        c = self.campaign
        if self.transcript:
            # Only structured transport metadata is fed back, never raw stderr.
            for line in self.transcript[-1].get('stderr', '').splitlines():
                try:
                    metadata = json.loads(line)
                except ValueError:
                    continue
                if isinstance(metadata, dict) and metadata.get('finish_reason') == 'length':
                    request['transport_feedback'] = {
                        'error': 'previous-response-truncated',
                        'guidance': 'No action was executed. Return one concise valid action. Keep reasoning brief and checker/fixture source focused so both fit within the output budget.'}
        request['instruction'] = (
            'Execute this configured compiled-target campaign. Select ready jobs; completed jobs '
            'are already done and their recorded evidence is supplied. Required jobs and their '
            'dependencies must be attempted before conclusion, including independent jobs when '
            'another branch fails. Optional synthesis remains advisory. Job completion means '
            'execution succeeded, never that correctness was proved. Distinguish formal, native '
            'and negative-control evidence. Conclude with honest limited scope. Reply with one '
            'JSON action matching answer_schema.')
        request['campaign'] = {'name': c.spec['name'], 'goal': c.spec.get('goal', ''),
                               'out_dir': str(c.out), 'jobs': [
            {'id': k, 'description': j.get('description', ''), 'status': c.status(k),
             'required': k in c.required(), 'requires': j.get('requires', []),
             'outputs': j.get('outputs', []), 'evidence_kind': j.get('evidence_kind', 'execution')}
            for k, j in c.jobs.items()]}
        # Use the same bounded representation as ordinary action observations.
        from o2t.agent.actions import _truncate
        request['campaign']['observations'] = _truncate(c.records)
        if c.gap_checks:
            request['instruction'] += (' When gap_checks is open, select an available observed gap and synthesize a supplemental native checker. Repair rejected candidates using validation feedback within remaining_attempts. Do not conclude until an independently validated check is accepted or attempts are exhausted. You choose the gap and input strategy within the configured coverage and control requirements. Formal unsupported results stay unsupported.')
            request['campaign']['gap_checks'] = _truncate(c.gap_checks.snapshot(), max_records=12)
            request['campaign']['gap_check_contract'] = c.gap_checks.contract()
        ready = [k for k in c.jobs if c.status(k) == 'ready']
        for action in request['actions']:
            if action['name'] == 'campaign-step':
                action['args_schema'] = {'id': {'type': 'string', 'required': True, 'enum': ready}}
        # Validate against the advertised enum too, not only the initial registry.
        self.registry['campaign-step'] = replace(self.registry['campaign-step'], args_schema={
            'id': {'type': 'string', 'required': True, 'enum': ready}})
        reply = super().call(request)
        with self.path.open('a') as fh:
            fh.write(json.dumps(self.transcript[-1]) + '\n')
        return reply


def run_campaign(args):
    if args.source or args.passes or args.include or args.exclude or args.fail_on_refuted or args.fail_on_agent_refuted:
        raise ValueError('--campaign cannot mix source triage or formal-refutation exit gates')
    out = args.out_dir or Path(tempfile.mkdtemp(prefix='cv-campaign-'))
    campaign = Campaign(args.campaign, out, args.action_timeout)
    if campaign.gap_checks and campaign.gap_checks.spec.get('required', True) and not args.enable_synthesis:
        raise ValueError('required gap_checks needs --enable-synthesis')
    if args.resume:
        campaign.resume(args.resume)
    elif (out / '.campaign/checkpoint.json').exists():
        raise ValueError('output directory already has a campaign; use --resume or a fresh directory')
    else:
        if any(campaign.artifact(p).exists() for j in campaign.jobs.values() for p in j.get('outputs', [])):
            raise ValueError('declared outputs already exist; use a fresh output directory')
    campaign.checkpoint()
    registry = campaign.registry(args.enable_synthesis)
    client = CampaignClient(campaign, registry, args.llm_command,
                            timeout=args.llm_timeout, budget=args.budget)
    entry = {'source': None, 'pass_name': campaign.spec['name'],
             'headline': {'status': 'advisory', 'reason': 'configured compiled-target campaign'}}
    if campaign.snapshot()['status'] == 'complete':
        agent = {'status': 'resumed-complete', 'llm_calls': 0, 'steps': [], 'formal_checks': [],
                 'staged_tools': [], 'conclusion': None}
    else:
        agent = run_pass_agent(entry, {}, client, {
            'staging': StagingArea(campaign.out / 'agent-staging') if args.enable_synthesis else None,
            'workdir': campaign.out, 'action_timeout': args.action_timeout}, registry,
            max_steps=args.max_steps_per_pass)
    report = {'mode': 'campaign', 'campaign': campaign.snapshot(), 'agent': agent,
              'llm_transcript': str(client.path), 'llm_calls_used': client.used}
    from o2t.agent.campaign_evidence import summarize
    report['evidence_summary'] = summarize(report['campaign'])
    _write(campaign.out / '.campaign' / campaign.session / 'report.json', report)
    return report, 0 if report['campaign']['status'] == 'complete' else 2


def render_summary(report):
    c = report['campaign']
    lines = [f"Campaign {c['name']}: {c['status']} (execution coverage, not a correctness verdict)"]
    for name, status in c['statuses'].items():
        r = c['jobs'].get(name, {})
        lines.append(f"  {name}: {status}; evidence={r.get('evidence_kind', 'none')}; exit={r.get('exit_code')}")
    if c.get('gap_checks'):
        gaps = c['gap_checks']
        lines.append(f"Supplemental gap checks: {gaps['status']}; attempts={len(gaps['attempts'])}; formal coverage unchanged")
        for attempt in gaps['attempts']:
            validation = attempt['validation']
            lines.append(f"  {attempt['gap']}: {validation['status']} (advisory native evidence)")
    evidence = report.get('evidence_summary', {})
    if evidence.get('formal'):
        from collections import Counter
        counts = Counter(r['status'] for r in evidence['formal'])
        lines.append('Observed O2T results: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
        lines.append('Unresolved formal cases: ' + ', '.join(r['case'] for r in evidence['unresolved']))
    if evidence.get('native'):
        values = sum(r.get('values_compared', 0) for r in evidence['native'] if type(r.get('values_compared', 0)) is int)
        differences = sum(r.get('mismatches', 0) for r in evidence['native'] if type(r.get('mismatches', 0)) is int)
        lines.append(f'Native observations: values={values}; mismatches={differences}; finite execution only')
    if evidence.get('controls'):
        lines.append('Planted/compatibility controls: ' + ', '.join(f"{r['case']}={r.get('status', 'unknown')}" for r in evidence['controls']))
    lines.append(f"Model calls: {report['llm_calls_used']}; formal claims are not inferred from job success.")
    return '\n'.join(lines) + '\n'
