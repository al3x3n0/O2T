#!/usr/bin/env python3
"""Two recovery paths must never CONTRADICT each other on the same fold.

O2T recovers a fold's obligation two ways:

  * Pass-IR (`o2t/intent/pass_graph.py`) parses the `PatternMatch` matcher TREE structurally --
    `m_Sub(m_Value(Kept), m_Zero())` -- binding `Kept` to the matched sub-term. Used by the agent's
    `recover-fold` action.
  * the lift/cascade (`cv-extract-pass-model.py --symexec`, strategy `symexec-fold-cascade`) builds
    its own guarded-rewrite model and proves each branch WITH and WITHOUT the code's guard.

They are largely complementary -- each declines what the other models -- but where BOTH decide, one
saying `proved` while the other says `unsound` means at least one is wrong, and a `miscompile` is
the more dangerous direction: it is a FALSE REFUTATION, and Track A's headline claim is zero of
those. Nothing gated this before, so the two contradicted each other on four sound folds while the
full 493-test suite passed -- `scalar_more_ops_fixture` asserts those folds are proved, but through
the INTENT pipeline, and no fixture ran the cascade on them.

KNOWN, UNFIXED, AND PINNED. The cascade leaves a matcher's OUTPUT BINDING free: for `foldSubZero`
(`X - 0 -> X`) its counterexample is `{Kept: -1, Op0: 0, Op1: 0}`, a state the match makes
impossible since `m_Value(Kept)` binds `Kept := Op0`. The rewrite then returns an arbitrary value,
`before == after` fails, and the branch is filed `insufficient-guard` -- blaming the pass author for
a constraint the MODEL dropped. This fixture does not paper over that: it pins the exact set so it
cannot GROW, and fails loudly when a contradiction is FIXED so the entry is removed rather than left
to rot. Fix direction: bind properly in the lift path (preferably by reusing `pass_graph` rather
than a third implementation), or DECLINE the shape -- a model that under-constrains must never
refute.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg  # noqa: E402

# Snippets whose folds either path may model. Keep this list broad: the point is to notice a NEW
# contradiction anywhere, not to curate agreement.
SNIPPETS = ("scalar_more_ops_snippet.cpp", "intent_inference_snippet.cpp",
            "poison_side_condition_snippet.cpp", "llvm_pass_snippet.cpp",
            "foldadd_multibranch.cpp")

# (snippet, function) pairs where Pass-IR proves and the cascade calls it a miscompile. Every entry
# is a KNOWN FALSE REFUTATION in the cascade, not a disputed fold: all four are elementary
# identities. Removing an entry is the definition of done for the binding fix.
KNOWN_CONTRADICTIONS = {
    ("scalar_more_ops_snippet.cpp", "foldSubZero"),      # X - 0 -> X
    ("scalar_more_ops_snippet.cpp", "foldOrZero"),       # X | 0 -> X
    ("scalar_more_ops_snippet.cpp", "foldAndAllOnes"),   # X & -1 -> X
    ("scalar_more_ops_snippet.cpp", "foldAndSelf"),      # X & X -> X
}


def _fold_sources(text: str):
    """(name, source) for each top-level fold function in a snippet."""
    out = []
    for m in re.finditer(r"(?m)^(?:Value \*|void )(\w+)\([^)]*\)\s*\{", text):
        depth, start = 0, m.start()
        for j in range(text.index("{", start), len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), text[start:j + 1]))
                    break
    return out


def _cascade(path: Path, z3: str, report: Path) -> dict:
    subprocess.run([sys.executable, str(ROOT / "tools" / "cv-extract-pass-model.py"),
                    "--mine", str(path), "--z3-bin", z3, "--symexec", "--report", str(report)],
                   capture_output=True, text=True, timeout=900)
    if not report.exists():
        return {}
    d = json.loads(report.read_text())
    return {r["function"]: ("unsound" if r["counts"]["unsound"]
                            else "sound" if r["counts"]["sound"] else "none")
            for r in d.get("model_reports", [])}


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("recovery_crosspath_fixture: z3 not found, skipped")
        return 0

    seen, decided = set(), 0
    with tempfile.TemporaryDirectory() as td:
        for snippet in SNIPPETS:
            path = ROOT / "tests" / "fixtures" / snippet
            if not path.exists():
                continue
            casc = _cascade(path, z3, Path(td) / "c.json")
            text = path.read_text()
            for name, src in _fold_sources(text):
                if name not in casc:
                    continue
                pair = pg.recover_from_function(src)
                pir = "declined" if pair is None else pg.reconcile(pair, z3).get("z3", "error")
                if pir == "declined" or casc[name] == "none":
                    continue                      # only one path decided: complementary, not a clash
                decided += 1
                if (pir == "proved") != (casc[name] == "sound"):
                    seen.add((snippet, name))

    assert decided >= 4, ("both paths must decide SOMETHING, or this fixture proves nothing", decided)
    new = seen - KNOWN_CONTRADICTIONS
    assert not new, (
        "NEW cross-path contradiction: Pass-IR and the symexec cascade disagree about whether a "
        "fold is sound. One of them is wrong, and if the cascade says `unsound` it is a FALSE "
        "REFUTATION -- the outcome Track A claims zero of.", sorted(new))
    fixed = KNOWN_CONTRADICTIONS - seen
    assert not fixed, (
        "a KNOWN cross-path contradiction no longer reproduces -- if the cascade's matcher BINDING "
        "was fixed, delete these entries from KNOWN_CONTRADICTIONS so the guard tightens rather "
        "than silently tolerating them", sorted(fixed))

    print(f"recovery_crosspath_fixture OK: {decided} folds decided by BOTH the Pass-IR recovery and "
          f"the symexec cascade; no NEW contradiction, and the {len(KNOWN_CONTRADICTIONS)} known "
          "ones still reproduce exactly. Those four are elementary identities (X-0, X|0, X&-1, X&X) "
          "that Pass-IR proves and the cascade calls miscompiles, because the cascade leaves a "
          "matcher's OUTPUT BINDING free -- a FALSE REFUTATION, pinned here so it cannot spread and "
          "cannot be quietly forgotten once fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
