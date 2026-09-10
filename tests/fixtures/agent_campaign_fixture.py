#!/usr/bin/env python3
"""CLI campaign regression: real commands, scripted decisions, no network/model."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import importlib.util
import io

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.campaign import Campaign, CampaignClient
from o2t.agent.llm import LLMClient
from o2t.agent.actions import validate_response
from o2t.agent.campaign_evidence import summarize


def main():
    # Formal/native/control observations cannot promote one another or hide failures.
    summary = summarize({'statuses': {'failed-job': 'failed'}, 'jobs': {
        'samples': {'status': 'completed', 'evidence_kind': 'native', 'result': [
            {'name': 'sample', 'status': 'agree', 'values_compared': 32, 'o2t': {'status': 'proved'}}]},
        'formal': {'status': 'completed', 'evidence_kind': 'formal', 'result': [
            {'name': 'loop', 'o2t': {'status': 'unsupported'},
             'alive2': {'status': 'ok', 'scope': 'bounded loop checking'}}]},
        'control': {'status': 'completed', 'evidence_kind': 'negative-control', 'result': [
            {'name': 'planted', 'status': 'refuted'}]}}})
    assert len(summary['formal']) == 1 and summary['unresolved'][0]['case'] == 'loop'
    assert summary['native'][0]['values_compared'] == 32
    assert summary['controls'][0]['case'] == 'planted'
    assert summary['execution_issues'] == [{'job': 'failed-job', 'status': 'failed'}]
    malformed = summarize({'statuses': {}, 'jobs': {'formal': {
        'status': 'completed', 'evidence_kind': 'formal', 'result': [
            {'name': 'malformed', 'o2t': {'status': ['proved']}}]}}})
    assert malformed['formal'][0]['status'] == 'unknown'
    # The RV recipe connects fresh build/verification outputs to later gap checks.
    spec = importlib.util.spec_from_file_location('rv_recipe', ROOT / 'tools/cv-agent-rv-campaign.py')
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        argv = ['recipe', '--directory', str(root / 'recipe'), '--rv-source', str(root / 'rv'),
                '--llvm16', str(root / 'llvm16'), '--llvm18', str(root / 'llvm18'),
                '--z3', '/bin/true', '--alive2', '/bin/true', '--cmake', '/bin/true']
        with patch('sys.argv', argv), patch.object(recipe.subprocess, 'check_output', return_value=b''), redirect_stdout(io.StringIO()):
            recipe.main()
        campaign = Campaign(root / 'recipe/campaign.json', root / 'out')
        assert campaign.jobs['build-rv']['outputs'] == ['rv-build/tools/rvTool']
        assert '-DLLVM_BUILD_LLVM_DYLIB=ON' in campaign.jobs['configure-rv']['argv']
        assert '-DLLVM_LINK_LLVM_DYLIB=ON' in campaign.jobs['configure-rv']['argv']
        assert campaign.jobs['vectorize']['requires'] == ['build-rv', 'prepare-inputs']
        assert campaign.gap_checks.spec['report_job'] == 'formal-checks'
        assert len(campaign.gap_checks.cases) == 12
        assert campaign.gap_checks.snapshot()['status'] == 'waiting'
        assert campaign.gap_checks.cases['guarded_division']['coverage'][0]['predicate'] == 'zero'
        assert 'upstream_simple/negative-zero-sign.dylib' in campaign.jobs['prepare-gap-controls']['outputs']
        registry = campaign.registry(True)
        assert registry['synthesize-gap-check'].args_schema['name']['pattern'].startswith('^cv-agent-')
        invalid, reason = validate_response(registry, {'action':'synthesize-gap-check', 'args': {
            'name':'wrong_name', 'purpose':'test', 'tool_source':'pass', 'fixture_source':'pass'}})
        assert invalid is None and 'must match' in reason
        client = CampaignClient(campaign, registry, '/bin/false')
        client.transcript.append({'stderr': 'sensitive raw noise\n{"finish_reason":"length"}\n'})
        request = {'actions': [{'name':'campaign-step'}]}
        with patch.object(LLMClient, 'call', return_value=None):
            client.call(request)
        assert request['transport_feedback']['error'] == 'previous-response-truncated'
        assert 'sensitive raw noise' not in json.dumps(request)
        assert not (root / 'out/rv-build').exists(), 'recipe executed a build outside agent control'
    # Provider serialization and error redaction without a network call.
    spec = importlib.util.spec_from_file_location('deepseek_provider', ROOT / 'tools/cv-agent-deepseek.py')
    provider_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider_module)
    response = {'choices':[{'message':{'content':'{"action":"conclude","args":{"proposal":"inconclusive"}}'},'finish_reason':'stop'}], 'model':'fixture', 'usage':{'total_tokens':3}}
    requests = []
    def fake_open(request, timeout):
        requests.append(json.loads(request.data))
        assert request.full_url == 'https://api.deepseek.com/chat/completions'
        assert request.headers['Authorization'] == 'Bearer fixture-secret'
        assert json.loads(request.data)['messages'][1]['content'] == '{"task": "fixture"}'
        return io.StringIO(json.dumps(response))
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'fixture-secret'}), \
            patch.object(provider_module, 'urlopen', fake_open), \
            patch('sys.stdin', io.StringIO('{"task":"fixture"}')), \
            redirect_stdout(stdout), redirect_stderr(stderr):
        assert provider_module.main(['--model','fixture']) == 0
    assert json.loads(stdout.getvalue())['action'] == 'conclude'
    assert 'fixture-secret' not in stdout.getvalue() + stderr.getvalue()
    assert 'reasoning_effort' not in requests[-1]
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'fixture-secret'}), \
            patch.object(provider_module, 'urlopen', fake_open), \
            patch('sys.stdin', io.StringIO('{"task":"fixture"}')), \
            redirect_stdout(stdout), redirect_stderr(stderr):
        assert provider_module.main(['--model','fixture','--thinking','enabled','--reasoning-effort','low']) == 0
    assert requests[-1]['thinking']=={'type':'enabled'} and requests[-1]['reasoning_effort']=='low'
    assert json.loads(stderr.getvalue())['reasoning_effort']=='low'
    assert 'fixture-secret' not in stdout.getvalue()+stderr.getvalue()
    response['choices'][0]['finish_reason'] = 'length'
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'fixture-secret'}), \
            patch.object(provider_module, 'urlopen', fake_open), \
            patch('sys.stdin', io.StringIO('{"task":"fixture"}')), \
            redirect_stdout(stdout), redirect_stderr(stderr):
        assert provider_module.main(['--model','fixture']) == 2
    assert not stdout.getvalue(), 'parseable truncated prefix must never become an action'
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        worker = root / 'worker.py'
        worker.write_text('''import json,os,sys,time
from pathlib import Path
if sys.argv[1]=='timeout':
 import subprocess
 subprocess.Popen([sys.executable,'-c',"import time;from pathlib import Path;time.sleep(2);Path("+repr(sys.argv[2])+").write_text('escaped')"])
 time.sleep(10)
if sys.argv[1]=='fail':raise SystemExit(3)
p=Path(sys.argv[2]);p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(json.dumps({'status':'refuted','scope':'planted control','secret_present':'DEEPSEEK_API_KEY' in os.environ,'stdin_empty':sys.stdin.read()==''}))
if sys.argv[1]=='refute':raise SystemExit(1)
''')
        provider = root / 'provider.py'
        provider.write_text('''import json,sys
r=json.load(sys.stdin)
ready=next(a for a in r['actions'] if a['name']=='campaign-step')['args_schema']['id']['enum']
# Try premature conclusion once in every segment, then obey measured readiness.
if not r['evidence'] or not ready: a={'action':'conclude','args':{'proposal':'proved'}}
else: a={'action':'campaign-step','args':{'id':ready[0]}}
print(json.dumps(a))
''')
        spec = {'version': 1, 'name': 'fixture', 'inputs': ['worker.py'], 'jobs': [
            {'id': 'first', 'argv': ['{python}', str(worker), 'refute', '{out_dir}/first.json'],
             'outputs': ['first.json'], 'result': 'first.json', 'evidence_kind': 'negative-control', 'success_exit_codes': [0, 1]},
            {'id': 'second', 'requires': ['first'],
             'argv': ['{python}', str(worker), 'write', '{out_dir}/second.json'],
             'outputs': ['second.json'], 'result': 'second.json', 'evidence_kind': 'native'}]}
        manifest = root / 'campaign.json'
        manifest.write_text(json.dumps(spec))
        out, report = root / 'out', root / 'report.json'
        def run(*extra):
            cmd = [sys.executable, str(ROOT / 'tools/cv-agent.py'), '--campaign', str(manifest),
                   '--out-dir', str(out), '--report', str(report), '--llm-command',
                   shlex.join([sys.executable, str(provider)]), '--budget', '8'] + list(extra)
            env = {**os.environ, 'DEEPSEEK_API_KEY': 'fixture-secret'}
            return subprocess.run(cmd, capture_output=True, text=True, input='fixture-input-secret', env=env, timeout=30)
        p = run('--budget', '2')
        assert p.returncode == 2, p.stderr
        r = json.loads(report.read_text())
        assert r['campaign']['statuses'] == {'first': 'completed', 'second': 'ready'}
        assert r['agent']['steps'][0]['observation']['error'] == 'campaign-incomplete'
        assert r['campaign']['jobs']['first']['exit_code'] == 1
        assert not r['campaign']['jobs']['first']['result']['secret_present']
        assert r['campaign']['jobs']['first']['result']['stdin_empty']
        first_mtime = (out / 'first.json').stat().st_mtime_ns
        checkpoint = out / '.campaign/checkpoint.json'
        p = run('--resume', str(checkpoint))
        assert p.returncode == 0, p.stderr
        r = json.loads(report.read_text())
        assert r['campaign']['status'] == 'complete'
        assert r['agent']['formal_checks'] == []
        assert r['agent']['conclusion']['trust'] == 'advisory'
        assert (out / 'first.json').stat().st_mtime_ns == first_mtime
        transcript = [json.loads(l) for l in Path(r['llm_transcript']).read_text().splitlines()]
        enums = [a['args_schema']['id']['enum'] for a in transcript[0]['request']['actions'] if a['name']=='campaign-step']
        assert enums == [['second']], enums
        assert 'not a correctness verdict' in p.stdout
        p = run('--resume', str(report))
        assert p.returncode == 0 and json.loads(report.read_text())['llm_calls_used'] == 0
        (out / 'first.json').write_text('{}')
        assert 'output changed' in run('--resume', str(checkpoint)).stderr
        # Changing declared sources invalidates resume independently of output hashes.
        worker.write_text(worker.read_text() + '\n# changed\n')
        assert 'declared inputs' in run('--resume', str(checkpoint)).stderr
        assert run().returncode == 2

        # A failed branch cannot excuse skipping independent required work.
        fail_spec = {'version': 1, 'name': 'failure', 'jobs': [
            {'id': 'fail', 'argv': [sys.executable, str(worker), 'fail']},
            {'id': 'blocked', 'requires': ['fail'], 'argv': ['must-not-run']},
            {'id': 'independent', 'argv': [sys.executable, str(worker), 'write', '{out_dir}/ok.json'], 'outputs': ['ok.json']}]}
        manifest.write_text(json.dumps(fail_spec))
        out = root / 'failed-out'
        p = run()
        assert p.returncode == 2, p.stderr
        statuses = json.loads(report.read_text())['campaign']['statuses']
        assert statuses == {'fail':'failed','blocked':'blocked','independent':'completed'}, statuses
        c = Campaign(manifest, root / 'reader')
        assert c.step(None, {'id':'blocked'}, None, None)['error'] == 'job-not-ready'
        assert 'error' in c.read(None, {'path':'../worker.py'}, None, None)
        try:
            c.artifact('safe/../../escaped')
            raise AssertionError('path traversal accepted')
        except ValueError:
            pass
        # Invalid configuration is refused before running any command.
        fail_spec['jobs'][0]['requires'] = ['blocked']
        manifest.write_text(json.dumps(fail_spec))
        assert 'cyclic' in run().stderr
        # Timeouts terminate the job's descendants, not just the immediate interpreter.
        if os.name == 'posix':
            sentinel = root / 'escaped'
            manifest.write_text(json.dumps({'version':1,'name':'timeout','jobs':[
                {'id':'timeout','argv':[sys.executable,str(worker),'timeout',str(sentinel)],'timeout':1}]}))
            c = Campaign(manifest, root / 'timeout')
            r = c.step(None, {'id':'timeout'}, None, None)
            assert r['status'] == 'failed' and 'timed out' in r['error']
            import time
            time.sleep(2)
            assert not sentinel.exists(), 'timed-out descendant escaped'
    print('agent_campaign_fixture OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
