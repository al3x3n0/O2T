// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp @ llvmorg-18.1.8
//
//   (X == C) | (Other <u (X + -C))   ->   (X - (C+1)) >=u Other
//   (X != C) & (Other >=u (X + -C))  ->   (X - (C+1)) <u  Other
//
// Two range checks collapsing into one, by translating the interval so the comparison is against a
// single subtraction. The `or` form and the `and` form are De Morgan duals, and upstream writes them
// as ONE function that inverts both predicates when `IsAnd` -- so the two arms are not copies, they
// are the same code reached with every predicate flipped.
//
// That is what this fold is here to exercise. It asks each icmp for the inverse of ITS OWN predicate
// (`LHS->getInversePredicate()`), which is the instance form rather than the static one. The shim had
// only the static form, and the shortcut spelling on `Value` collapses everything non-equality to
// ICMP_EQ -- correct for the equality predicates this fold checks first, wrong for the ordered ones
// it then matches on. Both arms are claimed here precisely because a single implementation serving
// both directions is where an inversion bug hides: the `and` arm is the one that inverts.
//
// NOT CLAIMED: the IsLogical arm, which inserts a freeze against the RHS's poison. The shim's
// unflagged builders do not propagate operand poison, so that arm would be discharged against a
// model that cannot see what the freeze is for.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp
#include "symbolic_llvm.h"
#include <cstring>

static Value *foldAndOrOfICmpEqConstantAndICmp(ICmpInst *LHS, ICmpInst *RHS,
                                               bool IsAnd, bool IsLogical,
                                               IRBuilderBase &Builder) {
  Value *LHS0 = LHS->getOperand(0);
  Value *RHS0 = RHS->getOperand(0);
  Value *RHS1 = RHS->getOperand(1);

  ICmpInst::Predicate LPred =
      IsAnd ? LHS->getInversePredicate() : LHS->getPredicate();
  ICmpInst::Predicate RPred =
      IsAnd ? RHS->getInversePredicate() : RHS->getPredicate();

  const APInt *CInt;
  if (LPred != ICmpInst::ICMP_EQ ||
      !match(LHS->getOperand(1), m_APIntAllowUndef(CInt)) ||
      !LHS0->getType()->isIntOrIntVectorTy() ||
      !(LHS->hasOneUse() || RHS->hasOneUse()))
    return nullptr;

  auto MatchRHSOp = [LHS0, CInt](const Value *RHSOp) {
    return match(RHSOp,
                 m_Add(m_Specific(LHS0), m_SpecificIntAllowUndef(-*CInt))) ||
           (CInt->isZero() && RHSOp == LHS0);
  };

  Value *Other;
  if (RPred == ICmpInst::ICMP_ULT && MatchRHSOp(RHS1))
    Other = RHS0;
  else if (RPred == ICmpInst::ICMP_UGT && MatchRHSOp(RHS0))
    Other = RHS1;
  else
    return nullptr;

  if (IsLogical)
    Other = Builder.CreateFreeze(Other);

  return Builder.CreateICmp(
      IsAnd ? ICmpInst::ICMP_ULT : ICmpInst::ICMP_UGE,
      Builder.CreateSub(LHS0, ConstantInt::get(LHS0->getType(), *CInt + 1)),
      Other);
}

// ---------------------------------------------------------------------------------------------
// The harness states the CALLER's contract: the fold receives the two icmps and a flag, and promises
// a value equivalent to their conjunction (IsAnd) or disjunction. Building that combination here is
// the harness's claim, not the fold's, which is why the result is also put to reference Alive2.
int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  IRBuilder Builder;
  Value X{"X"}, Other{"B"};
  std::string input;
  Value *out = nullptr;
  const char *f = argv[1];

  const unsigned long C = 5ul, NEGC = 0xFFFFFFFBul;    // -5 at i32
  bool is_or = !strcmp(f, "eqicmp_or");                // (X == 5) | (B <u X + -5)
  bool is_and = !strcmp(f, "eqicmp_and");              // (X != 5) & (B >=u X + -5)
  // The LOGICAL form, which is why the freeze is in this fold at all. `a || b` is
  // `select a, true, b`: when a is true the result is true whatever b is, INCLUDING poison. The
  // rewrite puts b's value into a plain comparison, where poison would propagate -- so upstream
  // freezes it. Both spellings are run: with the freeze (upstream's own code) and without it, which
  // is the same rewrite differing only in that one call.
  bool logical = !strcmp(f, "eqicmp_logical_or");
  bool logical_nofreeze = !strcmp(f, "eqicmp_logical_or_nofreeze");
  if (logical || logical_nofreeze) {
    Other.poison = "Bp";                               // the operand the logical `or` may not evaluate
    cv_decl("(declare-const Bp Bool)");
    Value *Cc = ConstantInt::get(&CV_I32, C);
    Value *NegC = ConstantInt::get(&CV_I32, NEGC);
    std::string addt = "(bvadd X " + NegC->t + ")";
    Value *add = cv_node(OP_ADD, addt.c_str(), &X, NegC);
    Value *LHS = Builder.CreateICmp(::ICMP_EQ, &X, Cc);
    Value *RHS = Builder.CreateICmp(::ICMP_ULT, &Other, add);
    input = "(ite (= " + LHS->t + " #b1) #b1 " + RHS->t + ")";      // select LHS, true, RHS
    // a logical or is poison if its condition is, or if the arm it actually SELECTS is
    CV_INPUT_POISON = "(and (= " + LHS->t + " #b0) " + RHS->poison + ")";
    out = foldAndOrOfICmpEqConstantAndICmp(LHS, RHS, /*IsAnd=*/false,
                                           /*IsLogical=*/logical, Builder);
  }
  if (is_or || is_and) {
    Value *Cc = ConstantInt::get(&CV_I32, C);
    Value *NegC = ConstantInt::get(&CV_I32, NEGC);
    std::string addt = "(bvadd X " + NegC->t + ")";
    Value *add = cv_node(OP_ADD, addt.c_str(), &X, NegC);
    // For the `and` arm every predicate is the inverse of the `or` arm's: EQ becomes NE and ULT
    // becomes UGE, and the fold must invert both back before it recognises anything.
    Value *LHS = Builder.CreateICmp(is_and ? ::ICMP_NE : ::ICMP_EQ, &X, Cc);
    Value *RHS = Builder.CreateICmp(is_and ? ::ICMP_UGE : ::ICMP_ULT, &Other, add);
    input = "(bv" + std::string(is_and ? "and " : "or ") + LHS->t + " " + RHS->t + ")";
    out = foldAndOrOfICmpEqConstantAndICmp(LHS, RHS, is_and, /*IsLogical=*/false, Builder);
  }
  cv_emit(input, out);
  return 0;
}
