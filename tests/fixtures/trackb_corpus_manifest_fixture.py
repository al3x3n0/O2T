#!/usr/bin/env python3
"""Pin the Track B corpus manifest's self-consistency (offline; no network, no z3).

WHY THIS EXISTS. The published Track B reach figure (1,705/1,835) was measured against local,
unpinned copies of LLVM's InstCombine tests that no longer existed, and the FILE LIST was recorded
nowhere in the repo -- so the headline could not be regenerated from a clean checkout. Worse, the
obvious way to refetch (`release/18.x`) is a MOVING branch: by 2026-09-03 those nine files summed to
1,937 functions, not 1,835. A denominator pinned to a moving branch decays into folklore, and a
figure nobody can reproduce is indistinguishable from one nobody measured.

WHAT THIS CAN AND CANNOT CHECK. Offline, it checks the manifest is internally coherent and that the
fetch script cannot drift away from it. It CANNOT verify the hashes -- that requires the corpus, and
vendoring ~2 MB of upstream test IR is what the manifest exists to avoid. Hash verification is
`cv-fetch-trackb-corpus.sh`'s job and it fails loudly on mismatch (verified by tampering). So this
fixture is deliberately NOT a corpus check: it is a check that the corpus is *nameable*.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "fixtures" / "trackb_corpus_manifest.json"
FETCH = ROOT / "tools" / "cv-fetch-trackb-corpus.sh"


def main() -> int:
    man = json.loads(MANIFEST.read_text())

    # A TAG, not a branch. This is the whole point: `release/18.x` moves, `llvmorg-18.1.8` does not.
    tag = man["tag"]
    assert re.fullmatch(r"llvmorg-\d+\.\d+\.\d+", tag), \
        ("the corpus must be pinned to an immutable release TAG -- a branch name here would "
         "reintroduce exactly the drift this manifest was written to stop", tag)
    assert "{tag}" in man["upstream"] and "{file}" in man["upstream"], man["upstream"]

    files = man["files"]
    assert len(files) == 9, ("all nine InstCombine test files", sorted(files))
    for name, rec in files.items():
        assert name.endswith(".ll"), name
        assert re.fullmatch(r"[0-9a-f]{64}", rec["sha256"]), (name, rec["sha256"])
        assert isinstance(rec["functions"], int) and rec["functions"] > 0, (name, rec)

    # The denominator must be the sum of its parts. A hand-edited total is how a headline drifts
    # away from the corpus it was measured on.
    total = sum(r["functions"] for r in files.values())
    assert man["total_functions"] == total, \
        ("total_functions must equal the sum of per-file counts", man["total_functions"], total)

    # THE FETCH SCRIPT MUST READ THIS FILE, not carry its own copy of the list. Two lists drift;
    # one list cannot. If the script stops referencing the manifest, this fails.
    script = FETCH.read_text()
    assert FETCH.stat().st_mode & 0o111, "fetch script must be executable"
    assert "trackb_corpus_manifest.json" in script, \
        ("the fetch script must derive tag/files/hashes FROM the manifest -- a second hard-coded "
         "list is how the two go out of sync silently", FETCH)
    for name in files:
        assert name not in script, \
            (f"{name} is hard-coded in the fetch script; it must come from the manifest", name)

    print(f"trackb_corpus_manifest_fixture OK: {len(files)} files pinned at {tag}, "
          f"{total} functions, totals consistent, fetch script derives everything from the "
          f"manifest (hash VERIFICATION is the script's job and needs the network)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
