namespace vendor_pass {
namespace llvm {
struct Value {};
struct Instruction {};
struct Constant {
  static Value *getNullValue(int);
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
};

namespace PatternMatch {
struct Pattern {};
Pattern m_Zero();
Pattern m_One();
Pattern m_Value(Value *&);
Pattern m_Deferred(Value *&);
Pattern m_Add(Pattern, Pattern);
Pattern m_Mul(Pattern, Pattern);
Pattern m_Xor(Pattern, Pattern);
bool match(Value *, Pattern);
} // namespace PatternMatch

Value *replaceInstUsesWith(Instruction &, Value *);
} // namespace llvm

using namespace llvm;
using namespace llvm::PatternMatch;

Value *buildNeutralAdd(IRBuilder &Builder, Value *Kept) {
  return Builder.CreateAdd(Kept, Constant::getNullValue(0));
}

Value *foldNeutralAdd(Value *Candidate, Value *Kept, Instruction &CurrentInst) {
  if (match(Candidate, m_Add(m_Value(Kept), m_Zero()))) {
    return replaceInstUsesWith(CurrentInst, Kept);
  }
  return Candidate;
}

Value *foldNeutralMul(Value *Candidate, Value *Kept, Instruction &CurrentInst) {
  if (match(Candidate, m_Mul(m_Value(Kept), m_One()))) {
    return replaceInstUsesWith(CurrentInst, Kept);
  }
  return Candidate;
}

Value *foldSelfXor(Value *Candidate, Value *Kept, Instruction &CurrentInst) {
  if (match(Candidate, m_Xor(m_Value(Kept), m_Deferred(Kept)))) {
    return replaceInstUsesWith(CurrentInst, Constant::getNullValue(0));
  }
  return Candidate;
}

Value *foldRebuiltNeutralAdd(Value *Candidate, Value *Kept,
                             Instruction &CurrentInst, IRBuilder &Builder) {
  if (match(Candidate, m_Add(m_Value(Kept), m_Zero()))) {
    return replaceInstUsesWith(
        CurrentInst, Builder.CreateAdd(Kept, Constant::getNullValue(0)));
  }
  return Candidate;
}

Value *foldRebuiltNeutralAddViaTemp(Value *Candidate, Value *Kept,
                                    Instruction &CurrentInst,
                                    IRBuilder &Builder) {
  if (match(Candidate, m_Add(m_Value(Kept), m_Zero()))) {
    Value *New = Builder.CreateAdd(Kept, Constant::getNullValue(0));
    return replaceInstUsesWith(CurrentInst, New);
  }
  return Candidate;
}

Value *foldRebuiltNeutralAddViaHelper(Value *Candidate, Value *Kept,
                                      Instruction &CurrentInst,
                                      IRBuilder &Builder) {
  if (match(Candidate, m_Add(m_Value(Kept), m_Zero()))) {
    Value *New = buildNeutralAdd(Builder, Kept);
    return replaceInstUsesWith(CurrentInst, New);
  }
  return Candidate;
}
} // namespace vendor_pass
