#!/usr/bin/env python3
"""Extract O2T generator configs from KLEE test artifacts."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from cv_optimization_registry import markers_for_config


FIELDS = {
    "arith_opcode": "u8",
    "rhs_mode": "u8",
    "extra_opcode": "u8",
    "predicate": "u8",
    "shape": "u8",
    "feature_bits": "u8",
    "memory_shape": "u8",
    "pointer_mode": "u8",
    "store_mode": "u8",
    "load_use_mode": "u8",
    "loop_shape": "u8",
    "loop_trip_mode": "u8",
    "induction_mode": "u8",
    "loop_use_mode": "u8",
    "vector_shape": "u8",
    "global_shape": "u8",
    "const_a": "i32",
    "const_b": "i32",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dump-dir", type=Path)
    source.add_argument("--klee-out", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--reducer", type=Path)
    parser.add_argument("--ktest-tool", default="ktest-tool")
    parser.add_argument("--reduce", action="store_true")
    return parser.parse_args()


def run_ktest_tool(ktest_tool: str, ktest: Path) -> str:
    completed = subprocess.run(
        [ktest_tool, str(ktest)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def dump_texts(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.dump_dir is not None:
        dumps = sorted(args.dump_dir.glob("*.txt"))
        return [(dump.stem, dump.read_text()) for dump in dumps]

    ktests = sorted(args.klee_out.glob("*.ktest"))
    return [(ktest.stem, run_ktest_tool(args.ktest_tool, ktest)) for ktest in ktests]


def signed_value(value: int, size: int) -> int:
    bits = size * 8
    sign_bit = 1 << (bits - 1)
    mask = 1 << bits
    return value - mask if value & sign_bit else value


def value_from_hex(hex_text: str, kind: str, size: int) -> int:
    if not hex_text:
        return 0
    raw = bytes.fromhex(hex_text)
    if len(raw) != size:
        raw = raw[-size:].rjust(size, b"\x00")
    signed = kind.startswith("i")
    return int.from_bytes(raw, byteorder="little", signed=signed)


def value_from_bytes(raw: bytes, kind: str, size: int) -> int:
    if len(raw) != size:
        raw = raw[-size:].rjust(size, b"\x00")
    signed = kind.startswith("i")
    return int.from_bytes(raw, byteorder="little", signed=signed)


def parse_dump(text: str) -> dict[str, int]:
    objects: dict[str, dict[str, str | int]] = {}
    current: dict[str, str | int] | None = None

    name_re = re.compile(r"object\s+\d+:\s+name:\s+(?:b)?'([^']+)'")
    size_re = re.compile(r"object\s+\d+:\s+size:\s+(\d+)")
    hex_re = re.compile(r"object\s+\d+:\s+hex\s*:\s*0x([0-9a-fA-F]*)")
    int_re = re.compile(r"object\s+\d+:\s+int\s*:\s*(-?\d+)")
    data_re = re.compile(r"object\s+\d+:\s+data:\s+(b'.*')")

    for line in text.splitlines():
        if match := name_re.search(line):
            current = {"name": match.group(1)}
            objects[match.group(1)] = current
            continue
        if current is None:
            continue
        if match := size_re.search(line):
            current["size"] = int(match.group(1))
        elif match := hex_re.search(line):
            current["hex"] = match.group(1)
        elif match := int_re.search(line):
            current["int"] = int(match.group(1))
        elif match := data_re.search(line):
            data = ast.literal_eval(match.group(1))
            if not isinstance(data, bytes):
                raise ValueError("ktest data field did not parse as bytes")
            current["data"] = data

    result: dict[str, int] = {}
    optional_defaults = {
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
    }
    missing = sorted(set(FIELDS) - set(objects) - set(optional_defaults))
    if missing:
        raise ValueError(f"missing symbolic objects: {', '.join(missing)}")

    for field, kind in FIELDS.items():
        if field not in objects:
            result[field] = optional_defaults[field]
            continue
        obj = objects[field]
        size = int(obj.get("size", 1 if kind == "u8" else 4))
        if "int" in obj:
            value = int(obj["int"])
            if kind.startswith("i") and value >= (1 << (size * 8 - 1)):
                value = signed_value(value, size)
        elif "hex" in obj:
            value = value_from_hex(str(obj["hex"]), kind, size)
        elif "data" in obj:
            value = value_from_bytes(obj["data"], kind, size)
        else:
            raise ValueError(f"object {field} has no int, hex, or data value")
        result[field] = value

    return result


def write_raw_config(path: Path, values: dict[str, int]) -> None:
    key_order = [
        "arith_opcode",
        "rhs_mode",
        "extra_opcode",
        "predicate",
        "shape",
        "feature_bits",
        "memory_shape",
        "pointer_mode",
        "store_mode",
        "load_use_mode",
        "loop_shape",
        "loop_trip_mode",
        "induction_mode",
        "loop_use_mode",
        "vector_shape",
        "global_shape",
        "const_a",
        "const_b",
    ]
    with path.open("w") as output:
        for key in key_order:
            output.write(f"{key}={values[key]}\n")


def read_config(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = int(value.strip(), 0)
    return values


def coverage_markers(config: dict[str, int]) -> list[str]:
    return markers_for_config(config, mode="coverage")


def normalize_and_emit(
    replay: Path, raw_cfg: Path, normalized_cfg: Path, output_ll: Path
) -> None:
    subprocess.run(
        [
            str(replay),
            "--config",
            str(raw_cfg),
            "--out",
            str(output_ll),
            "--write-config",
            str(normalized_cfg),
        ],
        check=True,
    )
    if not normalized_cfg.exists() or not output_ll.exists():
        raise RuntimeError("cv-replay did not produce expected config and IR outputs")


def reduce_config(reducer: Path, input_cfg: Path, output_cfg: Path) -> None:
    subprocess.run(
        [
            str(reducer),
            "--config",
            str(input_cfg),
            "--out",
            str(output_cfg),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if not output_cfg.exists():
        raise RuntimeError("cv-reduce-config did not produce expected output")


def main() -> int:
    args = parse_args()
    if not args.replay.exists():
        print(f"cv-replay not found: {args.replay}", file=sys.stderr)
        return 1
    if args.reduce and args.reducer is None:
        print("--reduce requires --reducer", file=sys.stderr)
        return 1
    if args.reducer is not None and not args.reducer.exists():
        print(f"cv-reduce-config not found: {args.reducer}", file=sys.stderr)
        return 1

    args.cases.mkdir(parents=True, exist_ok=True)
    dumps = dump_texts(args)
    if not dumps:
        print("no KLEE test cases found", file=sys.stderr)
        return 1

    manifest_path = args.cases / "manifest.jsonl"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with manifest_path.open("w") as manifest:
            for case_name, text in dumps:
                case_id = case_name.removesuffix(".ktest")
                try:
                    raw_values = parse_dump(text)
                except ValueError as exc:
                    print(f"{case_name}: {exc}", file=sys.stderr)
                    return 1

                raw_cfg = tmp_path / f"{case_id}.raw.cfg"
                normalized_cfg = args.cases / f"{case_id}.cfg"
                output_ll = args.cases / f"{case_id}.ll"
                reduced_cfg = args.cases / f"{case_id}.reduced.cfg"
                reduced_ll = args.cases / f"{case_id}.reduced.ll"

                write_raw_config(raw_cfg, raw_values)
                try:
                    normalize_and_emit(args.replay, raw_cfg, normalized_cfg, output_ll)
                except (RuntimeError, subprocess.CalledProcessError) as exc:
                    print(f"{case_name}: replay failed: {exc}", file=sys.stderr)
                    return 1

                manifest_config = normalized_cfg
                manifest_ir = output_ll
                if args.reduce:
                    try:
                        reduce_config(args.reducer, normalized_cfg, reduced_cfg)
                        subprocess.run(
                            [
                                str(args.replay),
                                "--config",
                                str(reduced_cfg),
                                "--out",
                                str(reduced_ll),
                            ],
                            check=True,
                        )
                    except (RuntimeError, subprocess.CalledProcessError) as exc:
                        print(f"{case_name}: reduction failed: {exc}", file=sys.stderr)
                        return 1
                    manifest_config = reduced_cfg
                    manifest_ir = reduced_ll

                manifest_values = read_config(manifest_config)

                manifest.write(
                    json.dumps(
                        {
                            "case": case_id,
                            "config": manifest_config.name,
                            "ir": manifest_ir.name,
                            "coverage": coverage_markers(manifest_values),
                            "reduced": args.reduce,
                            "replay": f"{args.replay} --config {manifest_config}",
                            "source": case_name,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )

    print(f"wrote {len(dumps)} case(s) to {args.cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
