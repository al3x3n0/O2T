namespace llvm {
struct Value {
  bool hasOneUse();
};
struct Instruction {};
struct Constant {
  static Value *getNullValue(int);
};

namespace PatternMatch {
struct Pattern {};
Pattern m_Zero();
Pattern m_One();
Pattern m_Value(Value *&);
Pattern m_Specific(Value *);
Pattern m_Deferred(Value *&);
Pattern m_Add(Pattern, Pattern);
Pattern m_c_Add(Pattern, Pattern);
Pattern m_Mul(Pattern, Pattern);
Pattern m_Xor(Pattern, Pattern);
bool match(Value *, Pattern);
} // namespace PatternMatch

Value *replaceInstUsesWith(Instruction &, Value *);
bool hasPoisonGeneratingFlags(Instruction &);
bool isGuaranteedNotToBePoison(Value *);
bool isKnownNonZero(Value *);
bool isKnownPositive(Value *);
bool isKnownNonNegative(Value *);
bool MaskedValueIsZero(Value *, unsigned);
bool isKnownToBeAPowerOfTwo(Value *);
bool shouldOptimizeForSize(Instruction &);
bool customLegalityCheck(Value *);
} // namespace llvm

using namespace llvm;
using namespace PatternMatch;

Value *foldFullAdd(Value *V, Value *X, Instruction &I) {
  if (match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldCommutedAdd(Value *V, Value *X, Instruction &I) {
  if (match(V, m_c_Add(m_Zero(), m_Value(X)))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldFullMul(Value *V, Value *X, Instruction &I) {
  if (match(V, m_Mul(m_Value(X), m_One()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldFullXor(Value *V, Value *X, Instruction &I) {
  if (match(V, m_Xor(m_Value(X), m_Deferred(X)))) {
    return replaceInstUsesWith(I, Constant::getNullValue(0));
  }
  return V;
}

Value *badFoldAdd(Value *V, Value *X, Value *Y, Instruction &I) {
  if (match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, Y);
  }
  return V;
}

Value *badFoldXor(Value *V, Value *X, Instruction &I) {
  if (match(V, m_Xor(m_Value(X), m_Deferred(X)))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithModeledPoisonGuard(Value *V, Value *X, Instruction &I) {
  if (!hasPoisonGeneratingFlags(I) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithUnmodeledPoisonGuard(Value *V, Value *X, Instruction &I) {
  if (isGuaranteedNotToBePoison(X) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithOneUseGuard(Value *V, Value *X, Instruction &I) {
  if (X->hasOneUse() && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithKnownNonZeroGuard(Value *V, Value *X, Instruction &I) {
  if (isKnownNonZero(X) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithKnownPositiveGuard(Value *V, Value *X, Instruction &I) {
  if (isKnownPositive(X) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithKnownNonNegativeGuard(Value *V, Value *X, Instruction &I) {
  if (isKnownNonNegative(X) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithMaskedValueIsZeroGuard(Value *V, Value *X, Instruction &I) {
  if (MaskedValueIsZero(X, 0xff) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithKnownPowerOfTwoGuard(Value *V, Value *X, Instruction &I) {
  if (isKnownToBeAPowerOfTwo(X) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithProfitabilityGuard(Value *V, Value *X, Instruction &I) {
  if (shouldOptimizeForSize(I) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}

Value *foldAddWithUnknownLegalityGuard(Value *V, Value *X, Instruction &I) {
  if (customLegalityCheck(X) && match(V, m_Add(m_Value(X), m_Zero()))) {
    return replaceInstUsesWith(I, X);
  }
  return V;
}
