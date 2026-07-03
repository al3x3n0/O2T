// Proprietary-style InstCombine folds exercising xor-self recognition via the standard
// PatternMatch `m_Deferred` self-idiom (not the pointer-equality form). Used by
// source_miner_ambiguity_fixture to lock in that xor-self is mined from `X ^ X` while a general
// two-operand xor is NOT mis-attributed to xor-self.
namespace llvm { namespace PatternMatch {
Value *foldXorSelfCommutative(Instruction &I) {
  Value *Z;
  if (match(&I, m_c_Xor(m_Value(Z), m_Deferred(Z))))   // X ^ X -> 0  (line 8)
    return getNullValue(I.getType());
  return nullptr;
}
Value *foldXorSelfPlain(Instruction &I) {
  Value *W;
  if (match(&I, m_Xor(m_Value(W), m_Deferred(W))))      // X ^ X -> 0  (line 14)
    return getNullValue(I.getType());
  return nullptr;
}
Value *foldGeneralXor(Instruction &I) {
  Value *A, *B;
  if (match(&I, m_Xor(m_Value(A), m_Value(B))))          // general xor, NOT self (line 20)
    return A;
  return nullptr;
}
} }
