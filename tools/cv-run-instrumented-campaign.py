#!/usr/bin/env python3
"""Apply/build/replay an existing O2T campaign against instrumented LLVM."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--llvm-source", type=Path, required=True)
    parser.add_argument("--llvm-build", type=Path, required=True)
    parser.add_argument("--playbook", type=Path, default=ROOT / "scripts" / "instrumented-llvm-playbook.sh")
    parser.add_argument("--summarizer", type=Path, default=ROOT / "tools" / "cv-summarize-manifest.py")
    parser.add_argument(
        "--verification-summarizer",
        type=Path,
        default=ROOT / "tools" / "cv-summarize-verification-campaign.py",
    )
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--verification-summary", type=Path)
    parser.add_argument("--verification-json", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--alive2", action="store_true")
    parser.add_argument("--alive2-bin", type=Path)
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def require_path(path: Path, label: str) -> bool:
    if path.exists():
        return True
    print(f"{label} does not exist: {path}", file=sys.stderr)
    return False


def require_dir(path: Path, label: str) -> bool:
    if path.is_dir():
        return True
    print(f"{label} is not a directory: {path}", file=sys.stderr)
    return False


def require_executable(path: Path, label: str) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    print(f"{label} is not executable: {path}", file=sys.stderr)
    return False


def write_command_log(path: Path, commands: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for command in commands:
            output.write(" ".join(command) + "\n")


def playbook_base(args: argparse.Namespace) -> list[str]:
    command = [str(args.playbook)]
    if args.execute:
        command.append("--execute")
    if args.allow_dirty:
        command.append("--allow-dirty")
    return command


def append_alive2_args(command: list[str], args: argparse.Namespace) -> None:
    if args.alive2:
        command.append("--alive2")
    if args.alive2_bin:
        command.extend(["--alive2-bin", str(args.alive2_bin)])


def main() -> int:
    args = parse_args()
    campaign = args.campaign
    patch = campaign / "instrumentation" / "instrumentation.patch"
    cases = campaign / "cases"
    manifest = cases / "opt" / "manifest.jsonl"
    summary = args.summary or (campaign / "instrumented-summary.txt")
    verification_summary = args.verification_summary or (campaign / "verification-summary.txt")
    verification_json = args.verification_json or (campaign / "verification-summary.json")
    command_log = campaign / "instrumented-commands.log"

    ok = True
    ok = require_dir(campaign, "campaign") and ok
    ok = require_path(patch, "instrumentation patch") and ok
    ok = require_dir(cases, "campaign cases") and ok
    ok = require_dir(args.llvm_source, "LLVM source") and ok
    ok = require_dir(args.llvm_build, "LLVM build") and ok
    ok = require_executable(args.playbook, "instrumented LLVM playbook") and ok
    if args.alive2_bin:
        ok = require_executable(args.alive2_bin, "Alive2 executable") and ok
    if args.execute:
        ok = require_executable(args.summarizer, "manifest summarizer") and ok
        ok = require_executable(args.verification_summarizer, "verification summarizer") and ok
    if not ok:
        return 2

    run_opt_command = [
        *playbook_base(args),
        "run-opt",
        "--require-observed-probes",
    ]
    append_alive2_args(run_opt_command, args)
    run_opt_command.extend([str(args.llvm_build), str(cases)])

    commands = [
        [*playbook_base(args), "check", str(args.llvm_source), str(args.llvm_build)],
        [*playbook_base(args), "apply", str(args.llvm_source), str(patch)],
        [*playbook_base(args), "configure", str(args.llvm_source), str(args.llvm_build)],
        run_opt_command,
    ]
    summarize_command = [str(args.summarizer), str(manifest), "--out", str(summary)]
    verification_command = [
        str(args.verification_summarizer),
        "--campaign",
        str(campaign),
        "--out",
        str(verification_summary),
        "--json-out",
        str(verification_json),
    ]
    commands.extend([summarize_command, verification_command])

    command_log.parent.mkdir(parents=True, exist_ok=True)
    write_command_log(command_log, commands)

    replay_commands = commands[:-2]
    for command in replay_commands:
        completed = run(command)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode != 0:
            print(completed.stderr, file=sys.stderr, end="")
            return completed.returncode

    if args.execute:
        if not manifest.exists():
            print(f"manifest does not exist after run-opt: {manifest}", file=sys.stderr)
            return 1
        for command in [summarize_command, verification_command]:
            summarized = run(command)
            if summarized.stdout:
                print(summarized.stdout, end="")
            if summarized.returncode != 0:
                print(summarized.stderr, file=sys.stderr, end="")
                return summarized.returncode
        print(f"summary: {summary}")
        print(f"verification_summary: {verification_summary}")
        print(f"verification_json: {verification_json}")

    print(f"commands: {command_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
