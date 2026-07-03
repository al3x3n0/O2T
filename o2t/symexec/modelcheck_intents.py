#!/usr/bin/env python3
"""Generate and run CBMC/ESBMC harnesses from validated scalar/CFG intent records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from o2t.prove import multiwidth as MW
from o2t.symexec import modelcheck as M

ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_EQUIVALENCE = {
    "scalar-bv32": "result",
    "cfg-bv32": "reachable-result",
}
SUPPORTED_WIDTHS = {1, 8, 16, 32, 64}


class UnsupportedIntent(ValueError):
    """Raised for a formal intent shape the model-checker generator does not cover."""


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_ident(value: str, fallback: str = "record") -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not out:
        out = fallback
    if not out:
        return ""
    if out[0].isdigit():
        out = "_" + out
    return out


def parse_widths(value: str | None) -> list[int] | None:
    value = value.strip() if isinstance(value, str) else value
    if value is None or value == "" or value == "native":
        return None
    widths: list[int] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            width = int(raw)
        except ValueError as exc:
            raise UnsupportedIntent(f"unsupported-width:{raw}") from exc
        if width not in SUPPORTED_WIDTHS:
            raise UnsupportedIntent(f"unsupported-width:{width}")
        if width not in widths:
            widths.append(width)
    if not widths:
        raise UnsupportedIntent("unsupported-widths")
    return widths


def record_formal(record: dict[str, Any]) -> dict[str, Any]:
    intent = record.get("intent_candidate")
    formal = intent.get("formal") if isinstance(intent, dict) else None
    if not isinstance(formal, dict):
        raise UnsupportedIntent("missing-formal")
    domain = str(formal.get("domain") or "")
    expected_equivalence = SUPPORTED_EQUIVALENCE.get(domain)
    if expected_equivalence is None:
        raise UnsupportedIntent(f"unsupported-domain:{formal.get('domain') or 'unset'}")
    if formal.get("equivalence") != expected_equivalence:
        raise UnsupportedIntent(f"unsupported-equivalence:{formal.get('equivalence') or 'unset'}")
    variables = formal.get("variables")
    if not isinstance(variables, list) or not variables or not all(isinstance(v, str) for v in variables):
        raise UnsupportedIntent("unsupported-variables")
    variable_bits = formal.get("variable_bits") or {}
    if not isinstance(variable_bits, dict):
        raise UnsupportedIntent("unsupported-variable-bits")
    for name, bits in variable_bits.items():
        if not isinstance(name, str) or name not in variables:
            raise UnsupportedIntent("unsupported-variable-bits")
        if bits not in SUPPORTED_WIDTHS:
            raise UnsupportedIntent(f"unsupported-variable-width:{bits}")
    widths = {int(variable_bits.get(name, 32)) for name in variables}
    if len(widths) != 1:
        raise UnsupportedIntent("unsupported-mixed-variable-widths")
    if formal.get("variable_sorts"):
        raise UnsupportedIntent("unsupported-variable-sorts")
    return formal


def formal_width(formal: dict[str, Any]) -> int:
    variable_bits = formal.get("variable_bits") or {}
    variables = formal.get("variables") or []
    widths = {int(variable_bits.get(name, 32)) for name in variables}
    if len(widths) != 1:
        raise UnsupportedIntent("unsupported-mixed-variable-widths")
    width = next(iter(widths))
    if width not in SUPPORTED_WIDTHS:
        raise UnsupportedIntent(f"unsupported-variable-width:{width}")
    return width


def domain_at_width(domain: str, width: int) -> str:
    if domain.startswith("scalar-bv"):
        return f"scalar-bv{width}"
    if domain.startswith("cfg-bv"):
        return f"cfg-bv{width}"
    if domain.startswith("loop-bv"):
        return f"loop-bv{width}"
    return domain


def flags(expr: dict[str, Any]) -> tuple[bool, bool, bool]:
    raw = expr.get("flags") or []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise UnsupportedIntent("unsupported-flags")
    allowed = {"nsw", "nuw", "exact"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise UnsupportedIntent("unsupported-flags:" + ",".join(unknown))
    return "nsw" in raw, "nuw" in raw, "exact" in raw


def expr_cpp(expr: Any, variables: set[str], default_width: int) -> str:
    if not isinstance(expr, dict):
        raise UnsupportedIntent("unsupported-expression")
    op = expr.get("op")
    if op == "var":
        name = expr.get("name")
        if not isinstance(name, str) or name not in variables:
            raise UnsupportedIntent("unsupported-variable-ref")
        return safe_ident(name, "v")
    if op == "bvconst":
        bits, value = expr.get("bits"), expr.get("value")
        if bits not in SUPPORTED_WIDTHS:
            raise UnsupportedIntent("unsupported-constant-width")
        if not isinstance(value, int):
            raise UnsupportedIntent("unsupported-constant")
        masked = value & ((1 << bits) - 1)
        return f"cv_value_w({masked}ULL, {bits}U)"
    if op == "poison":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 1:
            raise UnsupportedIntent("unsupported-poison")
        return f"cv_poison({expr_cpp(args[0], variables, default_width)})"
    if op == "freeze":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 1:
            raise UnsupportedIntent("unsupported-freeze")
        raw = args[0]
        if isinstance(raw, dict) and raw.get("op") == "undef":
            return f"cv_any_bv({default_width}U)"
        return f"cv_freeze({expr_cpp(raw, variables, default_width)})"
    if op in {"bvneg", "bvnot"}:
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 1:
            raise UnsupportedIntent(f"unsupported-{op}")
        return f"cv_{op}({expr_cpp(args[0], variables, default_width)})"
    if op == "ite":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 3:
            raise UnsupportedIntent("unsupported-ite")
        return f"cv_ite({expr_cpp(args[0], variables, default_width)}, {expr_cpp(args[1], variables, default_width)}, {expr_cpp(args[2], variables, default_width)})"
    binary = {
        "bvadd", "bvsub", "bvmul", "bvand", "bvor", "bvxor", "bvshl", "bvlshr", "bvashr",
        "bvudiv", "bvurem", "bvsdiv", "bvsrem", "eq", "ne", "bvslt", "bvsle", "bvsgt",
        "bvsge", "bvult", "bvule", "bvugt", "bvuge",
    }
    if op in binary:
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 2:
            raise UnsupportedIntent(f"unsupported-{op}")
        left = expr_cpp(args[0], variables, default_width)
        right = expr_cpp(args[1], variables, default_width)
        nsw, nuw, exact = flags(expr)
        if op in {"bvadd", "bvsub", "bvmul", "bvshl"}:
            return f"cv_{op}({left}, {right}, {str(nsw).lower()}, {str(nuw).lower()})"
        if op in {"bvlshr", "bvashr"}:
            return f"cv_{op}({left}, {right}, {str(exact).lower()})"
        if nsw or nuw or exact:
            raise UnsupportedIntent(f"unsupported-flags-for-{op}")
        return f"cv_{op}({left}, {right})"
    raise UnsupportedIntent(f"unsupported-op:{op or 'unset'}")


def assumption_cpp(assumption: Any, variables: set[str], variable_widths: dict[str, int]) -> str:
    if not isinstance(assumption, dict):
        raise UnsupportedIntent("unsupported-assumption")
    op = assumption.get("op")
    name = assumption.get("name")
    if op in {"not-poison", "not-eq", "cmp", "known-bits", "power-of-two"}:
        if not isinstance(name, str) or name not in variables:
            raise UnsupportedIntent(f"unsupported-{op}-assumption")
        var = safe_ident(name, "v")
        if op == "not-poison":
            return f"!{var}.poison"
        if op == "not-eq":
            if assumption.get("value") != 0:
                raise UnsupportedIntent("unsupported-not-eq-value")
            return f"{var}.bits != 0U"
        if op == "power-of-two":
            if assumption.get("nonzero") is not True:
                raise UnsupportedIntent("unsupported-power-of-two")
            return f"cv_is_power_of_two({var}.bits)"
        if op == "known-bits":
            zero_mask = assumption.get("zero_mask", 0)
            one_mask = assumption.get("one_mask", 0)
            if not isinstance(zero_mask, int) or not isinstance(one_mask, int) or zero_mask < 0 or one_mask < 0:
                raise UnsupportedIntent("unsupported-known-bits")
            mask = (1 << variable_widths[name]) - 1
            return f"(({var}.bits & {zero_mask & mask}ULL) == 0U && ({var}.bits & {one_mask & mask}ULL) == {one_mask & mask}ULL)"
        value = assumption.get("value")
        predicate = assumption.get("predicate")
        if not isinstance(value, int):
            raise UnsupportedIntent("unsupported-cmp-value")
        width = variable_widths[name]
        const = f"{value & ((1 << width) - 1)}ULL"
        return predicate_cpp(predicate, f"{var}.bits", const, width)
    if op == "rel":
        left = assumption.get("left")
        right = assumption.get("right")
        if not isinstance(left, str) or not isinstance(right, str) or left not in variables or right not in variables:
            raise UnsupportedIntent("unsupported-rel-assumption")
        width = variable_widths[left]
        if variable_widths[right] != width:
            raise UnsupportedIntent("unsupported-rel-assumption-width")
        return predicate_cpp(assumption.get("predicate"), f"{safe_ident(left)}.bits", f"{safe_ident(right)}.bits", width)
    raise UnsupportedIntent(f"unsupported-assumption:{op or 'unset'}")


def predicate_cpp(predicate: Any, left: str, right: str, width: int) -> str:
    table = {
        "eq": f"{left} == {right}",
        "ne": f"{left} != {right}",
        "ult": f"{left} < {right}",
        "ule": f"{left} <= {right}",
        "ugt": f"{left} > {right}",
        "uge": f"{left} >= {right}",
        "slt": f"cv_slt_w({left}, {right}, {width}U)",
        "sle": f"cv_sle_w({left}, {right}, {width}U)",
        "sgt": f"cv_sgt_w({left}, {right}, {width}U)",
        "sge": f"cv_sge_w({left}, {right}, {width}U)",
    }
    if predicate not in table:
        raise UnsupportedIntent("unsupported-predicate")
    return table[predicate]


def check_function_name(index: int, record: dict[str, Any], width: int, include_width: bool = False) -> str:
    marker = safe_ident(str(record.get("marker") or "intent"), "intent")
    source_function = safe_ident(str(record.get("source_function") or ""), "")
    if source_function:
        marker = f"{marker}_{source_function}"
    suffix = f"_w{width}" if include_width else ""
    return f"check_{index:04d}_{marker}{suffix}"


def harness_for_record(index: int, record: dict[str, Any], formal: dict[str, Any],
                       width: int, include_width: bool = False) -> tuple[str, str]:
    variables = set(formal["variables"])
    variable_bits = formal.get("variable_bits") or {}
    variable_widths = {name: int(variable_bits.get(name, 32)) for name in formal["variables"]}
    if set(variable_widths.values()) != {width}:
        raise UnsupportedIntent("unsupported-mixed-variable-widths")
    poison_variables = set(formal.get("poison_variables") or [])
    if not poison_variables <= variables:
        raise UnsupportedIntent("unsupported-poison-variables")
    function = check_function_name(index, record, width, include_width)
    lines = [
        '#include "modelcheck_llvm.h"',
        "",
        f'extern "C" void {function}() {{',
    ]
    for name in formal["variables"]:
        ident = safe_ident(name, "v")
        ctor = "cv_any_poison_bv" if name in poison_variables else "cv_any_bv"
        lines.append(f"  Value {ident} = {ctor}({width}U);")
    for assumption in formal.get("assumptions") or []:
        lines.append(f"  CV_ASSUME({assumption_cpp(assumption, variables, variable_widths)});")
    lines.append(f"  Value before = {expr_cpp(formal.get('before'), variables, width)};")
    lines.append(f"  Value after = {expr_cpp(formal.get('after'), variables, width)};")
    assertion = "cv_assert_refines" if str(formal.get("refinement", "equality")) == "refinement" else "cv_assert_equivalent"
    lines.append(f'  {assertion}(before, after, "{function} refinement");')
    lines += ["}", "", "int main() { return 0; }", ""]
    return function, "\n".join(lines)


def unsupported_result(
    index: int,
    record: dict[str, Any],
    reason: str,
    width: int | None = None,
    domain: str = "",
) -> dict[str, Any]:
    result = {
        "record_index": index,
        "marker": str(record.get("marker") or ""),
        "file": str(record.get("file") or ""),
        "line": int(record.get("line") or 0),
        "status": "unsupported",
        "reason": reason,
    }
    if domain:
        result["domain"] = domain
    if width is not None:
        result["width"] = width
    return {
        **result,
    }


def actionable_finding(result: dict[str, Any]) -> dict[str, Any] | None:
    status = str(result.get("status") or "")
    if status not in {"refuted", "error"}:
        return None
    reason = str(result.get("reason") or "")
    if status == "refuted" and not reason:
        reason = "counterexample"
    finding = {
        "record_index": int(result.get("record_index") or 0),
        "marker": str(result.get("marker") or ""),
        "file": str(result.get("file") or ""),
        "line": int(result.get("line") or 0),
        "status": status,
        "reason": reason,
        "function": str(result.get("source_function") or result.get("function") or result.get("fold") or ""),
        "harness": str(result.get("harness") or ""),
    }
    domain = str(result.get("domain") or "")
    if domain and domain != "scalar-bv32":
        finding["domain"] = domain
    source_function = str(result.get("source_function") or "")
    if source_function:
        finding["source_function"] = source_function
        finding["harness_function"] = str(result.get("function") or "")
    if "width" in result:
        finding["width"] = int(result.get("width") or 0)
    witness = str(result.get("witness_excerpt") or "")
    if witness:
        finding["witness_excerpt"] = witness
    return finding


def run_intent_modelcheck(input_path: Path, out_dir: Path, engine: str = "auto",
                          unwind: int = 8, timeout_s: int = 30,
                          widths: str = "native") -> dict[str, Any]:
    records = load_records(input_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    harness_dir = out_dir / "harnesses"
    harness_dir.mkdir(parents=True, exist_ok=True)
    engine_path, engine_name = M.resolve_engine(engine)
    results: list[dict[str, Any]] = []
    width_error = ""
    try:
        selected_widths = parse_widths(widths)
    except UnsupportedIntent as exc:
        selected_widths = []
        width_error = str(exc)
        results.append({"record_index": -1, "marker": "", "file": "", "line": 0,
                        "status": "error", "reason": str(exc)})

    for index, record in enumerate([] if width_error else records):
        try:
            formal = record_formal(record)
        except UnsupportedIntent as exc:
            results.append(unsupported_result(index, record, str(exc)))
            continue

        work_items: list[tuple[int, dict[str, Any], bool]] = []
        if selected_widths is None:
            try:
                width = formal_width(formal)
                instance_formal = dict(formal)
                instance_formal["domain"] = domain_at_width(str(formal.get("domain") or ""), width)
                work_items.append((width, instance_formal, False))
            except UnsupportedIntent as exc:
                results.append(unsupported_result(index, record, str(exc)))
                continue
        else:
            for width in selected_widths:
                try:
                    instance_formal = MW.formal_at_width(formal, width)
                    instance_formal["domain"] = domain_at_width(str(formal.get("domain") or ""), width)
                    work_items.append((width, instance_formal, True))
                except (MW.NonPortable, KeyError, TypeError, ValueError) as exc:
                    results.append(
                        unsupported_result(
                            index,
                            record,
                            f"unsupported-width-{width}:{type(exc).__name__}",
                            width,
                            domain_at_width(str(formal.get("domain") or ""), width),
                        )
                    )
                    continue

        for width, instance_formal, include_width in work_items:
            try:
                function, source = harness_for_record(index, record, instance_formal, width, include_width)
            except UnsupportedIntent as exc:
                results.append(
                    unsupported_result(
                        index,
                        record,
                        str(exc),
                        width,
                        str(instance_formal.get("domain") or formal.get("domain") or ""),
                    )
                )
                continue
            harness_path = harness_dir / f"{function}.cpp"
            harness_path.write_text(source, encoding="utf-8")
            base = {
                "record_index": index,
                "marker": str(record.get("marker") or ""),
                "file": str(record.get("file") or ""),
                "line": int(record.get("line") or 0),
                "domain": str(instance_formal.get("domain") or formal.get("domain") or ""),
                "source_function": str(record.get("source_function") or record.get("function") or ""),
                "width": width,
                "function": function,
                "harness": str(harness_path),
            }
            if engine_path is None:
                wanted = engine if engine != "auto" else "cbmc/esbmc"
                results.append({**base, "status": "skipped", "reason": f"model checker not found: {wanted}"})
                continue
            checked = M.run_fold(harness_path, function, engine_name, engine_path, unwind, timeout_s)
            checked.update(base)
            results.append(checked)
            continue

    counts = {status: sum(1 for item in results if item.get("status") == status)
              for status in ("proved", "refuted", "unsupported", "skipped", "error")}
    width_rollup: dict[str, dict[str, int]] = {}
    for item in results:
        key = str(item.get("width") or "none")
        bucket = width_rollup.setdefault(key, {status: 0 for status in ("proved", "refuted", "unsupported", "skipped", "error")})
        status = str(item.get("status") or "")
        if status in bucket:
            bucket[status] += 1
    findings = [
        finding
        for item in results
        if (finding := actionable_finding(item)) is not None
    ]
    return {
        "model": "o2t-modelcheck-intents-summary-v1",
        "input": str(input_path),
        "out_dir": str(out_dir),
        "engine": engine_name,
        "engine_path": engine_path or "",
        "width_mode": widths,
        "selected_widths": selected_widths or [],
        "records": len(records),
        "instances": len(results),
        **counts,
        "generated": sum(1 for item in results if item.get("harness")),
        "ok": counts["refuted"] == 0 and counts["error"] == 0,
        "widths": width_rollup,
        "findings": findings,
        "results": results,
    }
