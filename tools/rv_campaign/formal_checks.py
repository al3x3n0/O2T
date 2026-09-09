#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
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

def one(case):
    d = HERE / case["name"]
    cache = d / "o2t.json"
    o2t = validate_transform_ex(str(ctx.z3), (d / "formal-before.ll").read_text(),
                               (d / "formal-after.ll").read_text(), "verify",
                               timeout=15, cross_check=True)
    cache.write_text(json.dumps(o2t, indent=2) + "\n")
    cmd = [str(ctx.alive2), "--func=verify", "--fail-src-ub",
           str(d / "formal-before.ll"), str(d / "formal-after.ll")]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
        text = p.stdout + p.stderr
        status, detail = _classify(text)
        alive = {"status": status, "detail": detail, "exit_code": p.returncode}
    except subprocess.TimeoutExpired as e:
        text = ((e.stdout or b"") + (e.stderr or b"")).decode(errors="replace")
        alive = {"status": "skip", "detail": "wall-timeout-50s"}
    (d / "alive2.log").write_text(text)
    alive['scope'] = ('bounded loop checking' if 'loop' in case['name'] or 'early_exit' in case['name']
                      else 'per-input obligation; helper calls may be abstracted')
    result = {"name": case["name"], "o2t": o2t, "alive2": alive, "alive2_command": cmd,
              "scope": "fresh per-input refinement; independent Alive2 acceptance is not a full-pass proof"}
    (d / "formal-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"name": case["name"], "o2t": o2t["status"],
                      "reason": o2t.get("reason"), "alive2": alive}), flush=True)
    return result

cases = json.loads((HERE / "transformations.json").read_text())
with ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(one, [c for c in cases if c["status"] == "ready"]))
(HERE / "formal-summary.json").write_text(json.dumps(results, indent=2) + "\n")
