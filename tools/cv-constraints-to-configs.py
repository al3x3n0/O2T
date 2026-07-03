#!/usr/bin/env python3
"""Convert mined pass constraints into O2T seed configs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cv_analysis_facts import dse_lane_mask_bits, dse_lane_mask_width, normalize_analysis_facts
from cv_targeted_ir_configs import config_for_record, marker_filename, write_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--reducer", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--unsupported-jsonl", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array")
        return [record for record in data if isinstance(record, dict)]

    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def constraints_for(record: dict[str, Any]) -> dict[str, Any]:
    constraints = record.get("constraints", {})
    return constraints if isinstance(constraints, dict) else {}


def marker_for(record: dict[str, Any]) -> str:
    marker = record.get("marker")
    return marker if isinstance(marker, str) else ""


def config_for(record: dict[str, Any]) -> dict[str, int] | None:
    return config_for_record(record)


def config_source_for(record: dict[str, Any]) -> str:
    marker = marker_for(record)
    if marker and config_for_record({"marker": marker}) is not None:
        return "marker"
    return "constraints"


def unsupported_record(record: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "marker": marker_for(record),
        "status": "unsupported",
        "reason": reason,
        "source": record.get("file", record.get("pass", "")),
        "line": record.get("line"),
    }


def analysis_facts_for(record: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = record.get("evidence")
    if isinstance(evidence, dict):
        params = evidence.get("formal_parameters")
        if isinstance(params, dict):
            facts = normalize_analysis_facts(params.get("analysis_facts"))
            if facts:
                return facts
        facts = normalize_analysis_facts(evidence.get("analysis_facts"))
        if facts:
            return facts
        graph = evidence.get("source_intent_graph")
        if isinstance(graph, dict):
            facts = normalize_analysis_facts(graph.get("analysis_facts"))
            if facts:
                return facts
    graph = record.get("source_intent_graph")
    if isinstance(graph, dict):
        facts = normalize_analysis_facts(graph.get("analysis_facts"))
        if facts:
            return facts
    return normalize_analysis_facts(record.get("analysis_facts"))


def analysis_fact_kinds(facts: list[dict[str, Any]]) -> list[str]:
    return sorted({str(fact.get("kind") or "") for fact in facts if str(fact.get("kind") or "")})


def dse_partial_byte_mask(facts: list[dict[str, Any]]) -> str:
    for fact in facts:
        if str(fact.get("kind") or "") != "memory.overwrite.partial.fixed-byte-mask":
            continue
        mask = str(fact.get("byte_mask") or "")
        if mask:
            return mask
    return ""


def dse_symbolic_bound_width(facts: list[dict[str, Any]]) -> int:
    kinds = set(analysis_fact_kinds(facts))
    if "memory.overwrite.size.symbolic-bounded-four-lane" in kinds:
        return 4
    if "memory.overwrite.size.symbolic-bounded-eight-lane" in kinds:
        return 8
    return 0


def dse_generation_blocker(marker: str, facts: list[dict[str, Any]]) -> str:
    if marker not in {"probe.dse.dead-store", "probe.dse.overwritten-store"}:
        return ""
    if not facts:
        return ""
    kinds = set(analysis_fact_kinds(facts))
    unsupported = {
        str(fact.get("kind") or "")
        for fact in facts
        if str(fact.get("status") or "") == "unsupported"
    }
    if (
        "memory.volatile-atomic-blocker" in unsupported
        or "memory.volatile-blocker" in unsupported
        or "memory.atomic-unordered-blocker" in unsupported
        or "memory.atomic-ordered-blocker" in unsupported
        or "memory.atomic-ordering-unknown-blocker" in unsupported
    ):
        return "unsupported-volatile-or-atomic-memory"
    if "memory.unknown-intervening-effect" in unsupported:
        return "unsupported-intervening-memory-effect"
    if "alias.unknown" in kinds and "alias.noalias" not in kinds:
        return "unsupported-unresolved-memory-alias"
    if marker == "probe.dse.overwritten-store":
        if "memory.overwrite.partial" in kinds and "memory.overwrite.partial.fixed-byte-mask" not in kinds:
            return "unsupported-partial-overwrite"
        if "memory.overwrite.partial.fixed-byte-mask" in kinds:
            mask = dse_partial_byte_mask(facts)
            if not mask:
                return "unsupported-missing-partial-overwrite-byte-mask"
            if dse_lane_mask_bits(mask) is None:
                return "unsupported-partial-overwrite-byte-mask"
        if "memory.overwrite.nonoverlap" in kinds:
            return "unsupported-non-overlapping-overwrite"
        if (
            "memory.overwrite.unknown-size" in kinds
            and not dse_symbolic_bound_width(facts)
        ):
            return "unsupported-unknown-size-overwrite"
    if marker == "probe.dse.dead-store" and "memoryssa.dead-store" not in kinds:
        return "missing-memoryssa-dead-store"
    if marker == "probe.dse.overwritten-store" and "memoryssa.clobber" not in kinds:
        return "missing-memoryssa-clobber"
    if marker == "probe.dse.overwritten-store" and "memory.no-intervening-store" not in kinds:
        return "missing-no-intervening-store"
    if marker == "probe.dse.overwritten-store" and "memory.no-intervening-read" not in kinds:
        return "missing-no-intervening-read"
    if marker == "probe.dse.overwritten-store" and "memory.no-intervening-memory-effect" not in kinds:
        return "missing-no-intervening-memory-effect"
    if marker == "probe.dse.overwritten-store" and "memory.overwrite.size.known" not in kinds:
        return "missing-known-overwrite-size"
    if (
        marker == "probe.dse.overwritten-store"
        and "memory.overwrite.size.bounded-four-lane" not in kinds
        and "memory.overwrite.size.bounded-eight-lane" not in kinds
    ):
        return "missing-bounded-overwrite-size"
    if (
        marker == "probe.dse.overwritten-store"
        and "memory.overwrite.full" not in kinds
        and "memory.overwrite.partial.fixed-byte-mask" not in kinds
    ):
        return "missing-full-overwrite-range"
    return ""


def dse_scenario(marker: str, facts: list[dict[str, Any]]) -> str:
    kinds = set(analysis_fact_kinds(facts))
    if marker == "probe.dse.dead-store":
        return "complete-dead-store" if "memoryssa.dead-store" in kinds else "dead-store"
    if marker == "probe.dse.overwritten-store":
        if dse_symbolic_bound_width(facts):
            return "symbolic-bounded-overwrite"
        if "memory.overwrite.partial.fixed-byte-mask" in kinds:
            mask = dse_partial_byte_mask(facts)
            return f"partial-overwrite-fixed-byte-mask:{mask}" if mask else "partial-overwrite-fixed-byte-mask"
        if "memory.overwrite.full" in kinds:
            return "full-overwrite"
        if "alias.noalias" in kinds:
            return "noalias-protected-overwrite"
        return "complete-overwrite"
    return ""


def generation_key_for(record: dict[str, Any], facts: list[dict[str, Any]]) -> str:
    marker = marker_for(record)
    if marker.startswith("probe.dse.") and facts:
        source = str(record.get("file") or record.get("pass") or "")
        return f"{marker}:{source}:{record.get('line')}"
    return marker


def apply_dse_config_overrides(config: dict[str, int], marker: str, facts: list[dict[str, Any]]) -> dict[str, int]:
    if marker != "probe.dse.overwritten-store":
        return config
    kinds = set(analysis_fact_kinds(facts))
    symbolic_width = dse_symbolic_bound_width(facts)
    if symbolic_width:
        updated = dict(config)
        updated["feature_bits"] = int(updated.get("feature_bits", 0)) | 2
        updated["const_b"] = symbolic_width
        return updated
    if "memory.overwrite.partial.fixed-byte-mask" not in kinds:
        return config
    mask = dse_partial_byte_mask(facts)
    bits = dse_lane_mask_bits(mask)
    if bits is None:
        return config
    updated = dict(config)
    updated["store_mode"] = 2
    updated["const_a"] = bits
    updated["const_b"] = dse_lane_mask_width(mask) or 4
    return updated


def needs_dse_config_overrides(marker: str, facts: list[dict[str, Any]]) -> bool:
    if marker != "probe.dse.overwritten-store":
        return False
    return bool(dse_partial_byte_mask(facts)) or bool(dse_symbolic_bound_width(facts))


def output_stem_for(marker: str, record: dict[str, Any], facts: list[dict[str, Any]]) -> str:
    stem = marker_filename(marker)
    if marker.startswith("probe.dse.") and facts:
        try:
            line = int(record.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if line:
            return f"{stem}_line_{line}"
    return stem


def blocked_record(record: dict[str, Any], reason: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "marker": marker_for(record),
        "status": "blocked",
        "reason": reason,
        "source": record.get("file", record.get("pass", "")),
        "line": record.get("line"),
        "analysis_fact_kinds": analysis_fact_kinds(facts),
    }


def write_summary(path: Path | None, summary: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_unsupported(path: Path | None, records: list[dict[str, Any]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


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


def main() -> int:
    args = parse_args()
    if not args.replay.exists():
        print(f"cv-replay not found: {args.replay}", file=sys.stderr)
        return 1
    if not args.reducer.exists():
        print(f"cv-reduce-config not found: {args.reducer}", file=sys.stderr)
        return 1

    try:
        records = load_records(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.jsonl"
    generated = 0
    skipped = 0
    blocked = 0
    duplicates = 0
    unsupported: list[dict[str, Any]] = []
    generated_markers: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with manifest_path.open("w", encoding="utf-8") as manifest:
            seen: set[str] = set()
            for record in records:
                marker = marker_for(record)
                if not marker:
                    skipped += 1
                    unsupported.append(unsupported_record(record, "missing marker"))
                    continue
                facts = analysis_facts_for(record)
                generation_key = generation_key_for(record, facts)
                if generation_key in seen:
                    duplicates += 1
                    continue
                seen.add(generation_key)

                blocker = dse_generation_blocker(marker, facts)
                if blocker:
                    skipped += 1
                    blocked += 1
                    unsupported.append(blocked_record(record, blocker, facts))
                    continue

                config = config_for(record)
                if config is None:
                    skipped += 1
                    unsupported_item = unsupported_record(record, "unsupported constraints")
                    unsupported.append(unsupported_item)
                    message = f"unsupported constraints for {marker}"
                    if args.strict:
                        write_unsupported(args.unsupported_jsonl, unsupported)
                        write_summary(
                            args.summary_json,
                            {
                                "generated": generated,
                                "skipped": skipped,
                                "duplicates": duplicates,
                                "blocked": blocked,
                                "unsupported_markers": [item["marker"] for item in unsupported],
                                "generated_markers": generated_markers,
                                "status": {
                                    "generated": generated,
                                    "blocked": blocked,
                                    "unsupported": len([item for item in unsupported if item.get("status") == "unsupported"]),
                                },
                            },
                        )
                        print(message, file=sys.stderr)
                        return 1
                    print(message, file=sys.stderr)
                    continue
                config = apply_dse_config_overrides(config, marker, facts)

                stem = output_stem_for(marker, record, facts)
                raw_cfg = tmp_path / f"{stem}.raw.cfg"
                normalized_cfg = tmp_path / f"{stem}.normalized.cfg"
                reduced_cfg = args.out_dir / f"{stem}.cfg"
                output_ll = args.out_dir / f"{stem}.ll"

                write_config(raw_cfg, config)
                try:
                    normalize_config(args.replay, raw_cfg, normalized_cfg)
                    reduce_config(args.reducer, normalized_cfg, reduced_cfg, marker)
                    if needs_dse_config_overrides(marker, facts):
                        write_config(reduced_cfg, config)
                    emit_ir(args.replay, reduced_cfg, output_ll)
                except subprocess.CalledProcessError as exc:
                    print(f"failed to generate config for {marker}: {exc}", file=sys.stderr)
                    return 1

                manifest.write(
                    json.dumps(
                        {
                            "marker": marker,
                            "config": reduced_cfg.name,
                            "ir": output_ll.name,
                            "source": record.get("file", record.get("pass", "")),
                            "line": record.get("line"),
                            "coverage": [marker],
                            "generation_source": config_source_for(record),
                            "status": "generated",
                            "expected_generation_status": "generated",
                            "analysis_fact_kinds": analysis_fact_kinds(facts),
                            "dse_scenario": dse_scenario(marker, facts),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                generated += 1
                generated_markers.append(marker)

    write_unsupported(args.unsupported_jsonl, unsupported)
    write_summary(
        args.summary_json,
        {
            "generated": generated,
            "skipped": skipped,
            "blocked": blocked,
            "duplicates": duplicates,
            "unsupported_markers": [item["marker"] for item in unsupported],
            "generated_markers": generated_markers,
            "status": {
                "generated": generated,
                "blocked": blocked,
                "unsupported": len([item for item in unsupported if item.get("status") == "unsupported"]),
            },
        },
    )
    if generated == 0:
        print("no configs generated", file=sys.stderr)
        return 1

    print(f"generated {generated} config(s) in {args.out_dir}")
    if skipped:
        print(f"skipped {skipped} unsupported record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
