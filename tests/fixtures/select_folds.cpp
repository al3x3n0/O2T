// Non-local (select / if-converted) folds, mineable by the AST miner.
namespace llvm {
struct Value {};
struct Instruction {};
struct ICmpInst {
  enum Predicate { ICMP_EQ, ICMP_NE, ICMP_SGT, ICMP_SLT };
};
bool match(Value *, int);
int m_Select(int, int, int);
int m_SpecificICmp(int, int, int);
int m_Value(int &);
int m_Deferred(int);
int A, B, C, X;
Value *replaceInstUsesWith(Instruction &, Value *);
}  // namespace llvm
using namespace llvm;

// select(C, X, X) -> X : identical arms make the condition irrelevant (sound).
Value *foldSelectIdenticalArms(Value *V, Instruction &I, Value *Xv) {
  if (match(V, m_Select(m_Value(C), m_Value(X), m_Deferred(X)))) {
    return replaceInstUsesWith(I, Xv);
  }
  return nullptr;
}

// select(A == B, X, X) -> X : identical arms under an icmp guard (sound). Exercises
// the m_SpecificICmp condition lift (ICMP_EQ -> eq).
Value *foldSelectIcmpIdenticalArms(Value *V, Instruction &I, Value *Xv) {
  if (match(V, m_Select(m_SpecificICmp(ICmpInst::ICMP_EQ, m_Value(A), m_Value(B)),
                        m_Value(X), m_Deferred(X)))) {
    return replaceInstUsesWith(I, Xv);
  }
  return nullptr;
}
