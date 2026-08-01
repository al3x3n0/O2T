#!/usr/bin/env python3
"""The shim's predicate algebra is PROVED, not trusted.

`getSwappedPredicate` and `getInversePredicate` are pure functions with exact specifications:

    icmp swap(P) b, a  ==  icmp P a, b          (the operands are swapped)
    icmp inv(P)  a, b  ==  not (icmp P a, b)    (the result is negated)

The shim returned its argument UNCHANGED from the first, and collapsed every non-equality predicate
to `ICMP_EQ` in the second. Both versions compile and read plausibly. Measured against the
specification above, 16 of the 20 properties FAILED -- everything except the four EQ/NE cases, which
happen to be symmetric and self-inverse.

Nothing had noticed because the functions were unreachable: no fold could call them until `icmp` was
modelled. The moment one does -- any fold canonicalising operand order -- it silently builds the
wrong comparison. This is the fourth bug of the same family in this shim (bind-a-copy,
snapshot-instead-of-dereference, record-but-never-store, and now a wrong pure function), and the
common thread is that all of them were written to satisfy the compiler rather than to be executed.

So the algebra is checked against its specification by z3, for every modelled predicate, rather than
by reading the table. And the ACID TEST reverts both functions to their previous form and requires
the probe to fail -- otherwise a green result would only mean the probe cannot see the bug.

Needs clang++ and z3; self-skips without them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PROBE = ROOT / "tests" / "fixtures" / "vendor_folds" / "predicate_algebra_probe.cpp"
HEADER = ROOT / "o2t" / "symexec" / "symbolic_llvm.h"


def _clang():
    for cand in ("clang++", "/opt/homebrew/opt/llvm@18/bin/clang++", "/usr/bin/clang++"):
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


# CVPredicate enumerator order in the shim header; the probe prints the raw enum value.
_PREDS = ["eq", "ne", "ult", "ule", "ugt", "uge", "slt", "sle", "sgt", "sge"]


def _expected_match(pred: str, c: int, thr: int) -> bool:
    """Independent reading of `C Pred Threshold` at 32 bits -- computed here, NOT asked of the shim."""
    sc = c - (1 << 32) if c >> 31 else c
    st = thr - (1 << 32) if thr >> 31 else thr
    return {"eq": c == thr, "ne": c != thr,
            "ult": c < thr, "ule": c <= thr, "ugt": c > thr, "uge": c >= thr,
            "slt": sc < st, "sle": sc <= st, "sgt": sc > st, "sge": sc >= st}[pred]


def _run(clang, z3, include_dir, workdir):
    """Compile the probe against `include_dir` and return z3's verdicts."""
    exe = Path(workdir) / "probe"
    cc = subprocess.run([clang, "-std=c++17", "-I", str(include_dir), str(PROBE), "-o", str(exe)],
                        capture_output=True, text=True)
    assert cc.returncode == 0, cc.stderr[-600:]
    smt = subprocess.run([str(exe)], capture_output=True, text=True).stdout
    out = subprocess.run([z3, "-in"], input=smt, capture_output=True, text=True).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip() in ("sat", "unsat")]


