//===-- Minimal PatternMatch / InstCombine API surface for AST tree mining -==//
// The Clang-AST structured-tree front-end (o2t/mine/clang_tree.py) -include's
// this so clang -ast-dump=json resolves match()/m_*()/Builder.Create*()/
// replaceInstUsesWith() into clean, typed CallExpr nodes -- the C++ compiler's
// own parser produces the call tree, so O2T's regex hand-parser is removed from
// the trusted base. This is NOT the real LLVM header: just enough signatures for
// the AST to carry the call structure of a fold. Every matcher/builder is a
// variadic template returning an opaque type, so arbitrary nesting parses.
//===----------------------------------------------------------------------===//
#ifndef CV_INSTCOMBINE_PASS_API_H
#define CV_INSTCOMBINE_PASS_API_H
namespace llvm {

struct Value {};
struct Instruction {};
struct Constant {};
struct Type {};

// An opaque matcher result; every m_* returns one so nesting type-checks.
struct MatchV {};

// PatternMatch matchers -- variadic templates carry any argument tree.
template <class... A> MatchV m_Value(A...);
template <class... A> MatchV m_Specific(A...);
template <class... A> MatchV m_Deferred(A...);
template <class... A> MatchV m_Zero(A...);
template <class... A> MatchV m_ZeroInt(A...);
template <class... A> MatchV m_One(A...);
template <class... A> MatchV m_AllOnes(A...);
template <class... A> MatchV m_SpecificInt(A...);
template <class... A> MatchV m_Add(A...);
template <class... A> MatchV m_c_Add(A...);
template <class... A> MatchV m_Sub(A...);
template <class... A> MatchV m_Mul(A...);
template <class... A> MatchV m_c_Mul(A...);
template <class... A> MatchV m_And(A...);
template <class... A> MatchV m_c_And(A...);
template <class... A> MatchV m_Or(A...);
template <class... A> MatchV m_c_Or(A...);
template <class... A> MatchV m_Xor(A...);
template <class... A> MatchV m_c_Xor(A...);
template <class... A> MatchV m_Shl(A...);
template <class... A> MatchV m_LShr(A...);
template <class... A> MatchV m_AShr(A...);
template <class... A> MatchV m_SDiv(A...);
template <class... A> MatchV m_UDiv(A...);
template <class... A> MatchV m_SRem(A...);
template <class... A> MatchV m_URem(A...);
template <class... A> MatchV m_Neg(A...);
template <class... A> MatchV m_Not(A...);
template <class... A> MatchV m_OneUse(A...);
template <class... A> MatchV m_Freeze(A...);

// The match predicate: match(V, pattern) -> bool.
template <class V, class P> bool match(V, P);

// IRBuilder emitters -- return a Value*; variadic to carry any operand tree.
struct IRBuilder {
  template <class... A> Value *CreateAdd(A...);
  template <class... A> Value *CreateSub(A...);
  template <class... A> Value *CreateMul(A...);
  template <class... A> Value *CreateAnd(A...);
  template <class... A> Value *CreateOr(A...);
  template <class... A> Value *CreateXor(A...);
  template <class... A> Value *CreateShl(A...);
  template <class... A> Value *CreateLShr(A...);
  template <class... A> Value *CreateAShr(A...);
  template <class... A> Value *CreateUDiv(A...);
  template <class... A> Value *CreateSDiv(A...);
  template <class... A> Value *CreateNeg(A...);
  template <class... A> Value *CreateNot(A...);
  template <class... A> Value *CreateFreeze(A...);
  template <class... A> Value *CreateSelect(A...);
};

// A Builder instance so `Builder.Create*(...)` resolves to a member call in the AST.
extern IRBuilder Builder;

// The rewrite sink.
template <class I, class V> Value *replaceInstUsesWith(I, V);

// Analysis-query GUARDS -- declared so a `&& isKnownNonNegative(X)` conjunct resolves to a clean
// CallExpr the front-end can reconstruct into a precondition (rather than dropping it).
bool isKnownNonNegative(Value *);
bool isKnownNonZero(Value *);
bool isKnownPositive(Value *);
bool haveNoCommonBitsSet(Value *, Value *);
bool MaskedValueIsZero(Value *, Value *);
bool isGuaranteedNotToBePoison(Value *);
bool isGuaranteedNotToBeUndefOrPoison(Value *);
bool hasOneUse(Value *);

}  // namespace llvm
using namespace llvm;
#endif
