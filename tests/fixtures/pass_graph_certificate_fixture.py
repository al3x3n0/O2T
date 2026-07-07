#!/usr/bin/env python3
"""Re-checkable certificates for recovered folds (an unverified validator only weakly increases
confidence -- Rideau/Leroy; Besson/Blazy verified validators).

O2T's verdicts ultimately rest on a single z3 call. A certificate turns 'z3 said so' into an artifact
an INDEPENDENT checker can re-verify without z3:
  * a `refuted` verdict carries the concrete counterexample -- a self-contained proof of unsoundness;
  * a `proved` verdict is re-checked by exhaustive small-width, poison-aware enumeration.

`check_certificate` re-verifies WITHOUT invoking z3, and -- crucially -- it has TEETH: a tampered
counterexample, or a `proved` label on an actually-unsound obligation, is caught as `invalid`. So the
certificate is not a rubber stamp: an independent method must corroborate the verdict.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_certificate_fixture: z3 not found, skipped")
        return 0

    def rp(pred, rw):
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("expected a recovered fold", pred, rw)
        return pair

    def cert(pred, rw):
        pair = rp(pred, rw)
        c = pg.certify(pair, z3)
        return pair, c, pg.check_certificate(pair, c)

    # 1. PROVED value fold: independently confirmed by z3-free enumeration.
    pair, c, chk = cert("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);")
    assert c["verdict"] == "proved" and chk == "confirmed", (c, chk)

    # 2. REFUTED fold: the certificate carries a counterexample, independently confirmed to violate the
    #    obligation (a self-contained, z3-free proof of unsoundness).
    pair, c, chk = cert("match(&I, m_Sub(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X);")
    assert c["verdict"] == "refuted" and c["counterexample"] and chk == "confirmed", (c, chk)

    # 3. REFINEMENT folds are certified poison-aware: flag-drop proves (confirmed); adding a flag is
    #    unsound and its counterexample is confirmed.
    _, c, chk = cert("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",
                     "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));")
    assert c["verdict"] == "proved" and chk == "confirmed", ("flag-drop cert", c, chk)
    _, c, chk = cert("match(&I, m_Add(m_Value(X), m_Value(Y)))",
                     "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));")
    assert c["verdict"] == "refuted" and chk == "confirmed", ("flag-add cert", c, chk)

    # 4. HONEST abstention: an op outside the toolless evaluator (a width-changing cast; div/rem's
    #    div-by-zero convention) is `unchecked` -- those verdicts are corroborated by OTHER oracles
    #    (cross-width re-proof, the compiled shim), not silently rubber-stamped here.
    pair, c, chk = cert("match(&I, m_Trunc(m_ZExt(m_Value(X)))) && X->getType() == I.getType()",
                        "return replaceInstUsesWith(I, X);")
    assert c["verdict"] == "proved" and chk == "unchecked", ("cast cert must be unchecked here", c, chk)
    assert pg.reconcile_widths(pair, z3)["agree"], "the cast verdict is corroborated cross-width instead"

    # 5. TEETH -- the checker is not a rubber stamp:
    #    (a) a tampered counterexample on a SOUND fold does not actually refute -> invalid.
    sound = rp("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);")
    assert pg.check_certificate(sound, {"verdict": "refuted", "counterexample": {"x": 5}}) == "invalid", \
        "a bogus counterexample must be rejected"
    #    (b) a `proved` label on an actually-UNSOUND obligation is caught by the independent enumeration.
    unsound = rp("match(&I, m_Sub(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X);")
    assert pg.check_certificate(unsound, {"verdict": "proved"}) == "invalid", \
        "a false 'proved' must be caught independently of z3"

    print("pass_graph_certificate_fixture OK: a refuted verdict emits a z3-free-verifiable counterexample; "
          "a proved verdict is re-checked by exhaustive poison-aware enumeration; casts/div honestly abstain "
          "(corroborated cross-width); and the checker has teeth -- a tampered witness or a false 'proved' is "
          "flagged invalid, so the certificate genuinely reduces trust in the single z3 call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
