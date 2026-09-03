#!/usr/bin/env python3
"""A refutation may not rest on an opaque constant expression's freedom.

THE INCIDENT. The Track B corpus had reported ZERO refutations across its whole history. The first
one ever, found 2026-09-03 on LLVM 18.1.8's `mul.ll`, was FALSE:

    define i64 @test_mul_canonicalize_neg_is_not_undone(i64 %L1) {
      %v1 = ptrtoint ptr @X to i64 ; %B8 = sub i64 0, %v1 ; %B4 = mul i64 %B8, %L1 ; ret i64 %B4 }
    ->  %B4 = mul i64 %L1, sub (i64 0, i64 ptrtoint (ptr @X to i64))

which is plain commutativity of `mul`. The SOURCE computes the value structurally over the global
symbol `glob_X`; InstCombine's OUTPUT carries the folded constant expression, modelled as an opaque
free constant `cexpr_<digest>_<w>`. Constant expressions are shared across sides only when their
printed text is IDENTICAL, and here one side has instructions where the other has a constant, so
the two symbols are unrelated and z3 simply chose them inconsistently: the witness assigned
glob_X = 0x7d0cd246f4049eff and the cexpr symbol 0x68d7fbcf868b0003. That is not a counterexample,
it is the model's own slack handed back as evidence.

THE RULE, which this file already applied twice before (fast-math, uninterpreted FP): an
unconstrained symbol makes the TARGET's behaviour set LARGER. Refinement demands every target
behaviour be a source one, so proving over a larger target set is HARDER -- a proof stays
conservative and valid -- while a refutation drawn from that extra freedom is worthless. Proofs
stand; refutations decline.

THE COST, pinned deliberately rather than hidden. The guard is keyed on the query containing a
`cexpr_` symbol at all, so a GENUINE miscompile whose refutation needs a constant expression now
declines too (assertion 3 pins exactly this). That is the conservative direction and it is cheap
here: the guard only ever changes refutations, never proofs, and the corpus contained exactly one
refutation -- so its entire measured effect is the one false accusation it removes. If a real
cexpr-dependent miscompile ever needs catching, the fix is to INTERPRET constant expressions
structurally (`sub (i64 0, i64 ptrtoint (ptr @X to i64))` -> `bvsub #x0 glob_X`), not to loosen this.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate.corpus_tv import validate_transform_ex  # noqa: E402

BEFORE = """@X = external global i8
define i64 @f(i64 %L1) {
  %v1 = ptrtoint ptr @X to i64
  %B8 = sub i64 0, %v1
  %B4 = mul i64 %B8, %L1
  ret i64 %B4
}
"""
# InstCombine's real output shape: the negation folded into a constant expression, operands commuted.
AFTER_SOUND = """@X = external global i8
define i64 @f(i64 %L1) {
  %B4 = mul i64 %L1, sub (i64 0, i64 ptrtoint (ptr @X to i64))
  ret i64 %B4
}
"""
# Genuinely wrong, and its refutation would need the constant expression.
AFTER_WRONG_CEXPR = """@X = external global i8
define i64 @f(i64 %L1) {
  %B4 = add i64 %L1, sub (i64 0, i64 ptrtoint (ptr @X to i64))
  ret i64 %B4
}
"""
# Genuinely wrong with NO constant expression anywhere -- the control that keeps the guard scoped.
BEFORE_PLAIN = """define i64 @g(i64 %a, i64 %b) {
  %r = mul i64 %a, %b
  ret i64 %r
}
"""
AFTER_WRONG_PLAIN = """define i64 @g(i64 %a, i64 %b) {
  %r = add i64 %a, %b
  ret i64 %r
}
"""


def main() -> int:
    if shutil.which("z3") is None:
        print("opaque_const_expr_refutation_fixture: z3 not found, skipped")
        return 0

    # 1) THE REGRESSION. The sound commutativity fold must NOT be refuted. Before the guard this
    #    returned `refuted` with a witness -- an accusation of a miscompile against a correct LLVM.
    r = validate_transform_ex("z3", BEFORE, AFTER_SOUND, "f", timeout=20)
    assert r["status"] != "refuted", \
        ("a sound commutativity fold must never be refuted -- this exact pair was the corpus's "
         "first-ever refutation and it was false", r)
    assert r["status"] == "unsupported" and r.get("guard") == "opaque-const-expr", \
        ("and it must decline for the STATED reason, not by accident", r)

    # 2) THE GUARD IS SCOPED. A miscompile with no constant expression in sight must still be
    #    refuted. Without this, assertion 1 would be satisfied by simply never refuting anything --
    #    which would disable Track B's teeth entirely while looking like a soundness fix.
    r_plain = validate_transform_ex("z3", BEFORE_PLAIN, AFTER_WRONG_PLAIN, "g", timeout=20)
    assert r_plain["status"] == "refuted", \
        ("a plain `mul -> add` miscompile must still be refuted -- the guard must not cost the "
         "validator its teeth in general", r_plain)

    # 3) THE COST, ASSERTED SO IT CANNOT BE FORGOTTEN. A REAL miscompile whose refutation needs the
    #    constant expression also declines. This is not a bug to fix by weakening the guard; it is
    #    the conservative price, and the way to buy the teeth back is to interpret constant
    #    expressions structurally. Asserted rather than commented so that if someone later makes
    #    this refute (by interpreting cexprs properly), this fixture FAILS and forces the docstring
    #    and the ledger to be updated to match.
    r_bad = validate_transform_ex("z3", BEFORE, AFTER_WRONG_CEXPR, "f", timeout=20)
    assert r_bad["status"] == "unsupported" and r_bad.get("guard") == "opaque-const-expr", \
        ("a genuinely wrong cexpr-dependent transform currently DECLINES -- if this now refutes, "
         "constant expressions have been interpreted and this fixture's docstring is stale", r_bad)

    print("opaque_const_expr_refutation_fixture OK: the sound commutativity fold that produced the "
          "corpus's first-ever (false) refutation now declines `opaque-const-expr`; a miscompile "
          "with no constant expression is still refuted, so the guard is scoped and the teeth "
          "survive; the conservative cost -- a cexpr-dependent real miscompile also declines -- is "
          "pinned rather than hidden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
