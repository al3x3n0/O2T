#!/usr/bin/env python3
"""A second solver that cannot answer must not be reported as agreeing OR as disagreeing.

WHY THIS EXISTS. `meta.cross_check.run_solver` ran the replay solver with NO timeout. Measured: one
bitwuzla replay held a whole-corpus `--cross-check` for 78 minutes at 99% CPU while the parent sat
idle waiting on it, and since `--cross-check` also forces `jobs=1`, nothing else progressed either.
That is very likely why this cross-check kept being reported as "one commit-range behind" -- not
neglect, but a run that could not finish. After the bound, one file (`xor.ll`, 106 functions)
cross-checks in 5m07s.

The timeout alone would have been a soundness bug in the other direction. `agree` was computed as
`all(r == expect)`, so the string "timeout" compares unequal and the replay would be reported
`disagree` -- which this pipeline surfaces as a possible FALSE PROOF and exits non-zero on. Crying
false-proof because a solver was slow is how a real disagreement gets lost in the noise. The
project's discipline is that an absent answer is a DECLINE, never a verdict in either direction, so
a non-answer now yields `inconclusive`, and the corpus layer keeps those functions OUT of the
`cross_checked` count -- "independently confirmed" has to mean the oracle actually answered.

Needs z3 (it is the `expect`-producing solver here). No network.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.meta.cross_check import SOLVER_TIMEOUT, run_solver  # noqa: E402
from o2t.validate.scalar_ir import cross_check_smt  # noqa: E402

# A trivially unsat query: `(assert false)` -- every real solver answers `unsat` at once.
SMT = "(set-logic QF_BV)\n(assert false)\n(check-sat)\n"


def _fake_solver(tmp: Path, name: str, body: str) -> str:
    p = tmp / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def main() -> int:
    if shutil.which("z3") is None:
        print("cross_solver_timeout_fixture: z3 not found, skipped")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) THE BOUND EXISTS AND IS ENFORCED. A solver that never answers must be cut off, and
        #    must be cut off in about the timeout -- not "eventually".
        hangs = _fake_solver(tmp, "hangs", "sleep 600")
        t0 = time.monotonic()
        verdict = run_solver("hangs", hangs, SMT, timeout=2.0)
        elapsed = time.monotonic() - t0
        assert verdict == "timeout", ("a solver that does not answer must report `timeout`", verdict)
        assert elapsed < 30, ("the timeout must actually bound the call -- an unbounded replay is "
                              "what held a corpus run for 78 minutes", elapsed)
        #    ...and the default must be finite. A `None` default would reintroduce the stall.
        assert isinstance(SOLVER_TIMEOUT, (int, float)) and 0 < SOLVER_TIMEOUT <= 300, SOLVER_TIMEOUT

        # 2) A NON-ANSWER IS `inconclusive`, NOT `disagree`. This is the assertion that matters:
        #    before the fix "timeout" != "unsat" made this `disagree`, and the corpus tool reports a
        #    disagreement as a possible FALSE PROOF and exits 1. A slow solver would have raised a
        #    false alarm on a sound proof.
        res = cross_check_smt(SMT, "unsat", z3_bin="z3",
                              extra_solvers=(("hangs", _fake_solver(tmp, "h2", "sleep 600")),))
        assert res["status"] == "inconclusive", \
            ("a solver that could not answer must leave the replay INCONCLUSIVE -- reporting it as "
             "`disagree` cries false-proof over a slow solver", res)
        assert "hangs" in res.get("no_answer", []), res
        #    An errored solver is the same case: no answer, not a contradiction.
        res_err = cross_check_smt(SMT, "unsat", z3_bin="z3",
                                  extra_solvers=(("broken", _fake_solver(tmp, "b", "exit 3")),))
        assert res_err["status"] == "inconclusive", res_err

        # 3) THE TEETH. A solver that DOES answer and contradicts must still be `disagree`. Without
        #    this, assertion 2 could be satisfied by a change that simply stopped reporting
        #    disagreements at all -- which would silently disable the entire solver oracle.
        liar = _fake_solver(tmp, "liar", "cat >/dev/null; echo sat")
        res_bad = cross_check_smt(SMT, "unsat", z3_bin="z3", extra_solvers=(("liar", liar),))
        assert res_bad["status"] == "disagree", \
            ("a solver that ANSWERS and contradicts is a real disagreement and must still be "
             "reported -- this is the oracle's whole purpose", res_bad)

        # 4) And the ordinary case still passes: a solver that answers correctly agrees.
        honest = _fake_solver(tmp, "honest", "cat >/dev/null; echo unsat")
        res_ok = cross_check_smt(SMT, "unsat", z3_bin="z3", extra_solvers=(("honest", honest),))
        assert res_ok["status"] == "agree", res_ok
        assert "no_answer" not in res_ok, res_ok

    print("cross_solver_timeout_fixture OK: an unbounded replay is bounded (default "
          f"{SOLVER_TIMEOUT}s); a solver that cannot answer yields `inconclusive` rather than a "
          "false-proof alarm; one that answers and contradicts is still `disagree`; one that "
          "answers correctly still agrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
