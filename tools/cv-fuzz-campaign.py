#!/usr/bin/env python3
"""Coverage-guided mutation fuzzer over GeneratorConfig.

The seed/KLEE front-ends explore the config space blindly -- they never look at
what `opt` actually did. This fuzzer closes that loop AFL-style: it mutates a
corpus of configs, runs each generated module through `opt`, and keeps a config
only when it produces *new coverage*. Coverage is a bucketized fingerprint of the
optimizer's behavior:

  * the opcode histogram of the optimized IR (build-independent -- works even on a
    Release LLVM where `-stats` is empty),
  * the BEFORE->AFTER opcode DELTA -- how many of each opcode the optimizer added or
    removed, signed and bucketized. This fingerprints what the pass *did* (the
    transformation), not just what the output looks like, so two configs that produce
    similarly-shaped output via different transformations no longer collide -- the
    single biggest lever against a plateauing corpus on a Release LLVM, and
  * `opt -stats` transform counters when the LLVM build emits them.

A config whose optimized output lands in a (signal, bucket) pair never seen
before is "interesting" and joins the corpus for further mutation/crossover, so
the search grows toward configs that exercise more and different optimizations.

A config is a *finding* when `opt` crashes, produces no output, or -- most
valuable -- emits IR that fails to verify (`llvm-as` rejects the optimized
module: a genuine miscompile/verifier bug). With `--alive2` a refinement failure
is also a finding. `--minimize` shrinks each finding with
cv-reduce-failing-config.py.

Needs `opt` and `llvm-as` (set CV_LLVM_BIN, default /opt/homebrew/opt/llvm@18/bin,
then PATH). Runs fully locally -- no KLEE required.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Valid value sets per field, mirroring cv::normalizeConfig bounds.
FIELD_CHOICES: dict[str, list[int]] = {
    "arith_opcode": list(range(6)),
    "rhs_mode": list(range(4)),
    "extra_opcode": list(range(6)),
    "predicate": list(range(4)),
    "shape": list(range(5)),
    "feature_bits": list(range(8)),
    "memory_shape": list(range(6)),
    "pointer_mode": list(range(3)),
    "store_mode": list(range(3)),
    "load_use_mode": list(range(3)),
    "loop_shape": list(range(5)),
    "loop_trip_mode": list(range(3)),
    "induction_mode": list(range(3)),
    "loop_use_mode": list(range(3)),
    "vector_shape": list(range(25)),
    "global_shape": list(range(4)),
    "compose_bits": list(range(64)),
    "int_width": list(range(4)),
    "scalar_args": list(range(3)),
    "pointer_args": list(range(3)),
    "pointer_noalias": list(range(2)),
    "cast_mode": list(range(8)),
    "const_a": list(range(-8, 9)),
    "const_b": list(range(-8, 9)),
}
FIELD_ORDER = list(FIELD_CHOICES.keys())
DEFAULTS: dict[str, int] = {k: 0 for k in FIELD_ORDER}
DEFAULTS.update({"feature_bits": 1, "int_width": 2, "const_b": 1})

# Opcodes whose density fingerprints the optimized IR.
MNEMONICS = ("add sub mul udiv sdiv and or xor shl lshr ashr icmp select phi br "
             "switch load store alloca getelementptr call ret trunc zext sext "
             "bitcast insertelement extractelement shufflevector "
             "llvm.vector.reduce").split()
STAT_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+-\s+(.+?)\s*$")


def bucket(n: int) -> int:
    if n <= 0:
        return 0
    if n <= 2:
        return n
    if n <= 4:
        return 3
    if n <= 8:
        return 4
    if n <= 16:
        return 5
    if n <= 32:
        return 6
    return 7


def signed_bucket(n: int) -> int:
    """A signed magnitude bucket: the SIGN distinguishes opcodes the optimizer removed (negative)
    from ones it introduced (positive); the magnitude is bucketized like `bucket`."""
    if n == 0:
        return 0
    return bucket(abs(n)) if n > 0 else -bucket(-n)


def opcode_histogram(text: str) -> dict[str, int]:
    return {m: len(re.findall(r"(?<![\w.])" + re.escape(m) + r"(?![\w])", text))
            for m in MNEMONICS}


def opcode_coverage(text: str) -> set[tuple[str, int]]:
    return {("op:" + m, bucket(count)) for m, count in opcode_histogram(text).items()}


def delta_coverage(before: dict[str, int], after: dict[str, int]) -> set[tuple[str, int]]:
    """Fingerprint the TRANSFORMATION: the signed, bucketized change in each opcode's count, plus
    the overall instruction-count change. This rewards configs that make the optimizer do a new
    KIND or MAGNITUDE of work, which the absolute output histogram alone cannot see."""
    cov = {("delta:" + m, signed_bucket(after.get(m, 0) - before.get(m, 0))) for m in MNEMONICS}
    cov.add(("delta:total", signed_bucket(sum(after.values()) - sum(before.values()))))
    return cov


def stats_coverage(stderr: str) -> set[tuple[str, int]]:
    cov = set()
    for line in stderr.splitlines():
        mo = STAT_RE.match(line)
        if mo:
            cov.add(("stat:" + mo.group(2) + ":" + mo.group(3),
                     bucket(int(mo.group(1)))))
    return cov


def write_config(cfg: dict[str, int], path: Path) -> None:
    path.write_text("".join(f"{k}={cfg[k]}\n" for k in FIELD_ORDER))


def random_config(rng: random.Random) -> dict[str, int]:
    return {k: rng.choice(v) for k, v in FIELD_CHOICES.items()}


def mutate(cfg: dict[str, int], rng: random.Random) -> dict[str, int]:
    child = dict(cfg)
    for _ in range(rng.randint(1, 3)):
        field = rng.choice(FIELD_ORDER)
        child[field] = rng.choice(FIELD_CHOICES[field])
    return child


def crossover(a: dict[str, int], b: dict[str, int],
              rng: random.Random) -> dict[str, int]:
    return {k: (a[k] if rng.random() < 0.5 else b[k]) for k in FIELD_ORDER}


def first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


class Runner:
    def __init__(self, replay: Path, opt: Path, llvm_as: Path, passes: str,
                 workdir: Path, timeout: float | None) -> None:
        self.replay = replay
        self.opt = opt
        self.llvm_as = llvm_as
        self.passes = passes
        self.workdir = workdir
        self.timeout = timeout

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout)

    def generate(self, cfg: dict[str, int]) -> Path | None:
        cfg_path = self.workdir / "cand.cfg"
        ll = self.workdir / "cand.ll"
        write_config(cfg, cfg_path)
        proc = self._run([str(self.replay), "--config", str(cfg_path),
                          "--out", str(ll)])
        if proc.returncode != 0 or not ll.exists() or ll.stat().st_size == 0:
            return None
        # Discard configs whose *input* IR is already invalid: that is a
        # generator gap, not an opt finding.
        if self._run([str(self.llvm_as), str(ll), "-o", os.devnull]).returncode != 0:
            return None
        return ll

    def evaluate(self, ll: Path) -> tuple[set[tuple[str, int]], bool, str]:
        out = self.workdir / "opt.ll"
        try:
            proc = self._run([str(self.opt), "-S", "-passes=" + self.passes,
                              "-stats", str(ll), "-o", str(out)])
        except subprocess.TimeoutExpired:
            return set(), True, "opt timed out"
        if proc.returncode != 0:
            return set(), True, "opt failed: " + first_line(proc.stderr)
        if not out.exists() or out.stat().st_size == 0:
            return set(), True, "opt produced no output"
        verify = self._run([str(self.llvm_as), str(out), "-o", os.devnull])
        if verify.returncode != 0:
            return set(), True, "optimized IR failed to verify: " + first_line(verify.stderr)
        before, after = opcode_histogram(ll.read_text()), opcode_histogram(out.read_text())
        cov = (opcode_coverage(out.read_text()) | delta_coverage(before, after)
               | stats_coverage(proc.stderr))
        return cov, False, ""


def resolve_tool(explicit: Path | None, name: str) -> Path:
    if explicit:
        return explicit
    base = Path(os.environ.get("CV_LLVM_BIN", "/opt/homebrew/opt/llvm@18/bin"))
    cand = base / name
    if cand.exists():
        return cand
    return Path(name)  # fall back to PATH


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--replay", type=Path, default=ROOT / "build" / "cv-replay")
    parser.add_argument("--opt", type=Path)
    parser.add_argument("--llvm-as", type=Path)
    parser.add_argument("--passes", default="default<O2>")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--corpus-seeds", type=int, default=8)
    parser.add_argument("--crossover-rate", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--minimize", action="store_true",
                        help="shrink each finding with cv-reduce-failing-config.py")
    parser.add_argument("--reducer", type=Path,
                        default=ROOT / "tools" / "cv-reduce-failing-config.py")
    args = parser.parse_args()

    replay = args.replay
    opt = resolve_tool(args.opt, "opt")
    llvm_as = resolve_tool(args.llvm_as, "llvm-as")
    if not replay.exists():
        print(f"error: cv-replay not found at {replay}", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = args.out_dir / "corpus"
    findings_dir = args.out_dir / "findings"
    corpus_dir.mkdir(exist_ok=True)
    findings_dir.mkdir(exist_ok=True)

    rng = random.Random(args.seed)
    seen: set[tuple[str, int]] = set()
    corpus: list[dict[str, int]] = []
    findings: list[dict] = []
    evaluated = 0
    gen_invalid = 0

    with tempfile.TemporaryDirectory(prefix="cv-fuzz-") as tmp:
        runner = Runner(replay, opt, llvm_as, args.passes, Path(tmp), args.timeout)

        def consider(cfg: dict[str, int]) -> None:
            nonlocal evaluated, gen_invalid
            ll = runner.generate(cfg)
            if ll is None:
                gen_invalid += 1
                return
            cov, failed, reason = runner.evaluate(ll)
            evaluated += 1
            if failed:
                findings.append({"config": dict(cfg), "reason": reason})
            new = cov - seen
            if new:
                seen.update(new)
                corpus.append(dict(cfg))

        # Seed corpus: the default config plus random configs.
        consider(dict(DEFAULTS))
        for _ in range(args.corpus_seeds):
            consider(random_config(rng))
        if not corpus:
            corpus.append(dict(DEFAULTS))

        for _ in range(args.iterations):
            if len(corpus) >= 2 and rng.random() < args.crossover_rate:
                child = crossover(rng.choice(corpus), rng.choice(corpus), rng)
            else:
                child = mutate(rng.choice(corpus), rng)
            consider(child)

    for i, cfg in enumerate(corpus):
        write_config(cfg, corpus_dir / f"corpus{i:04d}.cfg")
    finding_records = []
    for i, f in enumerate(findings):
        cfg_path = findings_dir / f"finding{i:04d}.cfg"
        write_config(f["config"], cfg_path)
        finding_records.append({"config_path": str(cfg_path), "reason": f["reason"]})

    minimized = []
    if args.minimize and findings and args.reducer.exists():
        oracle = (f"{opt} -S -passes={args.passes} {{ll}} -o - 2>/dev/null "
                  f"| {llvm_as} - -o {os.devnull} 2>/dev/null")
        mdir = args.out_dir / "minimized"
        mdir.mkdir(exist_ok=True)
        for i, rec in enumerate(finding_records):
            out_cfg = mdir / f"finding{i:04d}.cfg"
            proc = subprocess.run(
                [sys.executable, str(args.reducer), "--config", rec["config_path"],
                 "--replay", str(replay), "--oracle", oracle, "--invert",
                 "--out", str(out_cfg)],
                capture_output=True, text=True)
            minimized.append({"finding": rec["config_path"],
                              "reduced": str(out_cfg) if proc.returncode == 0 else None,
                              "returncode": proc.returncode})

    summary = {
        "iterations": args.iterations,
        "evaluated": evaluated,
        "generator_invalid": gen_invalid,
        "corpus_size": len(corpus),
        "coverage_size": len(seen),
        "findings": len(findings),
        "passes": args.passes,
        "opt": str(opt),
        "finding_records": finding_records,
        "minimized": minimized,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"fuzz: {evaluated} evaluated, corpus {len(corpus)}, coverage {len(seen)}, "
          f"{len(findings)} finding(s) -> {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
