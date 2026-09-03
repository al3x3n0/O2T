#!/usr/bin/env python3
"""Does a check's verdict say anything about the PASS it is attached to?

A verdict can be entirely real and still be about nothing in particular, and a headline that
collapses such a verdict into `proved` certifies code it never read. Two ways this happens:

- A CANONICAL strategy discharges FIXED contracts. `memory-model` runs `cv-validate-memory` with no
  `--source` at all; its DSE/forwarding theorems hold whatever pass you point it at.

- A PASS-RUNNER strategy validates a real `opt` run, which IS about the pass -- but only when the
  pass being verified is the pass being RUN. `instcombine-ir` carries `canonical_pass=instcombine`,
  and `plan.py` deliberately keeps it feasible even when the pass under verification is unknown
  (`not strat.canonical_pass and not _runnable_pass(...)`), so it falls back to running InstCombine
  on canonical IR. That proves something true about LLVM and nothing about the vendor pass in front
  of it.

MEASURED, NOT THEORISED. A vendor pass whose source contains a planted unsound FP reduction --
`Builder.CreateFAddReduce` with no reassoc guard -- classifies `peephole` on its matcher idioms.
Its one SOURCE-targeted check, `symexec-fold-cascade`, answered `inconclusive` ("no fold functions
mined"). The other five checks -- three canonical-fallback pass-runners and two canonical -- all
answered `proved`, and the pass was reported **proved**. Nothing had read the planted bug; the
headline was assembled from theorems about other code.

FAIL SAFE IN THE OTHER DIRECTION. An unknown strategy counts as about the pass, so a new one must
opt OUT deliberately -- the alternative silently drops verdicts, and dropping a REFUTATION hides a
miscompile. Refutations are never filtered by attribution for the same reason: this module is
consulted for whether a POSITIVE verdict may certify a pass, not for whether a negative one may
accuse it.
"""

from __future__ import annotations


def is_about_pass(strategy: str, pass_name: str | None) -> bool:
    """True when a POSITIVE verdict from `strategy` may be attributed to `pass_name`."""
    from o2t.orchestrate.plan import STRATEGIES
    spec = STRATEGIES.get(str(strategy or ""))
    if spec is None:
        return True                                   # unknown: fail safe, must opt out explicitly
    if spec.target == "canonical":
        return False
    if spec.target == "pass-runner":
        canonical = str(getattr(spec, "canonical_pass", "") or "")
        if canonical and str(pass_name or "").strip().lower() != canonical.lower():
            return False                              # ran its canonical fallback, not this pass
    return True


def split_by_attribution(checks, pass_name: str | None):
    """(attributable, unattributed) partition of `checks` by `is_about_pass`."""
    about, other = [], []
    for check in checks:
        (about if is_about_pass(check.get("strategy"), pass_name) else other).append(check)
    return about, other
