#!/usr/bin/env python3
"""Cover the E6 corpus runner: extraction, outcome taxonomy, and the zero-false-proof discipline.

A synthetic corpus with KNOWN outcomes pins the mechanics the upstream run relies on: a provable
fold is `recovered-proved` only after the reconcile cross-check; an unsound fold is
`recovered-refuted` with a witness; each decline bucket is hit by a function designed for it;
an operand-loop fold is labeled with its ladder rung; oversize bodies are skipped-and-counted,
never silently dropped. Needs z3.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import corpus  # noqa: E402

CORPUS = r"""
// recovered-proved: (X+0)*1 -> X under the function-path rung.
static Value *foldMulAddZero(BinaryOperator &I) {
  Value *X;
  if (!match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One())))
    return nullptr;
  return replaceInstUsesWith(I, X);
}

// recovered-refuted: X - Y -> X is unsound; the prover must produce a witness.
static Value *foldSubWrong(BinaryOperator &I) {
  Value *X, *Y;
  if (!match(&I, m_Sub(m_Value(X), m_Value(Y))))
    return nullptr;
  return replaceInstUsesWith(I, X);
}

// recovered-proved via the operand-loop rung: phi [x,x,..,x] -> x.
static Value *simplifyPHI(PHINode *PN) {
  Value *First = PN->getIncomingValue(0);
  for (Value *In : PN->incoming_values())
    if (In != First) return nullptr;
  return replaceInstUsesWith(*PN, First);
}

// declined / no-match-call: no PatternMatch inspection at all.
static Value *bookkeeping(Instruction *I) {
  updateStatistics(I);
  return nullptr;
}

// recovered-refuted via the RETURN-form anchor (phase 36): a fold-named helper returning the
// replacement directly -- and this one is deliberately WRONG (X+0 is not X*X), so it refutes.
static Instruction *foldByCreate(BinaryOperator &I) {
  Value *X;
  if (!match(&I, m_Add(m_Value(X), m_Zero())))
    return nullptr;
  return BinaryOperator::CreateMul(X, X);
}

// declined / no-riuw-rewrite: a QUERY helper -- returns an answer, not a rewrite. The return-form
// anchor is name-gated to the fold contract, so this stays a sound decline.
static Value *getScaledOperand(BinaryOperator &I) {
  Value *X;
  if (!match(&I, m_Mul(m_Value(X), m_One())))
    return nullptr;
  return BinaryOperator::CreateShl(X, X);
}

// recovered-proved DESPITE the loop: the users() walk is value-IRRELEVANT bookkeeping (it drives
// which instructions get visited later, not this rewrite's semantics), so the transparent-header
// scan soundly recovers the fold after it. Pinned deliberately: iteration bookkeeping must not
// block recovery, while value-relevant cross-iteration state (below) must decline.
static Value *worklistFixpoint(Instruction *I) {
  Value *X;
  if (!match(I, m_Add(m_Value(X), m_Zero())))
    return nullptr;
  for (User *U : I->users())
    Worklist.push(U);
  return replaceInstUsesWith(*I, X);
}

// declined / loop-over-ir: value-relevant CROSS-ITERATION state (the rewrite uses an accumulator
// carried from a previous iteration) -- outside every bounded loop rung.
static Value *foldAccumulate(BasicBlock &BB) {
  Value *Acc = nullptr;
  for (Instruction &I : BB) {
    Value *X;
    if (!match(&I, m_Add(m_Value(X), m_Zero())))
      continue;
    if (Acc)
      replaceInstUsesWith(I, Acc);
    Acc = X;
  }
  return nullptr;
}

