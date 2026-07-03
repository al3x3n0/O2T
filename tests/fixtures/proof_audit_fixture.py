#!/usr/bin/env python3
"""Cover the proof-meaning meta-verifier (meta/proof_audit.py).

Asserts that across all families every proved deep contract is (1) non-vacuous in its premises
(assumption sets are satisfiable) and (2) load-bearing (every single-point mutation of the
transform is refuted with a witness, no survivors). Also checks the AUDITOR'S OWN teeth: a
deliberately tautological obligation produces a surviving mutant (flagged), and a contradictory
assumption set is reported unsatisfiable -- so a hollow proof cannot slip through. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.meta import proof_audit as pa


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("proof_audit_fixture: z3 not found, skipped")
        return 0

    rep = pa.run_audit(z3)

    # 1) the audit passes: every contract's premises SAT, every mutant killed, no survivors.
    assert rep["ok"], ("proof audit found a gap", rep["survivors"],
                       [p for p in rep["premise_checks"] if not p["ok"]])
    assert rep["survivors"] == [], rep["survivors"]
    assert rep["premises_satisfiable"], rep["premise_checks"]
    assert rep["killed"] == rep["mutants"] and rep["mutants"] >= 18, rep
    assert len(rep["families"]) >= 5, rep["families"]

    # 2) every audited contract proved originally and killed ALL its mutants (load-bearing proof).
    for r in rep["rows"]:
        assert r["original_proved"], ("audited contract no longer proves", r["contract"])
        assert all(m["killed"] for m in r["mutants"]), ("survivor", r["contract"], r["survivors"])

    # 3) AUDITOR TEETH (a): a tautological obligation has a SURVIVING mutant -> flagged not-ok.
    #    Both "original" and every "mutant" prove `a == a` regardless -> nothing is killed.
    taut = pa.mutation_kill(
        "tautology", "synthetic",
        lambda: _prove_trivial(z3),
        [("identity-still-proves", lambda: _prove_trivial(z3))])
    assert not taut["ok"] and taut["survivors"], ("auditor failed to flag a vacuous proof", taut)

    # 4) AUDITOR TEETH (b): a contradictory assumption set is reported unsatisfiable.
    assert pa.assumptions_satisfiable(z3, [{"op": "eq", "args": ["p", "q"]}])      # consistent
    assert not pa.assumptions_satisfiable(                                          # p==q AND p!=q
        z3, [{"op": "eq", "args": ["p", "q"]}, {"op": "ne", "args": ["p", "q"]}])

    # 5) the CLI agrees and exits 0.
    tool = ROOT / "tools" / "cv-audit-proofs.py"
    proc = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"ok": true' in proc.stdout, proc.stdout
    assert '"survivors": 0' in proc.stdout, proc.stdout

    print(f"proof_audit_fixture OK: {rep['contracts_audited']} proved contracts across "
          f"{len(rep['families'])} families audited -- all premises satisfiable, all "
          f"{rep['mutants']} single-point mutations killed with witnesses, no survivors; "
          "the auditor itself flags a tautological proof and a contradictory premise set")
    return 0


def _prove_trivial(z3_bin):
    """A deliberately vacuous obligation: `a == a`, valid no matter what."""
    smt = "\n".join(["(set-logic QF_BV)", "(declare-const a (_ BitVec 32))",
                     "(assert (not (= a a)))", "(check-sat)", ""])
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True).stdout
    head = out.strip().splitlines()[0].strip() if out.strip() else "error"
    return ("proved" if head == "unsat" else "refuted"), {}


if __name__ == "__main__":
    raise SystemExit(main())
