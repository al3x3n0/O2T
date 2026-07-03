#!/usr/bin/env python3
"""Audit whether instrumentation candidates were rewritten by the source tool."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


COVERAGE_ISSUES = {"skipped", "error", "missing-from-manifest"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--text-out", type=Path, required=True)
    parser.add_argument("--recommendations-out", type=Path)
    parser.add_argument("--require-coverage", action="store_true")
    return parser.parse_args()


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


def candidate_key(record: dict[str, Any]) -> tuple[str, int, str]:
    file_name = record.get("file", record.get("original_file", ""))
    return (
        str(Path(str(file_name)).resolve()) if file_name else "",
        int(record.get("line") or 0),
        str(record.get("marker", "")),
    )


def normalize_status(status: str) -> str:
    if status == "candidate":
        return "candidate-only"
    if status:
        return status
    return "unknown"


def detail_record(finding: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any]:
    status = "missing-from-manifest" if manifest is None else normalize_status(str(manifest.get("status", "")))
    return {
        "file": str(finding.get("file", "")),
        "line": int(finding.get("line") or 0),
        "marker": str(finding.get("marker", "")),
        "pass": str(finding.get("pass", "")),
        "predicate": str(
            finding.get("predicate_source")
            or finding.get("matched_pattern")
            or finding.get("source")
            or ""
        ),
        "finding_source": str(finding.get("finding_source", "")),
        "status": status,
        "message": "" if manifest is None else str(manifest.get("message", "")),
    }


def summarize(details: list[dict[str, Any]], patch: Path) -> dict[str, Any]:
    status_counts = Counter(str(record["status"]) for record in details)
    marker_counts: dict[str, Counter[str]] = defaultdict(Counter)
    pass_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for record in details:
        status = str(record["status"])
        marker_counts[str(record["marker"])][status] += 1
        pass_counts[str(record["pass"])][status] += 1

    return {
        "findings": len(details),
        "patch": str(patch),
        "patch_nonempty": patch.is_file() and patch.stat().st_size > 0,
        "status_counts": dict(sorted(status_counts.items())),
        "marker_counts": {
            marker: dict(sorted(counts.items())) for marker, counts in sorted(marker_counts.items())
        },
        "pass_counts": {
            pass_name: dict(sorted(counts.items())) for pass_name, counts in sorted(pass_counts.items())
        },
        "coverage_issues": sum(status_counts[status] for status in COVERAGE_ISSUES),
    }


def recommendation_kind(record: dict[str, Any]) -> str:
    status = str(record["status"])
    message = str(record.get("message", "")).lower()
    if status == "missing-from-manifest":
        return "missing-from-manifest"
    if status == "error":
        return "instrumenter-error"
    if status == "skipped" and "no candidate predicate matched" in message:
        return "unsupported-ast-shape"
    if status == "skipped" and "line" in message:
        return "line-mismatch"
    if status == "skipped" and "predicate" in message:
        return "predicate-text-mismatch"
    if status == "skipped":
        return "unsupported-ast-shape"
    return ""


def recommendation_action(kind: str) -> str:
    actions = {
        "missing-from-manifest": "check finding merge and instrumentation manifest generation",
        "instrumenter-error": "check compile commands, include paths, and source compatibility",
        "unsupported-ast-shape": "add or refine a Clang AST matcher or line-aware candidate predicate",
        "line-mismatch": "refresh source line numbers or rerun mining against the current checkout",
        "predicate-text-mismatch": "adjust predicate_source to match the exact if-condition text",
    }
    return actions.get(kind, "inspect instrumentation candidate")


def make_recommendations(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for record in details:
        kind = recommendation_kind(record)
        if not kind:
            continue
        recommendations.append(
            {
                "kind": kind,
                "file": record["file"],
                "line": record["line"],
                "marker": record["marker"],
                "pass": record["pass"],
                "predicate": record["predicate"],
                "status": record["status"],
                "message": record["message"],
                "recommendation": recommendation_action(kind),
            }
        )
    return recommendations


def write_jsonl(path: Path | None, records: list[dict[str, Any]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def write_text(
    path: Path,
    summary: dict[str, Any],
    details: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
) -> None:
    issue_details = [
        record for record in details if str(record["status"]) in COVERAGE_ISSUES
    ]
    lines = [
        "O2T Instrumentation Audit",
        f"findings: {summary['findings']}",
        f"patch_nonempty: {str(summary['patch_nonempty']).lower()}",
        "statuses:",
    ]
    for status, count in summary["status_counts"].items():
        lines.append(f"  {status}: {count}")

    lines.append("markers:")
    for marker, counts in summary["marker_counts"].items():
        rendered = ", ".join(f"{status}={count}" for status, count in counts.items())
        lines.append(f"  {marker}: {rendered}")

    if issue_details:
        lines.append("coverage_issues:")
        for record in issue_details:
            location = f"{record['file']}:{record['line']}"
            message = f" message={record['message']}" if record["message"] else ""
            lines.append(
                f"  {record['status']} {record['marker']} {location} predicate={record['predicate']}{message}"
            )

    if recommendations:
        lines.append("recommendations:")
        for record in recommendations:
            location = f"{record['file']}:{record['line']}"
            lines.append(
                f"  {record['kind']} {record['marker']} {location} action={record['recommendation']}"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        findings = load_records(args.findings)
        manifest_records = load_records(args.manifest)
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    manifest_by_key = {candidate_key(record): record for record in manifest_records}
    details = [
        detail_record(finding, manifest_by_key.get(candidate_key(finding)))
        for finding in findings
    ]
    summary = summarize(details, args.patch)
    recommendations = make_recommendations(details)
    report = {"summary": summary, "details": details, "recommendations": recommendations}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_jsonl(args.recommendations_out, recommendations)
    write_text(args.text_out, summary, details, recommendations)

    print(f"instrumentation_audit: {args.out}")
    print(f"instrumentation_audit_text: {args.text_out}")
    if args.recommendations_out:
        print(f"instrumentation_recommendations: {args.recommendations_out}")
    if args.require_coverage and summary["coverage_issues"]:
        print(f"instrumentation coverage issues: {summary['coverage_issues']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