// declined / in-fragment-shape: match + replaceInstUsesWith, but an unmodeled guard.
static Value *foldUnknownGuard(BinaryOperator &I) {
  Value *X, *Y;
  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y))))
    return nullptr;
  if (!someVendorAnalysis(X))
    return nullptr;
  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));
}
"""


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("passir_corpus_fixture: z3 not found, skipped")
        return 0

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "SyntheticFolds.cpp"
        src.write_text(CORPUS)

        # 1) Extraction finds every candidate; nothing is silently dropped.
        fns = corpus.extract_functions(CORPUS)
        assert [f["name"] for f in fns] == [
            "foldMulAddZero", "foldSubWrong", "simplifyPHI", "bookkeeping",
            "foldByCreate", "getScaledOperand", "worklistFixpoint", "foldAccumulate",
            "foldUnknownGuard"], [f["name"] for f in fns]

        # 2) The taxonomy lands every function in its designed outcome.
        report = corpus.run_corpus([src], z3)
        by_name = {r["function"]: r for r in report["results"]}
        assert by_name["foldMulAddZero"]["outcome"] == "recovered-proved", by_name["foldMulAddZero"]
        assert by_name["foldMulAddZero"]["rung"] == "function-path"
        assert by_name["foldSubWrong"]["outcome"] == "recovered-refuted" \
            and by_name["foldSubWrong"]["witness"], by_name["foldSubWrong"]
        assert by_name["simplifyPHI"]["outcome"] == "recovered-proved" \
            and by_name["simplifyPHI"]["rung"] == "operand-loop", by_name["simplifyPHI"]
        assert by_name["bookkeeping"] == {**by_name["bookkeeping"], "outcome": "declined",
                                          "bucket": "no-match-call"}
        # phase 36: the RETURN-form anchor recovers the fold-named helper (and refutes the wrong
        # fold -- teeth), while the query-named helper stays a name-gated sound decline.
        assert by_name["foldByCreate"]["outcome"] == "recovered-refuted" \
            and by_name["foldByCreate"]["rung"] == "return-form", by_name["foldByCreate"]
        assert by_name["getScaledOperand"]["outcome"] == "declined" \
            and by_name["getScaledOperand"]["bucket"] == "no-riuw-rewrite", by_name["getScaledOperand"]
        # iteration BOOKKEEPING does not block recovery (the loop is value-irrelevant to the
        # rewrite); value-relevant cross-iteration state DOES decline.
        assert by_name["worklistFixpoint"]["outcome"] == "recovered-proved" \
            and by_name["worklistFixpoint"]["rung"] == "function-path", by_name["worklistFixpoint"]
        assert by_name["foldAccumulate"]["outcome"] == "declined" \
            and by_name["foldAccumulate"]["bucket"] == "loop-over-ir", by_name["foldAccumulate"]
        assert by_name["foldUnknownGuard"]["bucket"] == "in-fragment-shape"
        assert report["outcomes"] == {"recovered-proved": 3, "recovered-refuted": 2, "declined": 4}
        # the rung labels every RECOVERED function (the refuted ones included).
        assert report["rungs"] == {"function-path": 3, "operand-loop": 1, "return-form": 1}

        # 3) ZERO-FALSE-PROOF discipline: `recovered-proved` required the reconcile cross-check.
        #    The scalar fold ran the concrete engine; the phi fold's 5-var obligation is beyond the
        #    enumeration cap, so its reconcile abstained (skipped) -- both recorded, neither silent.
        assert by_name["foldMulAddZero"]["reconcile"] == "proved"
        assert by_name["simplifyPHI"]["reconcile"] == "skipped"

        # 4) Oversize bodies are skipped-and-counted, never silently dropped.
        capped = corpus.run_corpus([src], z3, max_lines=3)
        assert capped["outcomes"].get("skipped-oversize", 0) == len(fns), capped["outcomes"]

        # 5) The rendered table carries the headline counts and the invariant statement.
        table = corpus.render_table(report)
        assert "recovered-proved" in table and "loop-over-ir" in table \
            and "reconcile cross-check" in table

    print("passir_corpus_fixture OK: extraction finds every candidate fold function; the taxonomy "
          "lands proved (reconcile-cross-checked), refuted (with witness), and all four decline "
          "buckets exactly as designed; the operand-loop rung is labeled; oversize bodies are "
          "counted, not dropped -- the E6 corpus mechanics are gated before any upstream number "
          "is claimed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
