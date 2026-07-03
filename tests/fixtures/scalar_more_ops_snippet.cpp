namespace llvm {
struct Value {};
struct Instruction {};
Value *replaceInstUsesWith(Instruction &, Value *);

namespace PatternMatch {
struct Pattern {};
Pattern m_Zero();
Pattern m_AllOnes();
Pattern m_Value(Value *&);
Pattern m_Deferred(Value *&);
Pattern m_Sub(Pattern, Pattern);
Pattern m_Or(Pattern, Pattern);
Pattern m_And(Pattern, Pattern);
bool match(Value *, Pattern);
} // namespace PatternMatch
} // namespace llvm

using namespace llvm;
using namespace llvm::PatternMatch;

Value *foldSubZero(Value *Candidate, Value *Kept, Instruction &I) {
  if (match(Candidate, m_Sub(m_Value(Kept), m_Zero()))) {
    return replaceInstUsesWith(I, Kept);
  }
  return Candidate;
}

Value *foldOrZero(Value *Candidate, Value *Kept, Instruction &I) {
  if (match(Candidate, m_Or(m_Value(Kept), m_Zero()))) {
    return replaceInstUsesWith(I, Kept);
  }
  return Candidate;
}

Value *foldAndAllOnes(Value *Candidate, Value *Kept, Instruction &I) {
  if (match(Candidate, m_And(m_Value(Kept), m_AllOnes()))) {
    return replaceInstUsesWith(I, Kept);
  }
  return Candidate;
}

Value *foldAndSelf(Value *Candidate, Value *Kept, Instruction &I) {
  if (match(Candidate, m_And(m_Value(Kept), m_Deferred(Kept)))) {
    return replaceInstUsesWith(I, Kept);
  }
  return Candidate;
}
