// Not an upstream fold: a PROPERTY PROBE for the shim's predicate algebra.
//
// `getSwappedPredicate` and `getInversePredicate` are pure functions with exact specifications:
//
//     icmp swap(P) b, a   ==   icmp P a, b        (swapping the operands)
//     icmp inv(P)  a, b   ==   not (icmp P a, b)  (negating the result)
//
// The shim used to return the argument UNCHANGED from the first and collapse every non-equality
// predicate to ICMP_EQ in the second. Both compile and read plausibly; both denote the wrong
// comparison. They were unreachable until icmp was modelled, at which point any fold canonicalising
// operand order would silently build the wrong instruction.
//
// This emits, for every modelled predicate, the two SMT equalities above so z3 can settle them.
#include "symbolic_llvm.h"
#include <cstdio>

static const CVPredicate PREDS[] = {ICMP_EQ, ICMP_NE, ICMP_ULT, ICMP_ULE, ICMP_UGT,
                                    ICMP_UGE, ICMP_SLT, ICMP_SLE, ICMP_SGT, ICMP_SGE};

// Second property: `m_SpecificInt_ICMP(Pred, T)` matches a CONSTANT C exactly when `C Pred T`
// holds -- signed predicates on the SIGNED reading of the bits, unsigned on the unsigned one. It is
// printed per case so the fixture can compare against an independent computation rather than
// against the shim's own opinion.
static void probe_specific_int_icmp() {
  static const unsigned long VALS[] = {0ul, 1ul, 7ul, 8ul, 0x7FFFFFFFul, 0x80000000ul, 0xFFFFFFFFul};
  for (CVPredicate p : PREDS)
    for (unsigned long c : VALS)
      for (unsigned long thr : VALS) {
        Value k = cv_bv(c); k.is_const = true;
        std::printf("; MATCH %d %lu %lu %d\n", (int)p, c, thr,
                    (int)match(k, m_SpecificInt_ICMP(p, APInt(32, thr))));
      }
}

int main() {
  probe_specific_int_icmp();
  std::printf("(set-logic QF_BV)\n(declare-const A (_ BitVec 32))\n(declare-const B (_ BitVec 32))\n");
  for (CVPredicate p : PREDS) {
    std::string base    = cv_icmp_term(p, "A", "B");
    std::string swapped = cv_icmp_term(cv_swap_pred(p), "B", "A");
    std::string inverse = cv_icmp_term(cv_inverse_pred(p), "A", "B");
    // each assertion is the NEGATION of the property, so `unsat` means the property holds
    std::printf("(push 1)(assert (not (= %s %s)))(check-sat)(pop 1)\n", base.c_str(), swapped.c_str());
    std::printf("(push 1)(assert (not (= %s (bvnot %s))))(check-sat)(pop 1)\n", base.c_str(), inverse.c_str());
  }
  return 0;
}
