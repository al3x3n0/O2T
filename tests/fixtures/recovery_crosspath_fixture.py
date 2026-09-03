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
KNOWN_CONTRADICTIONS: set = set()      # was four; the cascade now DECLINES those shapes instead


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

    seen, decided, cascade_verdicts, passir_proofs = set(), 0, 0, 0
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
                cascade_verdicts += casc[name] in ("sound", "unsound")
                passir_proofs += pir == "proved"
                # THE PROPERTY, stated so it holds even when the overlap is EMPTY: the cascade must
                # never call UNSOUND a fold the Pass-IR recovery proves. A decline on either side is
                # not a clash -- the paths are largely complementary by design.
                if pir == "proved" and casc[name] == "unsound":
                    seen.add((snippet, name))
                if pir == "declined" or casc[name] == "none":
                    continue
                decided += 1
                if (pir == "proved") != (casc[name] == "sound"):
                    seen.add((snippet, name))

    # NEITHER PATH MAY GO SILENT, or the contradiction check below passes vacuously. The overlap
    # where BOTH decide the same fold is allowed to be empty (a correct decline shrinks it -- which
    # is exactly what fixing the cascade's incomplete-guard refutations did), so what is pinned is
    # that each path still produces verdicts of its own.
    assert cascade_verdicts >= 8, ("the cascade must still decide folds", cascade_verdicts)
    assert passir_proofs >= 4, ("Pass-IR must still prove folds", passir_proofs)
    new = seen - KNOWN_CONTRADICTIONS
    assert not new, (
        "NEW cross-path contradiction: Pass-IR and the symexec cascade disagree about whether a "
        "fold is sound. One of them is wrong, and if the cascade says `unsound` it is a FALSE "
        "REFUTATION -- the outcome Track A claims zero of.", sorted(new))
    fixed = KNOWN_CONTRADICTIONS - seen
    assert not fixed, (
        "a KNOWN cross-path contradiction no longer reproduces -- delete these entries from "
        "KNOWN_CONTRADICTIONS so the guard tightens rather than silently tolerating them",
        sorted(fixed))

    print(f"recovery_crosspath_fixture OK: the symexec cascade decided {cascade_verdicts} folds and "
          f"the Pass-IR recovery proved {passir_proofs}; NO fold is proved by one and called a "
          "miscompile by the other. It used to be four -- X-0, X|0, X&-1, X&X, all elementary "
          "identities -- because an unrecognised guard clause was DROPPED, weakening the path "
          "condition until the obligation failed on a state the match makes impossible. Dropping a "
          "clause is harmless for a PROOF (a weaker guard is a stronger result) and unsound for a "
          "REFUTATION, so the cascade now declines those instead: a model that under-constrains "
          "must never refute")
    return 0


if __name__ == "__main__":
    sys.exit(main())
