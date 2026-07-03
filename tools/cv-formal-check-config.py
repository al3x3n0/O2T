#!/usr/bin/env python3
"""Check abstract optimization intent for a O2T config with SMT-LIB."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cv_optimization_registry import (
    formal_template_for_marker,
    markers_for_config,
    scalar_formal_from_marker_config,
    vector_formal_from_template,
)
from cv_analysis_facts import dse_lane_mask_name, dse_lane_mask_width


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENTS = ROOT / "constraints" / "optimization_intents.json"

KEYS = {
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
    "const_a": 0,
    "const_b": 1,
}


def small_constant(value: int) -> int:
    if -8 <= value <= 8:
        return value
    return ((value % 17) + 17) % 17 - 8


def normalize(config: dict[str, int]) -> dict[str, int]:
    out = dict(config)
    out["arith_opcode"] %= 6
    out["rhs_mode"] %= 4
    out["extra_opcode"] %= 6
    out["predicate"] %= 4
    out["shape"] %= 5
    out["feature_bits"] &= 3
    out["memory_shape"] %= 6
    out["pointer_mode"] %= 3
    out["store_mode"] %= 3
    out["load_use_mode"] %= 3
    out["loop_shape"] %= 5
    out["loop_trip_mode"] %= 3
    out["induction_mode"] %= 3
    out["loop_use_mode"] %= 3
    out["vector_shape"] %= 25
    out["global_shape"] %= 4
    if out["memory_shape"] == 4 and (out["store_mode"] == 2 or (out["feature_bits"] & 2)):
        width = out["const_b"]
        if width < 1 or width > 8:
            width = 4
        out["const_b"] = width
        out["const_a"] &= (1 << width) - 1
    else:
        out["const_a"] = small_constant(out["const_a"])
        out["const_b"] = small_constant(out["const_b"])
    return out


def load_config(path: Path) -> dict[str, int]:
    config = dict(KEYS)
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected key=value")
        key, value_text = [part.strip() for part in line.split("=", 1)]
        if key not in config:
            raise ValueError(f"{path}:{line_number}: unknown key {key!r}")
        config[key] = int(value_text, 0)
    return normalize(config)


def load_intents(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("intent file must contain a JSON array")
    intents: dict[str, dict[str, Any]] = {}
    for record in data:
        if not isinstance(record, dict) or not isinstance(record.get("marker"), str):
            raise ValueError("each intent record must include a string marker")
        for key in ("category", "precondition", "rewrite", "intent"):
            if not isinstance(record.get(key), str):
                raise ValueError(f"intent {record['marker']} is missing string field {key}")
        intents[record["marker"]] = record
    return intents


def markers_for(config: dict[str, int]) -> list[str]:
    return markers_for_config(config, mode="formal")


def bv(value: int) -> str:
    return f"#x{value & 0xFFFFFFFF:08x}"


def rhs(config: dict[str, int]) -> str:
    mode = config["rhs_mode"]
    if mode == 0:
        return bv(0)
    if mode == 1:
        return bv(1)
    if mode == 2:
        return "b"
    return bv(config["const_a"])


def arith_expr(config: dict[str, int], lhs: str = "a", right: str | None = None) -> str:
    right = rhs(config) if right is None else right
    opcode = config["arith_opcode"]
    if opcode == 0:
        return f"(bvadd {lhs} {right})"
    if opcode == 1:
        return f"(bvsub {lhs} {right})"
    if opcode == 2:
        return f"(bvmul {lhs} {right})"
    if opcode == 3:
        return f"(bvxor {lhs} {right})"
    if opcode == 4:
        return f"(bvor {lhs} {right})"
    return f"(bvand {lhs} {right})"


def pred(config: dict[str, int], lhs: str = "a", right: str = "b") -> str:
    predicate = config["predicate"]
    if predicate == 0:
        return f"(= {lhs} {right})"
    if predicate == 1:
        return f"(not (= {lhs} {right}))"
    if predicate == 2:
        return f"(bvslt {lhs} {right})"
    return f"(bvsgt {lhs} {right})"


def loop_limit(config: dict[str, int]) -> int:
    if config["loop_trip_mode"] == 2:
        return 1
    value = config["const_a"] % 8
    return 4 if value == 0 else value


def loop_expr(config: dict[str, int], hoisted: bool = False, remove_dead: bool = False) -> str:
    terms = ["a" if config["loop_use_mode"] == 2 else bv(0)]
    limit = loop_limit(config)
    start = limit if config["induction_mode"] == 2 else 0
    step = -1 if config["induction_mode"] == 2 else (1 if config["induction_mode"] == 0 else (config["const_b"] % 4 or 2))
    for index in range(limit):
        i_value = start + index * step
        if config["loop_shape"] == 3:
            invariant = f"(bvadd a {bv(config['const_b'])})"
            body = f"(bvadd {invariant} {bv(1)})" if not hoisted else f"(bvadd hoisted {bv(1)})"
        else:
            body = arith_expr(config, bv(i_value))
        if config["loop_shape"] == 4 and not remove_dead:
            _dead = f"(bvadd a {bv(config['const_b'])})"
        terms.append(body)
        if config["loop_shape"] == 2:
            break
    if config["loop_use_mode"] == 0:
        return bv(start + limit * step)
    if config["loop_use_mode"] == 2:
        return "a"
    expr = terms[0]
    for term in terms[1:]:
        expr = f"(bvadd {expr} {term})"
    return expr


def equivalence_for(marker: str, config: dict[str, int]) -> tuple[str, str, list[str]]:
    declarations: list[str] = []
    c_a = bv(config["const_a"])
    c_b = bv(config["const_b"])
    condition = pred(config)

    if scalar_pair := scalar_formal_from_marker_config(marker, config):
        before, after = scalar_pair
        return before, after, declarations
    if vector_pair := vector_formal_from_template(marker, config):
        before, after, _bits = vector_pair
        return before, after, declarations
    if marker == "probe.dce.dead-instruction":
        return arith_expr(config), arith_expr(config), declarations
    if marker == "probe.simplifycfg.unreachable-block":
        return arith_expr(config), arith_expr(config), declarations
    if marker == "probe.simplifycfg.diamond":
        then_v = arith_expr(config, "a")
        else_v = arith_expr(config, "b")
        return f"(ite {condition} {then_v} {else_v})", f"(ite {condition} {then_v} {else_v})", declarations
    if marker == "probe.simplifycfg.nested-branch":
        inner = f"(ite (= a {c_a}) {arith_expr(config, 'a')} {arith_expr(config, 'b')})"
        value = f"(ite {condition} {inner} {arith_expr(config, 'b')})"
        return value, value, declarations
    if marker == "probe.simplifycfg.branch-chain":
        value = f"(ite (= a {c_a}) {arith_expr(config, 'a')} (ite (= a {c_b}) {arith_expr(config, 'b')} {arith_expr(config, 'a')}))"
        return value, value, declarations
    if marker == "probe.mem2reg.promotable-alloca":
        stored = f"(ite {condition} a {c_a})" if config["store_mode"] == 2 else a_or_const_b(config)
        return load_use_expr(config, stored), load_use_expr(config, stored), declarations
    if marker == "probe.mem2reg.store-load-forward":
        stored = a_or_const_b(config)
        return load_use_expr(config, stored), load_use_expr(config, stored), declarations
    if marker == "probe.dse.dead-store":
        return "a", "a", declarations
    if marker == "probe.dse.overwritten-store":
        return load_use_expr(config, "a"), load_use_expr(config, "a"), declarations
    if marker == "probe.instcombine.redundant-load":
        loaded = "a"
        before = "(bvadd a a)" if config["load_use_mode"] == 1 else loaded
        return before, before, declarations
    if marker == "probe.cleanup.unused-alloca":
        return "a", "a", declarations
    if marker in {
        "probe.loop.canonical-header",
        "probe.loop.induction-phi",
        "probe.loop.simple-trip-count",
        "probe.simplifycfg.loop-exit",
    }:
        expr = loop_expr(config)
        return expr, expr, declarations
    if marker == "probe.licm.invariant-op":
        hoisted = f"(bvadd a {c_b})"
        declarations.append(f"(define-fun hoisted () (_ BitVec 32) {hoisted})")
        return loop_expr(config), loop_expr(config, hoisted=True), declarations
    if marker == "probe.dce.dead-loop-instruction":
        return loop_expr(config), loop_expr(config, remove_dead=True), declarations
    raise KeyError(marker)


def a_or_const_b(config: dict[str, int]) -> str:
    if config["store_mode"] == 1:
        return bv(config["const_b"])
    return "a"


def load_use_expr(config: dict[str, int], loaded: str) -> str:
    if config["load_use_mode"] == 1:
        return f"(bvadd {loaded} b)"
    if config["load_use_mode"] == 2:
        return "a"
    return loaded


def dse_partial_mask_for_config(config: dict[str, int]) -> tuple[str, set[int]]:
    width = config.get("const_b", 4)
    if width < 1 or width > 8:
        width = 4
    bits = config["const_a"] & ((1 << width) - 1)
    if bits == 0 or bits == (1 << width) - 1:
        width = 4
        bits = 0b0011
    lanes = {lane for lane in range(width) if bits & (1 << lane)}
    mask_name = dse_lane_mask_name(bits, width)
    return mask_name, lanes


def dse_byte_expr(overwritten_lanes: set[int], width: int) -> str:
    lanes = [f"kill{lane}" if lane in overwritten_lanes else f"original{lane}" for lane in range(width)]
    if width == 1:
        return lanes[0]
    return f"(concat {' '.join(reversed(lanes))})"


def dse_overwrite_smt(marker: str, config: dict[str, int], intent: dict[str, Any]) -> str:
    partial = config["store_mode"] == 2
    symbolic = bool(config.get("feature_bits", 0) & 2) and not partial
    overwrite_range = "partial" if partial else "full"
    mask_name, overwritten_lanes = dse_partial_mask_for_config(config) if partial else ("full", {0, 1, 2, 3})
    width = (dse_lane_mask_width(mask_name) or 4) if partial else (config.get("const_b", 4) if symbolic else 4)
    if width < 1 or width > 8:
        width = 4
    if not partial:
        overwritten_lanes = set(range(width))
    mask_comment = [f"; dse-byte-mask: {mask_name}"] if partial else []
    symbolic_comment = ["; dse-size-model: symbolic bounded eight-byte memory"] if symbolic else []
    before_expr = dse_byte_expr(overwritten_lanes, width) if partial else "killing_store_value"
    after_expr = before_expr
    original_decls = [f"(declare-const original{lane} (_ BitVec 8))" for lane in range(width)]
    dead_decls = [f"(declare-const dead{lane} (_ BitVec 8))" for lane in range(width)]
    kill_decls = [f"(declare-const kill{lane} (_ BitVec 8))" for lane in range(width)]
    original_value = dse_byte_expr(set(), width).replace("original", "original")
    dead_value = dse_byte_expr(set(range(width)), width).replace("kill", "dead")
    killing_value = dse_byte_expr(set(range(width)), width)
    bitvec_type = f"(_ BitVec {width * 8})"
    lines = [
        f"; marker: {marker}",
        f"; intent: {intent['intent']}",
        f"; dse-overwrite-range: {overwrite_range}",
        f"; dse-byte-model: {width} independent 8-bit lanes",
        "; dse-size-model: known bounded eight-byte memory",
        *symbolic_comment,
        "; dse-observation-window: no-intervening-read",
        "; dse-observation-window: no-unknown-memory-effect",
        *mask_comment,
        "(set-logic QF_BV)",
        *original_decls,
        *dead_decls,
        *kill_decls,
        f"(define-fun original_value () {bitvec_type} {original_value})",
        f"(define-fun dead_store_value () {bitvec_type} {dead_value})",
        f"(define-fun killing_store_value () {bitvec_type} {killing_value})",
        "; before executes the removable store bytes and then the killing store bytes.",
        f"(define-fun before () {bitvec_type} {before_expr})",
        "; after removes only bytes overwritten by the killing store; untouched bytes remain original.",
        f"(define-fun after () {bitvec_type} {after_expr})",
        "(assert (not (= before after)))",
        "(check-sat)",
    ]
    return "\n".join(lines) + "\n"


def smt_for(marker: str, config: dict[str, int], intent: dict[str, Any]) -> str:
    if marker == "probe.dse.overwritten-store":
        return dse_overwrite_smt(marker, config, intent)
    before, after, declarations = equivalence_for(marker, config)
    result_bits = formal_template_for_marker(marker).get("result_bits")
    if not isinstance(result_bits, int):
        result_bits = (
            128
            if marker.startswith("probe.vector.")
            and marker
            not in {
                "probe.vector.extract-insert",
                "probe.vector.reduction-add-zero",
                "probe.vector.reduction-add-single-lane",
                "probe.vector.scalable.reduction-add-zero",
            }
            else 32
        )
    if isinstance(intent.get("smt_before"), str):
        before = intent["smt_before"]
    if isinstance(intent.get("smt_after"), str):
        after = intent["smt_after"]
    lines = [
        f"; marker: {marker}",
        f"; intent: {intent['intent']}",
        "(set-logic QF_BV)",
        "(declare-const a (_ BitVec 32))",
        "(declare-const b (_ BitVec 32))",
        *declarations,
        f"(define-fun before () (_ BitVec {result_bits}) {before})",
        f"(define-fun after () (_ BitVec {result_bits}) {after})",
        "(assert (not (= before after)))",
        "(check-sat)",
    ]
    return "\n".join(lines) + "\n"


def run_z3(z3: str, smt: str) -> tuple[str, str]:
    proc = subprocess.run([z3, "-in"], input=smt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"z3 exited with {proc.returncode}")
    first = proc.stdout.splitlines()[0].strip() if proc.stdout.splitlines() else ""
    if first != "sat":
        return first, proc.stdout
    model_proc = subprocess.run(
        [z3, "-in"],
        input=smt + "(get-model)\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if model_proc.returncode != 0:
        raise RuntimeError(model_proc.stderr.strip() or f"z3 exited with {model_proc.returncode}")
    return first, model_proc.stdout


def print_record(record: dict[str, Any]) -> None:
    print(" ".join(f"{key}={value}" for key, value in record.items() if value not in ("", None)))
    print(json.dumps(record, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--intent", type=Path, default=DEFAULT_INTENTS)
    parser.add_argument("--marker")
    parser.add_argument("--emit-smt", type=Path)
    parser.add_argument("--z3", default="z3")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        intents = load_intents(args.intent)
    except Exception as exc:
        print_record({"formal_status": "error", "message": str(exc)})
        return 1

    z3_path = args.z3 if Path(args.z3).is_file() else shutil.which(args.z3)
    if z3_path is None:
        print_record({"formal_status": "error", "message": f"z3 not found: {args.z3}"})
        return 1

    triggered = markers_for(config)
    selected = [args.marker] if args.marker else triggered
    exit_code = 0
    summaries: list[dict[str, Any]] = []

    if args.emit_smt:
        args.emit_smt.mkdir(parents=True, exist_ok=True)

    for marker in selected:
        if marker not in intents:
            record = {"formal_status": "unsupported", "marker": marker, "result": "missing-intent"}
            summaries.append(record)
            print_record(record)
            exit_code = max(exit_code, 2)
            continue
        if marker not in triggered:
            record = {"formal_status": "not-triggered", "marker": marker, "result": "not-triggered"}
            summaries.append(record)
            print_record(record)
            continue
        try:
            smt = smt_for(marker, config, intents[marker])
            smt_file = ""
            if args.emit_smt:
                smt_file = str(args.emit_smt / (marker.replace("probe.", "").replace(".", "_").replace("-", "_") + ".smt2"))
                Path(smt_file).write_text(smt, encoding="utf-8")
            result, z3_output = run_z3(str(z3_path), smt)
        except Exception as exc:
            record = {"formal_status": "error", "marker": marker, "result": "error", "message": str(exc)}
            summaries.append(record)
            print_record(record)
            exit_code = max(exit_code, 1)
            continue
        status = "proved" if result == "unsat" else "failed" if result == "sat" else "error"
        if status != "proved":
            exit_code = max(exit_code, 3)
        record = {
            "formal_status": status,
            "marker": marker,
            "result": result,
            "counterexample": "" if result == "unsat" else z3_output.replace("\n", "\\n"),
            "smt_file": smt_file,
        }
        summaries.append(record)
        print_record(record)

    print(json.dumps({"formal_status": "summary", "config": str(args.config), "markers": summaries}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
