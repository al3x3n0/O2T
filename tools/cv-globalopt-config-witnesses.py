#!/usr/bin/env python3
"""Emit GlobalOpt before/after witness contracts from targeted configs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from cv_globalopt_witness import (
    MARKER,
    WITNESS_MODEL,
    compact_witness,
    global_initializer_contract_details,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPLAY = ROOT / "build" / "cv-replay"
DEFAULT_VERIFIER = ROOT / "tools" / "cv-verify-globalopt-witness-contract.py"


CASES = {
    1: {
        "name": "i32",
        "before_global": "@cv_dead_init = internal global i32 42",
        "after_global": "@cv_dead_init = internal global i32 0",
        "preamble": [],
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    },
    2: {
        "name": "ptr",
        "before_global": "@cv_dead_init = internal global ptr @cv_target",
        "after_global": "@cv_dead_init = internal global ptr null",
        "preamble": ["@cv_target = internal global i32 7"],
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    },
    3: {
        "name": "array",
        "before_global": "@cv_dead_init = internal global [2 x i32] [i32 1, i32 2]",
        "after_global": "@cv_dead_init = internal global [2 x i32] zeroinitializer",
        "preamble": [],
        "function": "define i32 @cv_observe(i32 %x) {\nentry:\n  ret i32 %x\n}",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--verify-contracts", action="store_true")
    parser.add_argument("--contract-out", type=Path)
    parser.add_argument("--contract-verifier", type=Path, default=DEFAULT_VERIFIER)
    parser.add_argument("--z3")
    parser.add_argument("--require-clean", action="store_true")
    return parser.parse_args()


def read_config(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected key=value")
        key, value = [part.strip() for part in line.split("=", 1)]
        values[key] = int(value, 0)
    return values


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def filename_key(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value)
    return cleaned.strip("_") or "globalopt"


def witness_ir(case: dict[str, Any], global_line: str) -> str:
    lines = [
        "; O2T GlobalOpt dead initializer witness",
        f"; witness_model: {WITNESS_MODEL}",
    ]
    lines.extend(str(line) for line in case.get("preamble", []))
    lines.extend([global_line, "", str(case.get("function") or ""), ""])
    return "\n".join(lines)


def replay_before_ir(replay: Path, config: Path, fallback: str) -> str:
    if not replay.is_file():
        return fallback
    proc = subprocess.run(
        [str(replay), "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 and proc.stdout.strip() else fallback


def emit_case(
    config_path: Path,
    shape: int,
    witness_root: Path,
    replay: Path,
) -> dict[str, Any]:
    case = CASES[shape]
    name = str(case["name"])
    stem = filename_key(config_path.stem)
    before_path = witness_root / f"{stem}.{name}.before.ll"
    after_path = witness_root / f"{stem}.{name}.after.ll"
    fallback_before = witness_ir(case, str(case["before_global"]))
    before = replay_before_ir(replay, config_path, fallback_before)
    after = before.replace(str(case["before_global"]), str(case["after_global"]), 1)
    if after == before:
        after = witness_ir(case, str(case["after_global"]))
    before_path.write_text(before, encoding="utf-8")
    after_path.write_text(after, encoding="utf-8")
    errors, details = global_initializer_contract_details(before, after, case)
    return {
        "name": name,
        "config": str(config_path),
        "before": str(before_path),
        "after": str(after_path),
        "status": "failed" if errors else "passed",
        "structural_checks": "failed" if errors else "passed",
        "structural_details": details,
        "failure_reasons": errors,
    }


def contract_command(args: argparse.Namespace, input_path: Path, contract_out: Path) -> list[str]:
    command = [
        sys.executable,
        str(args.contract_verifier),
        "--input",
        str(input_path),
        "--out",
        str(contract_out / "globalopt-witness-contract-verification.json"),
        "--report",
        str(contract_out / "globalopt-witness-contract-verification.txt"),
        "--emit-smt",
        str(contract_out / "smt"),
    ]
    if args.z3:
        command.extend(["--z3", args.z3])
    if args.require_clean:
        command.append("--require-clean")
    return command


def main() -> int:
    args = parse_args()
    if not args.cases_dir.is_dir():
        print(f"cases directory does not exist: {args.cases_dir}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    witness_root = args.out / "witnesses"
    witness_root.mkdir(parents=True, exist_ok=True)

    configs = sorted(args.cases_dir.glob("*.cfg"))
    cases: list[dict[str, Any]] = []
    skipped = 0
    for config_path in configs:
        try:
            config = read_config(config_path)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        shape = int(config.get("global_shape", 0)) % 4
        if shape == 0:
            skipped += 1
            continue
        cases.append(emit_case(config_path, shape, witness_root, args.replay))

    if not cases:
        print("no GlobalOpt witness configs found", file=sys.stderr)
        return 1 if args.strict else 0

    required_cases = ["i32", "ptr", "array"]
    status_by_name = {str(case.get("name") or ""): str(case.get("status") or "unset") for case in cases}
    missing_required_cases = [case for case in required_cases if status_by_name.get(case) != "passed"]
    errors = [reason for case in cases for reason in case.get("failure_reasons", []) if str(reason)]
    errors.extend(f"{case}-required-witness-case-missing" for case in missing_required_cases)
    primary = cases[0]
    witness = {
        "key": f"globalopt-config-witnesses|{args.cases_dir}",
        "marker": MARKER,
        "file": str(args.cases_dir),
        "line": 0,
        "status": "failed" if errors else "passed",
        "before": str(primary.get("before") or ""),
        "after": str(primary.get("after") or ""),
        "witness_model": WITNESS_MODEL,
        "required_cases": required_cases,
        "missing_required_cases": missing_required_cases,
        "cases": cases,
        "structural_checks": "passed" if all(case["structural_checks"] == "passed" for case in cases) else "failed",
        "failure_reasons": errors,
    }
    witness["witness_contract"] = compact_witness(witness, required_cases)["witness_contract"]
    result = {
        "model": "o2t-globalopt-config-witnesses-v1",
        "cases_dir": str(args.cases_dir),
        "skipped": skipped,
        "witnesses": {
            "enabled": True,
            "total": 1,
            "passed": 0 if errors else 1,
            "failed": 1 if errors else 0,
            "required_cases": required_cases,
            "records": [witness],
        },
    }
    output_json = args.out / "globalopt-witnesses.json"
    write_json(output_json, result)

    if args.verify_contracts:
        contract_out = args.contract_out or (args.out / "contract")
        contract_out.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(contract_command(args, output_json, contract_out), check=False)
        if completed.returncode != 0:
            return completed.returncode

    print(f"generated {len(cases)} GlobalOpt witness case(s) in {witness_root}")
    print(f"witness_json: {output_json}")
    return 1 if errors and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
