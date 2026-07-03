#!/usr/bin/env python3
"""Regression fixture for campaign-level GlobalOpt witness evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


MARKER = "probe.globalopt.dead-initializer"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def write_script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o755)
    return path


def run(command: list[str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expect:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(f"{command} returned {result.returncode}, expected {expect}")
    return result


def globalopt_coverage(path: Path, status: str) -> None:
    reasons = [] if status == "passed" else ["before-llvm-as-failed: failed"]
    write_json(path, {
        "model": "o2t-globalopt-coverage-v1",
        "witnesses": {
            "enabled": True,
            "required_cases": ["i32", "ptr", "array"],
            "records": [
                {
                    "key": f"GlobalOpt.cpp|321|{MARKER}",
                    "marker": MARKER,
                    "file": "GlobalOpt.cpp",
                    "line": 321,
                    "status": status,
                    "before": str(path.parent / "before.ll"),
                    "after": str(path.parent / "after.ll"),
                    "required_cases": ["i32", "ptr", "array"],
                    "missing_required_cases": [] if status == "passed" else ["i32"],
                    "cases": [
                        {
                            "name": name,
                            "status": status,
                            "failure_reasons": [] if status == "passed" else [f"{name}-failed"],
                        }
                        for name in ["i32", "ptr", "array"]
                    ],
                    "failure_reasons": reasons,
                }
            ],
        },
    })


def fixture_tools(work_dir: Path) -> dict[str, Path]:
    tools = work_dir / "tools"
    validated_record = {
        "marker": MARKER,
        "file": "GlobalOpt.cpp",
        "line": 321,
        "proof_status": "proved",
        "proof_result": "unsat",
        "promotion_status": "ready",
        "confidence": "high",
        "intent_candidate": {
            "marker": MARKER,
            "intent": "global-initializer-observable-equivalence",
            "precondition": "local global initializer is dead and has no uses",
            "rewrite": "replace the initializer with a default null initializer",
        },
        "evidence": {
            "formal_inference": "source-derived-intent-graph",
            "formal_parameters": {
                "global.initializer.safety_status": "complete",
                "global.initializer.observed_safety_facts": ["initializer-dead", "local-linkage", "no-uses"],
                "global.initializer.missing_safety_facts": [],
            },
        },
    }
    validated_json = json.dumps(validated_record, sort_keys=True)
    return {
        "miner": write_script(tools / "miner.py", "#!/usr/bin/env python3\nimport json\nprint('[]')\n"),
        "inferer": write_script(
            tools / "inferer.py",
            "#!/usr/bin/env python3\n"
            "import argparse, pathlib\n"
            "p=argparse.ArgumentParser(); p.add_argument('--out'); p.add_argument('--findings'); "
            "p.add_argument('--format'); p.add_argument('--min-confidence'); a=p.parse_args()\n"
            "pathlib.Path(a.out).write_text('[]\\n')\n",
        ),
        "validator": write_script(
            tools / "validator.py",
            "#!/usr/bin/env python3\n"
            "import argparse, pathlib\n"
            f"record = {validated_json!r}\n"
            "p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--out'); p.add_argument('--z3'); a=p.parse_args()\n"
            "pathlib.Path(a.out).write_text(record + '\\n')\n",
        ),
        "converter": write_script(
            tools / "converter.py",
            "#!/usr/bin/env python3\n"
            "import argparse, pathlib\n"
            "p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--out-dir'); p.add_argument('--replay'); p.add_argument('--reducer'); a=p.parse_args()\n"
            "pathlib.Path(a.out_dir).mkdir(parents=True, exist_ok=True)\n",
        ),
        "opt_checker": write_script(
            tools / "opt-checker.py",
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "root=pathlib.Path(sys.argv[-1]); out=root/'opt'; out.mkdir(parents=True, exist_ok=True); (out/'manifest.jsonl').write_text('')\n",
        ),
        "summarizer": write_script(
            tools / "summarizer.py",
            "#!/usr/bin/env python3\n"
            "import argparse, pathlib\n"
            "p=argparse.ArgumentParser(); p.add_argument('manifest'); p.add_argument('--out'); a=p.parse_args(); pathlib.Path(a.out).write_text('summary\\n')\n",
        ),
        "noop": write_script(tools / "noop.py", "#!/usr/bin/env python3\n"),
    }


def campaign_command(repo: Path, work_dir: Path, tools: dict[str, Path], out_name: str) -> list[str]:
    source = work_dir / "GlobalOpt.cpp"
    source.write_text("// fixture\n", encoding="utf-8")
    return [
        sys.executable,
        str(repo / "tools" / "cv-run-campaign.py"),
        str(source),
        "--out",
        str(work_dir / out_name),
        "--emit-intent-evidence",
        "--miner",
        str(tools["miner"]),
        "--intent-inferer",
        str(tools["inferer"]),
        "--intent-validator",
        str(tools["validator"]),
        "--constraints-to-configs",
        str(tools["converter"]),
        "--opt-checker",
        str(tools["opt_checker"]),
        "--summarizer",
        str(tools["summarizer"]),
        "--llm-reviewer",
        str(tools["noop"]),
        "--replay",
        str(tools["noop"]),
        "--reducer",
        str(tools["noop"]),
        "--z3",
        sys.executable,
    ]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def intent_by_marker(records: list[dict[str, Any]], marker: str) -> dict[str, Any]:
    for record in records:
        if record.get("marker") == marker:
            return record
    raise AssertionError(f"missing promoted intent for {marker}")


def main() -> int:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    tools = fixture_tools(args.work_dir)
    passing_coverage = args.work_dir / "globalopt-passed.json"
    failing_coverage = args.work_dir / "globalopt-failed.json"
    globalopt_coverage(passing_coverage, "passed")
    globalopt_coverage(failing_coverage, "failed")

    run(
        campaign_command(args.repo, args.work_dir, tools, "passed")
        + [
            "--require-intent-evidence",
            "--globalopt-coverage",
            str(passing_coverage),
        ]
    )
    passed = load_jsonl(args.work_dir / "passed" / "intent-evidence.jsonl")[0]
    assert passed["evidence_status"] == "verified"
    assert passed["globalopt_witness_status"] == "passed"
    commands = (args.work_dir / "passed" / "commands.log").read_text(encoding="utf-8")
    assert "--globalopt-coverage" in commands

    run(
        campaign_command(args.repo, args.work_dir, tools, "passed-promotion")
        + [
            "--require-intent-evidence",
            "--globalopt-coverage",
            str(passing_coverage),
            "--promote-intents",
            "--replace-existing-intents",
            "--require-promotable-intent",
        ]
    )
    proposed = load_json(args.work_dir / "passed-promotion" / "proposed-optimization-intents.json")
    promoted = intent_by_marker(proposed, MARKER)
    promoted_evidence = promoted["evidence"]
    assert promoted_evidence["evidence_status"] == "verified"
    assert promoted_evidence["globalopt_witness_status"] == "passed"
    assert promoted_evidence["globalopt_witness_before"].endswith("before.ll")
    assert promoted_evidence["globalopt_witness_after"].endswith("after.ll")
    assert promoted_evidence["globalopt_witness_manifest"].endswith("witness.json")
    assert promoted_evidence["globalopt_witness"]["status"] == "passed"
    promotion_report = (args.work_dir / "passed-promotion" / "intent-promotion-report.txt").read_text(encoding="utf-8")
    assert "GlobalOpt witness status" in promotion_report
    assert "passed: 1" in promotion_report
    assert "globalopt_witness=passed" in promotion_report
    promotion_commands = (args.work_dir / "passed-promotion" / "commands.log").read_text(encoding="utf-8")
    assert "cv-promote-intent-candidates.py" in promotion_commands
    assert "--replace-existing" in promotion_commands
    assert "--require-verified-evidence" in promotion_commands

    failed_result = run(
        campaign_command(args.repo, args.work_dir, tools, "failed-budget")
        + [
            "--globalopt-coverage",
            str(failing_coverage),
            "--max-globalopt-witness-failures",
            "0",
        ],
        expect=1,
    )
    assert "globalopt witness failures: 1 limit=0" in failed_result.stderr

    required_result = run(
        campaign_command(args.repo, args.work_dir, tools, "required-absent")
        + [
            "--require-intent-evidence",
            "--require-globalopt-witnesses",
        ],
        expect=1,
    )
    assert "intent evidence issues: 1" in required_result.stderr
    required = load_jsonl(args.work_dir / "required-absent" / "intent-evidence.jsonl")[0]
    assert required["evidence_status"] == "blocked"
    assert required["globalopt_witness_status"] == "absent"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