def main() -> int:
    z3, clang = shutil.which("z3"), _clang()
    if z3 is None or clang is None:
        print("predicate_algebra_fixture: needs clang++ and z3, skipped")
        return 0

    # 1) AS SHIPPED: every property holds. `unsat` means the negated property is unsatisfiable.
    with tempfile.TemporaryDirectory() as td:
        verdicts = _run(clang, z3, HEADER.parent, td)
    assert verdicts, "the probe must emit queries"
    assert all(v == "unsat" for v in verdicts), \
        (f"{sum(v != 'unsat' for v in verdicts)} of {len(verdicts)} predicate-algebra properties "
         "FAIL -- the shim denotes a different comparison than it claims", verdicts)
    total = len(verdicts)

    # 1b) `m_SpecificInt_ICMP(Pred, T)` matches a constant C exactly when `C Pred T` holds, with the
    #     SIGNED predicates reading the bits as signed. Every case is compared against an independent
    #     computation here rather than against the shim's own opinion -- the point of this file is
    #     that shim helpers which have never been executed are wrong until shown otherwise.
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "probe"
        cc = subprocess.run([clang, "-std=c++17", "-I", str(HEADER.parent), str(PROBE), "-o", str(exe)],
                            capture_output=True, text=True)
        assert cc.returncode == 0, cc.stderr[-600:]
        lines = [ln for ln in subprocess.run([str(exe)], capture_output=True, text=True)
                 .stdout.splitlines() if ln.startswith("; MATCH ")]
    assert lines, "the probe must emit m_SpecificInt_ICMP cases"
    bad = []
    for ln in lines:
        _, _, pi, c, thr, got = ln.split()
        pred = _PREDS[int(pi)]
        if bool(int(got)) != _expected_match(pred, int(c), int(thr)):
            bad.append((pred, int(c), int(thr), int(got)))
    assert not bad, (f"{len(bad)} of {len(lines)} m_SpecificInt_ICMP cases disagree with an "
                     "independent reading of the predicate", bad[:6])
    matches = len(lines)

    # 1c) The OrZero form of isKnownToBeAPowerOfTwo establishes a STRICTLY WEAKER fact. If both
    #     recorded the same query, `OrZero=true` would be grounded as strict power-of-two, asserting
    #     the value is NON-ZERO when the caller proved no such thing -- assuming more than was
    #     established, which is the shape of an unsound proof. Checked by z3 in both directions.
    from o2t.facts.value_tracking import scalar_assumption_smt          # noqa: E402
    from o2t.symexec.real_pass import _QUERY_FACT                       # noqa: E402
    strict = scalar_assumption_smt(_QUERY_FACT["power-of-two"], "X")
    weak = scalar_assumption_smt(_QUERY_FACT["power-of-two-or-zero"], "X")
    assert strict != weak, "the two power-of-two queries must not ground to the same fact"
    q = (f"(set-logic QF_BV)(declare-const X (_ BitVec 32))"
         f"(push 1)(assert (not (=> {strict} {weak})))(check-sat)(pop 1)"      # strict => weak
         f"(push 1)(assert (not (=> {weak} {strict})))(check-sat)(pop 1)")     # weak =/=> strict
    got = [ln.strip() for ln in subprocess.run([z3, "-in"], input=q, capture_output=True,
                                               text=True).stdout.split() if ln.strip() in ("sat", "unsat")]
    assert got == ["unsat", "sat"], ("strict power-of-two must IMPLY the or-zero form and not "
                                     "conversely -- otherwise OrZero is not weaker at all", got)

    # 2) ACID TEST: restore the previous definitions and require the probe to SEE the bug. Without
    #    this, a clean run would only show that the probe never asks a question that can fail.
    src = HEADER.read_text()
    broken = src.replace("inline CVPredicate cv_swap_pred(CVPredicate p) {\n  switch (p) {",
                         "inline CVPredicate cv_swap_pred(CVPredicate p) {\n  return p;\n  switch (p) {", 1)
    broken = broken.replace(
        "inline CVPredicate cv_inverse_pred(CVPredicate p) {\n  switch (p) {",
        "inline CVPredicate cv_inverse_pred(CVPredicate p) {\n"
        "  return p == ICMP_EQ ? ICMP_NE : ICMP_EQ;\n  switch (p) {", 1)
    assert broken != src, "both predicate functions must be present to revert"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "symbolic_llvm.h").write_text(broken)
        bad = _run(clang, z3, td, td)
    failures = sum(v == "sat" for v in bad)
    assert failures > 0, ("with the previous predicate algebra restored the probe must FAIL, "
                          "or it is not testing the property at all")

    print(f"predicate_algebra_fixture OK: all {total} predicate-algebra properties are PROVED by z3 "
          "against their specification -- `icmp swap(P) b,a == icmp P a,b` and "
          f"`icmp inv(P) a,b == not (icmp P a,b)` for every modelled predicate; all {matches} "
          "m_SpecificInt_ICMP cases agree with an independent reading of the predicate; and "
          "`isKnownToBeAPowerOfTwo(V, OrZero=true)` grounds to a STRICTLY WEAKER fact than the plain "
          "query, so it cannot assert non-zero when the caller established no such thing. The shim "
          "previously "
          "returned its argument unchanged from getSwappedPredicate and collapsed every "
          f"non-equality predicate to ICMP_EQ in getInversePredicate: {failures} of {total} "
          "properties fail under that version, and the four survivors are only the EQ/NE cases, "
          "which are symmetric and self-inverse. It went unnoticed because nothing could call these "
          "functions until icmp was modelled -- a pure function that compiles, reads correctly and "
          "denotes the wrong comparison")
    return 0


if __name__ == "__main__":
    sys.exit(main())
