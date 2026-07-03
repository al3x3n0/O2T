#!/usr/bin/env python3
"""Regression fixture for GlobalOpt coverage in the verification workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--z3", default="z3")
    return parser.parse_args()


def run(command: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}, expected {expect}")
    return result


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o755)
    return path


def workflow_command(repo: Path, work_dir: Path, out_name: str, source: Path) -> list[str]:
    return [
        sys.executable,
        str(repo / "tools" / "cv-run-verification-workflow.py"),
        "--out",
        str(work_dir / out_name),
        "--from-stage",
        "klee",
        "--to-stage",
        "klee",
        "--klee-campaign",
        str(work_dir / "existing-klee"),
        "--globalopt-coverage",
        "--globalopt-source",
        str(source),
    ]


def workflow_feed_command(repo: Path, work_dir: Path, out_name: str, source: Path, campaign_driver: Path) -> list[str]:
    return [
        sys.executable,
        str(repo / "tools" / "cv-run-verification-workflow.py"),
        "--out",
        str(work_dir / out_name),
        "--from-stage",
        "instrument",
        "--to-stage",
        "instrument",
        "--sources",
        str(source),
        "--campaign-driver",
        str(campaign_driver),
        "--globalopt-coverage",
        "--globalopt-source",
        str(source),
    ]


def strict_budget_args(repo: Path) -> list[str]:
    return [
        "--host-llvm-as",
        str(repo / "tests" / "fixtures" / "fake-llvm-as.sh"),
        "--globalopt-min-findings",
        "1",
        "--globalopt-min-graph-derived",
        "1",
        "--globalopt-max-unsupported",
        "0",
        "--globalopt-max-incomplete-safety",
        "0",
        "--globalopt-max-missing-fact",
        "local-linkage=0",
        "--globalopt-max-missing-fact",
        "no-uses=0",
        "--globalopt-max-new-unsupported",
        "0",
        "--globalopt-max-new-incomplete-safety",
        "0",
        "--globalopt-emit-witnesses",
        "--globalopt-min-witnesses",
        "1",
        "--globalopt-max-witness-failures",
        "0",
    ]


def fake_campaign_driver(work_dir: Path) -> Path:
    return write_script(
        work_dir / "fake-campaign.py",
        "#!/usr/bin/env python3\n"
        "import argparse, pathlib, sys\n"
        "parser = argparse.ArgumentParser(add_help=False)\n"
        "parser.add_argument('--out')\n"
        "parser.add_argument('--globalopt-coverage')\n"
        "parser.add_argument('--globalopt-witness-contract-verification')\n"
        "parser.add_argument('--predicate-provenance-verification')\n"
        "parser.add_argument('--verify-predicate-provenance', action='store_true')\n"
        "parser.add_argument('--emit-intent-evidence', action='store_true')\n"
        "parser.add_argument('--require-intent-evidence', action='store_true')\n"
        "parser.add_argument('--promote-intents', action='store_true')\n"
        "parser.add_argument('--replace-existing-intents', action='store_true')\n"
        "parser.add_argument('--require-promotable-intent', action='store_true')\n"
        "parser.add_argument('--require-globalopt-witnesses', action='store_true')\n"
        "parser.add_argument('--max-globalopt-witness-failures')\n"
        "args, _ = parser.parse_known_args()\n"
        "coverage = pathlib.Path(args.globalopt_coverage or '')\n"
        "contract = pathlib.Path(args.globalopt_witness_contract_verification or '')\n"
        "provenance = pathlib.Path(args.predicate_provenance_verification or '')\n"
        "required = [\n"
        "    args.emit_intent_evidence,\n"
        "    args.require_intent_evidence,\n"
        "    args.promote_intents,\n"
        "    args.replace_existing_intents,\n"
        "    args.require_promotable_intent,\n"
        "    args.require_globalopt_witnesses,\n"
        "    args.verify_predicate_provenance,\n"
        "    args.max_globalopt_witness_failures == '0',\n"
        "]\n"
        "if not all(required):\n"
        "    print('missing strict intent/globalopt flags', file=sys.stderr)\n"
        "    raise SystemExit(7)\n"
        "if not coverage.is_file():\n"
        "    print(f'globalopt coverage is missing: {coverage}', file=sys.stderr)\n"
        "    raise SystemExit(8)\n"
        "if not contract.is_file():\n"
        "    print(f'globalopt witness contract verification is missing: {contract}', file=sys.stderr)\n"
        "    raise SystemExit(9)\n"
        "if not provenance.is_file():\n"
        "    print(f'predicate provenance verification is missing: {provenance}', file=sys.stderr)\n"
        "    raise SystemExit(10)\n"
        "out = pathlib.Path(args.out)\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'consumed-globalopt-coverage.txt').write_text(str(coverage) + '\\n')\n",
    )


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    positive_source = args.repo / "tests" / "fixtures" / "global_dead_initializer_snippet.cpp"
    unsafe_source = args.repo / "tests" / "fixtures" / "global_dead_initializer_unsafe_snippet.cpp"
    missing_source = args.work_dir / "missing" / "GlobalOpt.cpp"
    campaign_driver = fake_campaign_driver(args.work_dir)

    planned_result = run(workflow_command(args.repo, args.work_dir, "planned", positive_source) + strict_budget_args(args.repo))
    assert "[globalopt-coverage]" in planned_result.stdout
    assert "--max-new-unsupported 0" in planned_result.stdout
    assert "--emit-witnesses" in planned_result.stdout
    planned_summary = load_json(args.work_dir / "planned" / "workflow-summary.json")
    assert planned_summary["execute"] is False
    assert [stage["stage"] for stage in planned_summary["stages"]] == ["globalopt-coverage"]
    assert "globalopt-coverage.json" in planned_summary["stages"][0]["artifacts"]["globalopt_coverage"]
    assert "globalopt-baseline-diff.json" in planned_summary["stages"][0]["artifacts"]["globalopt_baseline_diff"]
    assert "witnesses" in planned_summary["stages"][0]["artifacts"]["globalopt_witnesses"]

    planned_feed_result = run(
        workflow_feed_command(args.repo, args.work_dir, "planned-feed", positive_source, campaign_driver)
        + strict_budget_args(args.repo)
        + [
            "--require-intent-evidence",
            "--promote-intents",
            "--replace-existing-intents",
            "--require-promotable-intent",
            "--require-globalopt-witnesses",
            "--max-globalopt-witness-failures",
            "0",
            "--globalopt-verify-witness-contracts",
            "--globalopt-verify-witness-semantics",
            "--globalopt-require-witness-semantics",
            "--verify-predicate-provenance",
            "--alive2-bin",
            str(args.repo / "tests" / "fixtures" / "fake-alive-tv-success.sh"),
            "--z3",
            args.z3,
        ]
    )
    assert planned_feed_result.stdout.index("[globalopt-coverage]") < planned_feed_result.stdout.index("[instrument]")
    assert planned_feed_result.stdout.index("[globalopt-witness-contract]") < planned_feed_result.stdout.index("[instrument]")
    assert planned_feed_result.stdout.index("[predicate-provenance]") < planned_feed_result.stdout.index("[instrument]")
    assert "--emit-intent-evidence" in planned_feed_result.stdout
    assert "--promote-intents" in planned_feed_result.stdout
    assert "--globalopt-coverage" in planned_feed_result.stdout
    assert "--globalopt-witness-contract-verification" in planned_feed_result.stdout
    assert "--predicate-provenance-verification" in planned_feed_result.stdout
    assert "--verify-predicate-provenance" in planned_feed_result.stdout
    assert "--emit-alive2" in planned_feed_result.stdout
    assert "--require-alive2-proved" in planned_feed_result.stdout
    assert "--require-globalopt-witnesses" in planned_feed_result.stdout
    assert "--max-globalopt-witness-failures 0" in planned_feed_result.stdout
    planned_feed_summary = load_json(args.work_dir / "planned-feed" / "workflow-summary.json")
    assert [stage["stage"] for stage in planned_feed_summary["stages"]] == [
        "globalopt-coverage",
        "globalopt-witness-contract",
        "predicate-provenance",
        "instrument",
    ]

    run(
        workflow_command(args.repo, args.work_dir, "positive-execute", positive_source)
        + strict_budget_args(args.repo)
        + ["--execute"]
    )
    positive_summary = load_json(args.work_dir / "positive-execute" / "workflow-summary.json")
    assert positive_summary["stages"][0]["stage"] == "globalopt-coverage"
    assert positive_summary["stages"][0]["status"] == "passed"
    positive_coverage = load_json(
        args.work_dir / "positive-execute" / "globalopt-coverage" / "globalopt-coverage.json"
    )
    assert positive_coverage["budget_violations"] == []
    assert positive_coverage["candidates"]["graph_derived"] == 1
    assert positive_coverage["baseline_diff"]["new"] == 0
    assert positive_coverage["witnesses"]["passed"] == 1
    positive_baseline_path = args.work_dir / "positive-execute" / "globalopt-coverage" / "globalopt-baseline.json"
    assert positive_baseline_path.is_file()

    run(
        workflow_feed_command(args.repo, args.work_dir, "execute-feed", positive_source, campaign_driver)
        + strict_budget_args(args.repo)
        + [
            "--require-intent-evidence",
            "--promote-intents",
            "--replace-existing-intents",
            "--require-promotable-intent",
            "--require-globalopt-witnesses",
            "--max-globalopt-witness-failures",
            "0",
            "--globalopt-verify-witness-contracts",
            "--globalopt-verify-witness-semantics",
            "--globalopt-require-witness-semantics",
            "--verify-predicate-provenance",
            "--alive2-bin",
            str(args.repo / "tests" / "fixtures" / "fake-alive-tv-success.sh"),
            "--z3",
            args.z3,
            "--execute",
        ]
    )
    feed_summary = load_json(args.work_dir / "execute-feed" / "workflow-summary.json")
    assert [stage["stage"] for stage in feed_summary["stages"]] == [
        "globalopt-coverage",
        "globalopt-witness-contract",
        "predicate-provenance",
        "instrument",
    ]
    assert [stage["status"] for stage in feed_summary["stages"]] == ["passed", "passed", "passed", "passed"]
    consumed = args.work_dir / "execute-feed" / "instrumentation" / "consumed-globalopt-coverage.txt"
    assert consumed.read_text(encoding="utf-8").strip().endswith("globalopt-coverage.json")
    contract_verification = load_json(
        args.work_dir / "execute-feed" / "globalopt-witness-contract" / "globalopt-witness-contract-verification.json"
    )
    assert contract_verification["summary"]["formal_status"] == {"proved": 3}
    assert contract_verification["summary"]["semantic_status"] == {"proved": 3}
    assert (args.work_dir / "execute-feed" / "globalopt-witness-contract" / "alive2").is_dir()
    provenance_verification = load_json(
        args.work_dir / "execute-feed" / "predicate-provenance" / "predicate-provenance-verification.json"
    )
    assert provenance_verification["summary"]["status"] == {"passed": 1}

    witness_fail_result = run(
        workflow_command(args.repo, args.work_dir, "witness-fail-execute", positive_source)
        + [
            "--host-llvm-as",
            "/bin/false",
            "--globalopt-emit-witnesses",
            "--globalopt-max-witness-failures",
            "0",
        ]
        + ["--execute"],
        expect=1,
    )
    assert "budget violation: max-witness-failures actual=1 limit=0" in witness_fail_result.stderr
    witness_fail_summary = load_json(args.work_dir / "witness-fail-execute" / "workflow-summary.json")
    assert witness_fail_summary["stages"][0]["status"] == "failed"

    fail_result = run(
        workflow_command(args.repo, args.work_dir, "unsafe-execute", unsafe_source)
        + [
            "--globalopt-max-unsupported",
            "0",
            "--globalopt-max-incomplete-safety",
            "0",
        ]
        + ["--execute"],
        expect=1,
    )
    assert "budget violation: max-unsupported actual=1 limit=0" in fail_result.stderr
    unsafe_summary = load_json(args.work_dir / "unsafe-execute" / "workflow-summary.json")
    assert unsafe_summary["stages"][0]["status"] == "failed"
    unsafe_coverage = load_json(
        args.work_dir / "unsafe-execute" / "globalopt-coverage" / "globalopt-coverage.json"
    )
    assert unsafe_coverage["budget_violations"] == [
        {"actual": 1, "budget": "max-unsupported", "limit": 0},
        {"actual": 1, "budget": "max-incomplete-safety", "limit": 0},
    ]

    baseline_fail_result = run(
        workflow_command(args.repo, args.work_dir, "unsafe-baseline-execute", unsafe_source)
        + [
            "--globalopt-baseline",
            str(positive_baseline_path),
            "--globalopt-max-new-unsupported",
            "0",
            "--globalopt-max-new-incomplete-safety",
            "0",
        ]
        + ["--execute"],
        expect=1,
    )
    assert "budget violation: max-new-unsupported actual=1 limit=0" in baseline_fail_result.stderr
    unsafe_baseline_summary = load_json(args.work_dir / "unsafe-baseline-execute" / "workflow-summary.json")
    assert unsafe_baseline_summary["stages"][0]["status"] == "failed"
    unsafe_baseline_coverage = load_json(
        args.work_dir / "unsafe-baseline-execute" / "globalopt-coverage" / "globalopt-coverage.json"
    )
    assert unsafe_baseline_coverage["baseline_diff"]["new_unsupported"] == 1
    assert unsafe_baseline_coverage["baseline_diff"]["new_incomplete_safety"] == 1

    run(
        workflow_command(args.repo, args.work_dir, "missing-execute", missing_source)
        + strict_budget_args(args.repo)
        + ["--execute"]
    )
    missing_summary = load_json(args.work_dir / "missing-execute" / "workflow-summary.json")
    assert missing_summary["stages"][0]["status"] == "passed"
    missing_coverage = load_json(
        args.work_dir / "missing-execute" / "globalopt-coverage" / "globalopt-coverage.json"
    )
    assert missing_coverage["source_status"] == "source-not-found"
    assert missing_coverage["budget_violations"] == []
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
