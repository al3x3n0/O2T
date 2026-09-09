#!/usr/bin/env python3
import json,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from common import context
ctx = context()
HERE = H = ctx.out
ROOT = ctx.root
RV = ctx.rv
LLVM = LLVM16 = ctx.llvm16 / 'bin'
LLVM18 = ctx.llvm18 / 'bin'
def run(case):
 p=subprocess.run([sys.executable,str(Path(__file__).with_name('native_check.py')),'--config',str(ctx.config),'--out-dir',str(H),case['name']],capture_output=True,text=True,timeout=90)
 if p.returncode not in (0,1):raise RuntimeError(case['name']+' failed: '+p.stderr[-300:])
 return json.loads(p.stdout)
with ThreadPoolExecutor(max_workers=3) as pool:r=list(pool.map(run,json.loads((H/'cases.json').read_text())))
(H/'native-summary.json').write_text(json.dumps(r,indent=2)+'\n')
