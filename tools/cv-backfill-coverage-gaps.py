#!/usr/bin/env python3
"""Generate targeted O2T cases for KLEE coverage gaps."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cv_targeted_ir_configs import config_for_marker, marker_filename, write_config

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--markers", action="append", default=[])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--replay", type=Path, default=ROOT / "build" / "cv-replay")
    parser.add_argument("--reducer", type=Path, default=ROOT / "build" / "cv-reduce-config")
    parser.add_argument("--reduce", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--passes")
    parser.add_argument("--require-observed-probes", action="store_true")
    parser.add_argument("--host-opt", type=Path)
    parser.add_argument("--host-llvm-as", type=Path)
    parser.add_argument("--semantic-clang")
    parser.add_argument("--alive2", action="store_true")
    parser.add_argument("--alive2-bin", type=Path)
    parser.add_argument("--opt-checker", type=Path, default=ROOT / "scripts" / "opt-check-cases.sh")
    return parser.parse_args()


def require_path(path: Path, label: str) -> bool:
    if path.exists():
        return True
    print(f"{label} does not exist: {path}", file=sys.stderr)
    return False


def require_executable(path: Path, label: str) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    print(f"{label} is not executable: {path}", file=sys.stderr)
    return False


def normalize_config(replay: Path, raw_cfg: Path, normalized_cfg: Path) -> None:
    subprocess.run(
        [
            str(replay),
            "--config",
            str(raw_cfg),
            "--out",
            str(raw_cfg.with_suffix(".ll")),
            "--write-config",
            str(normalized_cfg),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def reduce_config(reducer: Path, normalized_cfg: Path, output_cfg: Path, marker: str) -> None:
    subprocess.run(
        [
            str(reducer),
            "--config",
            str(normalized_cfg),
            "--preserve",
            marker,
            "--out",
            str(output_cfg),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def emit_ir(replay: Path, cfg: Path, output_ll: Path) -> None:
    subprocess.run(
        [str(replay), "--config", str(cfg), "--out", str(output_ll)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def load_gap_markers(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return []
    summary = json.loads(path.read_text(encoding="utf-8"))
    markers = summary.get("markers", {}) if isinstance(summary, dict) else {}
    if not isinstance(markers, dict):
        return []
    result: list[tuple[str, str]] = []
    for gap_type in ["never_generated", "generated_not_observed"]:
        values = markers.get(gap_type, [])
        if isinstance(values, list):
            result.extend((str(marker), gap_type) for marker in values if str(marker))
    return result


def explicit_markers(values: list[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for value in values:
        for marker in value.split(","):
            marker = marker.strip()
            if marker:
                result.append((marker, "explicit"))
    return result


def dedupe_markers(markers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for marker, source in markers:
        if marker in seen:
            continue
        seen.add(marker)
        result.append((marker, source))
    return result


def run_check(args: argparse.Namespace) -> int:
    command = [str(args.opt_checker)]
    if args.require_observed_probes:
        command.append("--require-observed-probes")
    if args.alive2:
        command.append("--alive2")
    if args.alive2_bin:
        command.extend(["--alive2-bin", str(args.alive2_bin)])
    command.append(str(args.out_dir))
    if args.passes:
        command.append(args.passes)
    env = os.environ.copy()
    if args.host_opt:
        env["O2T_HOST_OPT"] = str(args.host_opt)
        env["COMPILERVERIF_HOST_OPT"] = str(args.host_opt)
    if args.host_llvm_as:
        env["O2T_HOST_LLVM_AS"] = str(args.host_llvm_as)
        env["COMPILERVERIF_HOST_LLVM_AS"] = str(args.host_llvm_as)
    if args.semantic_clang:
        env["O2T_SEMANTIC_CLANG"] = args.semantic_clang
        env["COMPILERVERIF_SEMANTIC_CLANG"] = args.semantic_clang
    completed = subprocess.run(command, env=env, text=True, check=False)
    return completed.returncode


def main() -> int:
    args = parse_args()
    if args.coverage_json is None and not args.markers:
        print("either --coverage-json or --markers is required", file=sys.stderr)
        return 2

    ok = True
    ok = require_executable(args.replay, "cv-replay") and ok
    if args.reduce:
        ok = require_executable(args.reducer, "cv-reduce-config") and ok
    if args.coverage_json:
        ok = require_path(args.coverage_json, "coverage summary JSON") and ok
    if args.check:
        ok = require_executable(args.opt_checker, "opt checker") and ok
    if args.host_opt:
        ok = require_executable(args.host_opt, "host opt") and ok
    if args.host_llvm_as:
        ok = require_executable(args.host_llvm_as, "host llvm-as") and ok
    if args.alive2_bin:
        ok = require_executable(args.alive2_bin, "Alive2 executable") and ok
    if not ok:
        return 2

    targets = dedupe_markers([*explicit_markers(args.markers), *load_gap_markers(args.coverage_json)])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.jsonl"

    generated = 0
    skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with manifest_path.open("w", encoding="utf-8") as manifest:
            for marker, gap_type in targets:
                config = config_for_marker(marker)
                if config is None:
                    skipped += 1
                    message = f"unsupported marker: {marker}"
                    print(message, file=sys.stderr)
                    if args.strict:
                        return 1
                    continue

                stem = marker_filename(marker)
                raw_cfg = tmp_path / f"{stem}.raw.cfg"
                normalized_cfg = args.out_dir / f"{stem}.cfg"
                output_cfg = normalized_cfg
                output_ll = args.out_dir / f"{stem}.ll"

                write_config(raw_cfg, config)
                try:
                    normalize_config(args.replay, raw_cfg, normalized_cfg)
                    if args.reduce:
                        reduced_cfg = args.out_dir / f"{stem}.reduced.cfg"
                        reduce_config(args.reducer, normalized_cfg, reduced_cfg, marker)
                        output_cfg = reduced_cfg
                    emit_ir(args.replay, output_cfg, output_ll)
                except subprocess.CalledProcessError as exc:
                    print(f"failed to generate config for {marker}: {exc}", file=sys.stderr)
                    return 1

                manifest.write(
                    json.dumps(
                        {
                            "case": stem,
                            "marker": marker,
                            "gap_type": gap_type,
                            "config": output_cfg.name,
                            "ir": output_ll.name,
                            "coverage": [marker],
                            "reduced": args.reduce,
                            "replay": f"{args.replay} --config {output_cfg}",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                generated += 1

    if generated == 0:
        print("no backfill configs generated", file=sys.stderr)
        return 1

    if args.check:
        checked = run_check(args)
        if checked != 0:
            return checked

    print(f"generated {generated} backfill config(s) in {args.out_dir}")
    if skipped:
        print(f"skipped {skipped} unsupported marker(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
