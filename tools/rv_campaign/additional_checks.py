#!/usr/bin/env python3
import json,re,subprocess,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import context
ctx = context()
HERE = H = ctx.out
ROOT = ctx.root
RV = ctx.rv
LLVM = LLVM16 = ctx.llvm16 / 'bin'
LLVM18 = ctx.llvm18 / 'bin'
from o2t.validate.corpus_tv import validate_transform_ex
from o2t.validate.alive_diff import _classify
jobs=[]
selected = {c['name'] for c in json.loads((H/'cases.json').read_text())}
for name in ['upstream_interleaved','upstream_masked_store','upstream_strided_store']:
 if name not in selected: continue
 d=H/name
 paths=[]
 for side in ['before','after']:
  s=(d/f'formal-{side}.ll').read_text()
  s=re.sub(r', !tbaa !\d+', '',s)
  p=d/f'no-tbaa-{side}.ll';p.write_text(s);paths.append(p)
 jobs.append((name+'-no-tbaa',*paths))
for name,case in [('lane-swap','integer_branches'),('unguarded-divisor','guarded_division')]:
 if case not in selected: continue
 d=H/'negative-controls'/name;d.mkdir(parents=True,exist_ok=True)
 src=H/case/'formal-before.ll';s=(H/case/'formal-after.ll').read_text()
 if name=='lane-swap':
  old='  ret <4 x i32> %.R21.b_SIMD.i'
  new='  %planted = shufflevector <4 x i32> %.R21.b_SIMD.i, <4 x i32> poison, <4 x i32> <i32 1, i32 0, i32 2, i32 3>\n  ret <4 x i32> %planted'
  assert s.count(old)==1;s=s.replace(old,new)
 else:
  old='%4 = udiv <4 x i32> %0, %divSelect.i';assert s.count(old)==1
  s=s.replace(old,'%4 = udiv <4 x i32> %0, %1')
 dst=d/'mutated.ll';dst.write_text(s)
 subprocess.run([str(LLVM16 / 'opt'),'-passes=verify','-disable-output',str(dst)],check=True)
 result=validate_transform_ex(str(ctx.z3),src.read_text(),s,'verify',timeout=15,cross_check=True)
 (d/'o2t.json').write_text(json.dumps(result,indent=2)+'\n')
 jobs.append((name,src,dst))
def run(job):
 name,src,dst=job
 cmd=[str(ctx.alive2),'--func=verify','--fail-src-ub',str(src),str(dst)]
 try:
  p=subprocess.run(cmd,capture_output=True,text=True,timeout=45);out=p.stdout+p.stderr
  status,detail=_classify(out)
 except subprocess.TimeoutExpired:
  out='Wall timeout after 45 seconds';status,detail='skip','wall-timeout'
 (dst.parent/(name+'-alive2.log')).write_text(out)
 r=dict(name=name,status=status,detail=detail,command=cmd,
        scope='metadata compatibility retry' if name.endswith('-no-tbaa') else 'planted defect, not an RV bug')
 if (dst.parent/'o2t.json').exists() and not name.endswith('-no-tbaa'):
  r['o2t']=json.loads((dst.parent/'o2t.json').read_text())
 print(json.dumps(r),flush=True);return r
with ThreadPoolExecutor(max_workers=3) as pool: results=list(pool.map(run,jobs))
(H/'additional-results.json').write_text(json.dumps(results,indent=2)+'\n')
