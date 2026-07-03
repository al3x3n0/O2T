#!/usr/bin/env python3
"""Run the KLEE-backed O2T harness campaign."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=os.environ.get("O2T_RUN_ID", os.environ.get("COMPILERVERIF_RUN_ID")))
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--harness", type=Path, default=ROOT / "harnesses" / "instcombine_harness.cpp")
    parser.add_argument("--replay", type=Path, default=ROOT / "build" / "cv-replay")
    parser.add_argument("--reducer", type=Path, default=ROOT / "build" / "cv-reduce-config")
    parser.add_argument("--extractor", type=Path, default=ROOT / "tools" / "cv-ktest-extract.py")
    parser.add_argument("--coverage-summarizer", type=Path, default=ROOT / "tools" / "cv-summarize-klee-campaign.py")
    parser.add_argument("--backfill-tool", type=Path, default=ROOT / "tools" / "cv-backfill-coverage-gaps.py")
    parser.add_argument("--feedback", action="store_true",
                        help="KLEE oracle-novelty feedback: assume only-novel coverage")
    parser.add_argument("--feedback-state", type=Path,
                        help="accumulated covered-markers JSON (default <out>/feedback-covered.json)")
    parser.add_argument("--feedback-generator", type=Path,
                        default=ROOT / "tools" / "cv-generate-klee-feedback.py")
    parser.add_argument("--minimizer", type=Path, default=ROOT / "tools" / "cv-minimize-campaign-failures.py")
    parser.add_argument("--failing-reducer", type=Path, default=ROOT / "tools" / "cv-reduce-failing-config.py")
    parser.add_argument("--single-config-oracle", type=Path, default=ROOT / "scripts" / "single-config-opt-oracle.sh")
    parser.add_argument("--campaign-packager", type=Path, default=ROOT / "tools" / "cv-package-verification-campaign.py")
    parser.add_argument("--klee-shell", type=Path, default=ROOT / "scripts" / "klee-shell.sh")
    parser.add_argument("--opt-checker", type=Path, default=ROOT / "scripts" / "opt-check-cases.sh")
    parser.add_argument("--reduce", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--passes")
    parser.add_argument("--require-observed-probes", action="store_true")
    parser.add_argument("--host-opt", type=Path)
    parser.add_argument("--host-llvm-as", type=Path)
    parser.add_argument("--semantic-clang")
    parser.add_argument("--alive2", action="store_true")
    parser.add_argument("--alive2-bin", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--coverage-summary", type=Path)
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--backfill-gaps", action="store_true")
    parser.add_argument("--backfill-dir", type=Path)
    parser.add_argument("--backfill-check", action="store_true")
    parser.add_argument("--minimize-failures", action="store_true",
                        help="auto-minimize opt-check failures into minimal configs")
    parser.add_argument("--minimize-dir", type=Path)
    parser.add_argument("--package-campaign", action="store_true")
    parser.add_argument("--package-out", type=Path)
    parser.add_argument("--instrumentation-dir", type=Path)
    parser.add_argument("--klee-arg", action="append", default=[])
    return parser.parse_args()


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def require_file(path: Path, label: str) -> bool:
    if path.is_file():
        return True
    print(f"{label} does not exist: {path}", file=sys.stderr)
    return False


def require_executable(path: Path, label: str) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    print(f"{label} is not executable: {path}", file=sys.stderr)
    return False


def append_alive2_args(command: list[str], args: argparse.Namespace) -> None:
    if args.alive2:
        command.append("--alive2")
    if args.alive2_bin:
        command.extend(["--alive2-bin", str(resolve(args.alive2_bin))])


def container_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise ValueError(f"path must be under mounted workspace {WORKSPACE_ROOT}: {path}") from None
    return "/work/" + relative.as_posix()


def command_text(command: list[str], env: dict[str, str] | None = None) -> str:
    prefix = []
    if env:
        prefix = [f"{key}={value}" for key, value in sorted(env.items())]
    return shlex.join([*prefix, *command])


def write_command_log(path: Path, commands: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for entry in commands:
            output.write(command_text(entry["command"], entry.get("env")) + "\n")


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        command,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def count_manifest_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def build_klee_script(
    *,
    harness: Path,
    build_dir: Path,
    klee_dir: Path,
    dump_dir: Path,
    klee_args: list[str],
    extra_cxxflags: list[str] | None = None,
) -> str:
    harness_bc = build_dir / "instcombine_harness.linked.bc"
    extra = (" " + shlex.join(extra_cxxflags)) if extra_cxxflags else ""
    klee_command = [
        "klee",
        f"--output-dir={container_path(klee_dir)}",
        *klee_args,
        container_path(harness_bc),
    ]
    return f"""
