#!/usr/bin/env python3
"""Golden-output regression harness for the optimization-intent inference tool.

Runs ``cv-infer-optimization-intent.py`` over a corpus of pass-source snippets
with a fixed miner and emits a deterministic, normalized digest of every intent
candidate. Used to prove that refactors of the inference pipeline (e.g. the
formal-derivation dispatch) keep byte-identical output.

  --capture FILE   run the corpus, write the digest to FILE
  --check FILE     run the corpus, compare to FILE, exit nonzero + diff on drift

The digest is JSON Lines, one canonicalized candidate per line (sort_keys),
ordered by (snippet, marker, line, marker-index) so the result is independent of
filesystem and miner ordering.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFER = ROOT / "tools" / "cv-infer-optimization-intent.py"
DEFAULT_MINER = ROOT / "build-clang-tools" / "cv-mine-pass-source-ast"
DEFAULT_CORPUS = ROOT / "tests" / "fixtures"


def run_inference(snippet: Path, miner: Path) -> list[dict]:
    with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as tmp:
        out = Path(tmp.name)
    try:
        proc = subprocess.run(
            [sys.executable, str(INFER), str(snippet), "--miner", str(miner),
             "--format", "jsonl", "--out", str(out)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return []
        records = []
        for line in out.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records
    finally:
        out.unlink(missing_ok=True)


# Fields whose value depends on the host environment (installed tools), not on the
# mined source -- excluded from the digest so the golden is machine-independent.
ENV_DEPENDENT_FIELDS = ("proof_tool_available",)

# Absolute paths to the mined snippet appear in several fields (the top-level
# `file` and nested `source_range.file` records). They are host/checkout dependent
# -- the repo's location and even its directory name (it was renamed once) -- so
# they are rewritten to a stable repo-relative form, keeping the golden machine-
# independent as documented above. Only the inference CONTENT is digested.
_REPO_PREFIX = str(ROOT) + "/"


def _normalize_paths(value):
    """Recursively rewrite absolute repo paths to repo-relative form."""
    if isinstance(value, str):
        return value[len(_REPO_PREFIX):] if value.startswith(_REPO_PREFIX) else value
    if isinstance(value, list):
        return [_normalize_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_paths(item) for key, item in value.items()}
    return value


def build_digest(corpus: Path, miner: Path, pattern: str,
                 only: set[str] | None = None) -> str:
    rows: list[tuple] = []
    snippets = sorted(corpus.glob(pattern))
    if only:
        snippets = [s for s in snippets if s.name in only]
    for snippet in snippets:
        records = run_inference(snippet, miner)
        for index, record in enumerate(records):
            key = (snippet.name, str(record.get("marker", "")),
                   int(record.get("line", 0) or 0), index)
            # Drop environment-dependent fields so the digest captures inference
            # CONTENT, not the host's installed toolchain. proof_tool_available is
            # `shutil.which("alive-tv") is not None` -- true/false by machine.
            for env_field in ENV_DEPENDENT_FIELDS:
                record.pop(env_field, None)
            rows.append((key, _normalize_paths(record)))
    rows.sort(key=lambda item: item[0])
    lines = [json.dumps(record, sort_keys=True) for _key, record in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", type=Path, help="write digest to this file")
    group.add_argument("--check", type=Path, help="compare against this digest")
    ap.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--pattern", default="*snippet*.cpp")
    ap.add_argument("--snippets", default="",
                    help="comma-separated basenames to restrict the corpus to")
    args = ap.parse_args()
    only = {name.strip() for name in args.snippets.split(",") if name.strip()} or None

    if not args.miner.exists():
        print(f"golden: miner not found: {args.miner}", file=sys.stderr)
        return 2

    digest = build_digest(args.corpus, args.miner, args.pattern, only)
    record_count = digest.count("\n")

    if args.capture:
        args.capture.parent.mkdir(parents=True, exist_ok=True)
        args.capture.write_text(digest)
        print(f"golden: captured {record_count} candidate(s) -> {args.capture}")
        return 0

    expected = args.check.read_text() if args.check.exists() else ""
    if digest == expected:
        print(f"golden: OK, {record_count} candidate(s) match {args.check.name}")
        return 0
    diff = difflib.unified_diff(
        expected.splitlines(keepends=True), digest.splitlines(keepends=True),
        fromfile="golden", tofile="current", n=1,
    )
    sys.stdout.writelines(diff)
    print(f"\ngolden: DRIFT ({record_count} current vs {expected.count(chr(10))} golden)",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
