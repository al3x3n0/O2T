#!/usr/bin/env python3
"""Reject a changed RV checkout before building; record the pinned identity."""
import json
import subprocess
from common import context

ctx = context()
revision = subprocess.check_output(['git', '-C', str(ctx.rv), 'rev-parse', 'HEAD'], text=True).strip()
changes = subprocess.check_output(['git', '-C', str(ctx.rv), 'status', '--porcelain'], text=True)
if revision != ctx.revision or changes:
    raise SystemExit('RV checkout must be clean and match the configured revision')
(ctx.out / 'target.json').write_text(json.dumps({'revision': revision, 'clean': True,
    'source': str(ctx.rv), 'scope': 'existing pinned checkout; fresh build directory'}, indent=2) + '\n')
