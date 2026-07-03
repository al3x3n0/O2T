namespace llvm {
struct Value {
  bool hasOneUse();
};
struct Instruction {};

namespace PatternMatch {
struct Pattern {};
Pattern m_Zero();
bool match(Value *, Pattern);
} // namespace PatternMatch

bool hasPoisonGeneratingFlags(Instruction &);
bool isGuaranteedNotToBePoison(Value *);
bool isKnownNonZero(Value *);
bool isKnownPositive(Value *);
bool isKnownNonNegative(Value *);
bool MaskedValueIsZero(Value *, unsigned);
bool isKnownPowerOf2(Value *);
Value *replaceInstUsesWith(Instruction &, Value *);
} // namespace llvm

using namespace llvm;
using namespace PatternMatch;

Value *modeledPoisonGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (!hasPoisonGeneratingFlags(I) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *unmodeledPoisonGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (isGuaranteedNotToBePoison(Op0) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *oneUseGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (Op0->hasOneUse() && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *knownNonZeroGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (isKnownNonZero(Op0) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *knownPositiveGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (isKnownPositive(Op0) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *knownNonNegativeGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (isKnownNonNegative(Op0) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *maskedValueIsZeroGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (MaskedValueIsZero(Op0, 0xff) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *knownPowerOfTwoGuard(Value *Op0, Value *Op1, Instruction &I) {
  if (isKnownPowerOf2(Op0) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}
