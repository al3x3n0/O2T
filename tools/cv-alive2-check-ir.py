#!/usr/bin/env python3
"""Run an Alive2-compatible checker on a before/after LLVM IR pair."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def classify(exit_code: int, output: str) -> tuple[str, str]:
    """Classify by Alive2's Summary counts, NOT the exit code.

    CRITICAL: alive-tv exits 0 even when it finds an INCORRECT transformation, so
    trusting the exit code reports real miscompiles as "proved". The authoritative
    signal is the "Summary:" block ("N correct / M incorrect / K failed-to-prove /
    L Alive2 errors")."""
    text = output.lower()

    def count(label: str) -> int | None:
        m = re.search(r"(\d+)\s+" + re.escape(label), text)
        return int(m.group(1)) if m else None

    correct = count("correct transformations")
    incorrect = count("incorrect transformations")
    failed_to_prove = count("failed-to-prove transformations")
    errors = count("alive2 errors")

    if correct is None and incorrect is None:
        # No summary emitted -> a parse/setup failure, not a refinement verdict.
        if "error:" in text or "fatal" in text or "invalid" in text:
            return "error", "alive2 reported an error (no summary block)"
        return "error", "alive2 produced no summary block"
    if incorrect:
        return "failed", f"alive2 found {incorrect} incorrect transformation(s)"
    if errors:
        return "error", f"alive2 reported {errors} error(s)"
    if failed_to_prove:
        return "unsupported", f"alive2 could not prove {failed_to_prove} transformation(s)"
    if correct:
        return "proved", f"alive2 proved {correct} transformation(s) correct"
    return "failed", "alive2 summary inconclusive"


SOUND_BEFORE = "define i32 @f(i32 %x) {\n  %r = add i32 %x, 0\n  ret i32 %r\n}\n"
SOUND_AFTER = "define i32 @f(i32 %x) {\n  ret i32 %x\n}\n"
UNSOUND_AFTER = "define i32 @f(i32 %x) {\n  %r = shl i32 %x, 1\n  ret i32 %r\n}\n"


def run_alive(executable: str, before: Path, after: Path):
    completed = subprocess.run([executable, str(before), str(after)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, check=False)
    return classify(completed.returncode, completed.stdout or ""), completed


def selftest(alive_tv: str, out: Path | None) -> int:
    """Real Alive2 must PROVE x+0->x and REFUTE the unsound x->x<<1. This is the
    teeth on the exit-code fix: alive-tv returns 0 even when incorrect."""
    executable = shutil.which(alive_tv)
    if executable is None:
        print(json.dumps({"alive2_status": "skipped", "reason": f"{alive_tv} not found"}))
        return 0
    import tempfile
    cases = []
    with tempfile.TemporaryDirectory() as d:
        bp = Path(d) / "before.ll"
        bp.write_text(SOUND_BEFORE)
        for label, after_ir, want in (("sound", SOUND_AFTER, "proved"),
                                      ("unsound", UNSOUND_AFTER, "failed")):
            ap = Path(d) / f"{label}.ll"
            ap.write_text(after_ir)
            (status, message), _ = run_alive(executable, bp, ap)
            cases.append({"case": label, "expected": want, "status": status,
                          "message": message, "ok": status == want})
    ok = all(c["ok"] for c in cases)
    report = {"alive2_selftest": cases, "ok": ok, "alive_tv": executable}
    if out:
        write_result(out, report)
    print(json.dumps({"ok": ok, "cases": cases}, sort_keys=True))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, help="input LLVM IR before optimization")
    parser.add_argument("--after", type=Path, help="input LLVM IR after optimization")
    parser.add_argument("--alive-tv", default="alive-tv", help="Alive2 checker executable")
    parser.add_argument("--out", type=Path, help="write JSON result to this path")
    parser.add_argument("--output-log", type=Path, help="write raw Alive2 stdout/stderr here")
    parser.add_argument("--selftest", action="store_true",
                        help="prove a sound pair and refute an unsound one via real Alive2")
    args = parser.parse_args()

    if args.selftest:
        return selftest(args.alive_tv, args.out)
    if args.before is None or args.after is None:
        parser.error("--before and --after are required (or use --selftest)")

    result = {
        "alive2_status": "not-run",
        "alive2_exit_code": None,
        "alive2_message": "",
        "alive2_output": str(args.output_log) if args.output_log else "",
    }

    for label, path in (("before", args.before), ("after", args.after)):
        if not path.exists():
            result.update(
                {
                    "alive2_status": "error",
                    "alive2_exit_code": None,
                    "alive2_message": f"{label} IR does not exist: {path}",
                }
            )
            write_result(args.out, result)
            print(json.dumps(result, sort_keys=True))
            return 1

    executable = shutil.which(args.alive_tv)
    if executable is None:
        result.update(
            {
                "alive2_status": "error",
                "alive2_exit_code": None,
                "alive2_message": f"Alive2 executable not found: {args.alive_tv}",
            }
        )
        write_result(args.out, result)
        print(json.dumps(result, sort_keys=True))
        return 1

    completed = subprocess.run(
        [executable, str(args.before), str(args.after)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    output = completed.stdout or ""
    if args.output_log:
        args.output_log.parent.mkdir(parents=True, exist_ok=True)
        args.output_log.write_text(output, encoding="utf-8")

    status, message = classify(completed.returncode, output)
    result.update(
        {
            "alive2_status": status,
            "alive2_exit_code": completed.returncode,
            "alive2_message": message,
        }
    )
    write_result(args.out, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if status in {"proved", "unsupported"} else 1


def write_result(path: Path | None, result: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
