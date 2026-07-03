#!/usr/bin/env python3
"""Run provider-specific LLM commands over O2T prompt bundles."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--keep-going", action="store_true")
    return parser.parse_args()


def load_prompts(path: Path) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"prompt line {line_no} is not a JSON object")
        prompts.append(record)
    return prompts


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, sort_keys=True) + "\n")


def error_record(index: int, prompt: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    record = {
        "bundle_index": index,
        "source_file": prompt.get("source_file", ""),
        "source_start_line": prompt.get("source_start_line", 0),
        "reason": reason,
    }
    record.update(extra)
    return record


def main() -> int:
    args = parse_args()
    raw_dir = args.out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    responses_path = args.out_dir / "responses.jsonl"
    errors_path = args.out_dir / "runner-errors.jsonl"
    responses_path.write_text("", encoding="utf-8")
    errors_path.write_text("", encoding="utf-8")

    try:
        prompts = load_prompts(args.prompts)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    responses = 0
    errors = 0
    for index, prompt in enumerate(prompts):
        prompt_text = json.dumps(prompt, sort_keys=True)
        completed = subprocess.run(
            args.command,
            input=prompt_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
            check=False,
        )

        raw_path = raw_dir / f"bundle-{index:04d}.json"
        raw_path.write_text(completed.stdout, encoding="utf-8")

        if completed.returncode != 0:
            append_jsonl(
                errors_path,
                error_record(
                    index,
                    prompt,
                    "command failed",
                    exit_code=completed.returncode,
                    stderr=completed.stderr,
                    raw_response=str(raw_path),
                ),
            )
            errors += 1
            if not args.keep_going:
                print(
                    f"bundle {index} failed with exit code {completed.returncode}",
                    file=sys.stderr,
                )
                return 1
            continue

        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            append_jsonl(
                errors_path,
                error_record(
                    index,
                    prompt,
                    "invalid JSON response",
                    stderr=completed.stderr,
                    json_error=str(exc),
                    raw_response=str(raw_path),
                ),
            )
            errors += 1
            if not args.keep_going:
                print(f"bundle {index} produced invalid JSON: {exc}", file=sys.stderr)
                return 1
            continue

        append_jsonl(responses_path, response if isinstance(response, dict) else {"candidates": response})
        responses += 1

    print(f"wrote {responses} response(s) to {responses_path}")
    if errors:
        print(f"recorded {errors} runner error(s) in {errors_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
