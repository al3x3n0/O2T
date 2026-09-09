#!/usr/bin/env python3
"""Native binding, control sensitivity and bounded repair via the campaign CLI."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.agent.gap_checks import validate_candidate
from o2t.agent.check_runner import equal_values
from o2t.agent.staging import StagingArea

CHECKER = '''import argparse,ctypes,json
p=argparse.ArgumentParser();p.add_argument('--before');p.add_argument('--after');a=p.parse_args()
A=ctypes.c_uint32*4;P=ctypes.POINTER(ctypes.c_uint32)
functions=[]
for path in (a.before,a.after):
 f=ctypes.CDLL(path).run_batch;f.argtypes=[P,P,P];f.restype=None;functions.append(f)
checked=mismatches=0;witness=None
for i in range(256):
 inputs=[[i,i+1,i+2,i+3],[3,5,7,9]];outputs=[]
 for f in functions:
  out=A();f(A(*inputs[0]),A(*inputs[1]),out);outputs.append(list(out))
 checked+=1
 if outputs[0]!=outputs[1]:
  mismatches+=1
  if witness is None:witness={'inputs':inputs,'before':outputs[0],'after':outputs[1]}
print(json.dumps({'status':'disagree' if mismatches else 'agree','checked':checked,'mismatches':mismatches,'witness':witness}))
raise SystemExit(1 if mismatches else 0)
'''
FIXTURE = '''import json,os,subprocess,sys
from pathlib import Path
checker=Path(__file__).with_name('cv-agent-native-check.py')
for target,expected in [('O2T_CHECK_AFTER',0),('O2T_CHECK_NEGATIVE',1)]:
 p=subprocess.run([sys.executable,str(checker),'--before',os.environ['O2T_CHECK_BEFORE'],'--after',os.environ[target]],capture_output=True,text=True)
 assert p.returncode==expected,(p.returncode,p.stderr)
 d=json.loads(p.stdout);assert d['checked']==256
 assert (d['mismatches']>0)==bool(expected)
'''
ABI = {'kind':'lanes','type':'uint32','width':4,'symbol':'run_batch'}


def main():
    assert not equal_values([0.0], [-0.0], {'type': 'float32', 'max_ulp': 1})
    assert equal_values([float('nan')], [float('nan')], {'type': 'float32', 'max_ulp': 0})
    compiler = shutil.which('cc')
    if not compiler:
        print('agent_gap_checks_fixture: cc unavailable, skipped');return 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td);paths={}
        for name,mutation in [('before',''),('after',''),('negative','o[0]^=1;')]:
            source=root/(name+'.c')
            source.write_text('#include <stdint.h>\nvoid run_batch(const uint32_t*x,const uint32_t*y,uint32_t*o){for(int i=0;i<4;i++)o[i]=x[i]+y[i];'+mutation+'}\n')
            paths[name]=root/(name+'.dylib')
            subprocess.run([compiler,'-shared','-fPIC',str(source),'-o',str(paths[name])],check=True,capture_output=True)
        area=StagingArea(root/'stage')
        def validate(tool=CHECKER,fixture=FIXTURE):
            record=area.stage_tool('cv-agent-native-check','fixture control',tool,fixture)
            return validate_candidate(record,paths,ABI,minimum=128,timeout=10)
        good=validate();assert good['status']=='accepted',good
        # A second, targeted defect must be detected as well as the generic one.
        zero_source = root / 'zero.c'
        zero_source.write_text('#include <stdint.h>\nvoid run_batch(const uint32_t*x,const uint32_t*y,uint32_t*o){for(int i=0;i<4;i++){o[i]=x[i]+y[i];if(y[i]==0)o[i]^=2;}}\n')
        zero_library = root / 'zero.dylib'
        subprocess.run([compiler, '-shared', '-fPIC', str(zero_source), '-o', str(zero_library)], check=True, capture_output=True)
        bank = {**paths, 'control-zero': zero_library}
        coverage = [{'id': 'zero-divisor', 'array': 1, 'predicate': 'zero', 'lanes': 'each', 'minimum': 1},
                    {'id': 'nonzero-divisor', 'array': 1, 'predicate': 'nonzero', 'lanes': 'each', 'minimum': 1}]
        record = area.stage_tool('cv-agent-native-check', 'fixture', CHECKER, FIXTURE)
        missing = validate_candidate(record, bank, ABI, 128, 10, coverage)
        assert 'zero-divisor' in missing['missing_coverage']
        assert missing['missing_coverage']['zero-divisor']['counts'] == [0,0,0,0]
        no_bank_detection = validate_candidate(record, bank, ABI, 128, 10)
        assert no_bank_detection['status'] == 'rejected' and 'control-zero' in no_bank_detection['reason']
        covered = CHECKER.replace('[3,5,7,9]]', '([0,0,0,0] if i==0 else [3,5,7,9])]')
        record = area.stage_tool('cv-agent-native-check', 'fixture', covered, FIXTURE)
        accepted = validate_candidate(record, bank, ABI, 128, 10, coverage)
        assert accepted['status'] == 'accepted', accepted
        assert set(accepted['fixtures']) == {'negative', 'control-zero'}
        assert accepted['runs']['control-zero']['witness_replayed']
        assert accepted['runs']['baseline']['trace']['coverage']['zero-divisor']['counts'] == [1,1,1,1]
        repeated = covered.replace('inputs=[[i,i+1,i+2,i+3]', 'inputs=[([0,1,2,3] if i<16 else [i,i+1,i+2,i+3])').replace('if i==0 else', 'if i<16 else')
        record = area.stage_tool('cv-agent-native-check', 'fixture', repeated, FIXTURE)
        repeated_result = validate_candidate(record, bank, ABI, 128, 10, [{**coverage[0], 'minimum': 2}])
        assert repeated_result['missing_coverage']['zero-divisor']['counts'] == [1,1,1,1], repeated_result
        # Float numeric equality misses sign-only defects; the independent oracle rejects it.
        float_paths = {}
        for name, mutation in [('before', ''), ('after', ''), ('negative', 'o[0]=-o[0];')]:
            source = root / ('float-' + name + '.c')
            source.write_text('void run_batch(const float*x,const float*y,float*o){for(int i=0;i<4;i++)o[i]=x[i];' + mutation + '}\n')
            float_paths[name] = source.with_suffix('.dylib')
            subprocess.run([compiler, '-shared', '-fPIC', str(source), '-o', str(float_paths[name])], check=True, capture_output=True)
        float_abi = {'kind':'lanes','type':'float32','width':4,'symbol':'run_batch','min':-512,'max':512,'max_ulp':0}
        float_checker = CHECKER.replace('ctypes.c_uint32', 'ctypes.c_float').replace('[i,i+1,i+2,i+3]', '[0,i,i+1,i+2]')
        record = area.stage_tool('cv-agent-native-check', 'fixture', float_checker, FIXTURE)
        floating = validate_candidate(record, float_paths, float_abi, 128, 10)
        assert floating['status'] == 'rejected' and floating['runs']['negative']['trace']['mismatches'] == 256
        exact_float = 'import struct\n' + float_checker.replace('outputs[0]!=outputs[1]', "struct.pack('4f',*outputs[0])!=struct.pack('4f',*outputs[1])")
        record = area.stage_tool('cv-agent-native-check', 'fixture', exact_float, FIXTURE)
        assert validate_candidate(record, float_paths, float_abi, 128, 10)['status'] == 'accepted'
        assert good['runs']['baseline']['trace']['unique_inputs']==256
        assert good['runs']['negative']['witness_replayed']
        assert good['broken_checker_fixture']['exit_code']!=0
        fake="import json;print(json.dumps({'status':'agree','checked':256,'mismatches':0,'witness':None}))"
        assert validate(fake)['status']=='rejected','invented results accepted'
        loader_only="import argparse,ctypes,json\np=argparse.ArgumentParser();p.add_argument('--before');p.add_argument('--after');a=p.parse_args();ctypes.CDLL(a.before);ctypes.CDLL(a.after)\n"+fake
        assert validate(loader_only)['status']=='rejected','loading without calling accepted'
        bad_fixture=validate(fixture='raise SystemExit(0)\n')
        assert 'broken sibling' in bad_fixture['reason'],bad_fixture
        # An embedded duplicate passes its own controls but ignores the poisoned sibling.
        duplicate="import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as td:\n p=Path(td)/'embedded_checker.py';p.write_text("+repr(CHECKER)+")\n"+ '\n'.join(' '+line for line in FIXTURE.replace("checker=Path(__file__).with_name('cv-agent-native-check.py')","checker=p").splitlines())
        duplicate_result = validate(fixture=duplicate)
        assert 'broken sibling' in duplicate_result['reason'], duplicate_result
        liar=CHECKER.replace("print(json.dumps(","if witness:witness['before'][0]+=10\nprint(json.dumps(")
        assert 'witness outputs' in validate(liar)['reason']
        # End-to-end scripted model chooses a gap, fails, then repairs without external editing.
        setup=root/'setup.py'
        setup.write_text('import json,shutil,sys\nfrom pathlib import Path\nout=Path(sys.argv[1])\n'+
            '\n'.join(f'shutil.copy2({str(p)!r},out/{(k+".dylib")!r})' for k,p in {**paths, 'zero':zero_library}.items())+
            "\n(out/'formal.json').write_text(json.dumps([{'name':'already-proved','o2t':{'status':'proved'}},{'name':'subject','o2t':{'status':'unsupported','reason':'fixture formal gap'}}]))\n")
        manifest=root/'campaign.json';out=root/'out'
        manifest.write_text(json.dumps({'version':1,'name':'gap-repair-fixture','inputs':[str(setup)],
            'jobs':[{'id':'prepare','argv':['{python}',str(setup),'{out_dir}'],
                     'outputs':['formal.json','before.dylib','after.dylib','negative.dylib','zero.dylib'],'result':'formal.json','evidence_kind':'formal'}],
            'gap_checks':{'report_job':'prepare','max_attempts':2,'min_unique':128,'cases':[
                {'id':'subject','before':'before.dylib','after':'after.dylib','negative':'negative.dylib','abi':ABI,
                 'controls':[{'id':'zero','artifact':'zero.dylib'}],'coverage':coverage}]}}))
        provider=root/'provider.py'
        provider.write_text('import json,sys\nr=json.load(sys.stdin);g=r["campaign"]["gap_checks"]\n'+
            "if g['status']=='waiting':a={'action':'campaign-step','args':{'id':'prepare'}}\n"+
            "elif not g['selected']:a={'action':'select-gap','args':{'id':'subject'}}\n"+
            "elif g['status']=='accepted':a={'action':'conclude','args':{'proposal':'inconclusive'}}\n"+
            "else:a={'action':'synthesize-gap-check','args':{'name':'cv-agent-native-check','purpose':'native fixture','tool_source':("+repr(CHECKER)+" if not g['attempts'] else "+repr(covered)+"),'fixture_source':"+repr(FIXTURE)+"}}\nprint(json.dumps(a))\n")
        cmd=[sys.executable,str(ROOT/'tools/cv-agent.py'),'--campaign',str(manifest),'--out-dir',str(out),
             '--llm-command',shlex.join([sys.executable,str(provider)]),'--enable-synthesis','--budget','8',
             '--report',str(root/'report.json')]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
        assert p.returncode==0,(p.stdout,p.stderr)
        r=json.loads((root/'report.json').read_text());g=r['campaign']['gap_checks']
        assert g['status']=='accepted' and len(g['attempts'])==2,g
        assert [a['validation']['status'] for a in g['attempts']]==['rejected','accepted']
        assert 'zero-divisor' in g['attempts'][0]['validation']['missing_coverage']
        assert r['agent']['formal_checks']==[]
        summary = r['evidence_summary']
        assert [x['case'] for x in summary['unresolved']] == ['subject']
        assert [x['status'] for x in summary['supplemental']] == ['rejected', 'accepted']
        assert summary['supplemental'][1]['native_observation']['unique_inputs'] == 256
        assert r['campaign']['jobs']['prepare']['result'][1]['o2t']['status']=='unsupported'
        assert [x['id'] for x in g['gaps']]==['subject']
        # Resume preserves validation, does not regenerate a checker, and checks its bytes.
        p=subprocess.run(cmd+['--resume',str(root/'report.json')],capture_output=True,text=True,timeout=20)
        assert p.returncode==0 and json.loads((root/'report.json').read_text())['llm_calls_used']==0
        accepted=g['attempts'][1]['candidate']
        Path(accepted['path']).write_text('raise SystemExit(0)\n')
        p=subprocess.run(cmd+['--resume',str(root/'report.json')],capture_output=True,text=True,timeout=20)
        assert p.returncode==2 and 'artifact changed' in p.stderr
    print('agent_gap_checks_fixture OK');return 0


if __name__=='__main__':raise SystemExit(main())
