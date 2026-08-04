// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp @ llvmorg-18.1.8
//
// The theorem, for the `and` case:
//     ((X & P1) != 0)  &  ((X & P2) != 0)   ->   (X & (P1|P2)) == (P1|P2)
// and dually for `or`:
//     ((X & P1) == 0)  |  ((X & P2) == 0)   ->   (X & (P1|P2)) != (P1|P2)
// both valid only because P1 and P2 are POWERS OF TWO -- one bit each, so "some bit of the mask is
// set" and "every bit of the mask is set" coincide. With a two-bit mask the rewrite is false, which
// is what makes the two power-of-two queries load-bearing rather than decorative.
//
// This is the first vendored fold whose soundness rests ENTIRELY on facts that come from LLVM's
// analyses rather than from the pattern. `combineAddSubWithShlAddSub` and the xor cascade are pure
// algebra; here the branch establishes nothing on its own and the rewrite is justified only by what
// `isKnownToBeAPowerOfTwo` returned. So it exercises the query-grounding path end to end: the fact
// each query establishes is emitted into the path condition by the driver, and the discharge is over
// exactly those facts. Corrupting the STRENGTH of one query -- asking for the OrZero form, which
// admits zero -- is enough to refute, without touching the rewrite at all.
//
// The ONLY departure from the source text is the enclosing class name: upstream defines this as a
// member of `InstCombinerImpl`, which the shim's pass object does not declare, so the definition is
// attached to a derived class. Every byte of the parameter list and body is upstream's.
//
// The `IsLogical` arm IS claimed, and it is the one that makes the freeze load-bearing. `a && b` is
// `select a, b, false`, which does not evaluate b, so b's mask may be poison where the whole
// expression is not; the rewrite folds that mask into a plain `or`, and upstream freezes it first.
// Upstream's own code proves; deleting only that one call refutes, with a witness in which the mask
// is poison. This arm was unverifiable until the shim's unflagged builders started carrying operand
// poison -- before that, the unfrozen version proved too.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp
#include "symbolic_llvm.h"
#include <cstring>

struct CVPass : InstCombinerImpl {
  Value *foldAndOrOfICmpsOfAndWithPow2(ICmpInst *LHS, ICmpInst *RHS, Instruction *CxtI, bool IsAnd,
                                       bool IsLogical);
};

Value *CVPass::foldAndOrOfICmpsOfAndWithPow2(ICmpInst *LHS,
                                                       ICmpInst *RHS,
                                                       Instruction *CxtI,
                                                       bool IsAnd,
                                                       bool IsLogical) {
  CmpInst::Predicate Pred = IsAnd ? CmpInst::ICMP_NE : CmpInst::ICMP_EQ;
  if (LHS->getPredicate() != Pred || RHS->getPredicate() != Pred)
    return nullptr;

  if (!match(LHS->getOperand(1), m_Zero()) ||
      !match(RHS->getOperand(1), m_Zero()))
    return nullptr;

  Value *L1, *L2, *R1, *R2;
  if (match(LHS->getOperand(0), m_And(m_Value(L1), m_Value(L2))) &&
      match(RHS->getOperand(0), m_And(m_Value(R1), m_Value(R2)))) {
    if (L1 == R2 || L2 == R2)
      std::swap(R1, R2);
    if (L2 == R1)
      std::swap(L1, L2);

    if (L1 == R1 &&
        isKnownToBeAPowerOfTwo(L2, false, 0, CxtI) &&
        isKnownToBeAPowerOfTwo(R2, false, 0, CxtI)) {
      // If this is a logical and/or, then we must prevent propagation of a
      // poison value from the RHS by inserting freeze.
      if (IsLogical)
        R2 = Builder.CreateFreeze(R2);
      Value *Mask = Builder.CreateOr(L2, R2);
      Value *Masked = Builder.CreateAnd(L1, Mask);
      auto NewPred = IsAnd ? CmpInst::ICMP_EQ : CmpInst::ICMP_NE;
      return Builder.CreateICmp(NewPred, Masked, Mask);
    }
  }

  return nullptr;
}

// ---------------------------------------------------------------------------------------------
// The harness states the CALLER's contract, because this fold does not receive the combined
// instruction: it receives the two icmps and a flag, and promises a value equivalent to their
// conjunction (IsAnd) or disjunction. A mistake in that statement would be the harness's, not the
// fold's, which is why the same claims are also put to reference Alive2.
int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  CVPass P;
  Value A{"A"}, B{"B"}, C{"C"};                 // the value under test and the two masks
  std::string input;
  Value *out = nullptr;
  const char *f = argv[1];

  // THE LOGICAL FORM, which is the arm the `freeze` in this fold exists for. `a && b` is
  // `select a, b, false`: when a is false the result is false whatever b is, INCLUDING poison. The
  // rewrite folds b's mask into a plain `or`, where poison WOULD propagate, so upstream freezes it
  // first. Run both ways -- upstream's code, and the same rewrite with that one call deleted.
  bool logical = !strcmp(f, "pow2_and_logical");
  bool logical_nofreeze = !strcmp(f, "pow2_and_logical_nofreeze");
  if (logical || logical_nofreeze) {
    C.poison = "Cp";                                  // the mask the logical `and` may not evaluate
    cv_decl("(declare-const Cp Bool)");
    Value *zero = ConstantInt::get(&CV_I32, 0ul);
    Value *ab = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *ac = cv_node(OP_AND, "(bvand A C)", &A, &C);
    ac->poison = C.poison;                            // an `and` is poison when an operand is
    Value *LHS = P.Builder.CreateICmp(::ICMP_NE, ab, zero);
    Value *RHS = P.Builder.CreateICmp(::ICMP_NE, ac, zero);
    input = "(ite (= " + LHS->t + " #b1) " + RHS->t + " #b0)";       // select LHS, RHS, false
    // a logical `and` is poison if its condition is, or if the arm it actually SELECTS is
    CV_INPUT_POISON = "(and (= " + LHS->t + " #b1) " + RHS->poison + ")";
    out = P.foldAndOrOfICmpsOfAndWithPow2(LHS, RHS, nullptr, /*IsAnd=*/true,
                                          /*IsLogical=*/logical);
  }

  // `and`: ((A & B) != 0) & ((A & C) != 0)  ->  (A & (B|C)) == (B|C)
  // `or` : ((A & B) == 0) | ((A & C) == 0)  ->  (A & (B|C)) != (B|C)
  bool is_and = !strncmp(f, "pow2_and", 8) && !logical && !logical_nofreeze;
  bool is_or = !strncmp(f, "pow2_or", 7);
  if (is_and || is_or) {
    CVPredicate p = is_and ? ::ICMP_NE : ::ICMP_EQ;
    Value *zero = ConstantInt::get(&CV_I32, 0ul);
    Value *ab = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *ac = cv_node(OP_AND, "(bvand A C)", &A, &C);
    Value *LHS = P.Builder.CreateICmp(p, ab, zero);
    Value *RHS = P.Builder.CreateICmp(p, ac, zero);
    // the caller's contract: this fold replaces the and/or of the two icmps
    input = "(bv" + std::string(is_and ? "and " : "or ") + LHS->t + " " + RHS->t + ")";
    out = P.foldAndOrOfICmpsOfAndWithPow2(LHS, RHS, nullptr, is_and, /*IsLogical=*/false);
  }
  cv_emit(input, out);
  return 0;
}
