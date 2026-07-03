#!/usr/bin/env python3
"""Regression fixture for predicate provenance verification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MARKER = "probe.globalopt.dead-initializer"
SLP_BINOP_MARKER = "probe.slp.vectorize-binop"
SLP_REDUCTION_MARKER = "probe.slp.vectorize-reduction"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    return parser.parse_args()


def run(command: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}, expected {expect}")
    return result


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")


def provenance(fact: str, family: str, source: str, line: int = 10) -> dict[str, Any]:
    return {
        "fact": fact,
        "status": "observed",
        "predicate_family": family,
        "source": source,
        "source_range": {
            "begin_line": line,
            "begin_column": 3,
            "end_line": line,
            "end_column": 10,
        },
        "subject": "GV",
    }


def slp_provenance(fact: str, family: str, line: int = 20) -> dict[str, Any]:
    return provenance(fact, family, f"{family} source", line)


def slp_facts(include_reduction: bool = False) -> list[dict[str, Any]]:
    facts = [
        slp_provenance("candidate-tree", "slp-role:candidate-tree"),
        slp_provenance("legality", "slp-role:legality"),
        slp_provenance("vector-emission", "slp-role:vector-emission"),
        slp_provenance("scalar-replacement", "slp-role:scalar-replacement"),
        slp_provenance("lane-mapping", "slp-contract:lane-map-bound-to-emitter"),
    ]
    if include_reduction:
        facts.extend([
            slp_provenance("reduction-source", "slp-reduction:source"),
            slp_provenance("reduction-result", "slp-reduction:result"),
        ])
    return facts


def record(items: list[dict[str, Any]], marker: str = MARKER) -> dict[str, Any]:
    return {
        "marker": marker,
        "file": "GlobalOpt.cpp",
        "line": 321,
        "evidence": {
            "formal_parameters": {
                "global.initializer.safety_provenance": items,
                "global.initializer.safety_provenance_status": "complete",
            }
        },
    }


def slp_record(items: list[dict[str, Any]], marker: str) -> dict[str, Any]:
    return {
        "marker": marker,
        "file": "SLPVectorizer.cpp",
        "line": 222,
        "evidence": {
            "optimization_transaction": {
                "kind": "slp-vectorize-reduction" if marker == SLP_REDUCTION_MARKER else "slp-vectorize-binop",
                "predicate_provenance": items,
            }
        },
    }


def top_level_fact_record(items: list[dict[str, Any]], marker: str = MARKER) -> dict[str, Any]:
    return {
        "marker": marker,
        "file": "GlobalOpt.cpp",
        "line": 654,
        "facts": items,
    }


def missing_source_record(marker: str = MARKER) -> dict[str, Any]:
    return {
        "marker": marker,
        "file": "GlobalOpt.cpp",
        "line": 987,
        "evidence": {"formal_parameters": {}},
    }


def verify(repo: Path, input_path: Path, out_dir: Path, expect: int = 0) -> tuple[dict[str, Any], str, str]:
    out = out_dir / "verification.json"
    report = out_dir / "verification.txt"
    result = run(
        [
            sys.executable,
            str(repo / "tools" / "cv-verify-predicate-provenance.py"),
            "--input",
            str(input_path),
            "--out",
            str(out),
            "--report",
            str(report),
            "--require-clean",
        ],
        expect=expect,
    )
    return json.loads(out.read_text(encoding="utf-8")), report.read_text(encoding="utf-8"), result.stderr


def validate_registry(repo: Path, registry: Path, out_dir: Path, expect: int = 0) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            str(repo / "tools" / "cv-validate-intent-registry.py"),
            "--intents",
            str(registry),
            "--out",
            str(out_dir / "intent-registry.jsonl"),
        ],
        expect=expect,
    )


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    good_facts = [
        provenance("initializer-dead", "isGlobalInitializerDead", "isGlobalInitializerDead(GV)"),
        provenance("local-linkage", "hasLocalLinkage", "GV->hasLocalLinkage()"),
        provenance("no-uses", "use_empty", "GV->use_empty()"),
    ]

    passed = args.work_dir / "passed"
    write_jsonl(passed / "input.jsonl", [record(good_facts)])
    passed_data, passed_report, _ = verify(args.repo, passed / "input.jsonl", passed)
    assert passed_data["summary"]["status"] == {"passed": 1}
    assert passed_data["summary"]["predicate_provenance_status"] == {"passed": 1}
    assert passed_data["records"][0]["contract_model"] == "predicate-provenance-contract-v1"
    assert passed_data["records"][0]["contract_marker"] == MARKER
    assert passed_data["records"][0]["provenance_source"] == "global.initializer.safety_provenance"
    assert passed_data["records"][0]["observed_facts"] == ["initializer-dead", "local-linkage", "no-uses"]
    assert "failed_checks: none" in passed_report

    top_level = args.work_dir / "top-level-facts"
    write_jsonl(top_level / "input.jsonl", [top_level_fact_record(good_facts)])
    top_data, _, _ = verify(args.repo, top_level / "input.jsonl", top_level)
    assert top_data["summary"]["status"] == {"passed": 1}
    assert top_data["records"][0]["provenance_source"] == "facts"

    missing_source = args.work_dir / "missing-source"
    write_jsonl(missing_source / "input.jsonl", [missing_source_record()])
    missing_source_data, _, _ = verify(args.repo, missing_source / "input.jsonl", missing_source, expect=1)
    assert missing_source_data["records"][0]["provenance_source"] == ""
    assert missing_source_data["summary"]["failed_checks"] == {
        "initializer-dead-provenance-missing": 1,
        "local-linkage-provenance-missing": 1,
        "no-uses-provenance-missing": 1,
    }

    missing = args.work_dir / "missing"
    write_jsonl(missing / "input.jsonl", [record([good_facts[0], good_facts[2]])])
    missing_data, _, missing_stderr = verify(args.repo, missing / "input.jsonl", missing, expect=1)
    assert missing_data["summary"]["failed_checks"]["local-linkage-provenance-missing"] == 1
    assert "predicate provenance verification failed: 1" in missing_stderr

    wrong_family = args.work_dir / "wrong-family"
    bad = [dict(item) for item in good_facts]
    bad[1]["predicate_family"] = "hasExternalLinkage"
    write_jsonl(wrong_family / "input.jsonl", [record(bad)])
    wrong_data, _, _ = verify(args.repo, wrong_family / "input.jsonl", wrong_family, expect=1)
    assert wrong_data["summary"]["failed_checks"]["local-linkage-predicate-family-mismatch"] == 1

    missing_range = args.work_dir / "missing-range"
    bad_range = [dict(item) for item in good_facts]
    bad_range[2]["source_range"] = {}
    write_jsonl(missing_range / "input.jsonl", [record(bad_range)])
    range_data, _, _ = verify(args.repo, missing_range / "input.jsonl", missing_range, expect=1)
    assert range_data["summary"]["failed_checks"]["no-uses-source-range-missing"] == 1

    ignored = args.work_dir / "ignored"
    write_jsonl(ignored / "input.jsonl", [record(good_facts), record(good_facts, "probe.instcombine.add-zero")])
    ignored_data, _, _ = verify(args.repo, ignored / "input.jsonl", ignored)
    assert ignored_data["summary"]["records"] == 1
    assert ignored_data["summary"]["ignored"] == 1

    slp_binop = args.work_dir / "slp-binop"
    write_jsonl(slp_binop / "input.jsonl", [slp_record(slp_facts(), SLP_BINOP_MARKER)])
    slp_binop_data, _, _ = verify(args.repo, slp_binop / "input.jsonl", slp_binop)
    assert slp_binop_data["summary"]["status"] == {"passed": 1}
    assert slp_binop_data["records"][0]["provenance_source"] == "evidence.optimization_transaction.predicate_provenance"

    slp_reduction = args.work_dir / "slp-reduction"
    write_jsonl(slp_reduction / "input.jsonl", [slp_record(slp_facts(True), SLP_REDUCTION_MARKER)])
    slp_reduction_data, _, _ = verify(args.repo, slp_reduction / "input.jsonl", slp_reduction)
    assert slp_reduction_data["summary"]["status"] == {"passed": 1}
    assert slp_reduction_data["records"][0]["observed_facts"] == [
        "candidate-tree",
        "legality",
        "vector-emission",
        "scalar-replacement",
        "lane-mapping",
        "reduction-source",
        "reduction-result",
    ]

    slp_missing_legality = args.work_dir / "slp-missing-legality"
    write_jsonl(
        slp_missing_legality / "input.jsonl",
        [slp_record([fact for fact in slp_facts() if fact["fact"] != "legality"], SLP_BINOP_MARKER)],
    )
    slp_missing_data, _, _ = verify(args.repo, slp_missing_legality / "input.jsonl", slp_missing_legality, expect=1)
    assert slp_missing_data["summary"]["failed_checks"]["legality-provenance-missing"] == 1

    slp_missing_reduction = args.work_dir / "slp-missing-reduction-result"
    write_jsonl(
        slp_missing_reduction / "input.jsonl",
        [slp_record([fact for fact in slp_facts(True) if fact["fact"] != "reduction-result"], SLP_REDUCTION_MARKER)],
    )
    slp_reduction_missing_data, _, _ = verify(
        args.repo,
        slp_missing_reduction / "input.jsonl",
        slp_missing_reduction,
        expect=1,
    )
    assert slp_reduction_missing_data["summary"]["failed_checks"]["reduction-result-provenance-missing"] == 1

    malformed_registry = args.work_dir / "malformed-registry"
    malformed_registry.mkdir(parents=True, exist_ok=True)
    bad_registry = [
        {
            "marker": MARKER,
            "category": "global",
            "precondition": "global initializer is proven unobservable",
            "rewrite": "replace the global initializer with a default null initializer",
            "intent": "global-initializer-observable-equivalence",
            "formal": {
                "domain": "global-initializer-observable-v1",
                "required_safety_facts": ["initializer-dead", "local-linkage"],
                "predicate_provenance": {
                    "model": "predicate-provenance-contract-v1",
                    "provenance_sources": ["global.initializer.safety_provenance"],
                    "facts": [
                        {"fact": "initializer-dead", "predicate_family": "isGlobalInitializerDead"},
                        {"fact": "no-uses", "predicate_family": "use_empty"},
                    ],
                },
            },
        }
    ]
    registry_path = malformed_registry / "optimization_intents.json"
    registry_path.write_text(json.dumps(bad_registry, indent=2) + "\n", encoding="utf-8")
    registry_result = validate_registry(args.repo, registry_path, malformed_registry, expect=1)
    assert "predicate_provenance facts must match required_safety_facts" in registry_result.stderr

    missing_sources_registry = args.work_dir / "missing-sources-registry"
    missing_sources_registry.mkdir(parents=True, exist_ok=True)
    bad_registry[0]["formal"]["required_safety_facts"] = ["initializer-dead", "no-uses"]
    bad_registry[0]["formal"]["predicate_provenance"].pop("provenance_sources")
    registry_path = missing_sources_registry / "optimization_intents.json"
    registry_path.write_text(json.dumps(bad_registry, indent=2) + "\n", encoding="utf-8")
    registry_result = validate_registry(args.repo, registry_path, missing_sources_registry, expect=1)
    assert "predicate_provenance.provenance_sources must be a non-empty array" in registry_result.stderr

    duplicate_sources_registry = args.work_dir / "duplicate-sources-registry"
    duplicate_sources_registry.mkdir(parents=True, exist_ok=True)
    bad_registry[0]["formal"]["predicate_provenance"]["provenance_sources"] = ["facts", "facts"]
    registry_path = duplicate_sources_registry / "optimization_intents.json"
    registry_path.write_text(json.dumps(bad_registry, indent=2) + "\n", encoding="utf-8")
    registry_result = validate_registry(args.repo, registry_path, duplicate_sources_registry, expect=1)
    assert "predicate_provenance repeats provenance source facts" in registry_result.stderr
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
