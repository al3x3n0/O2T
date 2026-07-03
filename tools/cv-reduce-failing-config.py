#!/usr/bin/env python3
"""Reduce a generator config while preserving a real differential failure.

The C++ reducer (`ConfigReducer.h` / `cv-reduce-config`) minimizes a config
while an *abstract* probe-marker predicate holds. That is the right tool for
shrinking a coverage witness, but it cannot minimize a config that triggers a
real failure in `opt`/`llvm-as`/Alive2 -- the abstract markers say nothing about
whether the failure survives.

This tool closes that gap with the standard delta-debugging contract: an
external *oracle* command decides whether a candidate config is still
"interesting" (still triggers the failure). For each config field it tries the
simplest values first, regenerates IR with `cv-replay`, and keeps a shrink only
when the oracle still reports the failure. The result is the smallest config
that still reproduces a genuine miscompile / verifier rejection / pass crash --
a witness a human can actually file.

Oracle contract:
  The oracle is a shell command run once per candidate. `{ll}` is substituted
  with the path to the candidate's generated IR and `{cfg}` with the candidate
  config file. By default exit code 0 means "still failing / interesting".
  Use --invert when the oracle is a *validity* check you want to fail (e.g.
  `llvm-as {ll}` to reduce an invalid-IR witness like a non-dominating select).

Examples:
  # Reduce an Alive2 refinement failure (alive-tv exits non-zero on mismatch):
  cv-reduce-failing-config.py --config fail.cfg --out min.cfg --invert \\
      --oracle 'scripts/replay-with-opt.sh {cfg} instcombine | alive-tv {ll} -'

  # Reduce an invalid-IR witness (keep shrinking while llvm-as rejects it):
  cv-reduce-failing-config.py --config fail.cfg --out min.cfg --invert \\
      --oracle 'llvm-as {ll} -o /dev/null'
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# Default config values mirror cv::defaultConfig() in src/GeneratorConfig.cpp.
DEFAULTS: dict[str, int] = {
    "arith_opcode": 0,
    "rhs_mode": 0,
    "extra_opcode": 0,
    "predicate": 0,
    "shape": 0,
    "feature_bits": 1,
    "memory_shape": 0,
    "pointer_mode": 0,
    "store_mode": 0,
    "load_use_mode": 0,
    "loop_shape": 0,
    "loop_trip_mode": 0,
    "induction_mode": 0,
    "loop_use_mode": 0,
    "vector_shape": 0,
    "global_shape": 0,
    "compose_bits": 0,
    "const_a": 0,
    "const_b": 1,
}

# Field write order matches cv::writeConfig() so output diffs cleanly.
FIELD_ORDER = list(DEFAULTS.keys())

# Candidate value sets mirror cv::reduceConfig() in ConfigReducer.h, extended
# with compose_bits. The reduction visits fields in this order; composition and
# the high-level shape knobs come first so the IR collapses early.
REDUCTION_FIELDS: list[tuple[str, list[int]]] = [
    ("compose_bits", [0, 1, 2, 3, 4, 5, 6, 7]),
    ("feature_bits", [0, 1, 2, 3]),
    ("global_shape", [0, 1, 2, 3]),
    ("vector_shape", list(range(25))),
    ("memory_shape", [0, 1, 2, 3, 4, 5]),
    ("loop_shape", [0, 1, 2, 3, 4]),
    ("shape", [0, 1, 2, 3, 4]),
    ("pointer_mode", [0, 1, 2]),
    ("store_mode", [0, 1, 2]),
    ("load_use_mode", [0, 1, 2]),
    ("loop_trip_mode", [0, 1, 2]),
    ("induction_mode", [0, 1, 2]),
    ("loop_use_mode", [0, 1, 2]),
    ("predicate", [0, 1, 2, 3]),
    ("extra_opcode", [0, 1, 2, 3, 4, 5]),
    ("rhs_mode", [0, 1, 2, 3]),
    ("arith_opcode", [0, 1, 2, 3, 4, 5]),
    ("const_a", [0, 1, -1]),
    ("const_b", [0, 1, -1]),
]

CONST_FIELDS = {"const_a", "const_b"}


def simplicity_key(field: str, value: int) -> tuple[int, int]:
    """Lower keys are simpler. Used to order candidates and to skip values that
    are not strictly simpler than the current one (which bounds oracle calls)."""
    if field == "compose_bits":
        return (bin(value & 0xFF).count("1"), value)
    if field in CONST_FIELDS:
        rank = {0: 0, 1: 1, -1: 2}.get(value, 3)
        return (rank, abs(value))
    return (value, value)


def parse_config(path: Path) -> dict[str, int]:
    config = dict(DEFAULTS)
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"expected key=value: {raw!r}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in DEFAULTS:
            raise ValueError(f"unknown key: {key!r}")
        config[key] = int(value, 0)
    return config


def write_config(config: dict[str, int], path: Path) -> None:
    path.write_text("".join(f"{key}={config[key]}\n" for key in FIELD_ORDER))


class Oracle:
    def __init__(self, replay: Path, command: str, invert: bool,
                 workdir: Path, timeout: float | None) -> None:
        self.replay = replay
        self.command = command
        self.invert = invert
        self.workdir = workdir
        self.timeout = timeout
        self.calls = 0
        self.replay_failures = 0

    def interesting(self, config: dict[str, int]) -> bool:
        """Generate IR for `config` and ask the oracle if the failure persists."""
        self.calls += 1
        cfg_path = self.workdir / "candidate.cfg"
        ll_path = self.workdir / "candidate.ll"
        write_config(config, cfg_path)

        replayed = subprocess.run(
            [str(self.replay), "--config", str(cfg_path), "--out", str(ll_path)],
            capture_output=True, text=True)
        if replayed.returncode != 0 or not ll_path.exists():
            # A config that cannot even be replayed is not a valid witness.
            self.replay_failures += 1
            return False

        command = self.command.replace("{ll}", shlex.quote(str(ll_path)))
        command = command.replace("{cfg}", shlex.quote(str(cfg_path)))
        try:
            result = subprocess.run(command, shell=True, capture_output=True,
                                    text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return False
        ok = result.returncode == 0
        return (not ok) if self.invert else ok


def reduce_config(config: dict[str, int], oracle: Oracle,
                  max_rounds: int) -> tuple[dict[str, int], int]:
    current = dict(config)
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        changed = False
        for field, candidates in REDUCTION_FIELDS:
            cur_value = current[field]
            cur_key = simplicity_key(field, cur_value)
            simpler = sorted(
                (c for c in candidates
                 if simplicity_key(field, c) < cur_key),
                key=lambda c: simplicity_key(field, c))
            for candidate in simpler:
                trial = dict(current)
                trial[field] = candidate
                if oracle.interesting(trial):
                    current = trial
                    changed = True
                    break  # candidates are simplest-first, so stop at the first hit
        if not changed:
            break
    return current, rounds


def changed_fields(before: dict[str, int],
                   after: dict[str, int]) -> dict[str, dict[str, int]]:
    return {key: {"from": before[key], "to": after[key]}
            for key in FIELD_ORDER if before[key] != after[key]}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, type=Path,
                        help="starting (failing) config")
    parser.add_argument("--oracle", required=True,
                        help="oracle command; {ll}/{cfg} are substituted")
    parser.add_argument("--replay", type=Path,
                        default=Path(__file__).resolve().parent.parent / "build" / "cv-replay")
    parser.add_argument("--out", type=Path, help="write reduced config here (default stdout)")
    parser.add_argument("--report", type=Path, help="write a JSON reduction report here")
    parser.add_argument("--invert", action="store_true",
                        help="treat non-zero oracle exit as 'still failing'")
    parser.add_argument("--max-rounds", type=int, default=5,
                        help="max fixed-point passes over the fields (default 5)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="per-oracle-invocation timeout in seconds")
    parser.add_argument("--allow-uninteresting-start", action="store_true",
                        help="do not fail if the starting config is not interesting")
    args = parser.parse_args()

    if not args.replay.exists():
        print(f"error: cv-replay not found at {args.replay}", file=sys.stderr)
        return 2

    try:
        start = parse_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="cv-reduce-fail-") as tmp:
        oracle = Oracle(args.replay, args.oracle, args.invert,
                        Path(tmp), args.timeout)

        if not oracle.interesting(start):
            message = ("starting config is not interesting under the oracle; "
                       "nothing to reduce")
            if not args.allow_uninteresting_start:
                print(f"error: {message}", file=sys.stderr)
                return 1
            print(f"warning: {message}", file=sys.stderr)

        reduced, rounds = reduce_config(start, oracle, args.max_rounds)

    report = {
        "starting_config": start,
        "reduced_config": reduced,
        "changed_fields": changed_fields(start, reduced),
        "rounds": rounds,
        "oracle_calls": oracle.calls,
        "replay_failures": oracle.replay_failures,
        "invert": args.invert,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_config(reduced, args.out)
    else:
        sys.stdout.write("".join(f"{key}={reduced[key]}\n" for key in FIELD_ORDER))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")

    print(f"reduced {len(report['changed_fields'])} field(s) in {rounds} round(s) "
          f"using {oracle.calls} oracle call(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
