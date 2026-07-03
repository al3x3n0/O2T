#!/usr/bin/env python3
"""Auto-minimize failing cases from an opt-check manifest.

After `opt-check-cases.sh` runs over a campaign's cases it writes a JSONL
manifest where each record has a `status` ("passed"/"failed"), the `config` that
produced the case, and the `passes` pipeline. This tool walks that manifest and,
for every failed case, runs `cv-reduce-failing-config.py` to shrink the config
down to a minimal witness that still reproduces the failure under a caller-
supplied oracle.

It is oracle-agnostic: the campaign passes an oracle that re-runs the opt check
on a single candidate config (see `scripts/single-config-opt-oracle.sh`), but any
`{cfg}`/`{ll}` command works, which keeps the tool testable without a real LLVM
toolchain.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def read_failed_records(manifest: Path) -> list[dict]:
    failed: list[dict] = []
    seen_configs: set[str] = set()
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("status") != "failed":
            continue
        config = record.get("config")
        if not config or config in seen_configs:
            continue
        seen_configs.add(config)
        failed.append(record)
    return failed


def case_name(record: dict, index: int) -> str:
    name = record.get("case")
    if name:
        return str(name)
    config = record.get("config")
    if config:
        return Path(config).stem
    return f"case{index}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opt-manifest", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--oracle", required=True,
                        help="oracle command for cv-reduce-failing-config.py ({cfg}/{ll})")
    parser.add_argument("--reducer", type=Path,
                        default=Path(__file__).resolve().parent / "cv-reduce-failing-config.py")
    parser.add_argument("--replay", type=Path,
                        default=Path(__file__).resolve().parent.parent / "build" / "cv-replay")
    parser.add_argument("--invert", action="store_true",
                        help="pass --invert to the reducer (oracle non-zero == still failing)")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--summary-json", type=Path,
                        help="defaults to <out-dir>/summary.json")
    args = parser.parse_args()

    if not args.opt_manifest.exists():
        print(f"error: opt manifest not found: {args.opt_manifest}", file=sys.stderr)
        return 2
    if not args.reducer.exists():
        print(f"error: reducer not found: {args.reducer}", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.summary_json or (args.out_dir / "summary.json")

    failed = read_failed_records(args.opt_manifest)
    results: list[dict] = []
    minimized = 0

    for index, record in enumerate(failed):
        name = case_name(record, index)
        config = record.get("config")
        out_cfg = args.out_dir / f"{name}.cfg"
        report_path = args.out_dir / f"{name}.report.json"

        command = [
            sys.executable, str(args.reducer),
            "--config", str(config),
            "--replay", str(args.replay),
            "--oracle", args.oracle,
            "--out", str(out_cfg),
            "--report", str(report_path),
            "--max-rounds", str(args.max_rounds),
        ]
        if args.invert:
            command.append("--invert")
        if args.timeout is not None:
            command.extend(["--timeout", str(args.timeout)])

        completed = subprocess.run(command, capture_output=True, text=True)
        entry: dict = {
            "case": name,
            "config": config,
            "message": record.get("message", ""),
            "returncode": completed.returncode,
        }
        if completed.returncode == 0 and report_path.exists():
            report = json.loads(report_path.read_text())
            entry["status"] = "minimized"
            entry["reduced_config_path"] = str(out_cfg)
            entry["changed_fields"] = len(report.get("changed_fields", {}))
            entry["oracle_calls"] = report.get("oracle_calls")
            minimized += 1
        else:
            # A non-zero reducer exit usually means the oracle did not reproduce
            # the failure on the standalone config (e.g. it needed campaign
            # context). Record it instead of aborting the whole sweep.
            entry["status"] = "not-reproduced" if completed.returncode == 1 else "error"
            entry["stderr"] = completed.stderr.strip()
        results.append(entry)

    summary = {
        "opt_manifest": str(args.opt_manifest),
        "failed_cases": len(failed),
        "minimized": minimized,
        "not_reproduced": sum(1 for r in results if r["status"] == "not-reproduced"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"minimized {minimized}/{len(failed)} failing case(s) -> {args.out_dir}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
