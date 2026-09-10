#!/usr/bin/env python3
"""Actual default CLI: invent a graph and checker for a non-RV repository, then execute."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from o2t.agent.campaign_planner import Planner, digest, action_envelope
from o2t.agent.campaign import Campaign

CHECKER='''import argparse, importlib.util, json, os, sys
sys.dont_write_bytecode=True
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source');p.add_argument('--out');p.add_argument('--control',action='store_true');a=p.parse_args()
assert 'DEEPSEEK_API_KEY' not in os.environ
s=importlib.util.spec_from_file_location('subject',a.source);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
count=0
for x in range(-32,33):
 expected=x+x
 actual=m.double(x)+(1 if a.control else 0)
 count+=actual!=expected
assert (count==65 if a.control else count==0)
Path(a.out).write_text(json.dumps({'status':'detected' if a.control else 'agree','checked':65,'mismatches':count}))
'''

def main():
 for invalid in ({'action':'read-source','args':{},'type':'unexpected'},
                 {'action':'read-source','args':{},'extra':True}):
  try:action_envelope(invalid)
  except ValueError:pass
  else:raise AssertionError('unknown envelope metadata accepted')
 with tempfile.TemporaryDirectory() as td:
  h=Path(td);target=h/'arithmetic';target.mkdir()
  (target/'subject.py').write_text('def double(x):\n    return 2*x\n')
  for a in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture']):
   subprocess.run(['git','-C',str(target),*a],check=True,capture_output=True)
  jobs=[]
  for name,kind in [('compare','native'),('corrupt','negative-control')]:
   jobs.append({'id':name,'argv':['{tool:python}','{manifest_dir}/files/probe.py','--source','{repo}/subject.py','--out','{out_dir}/'+name+'.json']+(['--control'] if name=='corrupt' else []),
                'requires':['compare'] if name=='corrupt' else [],'outputs':[name+'.json'],'result':name+'.json','evidence_kind':kind})
  plan={'name':'authored-arithmetic','jobs':jobs,'checks':[{'risk':'Arithmetic mismatch','jobs':['compare','corrupt'],
        'sources':[{'path':'subject.py','quote':'return 2*x'}],'oracle':'Compare independently with x+x; corrupt outputs as sensitivity control.',
        'scope':'65 integer inputs only, not a proof.'}],'limitations':['Finite domain; no claim of universal correctness.']}
  # The author may use fully qualified output placeholders; compilation normalizes them.
  jobs[0]['outputs']=['{out_dir}/compare.json'];jobs[0]['result']='{out_dir}/compare.json'
  provider=h/'provider.py'
  provider.write_text('''import json,sys
r=json.load(sys.stdin)
if r['task']=='plan-autonomous-verification-campaign':
 assert 'capabilities' not in r and 'cases' not in r
 h=r['history']
 if not h:a={'action':'submit-plan','args':PLAN}
 elif len(h)==1:
  assert 'first inspect' in h[-1]['observation']['error']
  a={'action':'read-source','args':{'path':'subject.py'}}
 elif len(h)==2:a={'action':'submit-plan','args':PLAN}
 elif len(h)==3:
  assert 'write the referenced staged file' in h[-1]['observation']['error']
  a={'action':'write-file','args':{'path':'probe.py','content':CHECKER}}
 else:a={'action':'submit-plan','args':PLAN}
elif r['task']=='review-autonomous-campaign':
 assert 'history' not in r and r['bundle']['generated_files']['probe.py']==CHECKER
 assert 'source_sha256' not in r['bundle'] and r['bundle']['input_fingerprint']['files']>=3
 assert len(r['bundle_sha256'])==64 and 'explicitly created' in r['runner_contract']['outputs']
 assert r['bundle']['plan']['jobs'][1]['requires']==['compare']
 a={'action':'review-plan','args':{'status':'supported','findings':[],'scope':'Fixture review of the finite arithmetic check.'}}
else:
 ready=[j['id'] for j in r['campaign']['jobs'] if j['status']=='ready']
 a={'action':'campaign-step','args':{'id':ready[0]}} if ready else {'action':'conclude','args':{'proposal':'inconclusive','reason':'Finite comparisons and planted control completed.'}}
print(json.dumps(dict(a,type='json_object')))
'''.replace('PLAN',repr(plan)).replace('CHECKER',repr(CHECKER)))
  out=h/'run';command=shlex.join([sys.executable,str(provider)])
  argv=[sys.executable,str(ROOT/'tools/cv-agent-design-campaign.py'),'--target',str(target),'--out-dir',str(out),
        '--goal','Verify double on a finite domain with a negative control','--llm-command',command,'--budget','8','--execution-budget','3','--execute']
  result=subprocess.run(argv,capture_output=True,text=True,env={**os.environ,'DEEPSEEK_API_KEY':'fixture-only'})
  assert result.returncode==0,(result.stdout,result.stderr)
  report=json.loads((out/'planning-report.json').read_text())
  assert report['mode']=='autonomous-planning' and report['status']=='ready' and report['model_calls']==6
  assert report['review_calls']==1 and report['jobs']==['compare','corrupt']
  assert (out/'staged/files/probe.py').read_text()==CHECKER
  executed=json.loads((out/'execution-report.json').read_text())
  assert executed['campaign']['status']=='complete'
  assert json.loads((out/'execution/compare.json').read_text())['mismatches']==0
  assert json.loads((out/'execution/corrupt.json').read_text())['mismatches']==65
  assert not list(target.glob('*.json'))
  # Real execution failure -> log feedback -> model patch -> fresh review/run.
  broken=copy.deepcopy(plan)
  broken['jobs'][0]['argv'][broken['jobs'][0]['argv'].index('--out')]='--bad-output'
  broken_provider=h/'broken-provider.py'
  broken_provider.write_text(provider.read_text().replace(repr(plan),repr(broken)))
  feedback_provider=h/'feedback-provider.py'
  feedback_provider.write_text('''import json,subprocess,sys
r=json.load(sys.stdin)
if r.get('task')=='plan-autonomous-verification-campaign' and r.get('execution_feedback'):
 f=r['execution_feedback']['failed_jobs']
 assert len(f)==1 and f[0]['id']=='compare' and f[0]['exit_code']!=0
 assert '--out' in f[0]['stderr'] and r['current_plan']['plan']['jobs'][1]['evidence_kind']=='negative-control'
 argv=r['current_plan']['plan']['jobs'][0]['argv'][:]
 argv[argv.index('--bad-output')]='--out'
 print(json.dumps({'action':'patch-plan','args':{'base_sha256':r['current_plan']['sha256'],'job_updates':[{'id':'compare','argv':argv}]}}))
else:
 if r.get('task')=='review-autonomous-campaign' and r['bundle'].get('execution_feedback'):
  assert r['bundle']['execution_feedback']['failed_jobs'][0]['id']=='compare'
 sys.exit(subprocess.run([sys.executable,PROVIDER],input=json.dumps(r),text=True).returncode)
'''.replace('PROVIDER',repr(str(broken_provider))))
  repair_argv=argv[:]
  repair_argv[repair_argv.index('--out-dir')+1]=str(h/'execution-repair')
  repair_argv[repair_argv.index('--llm-command')+1]=shlex.join([sys.executable,str(feedback_provider)])
  repaired_run=subprocess.run(repair_argv,capture_output=True,text=True,env={**os.environ,'DEEPSEEK_API_KEY':'fixture-only'})
  assert repaired_run.returncode==0,(repaired_run.stdout,repaired_run.stderr)
  attempts=json.loads((h/'execution-repair/campaign-run.json').read_text())
  assert attempts['status']=='complete' and len(attempts['attempts'])==2
  first=json.loads((h/'execution-repair/execution-report.json').read_text())
  assert first['campaign']['jobs']['compare']['status']=='failed'
  final=h/'execution-repair/repair-01'
  assert json.loads((final/'execution/compare.json').read_text())['mismatches']==0
  assert json.loads((final/'execution/corrupt.json').read_text())['mismatches']==65
  assert json.loads((final/'planning-report.json').read_text())['review_calls']==1
  resumed_repair=Planner(target,h/'resumed-execution-repair').run(command,'test',budget=1,draft=final)
  assert resumed_repair['status']=='ready' and resumed_repair['review_calls']==1
  assert json.loads((h/'resumed-execution-repair/staged/plan.json').read_text())==json.loads((final/'staged/plan.json').read_text())
  # Legacy repair histories omitted imported proposals; recover via checked ancestry.
  saved_history=(final/'history.json').read_text()
  legacy_history=[item for item in json.loads(saved_history) if item.get('origin')!='draft-proposal']
  (final/'history.json').write_text(json.dumps(legacy_history))
  legacy_repair=Planner(target,h/'legacy-repair').run(command,'test',budget=1,draft=final)
  assert legacy_repair['status']=='ready'
  assert json.loads((h/'legacy-repair/staged/plan.json').read_text())==json.loads((final/'staged/plan.json').read_text())
  (final/'history.json').write_text(saved_history)
  assert not (h/'execution-repair/execution/compare.json').exists()
  feedback=json.loads((final/'execution-feedback.json').read_text())
  evidence=Path(next(iter(feedback['artifact_sha256'])))
  original=evidence.read_bytes()
  try:
   evidence.write_bytes(original+b'\n')
   try:Planner(target,h/'tampered-feedback').run(command,'test',budget=1,draft=h/'execution-repair',feedback=feedback)
   except ValueError as exc:assert 'feedback artifact changed' in str(exc)
   else:raise AssertionError('changed failure evidence accepted')
  finally:evidence.write_bytes(original)
  exhausted_argv=repair_argv[:]
  exhausted_argv[exhausted_argv.index('--out-dir')+1]=str(h/'exhausted-repairs')
  exhausted_argv[exhausted_argv.index('--llm-command')+1]=shlex.join([sys.executable,str(broken_provider)])
  exhausted_argv+=['--repair-rounds','1']
  exhausted_run=subprocess.run(exhausted_argv,capture_output=True,text=True)
  assert exhausted_run.returncode==2
  exhausted=json.loads((h/'exhausted-repairs/campaign-run.json').read_text())
  assert exhausted['status']=='incomplete' and len(exhausted['attempts'])==2
  assert not (h/'exhausted-repairs/repair-02').exists()
  # Even successful jobs cannot mutate the published inputs unnoticed.
  tampering=CHECKER+"\np=Path(__file__).parent.parent/'plan.json'\np.write_text(p.read_text()+'\\n')\n"
  tamper_provider=h/'tamper-provider.py'
  tamper_provider.write_text(provider.read_text().replace(repr(CHECKER),repr(tampering)))
  tamper_argv=argv[:]
  tamper_argv[tamper_argv.index('--out-dir')+1]=str(h/'tampered-publication')
  tamper_argv[tamper_argv.index('--llm-command')+1]=shlex.join([sys.executable,str(tamper_provider)])
  tamper_run=subprocess.run(tamper_argv,capture_output=True,text=True)
  assert tamper_run.returncode==2 and 'campaign changed during execution' in tamper_run.stderr
  assert json.loads((h/'tampered-publication/campaign-run.json').read_text())['status']=='incomplete'
  assert not (h/'tampered-publication/repair-01').exists()
  # Structural validation rejects invented citations, unknown tools, cycles and escapes.
  p=Planner(target,h/'validate');p.read({'path':'subject.py'});p.write({'path':'probe.py','content':CHECKER})
  wrong_path=copy.deepcopy(plan);wrong_path['checks'][0]['sources'][0]['path']='wrong.py'
  try:p.validate(wrong_path,'test')
  except ValueError as exc:assert 'matches inspected source: subject.py' in str(exc)
  else:raise AssertionError('accepted wrongly attributed citation')
  resumed=Planner(target,h/'continued').run(command,'Requalify the same finite check',budget=1,draft=out)
  assert resumed['status']=='ready' and resumed['review_calls']==resumed['model_calls']==1
  assert (h/'continued/staged/files/probe.py').read_text()==CHECKER
  assert not (h/'continued/execution/compare.json').exists(),'imported executed work'
  # Keep complete verbose assessments; reject oversized text without truncating a verdict.
  for name,scope,message,expected_status in (
   ('verbose-review','Bounded scope. '*60,'Concrete finding. '*40,'ready'),
   ('oversized-scope','x'*2001,'Concrete finding.','incomplete'),
   ('oversized-finding','Bounded scope.','x'*2001,'incomplete')):
   response={'action':'review-plan','args':{'status':'supported','scope':scope,
     'findings':[{'job':'compare','message':message}]}}
   verbose_provider=h/(name+'.py')
   verbose_provider.write_text('import json,sys\nr=json.load(sys.stdin)\nassert r["task"]=="review-autonomous-campaign"\nprint(json.dumps('+repr(response)+'))\n')
   verbose=Planner(target,h/name).run(shlex.join([sys.executable,str(verbose_provider)]),'test',budget=1,draft=out)
   assert verbose['status']==expected_status
   if expected_status=='ready':
    assert json.loads((h/name/'staged/review.json').read_text())['assessment']==response['args']
  repair_provider=h/'repair-provider.py'
  repair_provider.write_text('''import json,sys
r=json.load(sys.stdin)
if r['task']=='review-autonomous-campaign':
 repaired=r['bundle']['plan']['jobs'][0].get('description')=='Finite comparison over 65 inputs'
 a={'action':'review-plan','args':{'status':'supported' if repaired else 'revise','findings':[] if repaired else [{'job':'compare','message':'Describe the finite input count.'}],'scope':'Finite fixture review.'}}
else:
 assert r['current_plan']['plan']['jobs'][1]['evidence_kind']=='negative-control'
 a={'action':'patch-plan','args':{'base_sha256':r['current_plan']['sha256'],'job_updates':[{'id':'compare','description':'Finite comparison over 65 inputs'}]}}
print(json.dumps(a))
''')
  patched=Planner(target,h/'patched').run(shlex.join([sys.executable,str(repair_provider)]),'test',budget=3,draft=out)
  assert patched['status']=='ready' and patched['review_calls']==2 and patched['model_calls']==3
  repaired_plan=json.loads((h/'patched/staged/plan.json').read_text())
  expected=copy.deepcopy(plan);expected['jobs'][0]['description']='Finite comparison over 65 inputs'
  assert repaired_plan==expected,'partial repair lost unrelated graph fields'
  # A rejected draft resumes with its findings, author repair first, fresh review last.
  rejected_draft=Planner(target,h/'rejected-draft').run(shlex.join([sys.executable,str(repair_provider)]),'test',budget=1,draft=out)
  assert rejected_draft['status']=='incomplete'
  carry_provider=h/'carry-provider.py'
  carry_provider.write_text(repair_provider.read_text().replace(
   "assert r['current_plan']['plan']['jobs'][1]['evidence_kind']=='negative-control'",
   "assert r['draft_review_feedback']['review']['assessment']['findings'][0]['message']=='Describe the finite input count.'"))
  carried=Planner(target,h/'carried').run(shlex.join([sys.executable,str(carry_provider)]),'test',budget=2,draft=h/'rejected-draft')
  assert carried['status']=='ready' and carried['model_calls']==2 and carried['review_calls']==1
  assert json.loads((h/'carried/staged/plan.json').read_text())==expected
  reimported=Planner(target,h/'reimported').run(command,'test',budget=1,draft=h/'patched')
  assert reimported['status']=='ready'
  assert json.loads((h/'reimported/staged/plan.json').read_text())==expected
  # Invalid whole-plan rewrites must not erase a previously reviewed graph.
  prior_history=json.loads((h/'patched/history.json').read_text())
  prior_history.append({'action':{'action':'submit-plan','args':{'name':'broken','jobs':[]}},'observation':{'error':'invalid'}})
  (h/'patched/history.json').write_text(json.dumps(prior_history))
  recovered=Planner(target,h/'recovered').run(command,'test',budget=1,draft=h/'patched')
  assert recovered['status']=='ready'
  assert json.loads((h/'recovered/staged/plan.json').read_text())==expected
  pending_provider=h/'pending-provider.py'
  pending_provider.write_text('''import json,sys
r=json.load(sys.stdin)
if r['task']=='review-autonomous-campaign':
 a={'action':'review-plan','args':{'status':'revise','findings':[{'job':'compare','message':'Use the revised checker filename.'}],'scope':'Fixture draft continuation.'}}
elif r['history'][-1]['action'].get('action')=='patch-plan':
 assert 'write the referenced staged files' in r['history'][-1]['observation']['error']
 a={'action':'write-file','args':{'path':'probe-v2.py','content':CHECKER}}
else:
 argv=r['current_plan']['plan']['jobs'][0]['argv'][:]
 argv[1]='{manifest_dir}/files/probe-v2.py'
 a={'action':'patch-plan','args':{'base_sha256':r['current_plan']['sha256'],'job_updates':[{'id':'compare','argv':argv}]}}
print(json.dumps(a))
'''.replace('CHECKER',repr(CHECKER)))
  pending_result=Planner(target,h/'pending').run(shlex.join([sys.executable,str(pending_provider)]),'test',budget=3,draft=out)
  assert pending_result['status']=='incomplete'
  resumed_pending=Planner(target,h/'resumed-pending').run(command,'test',budget=1,draft=h/'pending')
  assert resumed_pending['status']=='ready'
  assert json.loads((h/'resumed-pending/staged/plan.json').read_text())['jobs'][0]['argv'][1].endswith('/probe-v2.py')
  p.current_plan=copy.deepcopy(plan)
  for edit in ({'base_sha256':'stale','job_updates':[]},
               {'base_sha256':digest(plan),'remove_jobs':['unknown']},
               {'base_sha256':digest(plan),'job_updates':[{'id':'compare'},{'id':'compare'}]}):
   try:p.patch(edit)
   except ValueError:pass
   else:raise AssertionError('invalid incremental edit accepted')
  assert p.current_plan==plan
  # An unapproved draft cannot be published on rejection, malformed review, or provider failure.
  for name,response,exit_code,expected_calls in (
   ('revise',{'action':'review-plan','args':{'status':'revise','findings':[{'job':'campaign','message':'Clarify the finite input domain.'}],'scope':'Finite checks only.'}},0,1),
   ('malformed',{},0,2),
   ('failed-review',{'action':'review-plan','args':{'status':'supported','findings':[],'scope':'Finite checks only.'}},2,2),
  ):
   reviewer=h/(name+'.py')
   reviewer.write_text('import json,sys\nr=json.load(sys.stdin)\nassert r["task"]=="review-autonomous-campaign"\nprint(json.dumps('+repr(response)+'))\nsys.exit('+str(exit_code)+')\n')
   rejected=Planner(target,h/name).run(shlex.join([sys.executable,str(reviewer)]),'test',budget=expected_calls,draft=out)
   assert rejected['status']=='incomplete' and rejected['review_calls']==expected_calls
   assert not (h/name/'staged/campaign.json').exists()
  optional=copy.deepcopy(plan)
  optional['jobs'].append({'id':'optional-report','argv':['{tool:python}','--version'],'outputs':[],'result':None})
  compiled=p.validate(optional,'test')
  assert 'result' not in compiled['jobs'][-1] and compiled['jobs'][0]['result']=='compare.json'
  for mutate in (
   lambda v:v['jobs'][0].update(requires=['corrupt']),
   lambda v:v['jobs'][0].update(outputs=['../outside']),
   lambda v:v['checks'][0]['sources'][0].update(quote='invented source'),
   lambda v:v['jobs'][0]['argv'].__setitem__(0,'/invented/tool'),
   lambda v:v['jobs'][1].update(evidence_kind='execution'),
   lambda v:v['jobs'][0].update(required=False),
  ):
   v=copy.deepcopy(plan);mutate(v)
   try:p.validate(v,'test')
   except ValueError:pass
   else:raise AssertionError('invalid plan accepted')
  for path in ('../outside','/tmp/out','a/../b'):
   try:p.write({'path':path,'content':'x'})
   except ValueError:pass
   else:raise AssertionError('staging escape accepted')
  try:p.read({'path':'../outside'})
  except ValueError:pass
  else:raise AssertionError('source escape accepted')
  p.approved=digest(p.bundle(plan))
  p.write({'path':'probe.py','content':CHECKER+'\n'})
  try:p.publish(plan,'test')
  except ValueError:pass
  else:raise AssertionError('changed code reused review')
  assert not (h/'validate/staged/campaign.json').exists()
  # Failure/exhaustion leaves no runnable manifest.
  bad=h/'bad.py';bad.write_text('print("{}")\n')
  failed=Planner(target,h/'failed').run(shlex.join([sys.executable,str(bad)]),'test',budget=1)
  assert failed['status']=='incomplete' and not (h/'failed/staged/campaign.json').exists()
 print('agent_campaign_planner_fixture: OK')

if __name__=='__main__':main()
