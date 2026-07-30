#!/usr/bin/env python3
"""Differential against reference Alive2 -- the independent GROUND-TRUTH oracle O2T otherwise lacks.

O2T's whole soundness argument rests on hand-written SMT encodings. Its internal cross-checks share
those encodings (the second solver reuses the same SMT text), and its execution oracle
(o2t/validate/concrete_tv.py, via `lli`) is a VALUE oracle -- it cannot see a purely-POISON difference
(an unjustified `nsw`/`exact`, where the values agree). Reference Alive2 is the missing piece: it
independently models LLVM's poison/undef/UB refinement, so a poison-encoding bug in O2T that both z3
(same encoding) and lli (value-only) miss is caught here.

`alive_refines` runs `alive-tv before.ll after.ll` (target refines source, function-wise) and reads its
`Summary:` block -- CRITICAL: alive-tv exits 0 even when it finds an INCORRECT transform, so the verdict
is the text, not the exit code. `differential` runs O2T's whole-function TV and Alive2 on the same pair
and reports agreement -- an O2T `proved` that Alive2 calls incorrect is an O2T FALSE PROOF; an O2T
`refuted` that Alive2 calls correct is a FALSE REFUTATION.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

_CORRECT = re.compile(r"(\d+)\s+correct transformations")
_INCORRECT = re.compile(r"(\d+)\s+incorrect transformations")


def _classify(output: str) -> tuple[str, str]:
    """alive-tv stdout -> (proved | refuted | skip, detail).

    `skip` covers THREE different situations, and collapsing them loses information a caller needs:
    alive-tv could not parse the input, or it parsed and could not decide (a timeout or a query too
    hard for it), or it was never reached at all. A caller that treats `skip` as agreement is
    treating a NON-ANSWER as confirmation -- and Alive2 times out more readily than one expects, e.g.
    on `(A&B)^(A|B) -> A^B` when the arguments are not `noundef`, because undef semantics quantify
    over every use of a multiply-used argument. The detail says which kind of silence it was.
    """
    cm, im = _CORRECT.search(output), _INCORRECT.search(output)
    if cm is None and im is None:
        return "skip", "unparsed"                     # alive-tv could not parse / crashed
    incorrect = int(im.group(1)) if im else 0
    correct = int(cm.group(1)) if cm else 0
    if incorrect:
        return "refuted", "incorrect"                 # target does NOT refine source
    if correct:
        return "proved", "correct"
    detail = "timeout" if "Timeout" in output else "failed-to-prove"
    return "skip", detail                             # 0 correct, 0 incorrect


def alive_refines(before_ll: str, after_ll: str, alive_bin: str = "alive-tv", timeout: int = 60) -> dict:
    """Ask Alive2 whether `after` refines `before` (function-wise). Returns {status, ...}."""
    with tempfile.TemporaryDirectory() as d:
        bf, af = Path(d) / "before.ll", Path(d) / "after.ll"
        bf.write_text(before_ll)
        af.write_text(after_ll)
        try:
            out = subprocess.run([alive_bin, str(bf), str(af)], capture_output=True, text=True,
                                 errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "skip", "detail": "unavailable", "reason": "alive-tv unavailable or timed out"}
    status, detail = _classify(out.stdout + out.stderr)
    return {"status": status, "detail": detail}


def differential(z3_bin: str, alive_bin: str, before_ll: str, after_ll: str, func: str,
                 timeout: int = 30) -> dict:
    """Cross-check O2T's whole-function TV against Alive2 on the same pair. Returns a dict with
    `verdict`: agree | o2t-false-proof | o2t-false-refutation | inconclusive, plus both sub-verdicts.
    A disagreement where O2T is the more-permissive one is a soundness bug in O2T's encoding."""
    from o2t.validate import scalar_ir as si
    o2t = si.validate_transform(z3_bin, before_ll, after_ll, func, timeout=timeout)["status"]
    alive = alive_refines(before_ll, after_ll, alive_bin, timeout=max(timeout, 60))["status"]
    if o2t == "proved" and alive == "refuted":
        verdict = "o2t-false-proof"                   # O2T proved, Alive2 says unsound -> encoding bug
    elif o2t == "refuted" and alive == "proved":
        verdict = "o2t-false-refutation"
    elif o2t in ("proved", "refuted") and o2t == alive:
        verdict = "agree"
    else:
        verdict = "inconclusive"                      # O2T unsupported / Alive2 skip / one declined
    return {"verdict": verdict, "o2t": o2t, "alive2": alive, "function": func}
