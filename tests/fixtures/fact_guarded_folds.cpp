// Fact-guarded InstCombine-style folds. Each rewrite is legal ONLY because a
// ValueTracking query proved a precondition about an operand; O2T lowers that
// analysis fact to an SMT side-condition (the analysis-fact bridge) and proves
// the guarded rewrite equivalent to the original for ALL inputs. Drop the guard
// and the same rewrite is refuted with a concrete counterexample.
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/IRBuilder.h"

using namespace llvm;

// urem X, P  ->  X & (P - 1)   sound exactly when P is a power of two.
Value *foldURemPow2(BinaryOperator &I, Value *Op0, Value *Op1, IRBuilder<> &Builder,
                    Type *Ty) {
  if (isKnownToBeAPowerOfTwo(Op1))
    return Builder.CreateAnd(Op0, Builder.CreateSub(Op1, ConstantInt::get(Ty, 1)));
  return nullptr;
}

// sdiv X, Y  ->  udiv X, Y   sound when both operands are known non-negative
// (and the divisor is known non-zero, which the fold also establishes).
Value *foldSDivNonNeg(BinaryOperator &I, Value *Op0, Value *Op1, IRBuilder<> &Builder) {
  if (isKnownNonNegative(Op0) && isKnownNonNegative(Op1) && isKnownNonZero(Op1))
    return Builder.CreateUDiv(Op0, Op1);
  return nullptr;
}
