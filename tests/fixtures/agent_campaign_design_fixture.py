#!/usr/bin/env python3
"""Source-grounded design, invalid-plan repair, and selected-corpus execution."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.campaign_design import Designer
from o2t.agent.campaign import Campaign
from o2t.agent.campaign_design_review import review_plan, plan_digest, review_with_retry
from o2t.agent.llm import LLMClient


def main():
    with tempfile.TemporaryDirectory() as td:
        h = Path(td); target = h/'rv'; target.mkdir()
        (target/'test/suite').mkdir(parents=True)
        (target/'CMakeLists.txt').write_text('project(RV)\n')
        (target/'test/suite/test_001_simple-wfv.cpp').write_text('float foo(float x, float y) { return x + y; }\n')
        for argv in (['init','-q'], ['add','.'], ['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture']):
            subprocess.run(['git','-C',str(target),*argv],check=True,capture_output=True)
        rev = subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
        llvm = h/'llvm'; (llvm/'bin').mkdir(parents=True)
        compiler = llvm/'bin/clang++'
        compiler.write_text('#!'+sys.executable+'\nimport sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index("-o")+1]).write_text("; fixture compiled IR")\n')
        compiler.chmod(0o755)
        for name in ('clang', 'opt', 'llvm-link'):
            (llvm/'bin'/name).symlink_to(compiler)
        config = h/'toolchain.json'
        config.write_text(json.dumps(dict(rv=str(target), revision=rev, llvm16=str(llvm),llvm18=str(llvm),z3=sys.executable,alive2=sys.executable)))
        d = Designer(target,config,h/'direct')
        case = dict(id='guarded_division',risk='The zero guard protects unsigned division.',
                    evidence={'path':'@targeted/guarded_division','quote':'if (y == 0) return x;'},coverage=[])
        plan = dict(name='guarded division design',cases=[case],limitations=['Selected corpus only; finite native checks do not prove equivalence.'])
        try: d.validate(plan)
        except ValueError: pass
        else: raise AssertionError('accepted unread citation')
        d.read('@targeted/guarded_division')
        assert d.validate(plan)==plan
        formatted=copy.deepcopy(plan)
        formatted['cases'][0]['evidence']['quote']='if (y == 0)\n  return x;'
        assert d.validate(formatted)==formatted
        try:d.compile(plan)
        except ValueError as exc:assert 'requires a supported design review' in str(exc)
        else:raise AssertionError('compiled without review')
        supported = {'action':'review-design','args':{'cases':[{'id':'guarded_division',
            'status':'supported','reason':'The source guards the divisor.',
            'quote':'if (y == 0) return x;','coverage':'Both divisor paths have required inputs.'}],
            'goal':{'status':'supported','reason':'Matches the requested bounded scope.'}}}
        class Client:
            remaining=1
            transcript=[]
            def call(self, request):
                assert 'history' not in request and request['cases'][0]['abi']['type']=='uint32'
                self.transcript=[{'seq':1,'exit_status':0}]
                return self.reply
        for mutate in (
            lambda p:p['args']['cases'].clear(),
            lambda p:p['args']['cases'][0].update(quote='invented source quotation'),
            lambda p:p['args']['cases'][0].update(id='unselected'),
            lambda p:p['args']['goal'].update(status='proved'),
        ):
            c=Client();c.reply=copy.deepcopy(supported);mutate(c.reply)
            assert review_plan(c,plan,d.cases,d.reads,'test')['status']=='incomplete'
        c=Client();c.reply=copy.deepcopy(supported);c.reply['args']['goal']['status']='uncertain'
        assert review_plan(c,plan,d.cases,d.reads,'test')['status']=='revision-required'
        c.remaining=0
        assert review_plan(c,plan,d.cases,d.reads,'test')['status']=='incomplete'
        class RetryClient:
            def __init__(self, replies, budget=2):
                self.replies=replies;self.remaining=budget;self.transcript=[];self.requests=[]
            def call(self, request):
                self.remaining-=1;self.requests.append(request)
                self.transcript.append({'seq':len(self.requests),'exit_status':0,'stderr':'untrusted raw noise'})
                return self.replies[len(self.requests)-1]
        retry=RetryClient([None,supported])
        attempts=review_with_retry(retry,plan,d.cases,d.reads,'test')
        assert [r['status'] for r in attempts]==['incomplete','supported']
        assert attempts[0]['plan_sha256']==attempts[1]['plan_sha256']
        assert retry.requests[0]['cases']==retry.requests[1]['cases']
        assert 'format_feedback' in retry.requests[1] and 'untrusted raw noise' not in json.dumps(retry.requests[1])
        assert 'history' not in retry.requests[1]
        rejection=copy.deepcopy(supported);rejection['args']['cases'][0]['status']='revise'
        retry=RetryClient([rejection])
        assert len(review_with_retry(retry,plan,d.cases,d.reads,'test'))==1
        retry=RetryClient([None],budget=1)
        assert len(review_with_retry(retry,plan,d.cases,d.reads,'test'))==1
        retry=RetryClient([None,None],budget=3)
        assert len(review_with_retry(retry,plan,d.cases,d.reads,'test'))==2 and retry.remaining==1
        d.approved_review={'status':'supported','plan_sha256':plan_digest(plan)}
        changed=copy.deepcopy(plan);changed['cases'][0]['risk']='A different claim'
        try:d.compile(changed)
        except ValueError as exc:assert 'requires a supported design review' in str(exc)
        else:raise AssertionError('reused approval for changed plan')
        for mutate in (
            lambda p:p.update(argv=['sh','-c','bad']),
            lambda p:p['cases'][0]['evidence'].update(quote='invented source text'),
            lambda p:p['cases'][0].update(id='unknown'),
            lambda p:p['cases'].append(copy.deepcopy(p['cases'][0])),
            lambda p:p.update(limitations=[]),
            lambda p:p['cases'][0].update(coverage=[{'id':'input-1-zero','array':1,'predicate':'nonzero','lanes':'any','minimum':1}]),
            lambda p:p['cases'][0].update(coverage=[{'id':'bad-negative','array':1,'predicate':'negative_zero','lanes':'each','minimum':1}]),
        ):
            p=copy.deepcopy(plan);mutate(p)
            try:d.validate(p)
            except ValueError:pass
            else:raise AssertionError('invalid design accepted: '+str(p))
        for path in ('../outside','/etc/passwd','.git/config'):
            try:d.read(path)
            except ValueError:pass
            else:raise AssertionError('read outside source inventory')
        plan['cases'][0]['coverage'] = [{'id':'numerator-nonzero','array':0,
            'predicate':'nonzero','lanes':'each','minimum':2}]
        provider = h/'provider.py'
        provider.write_text('''import json,sys
r=json.load(sys.stdin)
if r['task']=='review-verification-campaign-design':
 assert 'history' not in r
 rows=[{'id':c['proposal']['id'],'status':'supported','reason':'Matches the source guard.',
        'quote':c['proposal']['evidence']['quote'],'coverage':'Zero and nonzero divisors are mandatory.'} for c in r['cases']]
 print(json.dumps({'action':'review-design','args':{'cases':rows,'goal':{'status':'supported','reason':'Scoped goal is covered.'}}}))
 sys.exit(0)
h=r['history']
if not h:
 a={'action':'submit-design','args':PLAN}
elif len(h)==1:
 assert 'error' in h[-1]['observation']
 a={'action':'read-source','args':{'path':'@targeted/guarded_division'}}
else:
 a={'action':'submit-design','args':PLAN}
print(json.dumps(a))
'''.replace('PLAN',repr(plan)))
        out=h/'designed'
        cmd=[sys.executable,str(ROOT/'tools/cv-agent-design-campaign.py'),'--target',str(target),
             '--toolchain-config',str(config),'--out-dir',str(out),'--goal','Verify zero-divisor behavior',
             '--llm-command',shlex.join([sys.executable,str(provider)]),'--budget','4']
        run=subprocess.run(cmd,capture_output=True,text=True)
        assert run.returncode==0,(run.stdout,run.stderr)
        report=json.loads((out/'design-report.json').read_text())
        assert report['status']=='accepted' and report['model_calls']==4 and report['review_calls']==1
        assert report['selected_cases']==['guarded_division'] and len(report['omitted_cases'])==11
        campaign=Campaign(Path(report['manifest']),out/'execution')
        assert campaign.fingerprint==report['fingerprint']
        assert campaign.gap_checks.cases.keys()=={'guarded_division'}
        assert len(campaign.gap_checks.cases['guarded_division']['controls'])==1
        requirements=campaign.gap_checks.cases['guarded_division']['coverage']
        assert [r['id'] for r in requirements]==['input-1-zero','input-1-nonzero','numerator-nonzero']
        assert campaign.jobs['prepare-inputs']['outputs']==['cases.json','guarded_division/scalar.ll']
        assert not (out/'execution/cases.json').exists(),'design executed target jobs'
        assert all(j.get('required',True) for j in campaign.jobs.values())
        assert {j['evidence_kind'] for j in campaign.jobs.values()} >= {'formal','native','negative-control'}
        # Exercise the actual input preparer; its compiler is a fixture executable.
        subprocess.run([sys.executable,str(ROOT/'tools/rv_campaign/prepare_cases.py'),'--config',str(out/'compiled/config.json'),'--out-dir',str(out/'execution')],check=True,capture_output=True)
        cases=json.loads((out/'execution/cases.json').read_text())
        assert [c['name'] for c in cases]==['guarded_division']
        assert (out/'execution/guarded_division/scalar.ll').exists()
        assert not (out/'execution/upstream_simple').exists()
        # A saved incorrect plan is reviewed first, then repaired by the designer.
        initial=copy.deepcopy(plan);initial['cases'][0]['risk']='The source uses variable shifts.'
        initial_path=h/'initial.json';initial_path.write_text(json.dumps(initial))
        repair=h/'repair.py'
        repair.write_text('''import json,sys
r=json.load(sys.stdin)
if r['task']=='review-verification-campaign-design':
 c=r['cases'][0];bad='variable shifts' in c['proposal']['risk']
 a={'action':'review-design','args':{'cases':[{'id':c['proposal']['id'],
 'status':'revise' if bad else 'supported','reason':'There are no shifts; describe the zero-divisor guard.',
 'quote':'if (y == 0) return x;','coverage':'Mandatory requirements include both divisor paths.'}],
 'goal':{'status':'supported','reason':'The selected case fits the goal.'}}}
else:
 assert any('review' in x.get('observation',{}) for x in r['history'])
 a={'action':'submit-design','args':PLAN}
print(json.dumps(a))
'''.replace('PLAN',repr(plan)))
        repaired=h/'repaired'
        result=Designer(target,config,repaired).run(shlex.join([sys.executable,str(repair)]),'division',
                                                   budget=3,initial_plan=initial_path)
        assert result['status']=='accepted' and result['model_calls']==3 and result['review_calls']==2
        assert [r['status'] for r in result['reviews']]==['revision-required','supported']
        assert json.loads((repaired/'compiled/design.json').read_text())==plan
        # A malformed reviewer response is retried without a designer call.
        malformed=h/'malformed.py';marker=h/'review-count'
        malformed.write_text('''import json,sys
from pathlib import Path
r=json.load(sys.stdin)
assert r['task']=='review-verification-campaign-design'
p=Path(MARKER)
if not p.exists():
 p.write_text('1'); print('{"action":"review-design"'); sys.exit(0)
assert 'format_feedback' in r and 'history' not in r
print(json.dumps(REPLY))
'''.replace('MARKER',repr(str(marker))).replace('REPLY',repr(supported)))
        retry_out=h/'retry-out'
        result=Designer(target,config,retry_out).run(shlex.join([sys.executable,str(malformed)]),'division',
                                                   budget=2,initial_plan=initial_path)
        assert result['status']=='accepted' and result['model_calls']==result['review_calls']==2
        assert [r['status'] for r in result['reviews']]==['incomplete','supported']
        assert json.loads((retry_out/'compiled/design.json').read_text())==initial
        # One call can reject a seed, but cannot fabricate a repair or compile it.
        bounded=h/'bounded'
        result=Designer(target,config,bounded).run(shlex.join([sys.executable,str(repair)]),'division',
                                                  budget=1,initial_plan=initial_path)
        assert result['status']=='incomplete' and not (bounded/'compiled').exists()
        # Changed inputs cannot be silently compiled after planning.
        config.write_text(config.read_text()+'\n')
        try:d.validate(plan)
        except ValueError as exc:assert 'changed during design' in str(exc)
        else:raise AssertionError('accepted changed planning inputs')
        # Budget exhaustion never emits a runnable campaign.
        empty=h/'exhausted'; bad=h/'bad.py';bad.write_text('print("{}")\n')
        failed=Designer(target,config,empty).run(shlex.join([sys.executable,str(bad)]),'test',budget=2)
        assert failed['status']=='incomplete' and failed['model_calls']==2
        assert not (empty/'compiled/campaign.json').exists()
    print('agent_campaign_design_fixture: OK')


if __name__=='__main__':main()
