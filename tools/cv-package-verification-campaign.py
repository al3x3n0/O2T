#!/usr/bin/env python3
"""Package KLEE and backfill cases for replay against instrumented LLVM."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--klee-campaign", type=Path, required=True)
    parser.add_argument("--instrumentation", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def case_entries(source_dir: Path, origin: str) -> list[dict[str, Any]]:
    manifest = source_dir / "manifest.jsonl"
    records = load_jsonl(manifest)
    entries: list[dict[str, Any]] = []
    if records:
        for record in records:
            config_name = str(record.get("config") or "")
            if not config_name:
                continue
            config = source_dir / config_name
            ir_name = str(record.get("ir") or "")
            ir = source_dir / ir_name if ir_name else config.with_suffix(".ll")
            entries.append(
                {
                    "origin": origin,
                    "source_manifest": manifest,
                    "source_record": record,
                    "config": config,
                    "ir": ir if ir.exists() else None,
                }
            )
        return entries

    for config in sorted(source_dir.glob("*.cfg")):
        entries.append(
            {
                "origin": origin,
                "source_manifest": None,
                "source_record": {"case": config.stem},
                "config": config,
                "ir": config.with_suffix(".ll") if config.with_suffix(".ll").exists() else None,
            }
        )
    return entries


def unique_name(used: set[str], preferred: str) -> str:
    if preferred not in used:
        used.add(preferred)
        return preferred
    stem = Path(preferred).stem
    suffix = Path(preferred).suffix
    index = 2
    while True:
        candidate = f"{stem}_{index}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def package_cases(klee_campaign: Path) -> list[dict[str, Any]]:
    entries = case_entries(klee_campaign / "cases", "klee")
    backfill = klee_campaign / "backfill"
    if backfill.is_dir():
        entries.extend(case_entries(backfill, "backfill"))
    return entries


def copy_if_requested(src: Path, dst: Path, dry_run: bool) -> None:
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main() -> int:
    args = parse_args()
    if not args.klee_campaign.is_dir():
        print(f"KLEE campaign is not a directory: {args.klee_campaign}", file=sys.stderr)
        return 2
    if args.instrumentation and not args.instrumentation.is_dir():
        print(f"instrumentation is not a directory: {args.instrumentation}", file=sys.stderr)
        return 2

    entries = package_cases(args.klee_campaign)
    entries = [entry for entry in entries if entry["config"].is_file()]
    if not entries:
        print(f"no .cfg cases found under {args.klee_campaign}", file=sys.stderr)
        return 1

    copied_records: list[dict[str, Any]] = []
    used_names: set[str] = set()
    cases_out = args.out / "cases"
    for entry in entries:
        origin = entry["origin"]
        config = entry["config"]
        source_record = entry["source_record"]
        preferred = f"{origin}_{config.name}"
        output_config_name = unique_name(used_names, preferred)
        output_config = cases_out / output_config_name
        copy_if_requested(config, output_config, args.dry_run)

        output_ir_name = ""
        ir = entry["ir"]
        if isinstance(ir, Path) and ir.is_file():
            output_ir_name = Path(output_config_name).with_suffix(".ll").name
            copy_if_requested(ir, cases_out / output_ir_name, args.dry_run)

        copied_records.append(
            {
                "case": Path(output_config_name).stem,
                "origin": origin,
                "config": output_config_name,
                "ir": output_ir_name,
                "marker": source_record.get("marker", ""),
                "gap_type": source_record.get("gap_type", ""),
                "source_config": str(config),
                "source_manifest": str(entry["source_manifest"] or ""),
            }
        )

    copied_patch = False
    if args.instrumentation:
        patch = args.instrumentation / "instrumentation.patch"
        if patch.exists():
            copy_if_requested(patch, args.out / "instrumentation" / "instrumentation.patch", args.dry_run)
            copied_patch = True
        for sidecar_name in ["instrumentation-manifest.jsonl", "instrumentation-candidates.json"]:
            sidecar = args.instrumentation / sidecar_name
            if sidecar.exists():
                copy_if_requested(sidecar, args.out / "instrumentation" / sidecar_name, args.dry_run)

    summary = {
        "klee_campaign": str(args.klee_campaign),
        "out": str(args.out),
        "dry_run": args.dry_run,
        "cases": len(copied_records),
        "origins": {
            "klee": sum(1 for record in copied_records if record["origin"] == "klee"),
            "backfill": sum(1 for record in copied_records if record["origin"] == "backfill"),
        },
        "instrumentation_patch": copied_patch,
    }

    if not args.dry_run:
        cases_out.mkdir(parents=True, exist_ok=True)
        with (cases_out / "source-manifest.jsonl").open("w", encoding="utf-8") as output:
            for record in copied_records:
                output.write(json.dumps(record, sort_keys=True) + "\n")
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "package-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