set -euo pipefail

pick_tool() {{
  for tool in "$@"; do
    if command -v "${{tool}}" >/dev/null 2>&1; then
      printf '%s\\n' "${{tool}}"
      return 0
    fi
  done
  return 1
}}

cxx=$(pick_tool "${{O2T_KLEE_CXX:-${{COMPILERVERIF_KLEE_CXX:-clang++}}}}" clang++-13 clang++-12 clang++) || {{
  echo "no clang++ tool found in KLEE container" >&2
  exit 1
}}

"${{cxx}}" -std=c++17 -g -O0 -Xclang -disable-O0-optnone \\
  -DO2T_WITH_KLEE=1{extra} -I include -emit-llvm -c \\
  {shlex.quote(container_path(harness))} -o {shlex.quote(container_path(harness_bc))}

{shlex.join(klee_command)}

shopt -s nullglob
tests=({shlex.quote(container_path(klee_dir))}/*.ktest)
if (( ${{#tests[@]}} == 0 )); then
  echo "KLEE produced no .ktest files in {container_path(klee_dir)}" >&2
  exit 1
fi

for test in "${{tests[@]}}"; do
  ktest-tool "${{test}}" > {shlex.quote(container_path(dump_dir))}/$(basename "${{test}}").txt
done
""".strip()


def write_summary(
    path: Path,
    *,
    run_id: str,
    out_dir: Path,
    commands: list[dict[str, Any]],
    dry_run: bool,
    case_count: int,
    check_status: str,
    exit_code: int,
) -> None:
    summary = {
        "run_id": run_id,
        "out_dir": str(out_dir),
        "build_dir": str(out_dir / "build"),
        "klee_dir": str(out_dir / "klee"),
        "dump_dir": str(out_dir / "ktest-dumps"),
        "cases_dir": str(out_dir / "cases"),
        "dry_run": dry_run,
        "case_count": case_count,
        "check_status": check_status,
        "exit_code": exit_code,
        "commands": [
            {
                "command": command_text(entry["command"], entry.get("env")),
                "returncode": entry.get("returncode"),
            }
            for entry in commands
        ],
    }
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or default_run_id()
    out_dir = resolve(args.out_dir) if args.out_dir else ROOT / "klee-out" / "instcombine" / run_id
    build_dir = out_dir / "build"
    klee_dir = out_dir / "klee"
    dump_dir = out_dir / "ktest-dumps"
    cases_dir = out_dir / "cases"
    command_log = out_dir / "commands.log"
    summary_json = out_dir / "summary.json"
    coverage_summary = resolve(args.coverage_summary) if args.coverage_summary else out_dir / "coverage-summary.txt"
    coverage_json = resolve(args.coverage_json) if args.coverage_json else out_dir / "coverage-summary.json"
    backfill_dir = resolve(args.backfill_dir) if args.backfill_dir else out_dir / "backfill"
    backfill_summary = backfill_dir / "coverage-summary.txt"
    backfill_json = backfill_dir / "coverage-summary.json"
    backfill_manifest = backfill_dir / "manifest.jsonl"
    backfill_opt_manifest = backfill_dir / "opt" / "manifest.jsonl"
    package_out = resolve(args.package_out) if args.package_out else out_dir / "verification-campaign"
    manifest = cases_dir / "manifest.jsonl"
    opt_manifest = cases_dir / "opt" / "manifest.jsonl"
    minimize_dir = resolve(args.minimize_dir) if args.minimize_dir else out_dir / "minimized"
    minimize_summary = minimize_dir / "summary.json"

    harness = resolve(args.harness)
    replay = resolve(args.replay)
    reducer = resolve(args.reducer)
    extractor = resolve(args.extractor)
    coverage_summarizer = resolve(args.coverage_summarizer)
    backfill_tool = resolve(args.backfill_tool)
    campaign_packager = resolve(args.campaign_packager)
    klee_shell = resolve(args.klee_shell)
    opt_checker = resolve(args.opt_checker)
    minimizer = resolve(args.minimizer)
    failing_reducer = resolve(args.failing_reducer)
    single_config_oracle = resolve(args.single_config_oracle)
    feedback_generator = resolve(args.feedback_generator)
    feedback_state = resolve(args.feedback_state) if args.feedback_state else out_dir / "feedback-covered.json"
    feedback_include = out_dir / "feedback-include"
    feedback_header = feedback_include / "o2t" / "GeneratedKleeFeedback.h"

    ok = True
    ok = require_file(harness, "harness") and ok
    ok = require_executable(extractor, "ktest extractor") and ok
    ok = require_executable(coverage_summarizer, "KLEE coverage summarizer") and ok
    if args.backfill_gaps:
        ok = require_executable(backfill_tool, "coverage gap backfill tool") and ok
    if args.package_campaign:
        ok = require_executable(campaign_packager, "verification campaign packager") and ok
        if args.instrumentation_dir and not resolve(args.instrumentation_dir).is_dir():
            print(f"instrumentation directory does not exist: {resolve(args.instrumentation_dir)}", file=sys.stderr)
            ok = False
    ok = require_executable(klee_shell, "KLEE shell") and ok
    if not args.dry_run:
        ok = require_executable(replay, "cv-replay") and ok
    if args.reduce or args.backfill_gaps:
        ok = require_executable(reducer, "cv-reduce-config") and ok
    if args.check:
        ok = require_executable(opt_checker, "opt checker") and ok
    if args.minimize_failures:
        ok = require_executable(opt_checker, "opt checker") and ok
        ok = require_executable(minimizer, "campaign failure minimizer") and ok
        ok = require_executable(failing_reducer, "failure-preserving reducer") and ok
        ok = require_executable(single_config_oracle, "single-config opt oracle") and ok
    if args.feedback:
        ok = require_executable(feedback_generator, "KLEE feedback generator") and ok
    if args.host_opt:
        ok = require_executable(resolve(args.host_opt), "host opt") and ok
    if args.host_llvm_as:
        ok = require_executable(resolve(args.host_llvm_as), "host llvm-as") and ok
    if args.alive2_bin:
        ok = require_executable(resolve(args.alive2_bin), "Alive2 executable") and ok

    try:
        for path in [out_dir, build_dir, klee_dir, dump_dir, cases_dir, harness]:
            container_path(path)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        ok = False

    if not ok:
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    klee_dir.mkdir(parents=True, exist_ok=True)
    dump_dir.mkdir(parents=True, exist_ok=True)
    cases_dir.mkdir(parents=True, exist_ok=True)

    extra_cxxflags: list[str] = []
    if args.feedback:
        extra_cxxflags = ["-I", container_path(feedback_include),
                          "-DO2T_KLEE_FEEDBACK=1"]

    klee_script = build_klee_script(
        harness=harness,
        build_dir=build_dir,
        klee_dir=klee_dir,
        dump_dir=dump_dir,
        klee_args=args.klee_arg,
        extra_cxxflags=extra_cxxflags,
    )
    commands: list[dict[str, Any]] = []
    # Regenerate the feedback header from accumulated coverage before the build,
    # so the harness assumes only-novel coverage on this run.
    feedback_gen_command: list[str] | None = None
    feedback_accumulate_command: list[str] | None = None
    if args.feedback:
        feedback_gen_command = [
            str(feedback_generator), "--state", str(feedback_state),
            "--out", str(feedback_header),
        ]
        commands.append({"command": feedback_gen_command})
    commands.append({"command": [str(klee_shell), "bash", "-lc", klee_script]})

    extract_command = [
        str(extractor),
        "--dump-dir",
        str(dump_dir),
        "--cases",
        str(cases_dir),
        "--replay",
        str(replay),
    ]
    if args.reduce:
        extract_command.extend(["--reduce", "--reducer", str(reducer)])
    commands.append({"command": extract_command})

    check_status = "not-requested"
    if args.check:
        check_command = [str(opt_checker)]
        if args.require_observed_probes:
            check_command.append("--require-observed-probes")
        append_alive2_args(check_command, args)
        check_command.append(str(cases_dir))
        if args.passes:
            check_command.append(args.passes)
        check_env: dict[str, str] = {}
        if args.host_opt:
            check_env["O2T_HOST_OPT"] = str(resolve(args.host_opt))
            check_env["COMPILERVERIF_HOST_OPT"] = str(resolve(args.host_opt))
        if args.host_llvm_as:
            check_env["O2T_HOST_LLVM_AS"] = str(resolve(args.host_llvm_as))
            check_env["COMPILERVERIF_HOST_LLVM_AS"] = str(resolve(args.host_llvm_as))
        if args.semantic_clang:
            check_env["O2T_SEMANTIC_CLANG"] = args.semantic_clang
            check_env["COMPILERVERIF_SEMANTIC_CLANG"] = args.semantic_clang
        commands.append({"command": check_command, "env": check_env})

    coverage_command = [
        str(coverage_summarizer),
        "--cases-manifest",
        str(manifest),
        "--runner-summary",
        str(summary_json),
        "--out",
        str(coverage_summary),
        "--json-out",
        str(coverage_json),
    ]
    if args.check:
        coverage_command.extend(["--opt-manifest", str(opt_manifest)])
    commands.append({"command": coverage_command})

    if args.feedback:
        feedback_accumulate_command = [
            str(feedback_generator), "--covered-json", str(coverage_json),
            "--state", str(feedback_state), "--update-state", str(feedback_state),
        ]
        commands.append({"command": feedback_accumulate_command})

    minimize_command: list[str] | None = None
    minimize_env: dict[str, str] = {}
    if args.minimize_failures:
        minimize_command = [
            str(minimizer),
            "--opt-manifest", str(opt_manifest),
            "--out-dir", str(minimize_dir),
            "--reducer", str(failing_reducer),
            "--replay", str(replay),
            "--oracle", f"{single_config_oracle} {{cfg}}",
            "--invert",
        ]
        minimize_env = {"CV_OPT_CHECKER": str(opt_checker)}
        if args.passes:
            minimize_env["CV_PASSES"] = args.passes
        if args.require_observed_probes:
            minimize_env["CV_REQUIRE_OBSERVED"] = "1"
        if args.alive2:
            minimize_env["CV_ALIVE2"] = "1"
        if args.alive2_bin:
            minimize_env["CV_ALIVE2_BIN"] = str(resolve(args.alive2_bin))
        if args.host_opt:
            minimize_env["O2T_HOST_OPT"] = str(resolve(args.host_opt))
            minimize_env["COMPILERVERIF_HOST_OPT"] = str(resolve(args.host_opt))
        if args.host_llvm_as:
            minimize_env["O2T_HOST_LLVM_AS"] = str(resolve(args.host_llvm_as))
            minimize_env["COMPILERVERIF_HOST_LLVM_AS"] = str(resolve(args.host_llvm_as))
        if args.semantic_clang:
            minimize_env["O2T_SEMANTIC_CLANG"] = args.semantic_clang
            minimize_env["COMPILERVERIF_SEMANTIC_CLANG"] = args.semantic_clang
        commands.append({"command": minimize_command, "env": minimize_env})

    if args.backfill_gaps:
        backfill_command = [
            str(backfill_tool),
            "--coverage-json",
            str(coverage_json),
            "--out-dir",
            str(backfill_dir),
            "--replay",
            str(replay),
            "--reducer",
            str(reducer),
            "--reduce",
        ]
        if args.backfill_check:
            backfill_command.append("--check")
            if args.require_observed_probes:
                backfill_command.append("--require-observed-probes")
            if args.alive2:
                backfill_command.append("--alive2")
            if args.alive2_bin:
                backfill_command.extend(["--alive2-bin", str(resolve(args.alive2_bin))])
            if args.passes:
                backfill_command.extend(["--passes", args.passes])
            if args.host_opt:
                backfill_command.extend(["--host-opt", str(resolve(args.host_opt))])
            if args.host_llvm_as:
                backfill_command.extend(["--host-llvm-as", str(resolve(args.host_llvm_as))])
            if args.semantic_clang:
                backfill_command.extend(["--semantic-clang", args.semantic_clang])
        commands.append({"command": backfill_command})

        backfill_summary_command = [
            str(coverage_summarizer),
            "--cases-manifest",
            str(backfill_manifest),
            "--out",
            str(backfill_summary),
            "--json-out",
            str(backfill_json),
        ]
        if args.backfill_check:
            backfill_summary_command.extend(["--opt-manifest", str(backfill_opt_manifest)])
        commands.append({"command": backfill_summary_command})

    if args.package_campaign:
        package_command = [
            str(campaign_packager),
            "--klee-campaign",
            str(out_dir),
            "--out",
            str(package_out),
        ]
        if args.instrumentation_dir:
            package_command.extend(["--instrumentation", str(resolve(args.instrumentation_dir))])
        commands.append({"command": package_command})

    write_command_log(command_log, commands)
    if args.dry_run:
        for entry in commands:
            print(command_text(entry["command"], entry.get("env")))
        write_summary(
            summary_json,
            run_id=run_id,
            out_dir=out_dir,
            commands=commands,
            dry_run=True,
            case_count=0,
            check_status="dry-run",
            exit_code=0,
        )
        print(f"commands: {command_log}")
        print(f"summary: {summary_json}")
        print(f"coverage_summary: {coverage_summary}")
        print(f"coverage_json: {coverage_json}")
        if args.minimize_failures:
            print(f"minimized_failures: {minimize_dir}")
            print(f"minimized_summary: {minimize_summary}")
        if args.backfill_gaps:
            print(f"backfill: {backfill_dir}")
            print(f"backfill_summary: {backfill_summary}")
            print(f"backfill_json: {backfill_json}")
        if args.package_campaign:
            print(f"verification_campaign: {package_out}")
        return 0

    exit_code = 0
    executable_commands = [
        entry
        for entry in commands
        if entry["command"] != coverage_command
        and (minimize_command is None or entry["command"] != minimize_command)
        and (feedback_accumulate_command is None or entry["command"] != feedback_accumulate_command)
        and (not args.backfill_gaps or entry["command"][0] != str(backfill_tool))
        and (not args.backfill_gaps or entry["command"][:2] != [str(coverage_summarizer), "--cases-manifest"])
        and (not args.package_campaign or entry["command"][0] != str(campaign_packager))
    ]
    for entry in executable_commands:
        completed = run(entry["command"], env=entry.get("env"))
        entry["returncode"] = completed.returncode
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode != 0:
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            exit_code = completed.returncode
            if entry["command"][0] == str(opt_checker):
                check_status = "failed"
            break
        if entry["command"][0] == str(opt_checker):
            check_status = "passed"

    if args.check and check_status == "not-requested":
        check_status = "skipped"

    case_count = count_manifest_records(manifest)
    if args.check and opt_manifest.exists():
        case_count = count_manifest_records(opt_manifest)
    write_summary(
        summary_json,
        run_id=run_id,
        out_dir=out_dir,
        commands=commands,
        dry_run=False,
        case_count=case_count,
        check_status=check_status,
        exit_code=exit_code,
    )
    if exit_code == 0 or manifest.exists():
        completed = run(coverage_command)
        for entry in commands:
            if entry["command"] == coverage_command:
                entry["returncode"] = completed.returncode
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode != 0 and exit_code == 0:
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            exit_code = completed.returncode
    if (args.feedback and feedback_accumulate_command is not None
            and coverage_json.exists()):
        # Accumulate this run's generated markers so the next run's feedback
        # header steers KLEE further toward the frontier.
        completed = run(feedback_accumulate_command)
        for entry in commands:
            if entry["command"] == feedback_accumulate_command:
                entry["returncode"] = completed.returncode
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
    if args.minimize_failures and minimize_command is not None and opt_manifest.exists():
        # Runs even when the opt check failed -- that is exactly when there are
        # failing cases worth minimizing. A minimizer error is surfaced but does
        # not overwrite an already-failing exit code.
        completed = run(minimize_command, env=minimize_env)
        for entry in commands:
            if entry["command"] == minimize_command:
                entry["returncode"] = completed.returncode
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        if completed.returncode != 0 and exit_code == 0:
            exit_code = completed.returncode

    if args.backfill_gaps and exit_code == 0 and coverage_json.exists():
        for entry in commands:
            is_backfill = entry["command"][0] == str(backfill_tool)
            is_backfill_summary = (
                entry["command"][0] == str(coverage_summarizer)
                and str(backfill_manifest) in entry["command"]
            )
            if not (is_backfill or is_backfill_summary):
                continue
            if is_backfill_summary and not backfill_manifest.exists():
                continue
            completed = run(entry["command"])
            entry["returncode"] = completed.returncode
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.returncode != 0:
                if completed.stderr:
                    print(completed.stderr, file=sys.stderr, end="")
                exit_code = completed.returncode
                break
    if args.package_campaign and exit_code == 0:
        for entry in commands:
            if entry["command"][0] != str(campaign_packager):
                continue
            completed = run(entry["command"])
            entry["returncode"] = completed.returncode
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.returncode != 0:
                if completed.stderr:
                    print(completed.stderr, file=sys.stderr, end="")
                exit_code = completed.returncode
            break
    write_command_log(command_log, commands)

    print(f"KLEE run: {out_dir}")
    print(f"Cases: {cases_dir}")
    print(f"commands: {command_log}")
    print(f"summary: {summary_json}")
    print(f"coverage_summary: {coverage_summary}")
    print(f"coverage_json: {coverage_json}")
    if args.minimize_failures:
        print(f"minimized_failures: {minimize_dir}")
        print(f"minimized_summary: {minimize_summary}")
    if args.backfill_gaps:
        print(f"backfill: {backfill_dir}")
        print(f"backfill_summary: {backfill_summary}")
        print(f"backfill_json: {backfill_json}")
    if args.package_campaign:
        print(f"verification_campaign: {package_out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
