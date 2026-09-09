#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from common import context
ctx = context()
HERE = H = ctx.out
ROOT = ctx.root
RV = ctx.rv
LLVM = LLVM16 = ctx.llvm16 / 'bin'
LLVM18 = ctx.llvm18 / 'bin'
def run(cmd, log, timeout=60, env=None):
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       timeout=timeout, env=env)
    log.write_text(p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name}: exit {p.returncode}; see {log.name}")
    return p

def wrapper(case, vector):
    if case["kind"] == "memory":
        call = "foo_SIMD(0, a);" if vector else "foo(0,a); foo(1,a); foo(2,a); foo(3,a);"
        return f'''extern "C" void foo(int,float*);
extern "C" void foo_SIMD(int,float*);
extern "C" void verify(float* a) {{ {call} }}
extern "C" void run_batch(float* a) {{ verify(a); }}
'''
    scalar = "unsigned" if case["kind"] == "integer" else "float"
    uniform = case["kind"] == "float_uniform"
    vargs = f"{scalar},Vec" if uniform else "Vec,Vec"
    vec_call = "foo_SIMD(x[0],y)" if uniform else "foo_SIMD(x,y)"
    scalar_calls = ",".join(f"foo(x[{0 if uniform else i}],y[{i}])" for i in range(4))
    expr = vec_call if vector else f"Vec{{{scalar_calls}}}"
    return f'''typedef {scalar} Vec __attribute__((ext_vector_type(4)));
extern "C" {scalar} foo({scalar},{scalar});
extern "C" Vec foo_SIMD({vargs});
extern "C" Vec verify(Vec x, Vec y) {{ return {expr}; }}
extern "C" void run_batch(const {scalar}* x, const {scalar}* y, {scalar}* out) {{
  Vec a={{x[0],x[1],x[2],x[3]}}, b={{y[0],y[1],y[2],y[3]}};
  Vec r=verify(a,b);
  out[0]=r[0];out[1]=r[1];out[2]=r[2];out[3]=r[3];
}}
'''

def one(case):
    d = HERE / case["name"]
    result = dict(case)
    try:
        env = {**os.environ, "RV_ARCH": "advsimd"}
        cmd = [ctx.out / "rv-build/tools/rvTool", "-wfv", "-w", "4", "-k", "foo",
               "-t", "foo_SIMD", "-s", case["shapes"], "-i", d / "scalar.ll", "-o", d / "vector.ll"]
        result["vectorize_command"] = [str(x) for x in cmd]
        run(cmd, d / "vectorize.log", timeout=60, env=env)
        run([LLVM16 / "opt", "-passes=verify", "-disable-output", d / "vector.ll"], d / "verify.log")
        text = (d / "vector.ll").read_text()
        assert re.search(r"define[^\n]*@foo_SIMD\(", text), "vector function absent"
        result["generated_vector_function"] = True
        for label, ir in [("before", "scalar.ll"), ("after", "vector.ll")]:
            source = d / f"wrapper-{label}.cpp"
            source.write_text(wrapper(case, label == "after"))
            run([LLVM18 / "clang++", "-std=c++17", "-O0", "-ffp-contract=off", "-dynamiclib",
                 d / ir, source, "-o", d / f"{label}.dylib"], d / f"native-{label}.log")
            run([LLVM16 / "clang++", "-std=c++17", "-O1", "-ffp-contract=off", "-fno-vectorize",
                 "-fno-slp-vectorize", "-S", "-emit-llvm", source, "-o", d / f"wrapper-{label}.ll"],
                d / f"wrapper-{label}.log")
            run([LLVM16 / "llvm-link", "-S", d / ir, d / f"wrapper-{label}.ll",
                 "-o", d / f"linked-{label}.ll"], d / f"link-{label}.log")
            linked = (d / f"linked-{label}.ll").read_text()
            linked = re.sub(r"^(define [^\n]*@foo(?:_SIMD)?\([^\n]*\)[^{]*)\{",
                            r"\1 alwaysinline {", linked, flags=re.M)
            (d / f"inline-{label}.ll").write_text(linked)
            run([LLVM16 / "opt", "-passes=always-inline,function(sroa,simplifycfg)", "-S",
                 d / f"inline-{label}.ll", "-o", d / f"formal-{label}.ll"], d / f"inline-{label}.log")
        result["status"] = "ready"
    except (Exception, subprocess.TimeoutExpired) as exc:
        result["status"] = "error"
        result["reason"] = str(exc)
    (d / "transformation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result.get(k) for k in ("name", "status", "reason")}), flush=True)
    return result

cases = json.loads((HERE / "cases.json").read_text())
with ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(one, cases))
(HERE / "transformations.json").write_text(json.dumps(results, indent=2) + "\n")
