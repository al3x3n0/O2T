#!/usr/bin/env python3
"""Execution-based differential testing -- the proof-of-value miscompile finder.

Alive2 proves a single transform sound; this catches miscompiles without it, by
*running the program*. cv-grammar-gen `--main` emits a deterministic, UB-free
module that folds @test's results over a range of inputs into the process exit
code. For a UB-free program every correct optimizer must preserve that exit code,
so this tool runs each module through several optimizer configurations (and,
optionally, extra/older `opt` binaries) under one `lli` and flags any divergence
in exit codes -- a genuine miscompile (or a crash in one config but not another).

Two modes:
  * default: generate seeds via cv-grammar-gen, run the differential, report (and
    optionally `--minimize` each finding via cv-reduce-ir.py).
  * `--check-one FILE`: run the configs on FILE and exit 0 iff they diverge --
    used as the interestingness oracle when minimizing.

Needs `opt`/`lli` (set CV_LLVM_BIN, default /opt/homebrew/opt/llvm@18/bin).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def resolve_tool(explicit, name):
    if explicit:
        return str(explicit)
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    return str(base / name) if (base / name).exists() else name


def build_configs(args):
    # Each config: (label, opt_binary_or_None, passes_or_None).
    opt = resolve_tool(args.opt, "opt")
    configs = [("raw", None, None)]
    for p in args.passes_list.split(","):
        p = p.strip()
        if p:
            configs.append((f"opt:{p}", opt, p))
    for extra in args.extra_opt or []:
        label, _, path = extra.partition("=")
        if not path:
            label, path = Path(extra).name, extra
        configs.append((f"x:{label}", path, args.extra_passes))
    return configs


def outcome(config, module: Path, lli: str, workdir: Path, timeout: float):
    label, opt_bin, passes = config
    run_ll = module
    if opt_bin is not None:
        out = workdir / "o.ll"
        p = subprocess.run([opt_bin, "-S", f"-passes={passes}", str(module),
                            "-o", str(out)], capture_output=True, text=True)
        if p.returncode != 0 or not out.exists():
            return ("opt-error",)
        run_ll = out
    try:
        # Capture stdout as BYTES (the driver streams the full 32-bit accumulator via putchar, which
        # emits arbitrary bytes -- not valid UTF-8), and observe BOTH the exit code and a digest of
        # that wide output. The stdout digest catches value miscompiles the 8-bit exit code aliases.
        r = subprocess.run([lli, str(run_ll)], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ("timeout",)
    digest = hashlib.sha256(r.stdout).hexdigest()[:16] if r.stdout else ""
    return ("exit", r.returncode, digest)


def diverges(module: Path, configs, lli: str, workdir: Path, timeout: float):
    results = {label: outcome(c, module, lli, workdir, timeout)
              for c, label in ((c, c[0]) for c in configs)}
    distinct = set(results.values())
    return len(distinct) > 1, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-one", type=Path,
                        help="run configs on this .ll, exit 0 iff they diverge")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--instructions", type=int, default=30)
    parser.add_argument("--cfg", action="store_true")
    parser.add_argument("--generator", type=Path, default=ROOT / "tools" / "cv-grammar-gen.py")
    parser.add_argument("--opt")
    parser.add_argument("--lli")
    parser.add_argument("--passes-list", default="default<O0>,default<O1>,default<O2>,default<O3>")
    parser.add_argument("--extra-opt", action="append",
                        help="extra opt binary (label=path or path) run at --extra-passes")
    parser.add_argument("--extra-passes", default="default<O2>")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--minimize", action="store_true")
    parser.add_argument("--reducer", type=Path, default=ROOT / "tools" / "cv-reduce-ir.py")
    args = parser.parse_args()

    lli = resolve_tool(args.lli, "lli")
    configs = build_configs(args)

    if args.check_one:
        with tempfile.TemporaryDirectory(prefix="cv-diff-chk-") as tmp:
            div, _ = diverges(args.check_one, configs, lli, Path(tmp), args.timeout)
        return 0 if div else 1

    generated = 0
    findings = []
    with tempfile.TemporaryDirectory(prefix="cv-diff-") as tmp:
        tmpd = Path(tmp)
        gen_cmd_base = [sys.executable, str(args.generator), "--main",
                        "--instructions", str(args.instructions)]
        if args.cfg:
            gen_cmd_base.append("--cfg")
        for i in range(args.count):
            seed = args.seed + i
            module = tmpd / "m.ll"
            g = subprocess.run(gen_cmd_base + ["--seed", str(seed), "--out", str(module)],
                               capture_output=True, text=True)
            if g.returncode != 0 or not module.exists():
                continue
            generated += 1
            div, results = diverges(module, configs, lli, tmpd, args.timeout)
            if div:
                findings.append({"seed": seed,
                                 "results": {k: list(v) for k, v in results.items()},
                                 "module": module.read_text()})

    minimized = 0
    finding_out = []
    if args.out_dir and findings:
        (args.out_dir / "findings").mkdir(parents=True, exist_ok=True)
    for rec in findings:
        entry = {"seed": rec["seed"], "results": rec["results"]}
        if args.out_dir:
            fpath = args.out_dir / "findings" / f"diff{rec['seed']:05d}.ll"
            fpath.write_text(rec["module"])
            entry["finding_path"] = str(fpath)
            if args.minimize:
                mpath = args.out_dir / "minimized" / f"diff{rec['seed']:05d}.ll"
                mpath.parent.mkdir(parents=True, exist_ok=True)
                oracle = (f"{sys.executable} {__file__} "
                          f"--check-one {{ll}} --opt {resolve_tool(args.opt, 'opt')} "
                          f"--lli {lli} --passes-list '{args.passes_list}'")
                for extra in args.extra_opt or []:
                    oracle += f" --extra-opt '{extra}'"
                proc = subprocess.run(
                    [sys.executable, str(args.reducer), "--input", str(fpath),
                     "--out", str(mpath), "--oracle", oracle],
                    capture_output=True, text=True)
                entry["minimized_path"] = str(mpath) if proc.returncode == 0 else None
                if proc.returncode == 0:
                    minimized += 1
        finding_out.append(entry)

    summary = {"generated": generated, "configs": [c[0] for c in configs],
               "findings": len(findings), "minimized": minimized,
               "finding_records": finding_out}
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"differential: {generated} run, {len(findings)} divergence finding(s), "
          f"{minimized} minimized [configs: {', '.join(c[0] for c in configs)}]",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
