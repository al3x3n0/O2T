#!/usr/bin/env python3
"""Generate out-of-tree LLVM pass instrumentation artifacts."""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MINER = ROOT / "tools" / "cv-mine-pass-source.py"
DEFAULT_INSTRUMENTER = ROOT / "build" / "cv-instrument-pass-source"


def parse_csv(text: str | None) -> set[str]:
    if not text:
        return set()
    return {item.strip() for item in text.split(",") if item.strip()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--markers")
    parser.add_argument("--passes")
    parser.add_argument("--llm-findings", type=Path)
    parser.add_argument("--compile-commands", type=Path)
    parser.add_argument("--instrumenter", type=Path, default=DEFAULT_INSTRUMENTER)
    parser.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    return parser.parse_args()


def load_findings(miner: Path, paths: list[Path]) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [str(miner), *[str(path) for path in paths]],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    data = json.loads(completed.stdout)
    if not isinstance(data, list):
        raise ValueError("miner output was not a JSON array")
    return [record for record in data if isinstance(record, dict)]


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []
    return [
        record
        for record in (json.loads(line) for line in text.splitlines() if line.strip())
        if isinstance(record, dict)
    ]


def finding_key(record: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(Path(str(record.get("file", ""))).resolve()),
        int(record.get("line") or 0),
        str(record.get("marker", "")),
    )


def merge_findings(
    static_findings: list[dict[str, Any]],
    llm_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for source_name, findings in [("static", static_findings), ("llm", llm_findings)]:
        for finding in findings:
            record = dict(finding)
            record.setdefault("finding_source", source_name)
            key = finding_key(record)
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
    return merged


def filter_findings(
    findings: list[dict[str, Any]], markers: set[str], passes: set[str]
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for finding in findings:
        marker = str(finding.get("marker", ""))
        pass_name = str(finding.get("pass", ""))
        if markers and marker not in markers:
            continue
        if passes and pass_name not in passes:
            continue
        selected.append(finding)
    return selected


def common_source_root(paths: list[Path]) -> Path:
    resolved = [path.resolve() for path in paths]
    roots = [path if path.is_dir() else path.parent for path in resolved]
    return Path(__import__("os").path.commonpath([str(path) for path in roots]))


def relative_to_root(path: Path, source_root: Path) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(source_root)
    except ValueError:
        return Path(resolved.name)


def manifest_record(
    finding: dict[str, Any],
    source_root: Path,
    out_dir: Path,
    status: str,
    message: str = "",
) -> dict[str, Any]:
    original = Path(str(finding.get("file", ""))).resolve()
    relative = relative_to_root(original, source_root)
    rewritten = out_dir / "rewritten" / relative
    return {
        "original_file": str(original),
        "rewritten_file": str(rewritten),
        "marker": finding.get("marker", ""),
        "line": finding.get("line"),
        "matched_pattern": finding.get("matched_pattern", ""),
        "pass": finding.get("pass", ""),
        "predicate_kind": finding.get("predicate_kind", ""),
        "finding_source": finding.get("finding_source", ""),
        "patch": str(out_dir / "instrumentation.patch"),
        "status": status,
        "message": message,
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def compile_commands_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.resolve().parent if path.name == "compile_commands.json" else path.resolve()


def run_instrumenter(
    instrumenter: Path,
    source_file: Path,
    markers: set[str],
    compile_commands: Path | None,
    candidate_file: Path | None,
) -> subprocess.CompletedProcess[str]:
    command = [str(instrumenter)]
    if markers:
        command.append("--markers=" + ",".join(sorted(markers)))
    if candidate_file is not None:
        command.append("--candidate-file=" + str(candidate_file))
    if compile_commands is not None:
        command.extend(["-p", str(compile_commands)])
    command.append(str(source_file))
    command.extend(["--", "-std=c++17", f"-I{ROOT / 'include'}"])
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def copy_unmodified_inputs(selected_files: list[Path], source_root: Path, out_dir: Path) -> None:
    for source in selected_files:
        relative = relative_to_root(source, source_root)
        target = out_dir / "original" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def write_rewritten_file(out_dir: Path, source_root: Path, source: Path, text: str) -> Path:
    relative = relative_to_root(source, source_root)
    target = out_dir / "rewritten" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def write_candidate_file(
    out_dir: Path,
    source_root: Path,
    source: Path,
    findings: list[dict[str, Any]],
) -> Path:
    relative = relative_to_root(source, source_root)
    target = out_dir / "candidates" / relative.with_suffix(relative.suffix + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n")
    return target


def generate_patch(source_root: Path, rewrites: dict[Path, Path], patch_path: Path) -> None:
    with patch_path.open("w") as patch:
        for original, rewritten in sorted(rewrites.items(), key=lambda item: str(item[0])):
            relative = relative_to_root(original, source_root)
            original_lines = original.read_text(errors="replace").splitlines(keepends=True)
            rewritten_lines = rewritten.read_text(errors="replace").splitlines(keepends=True)
            patch.writelines(
                difflib.unified_diff(
                    original_lines,
                    rewritten_lines,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )


def main() -> int:
    args = parse_args()
    markers = parse_csv(args.markers)
    passes = parse_csv(args.passes)

    if not args.miner.exists():
        print(f"miner not found: {args.miner}", file=sys.stderr)
        return 1
    if args.llm_findings and not args.llm_findings.is_file():
        print(f"LLM findings file does not exist: {args.llm_findings}", file=sys.stderr)
        return 1

    try:
        static_findings = load_findings(args.miner, args.paths)
        llm_findings = load_records(args.llm_findings) if args.llm_findings else []
        findings = merge_findings(static_findings, llm_findings)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"failed to mine sources: {exc}", file=sys.stderr)
        return 1

    selected = filter_findings(findings, markers, passes)
    source_root = common_source_root(args.paths)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "instrumentation-manifest.jsonl"
    candidate_path = args.out_dir / "instrumentation-candidates.json"
    patch_path = args.out_dir / "instrumentation.patch"

    candidate_path.write_text(json.dumps(selected, indent=2, sort_keys=True) + "\n")

    if args.dry_run:
        records = [
            manifest_record(finding, source_root, args.out_dir, "candidate")
            for finding in selected
        ]
        write_jsonl(manifest_path, records)
        patch_path.write_text("")
        print(f"found {len(selected)} candidate(s)")
        print(f"Manifest: {manifest_path}")
        return 0

    if not args.instrumenter.exists():
        print(f"instrumenter not found: {args.instrumenter}", file=sys.stderr)
        return 1

    by_file: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for finding in selected:
        by_file[Path(str(finding["file"])).resolve()].append(finding)

    selected_files = sorted(by_file)
    copy_unmodified_inputs(selected_files, source_root, args.out_dir)

    records: list[dict[str, Any]] = []
    rewrites: dict[Path, Path] = {}
    compile_dir = compile_commands_dir(args.compile_commands)

    for source_file, file_findings in sorted(by_file.items()):
        file_markers = {str(finding["marker"]) for finding in file_findings}
        candidate_file = write_candidate_file(
            args.out_dir, source_root, source_file, file_findings
        )
        completed = run_instrumenter(
            args.instrumenter.resolve(),
            source_file,
            file_markers,
            compile_dir,
            candidate_file,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or "instrumenter failed"
            records.extend(
                manifest_record(finding, source_root, args.out_dir, "error", message)
                for finding in file_findings
            )
            continue

        rewritten_path = write_rewritten_file(
            args.out_dir, source_root, source_file, completed.stdout
        )
        original_text = source_file.read_text(errors="replace")
        changed = completed.stdout != original_text
        if changed:
            rewrites[source_file] = rewritten_path
        records.extend(
            manifest_record(
                finding,
                source_root,
                args.out_dir,
                "rewritten" if changed else "skipped",
                "" if changed else "no candidate predicate matched",
            )
            for finding in file_findings
        )

    generate_patch(source_root, rewrites, patch_path)
    write_jsonl(manifest_path, records)

    failures = sum(1 for record in records if record["status"] == "error")
    print(f"processed {len(selected)} candidate(s) in {len(by_file)} file(s)")
    print(f"Patch: {patch_path}")
    print(f"Manifest: {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
