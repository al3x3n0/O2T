//===-- Real InstCombine folds (verbatim bodies) for the SOURCE-FILE-mode test -------------===//
// These are compiled against the REAL LLVM 18 public headers (PatternMatch.h / IRBuilder.h /
// Instructions.h), so clang -ast-dump=json produces the genuine AST -- no minimal API stub, no
// approximation. The Clang-AST front-end (source-file mode) reads its matcher/rewrite trees from
// this real AST, with O2T's regex parser fully out of the loop. The fold bodies are verbatim
// upstream (LLVM 18); only the enclosing signatures are trimmed to free functions taking the
// Builder as a plain parameter (upstream's `InstCombiner::BuilderTy` is `IRBuilder<>`), so the
// unit is self-contained and needs only installed headers -- gateable in CI wherever LLVM 18 is
// present.
//===----------------------------------------------------------------------===//
#include "llvm/IR/PatternMatch.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/Instructions.h"
#include <cassert>
using namespace llvm;
using namespace llvm::PatternMatch;

// The rewrite sink (InstCombiner member in upstream; a free decl here so the unit is standalone).
Value *replaceInstUsesWith(Instruction &, Value *);

// VERBATIM cascade body from InstCombineAndOrXor.cpp (first three arms): each proves A ^ B.
static Instruction *foldXorToXor(BinaryOperator &I, IRBuilder<> &Builder) {
  assert(I.getOpcode() == Instruction::Xor);
  Value *Op0 = I.getOperand(0);
  Value *Op1 = I.getOperand(1);
  Value *A, *B;
  // (A & B) ^ (A | B) -> A ^ B  (+ commuted)
  if (match(&I, m_c_Xor(m_And(m_Value(A), m_Value(B)),
                        m_c_Or(m_Deferred(A), m_Deferred(B)))))
    return BinaryOperator::CreateXor(A, B);
  // (A | ~B) ^ (~A | B) -> A ^ B  (+ commuted)
  if (match(&I, m_Xor(m_c_Or(m_Value(A), m_Not(m_Value(B))),
                      m_c_Or(m_Not(m_Deferred(A)), m_Deferred(B)))))
    return BinaryOperator::CreateXor(A, B);
  // (A & ~B) ^ (~A & B) -> A ^ B  (+ commuted)
  if (match(&I, m_Xor(m_c_And(m_Value(A), m_Not(m_Value(B))),
                      m_c_And(m_Not(m_Deferred(A)), m_Deferred(B)))))
    return BinaryOperator::CreateXor(A, B);
  return nullptr;
}

// VERBATIM body from InstCombineAddSub.cpp: (-B << Cnt) + A -> A - (B << Cnt).
static Instruction *combineAddSubWithShlAddSub(IRBuilder<> &Builder,
                                               const BinaryOperator &I) {
  Value *A, *B, *Cnt;
  if (match(&I,
            m_c_Add(m_OneUse(m_Shl(m_OneUse(m_Neg(m_Value(B))), m_Value(Cnt))),
                    m_Value(A)))) {
    Value *NewShl = Builder.CreateShl(B, Cnt);
    return BinaryOperator::CreateSub(A, NewShl);
  }
  return nullptr;
}

// A nested-identity RIUW fold (real matcher/builder API): (X + 0) * 1 -> X.
static Value *foldMulAddZeroOne(IRBuilder<> &Builder, BinaryOperator &I) {
  Value *X;
  if (match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One())))
    return replaceInstUsesWith(I, X);
  return nullptr;
}

// A WRONG fold (teeth): sub X, Y -> X is unsound; the source-file path must refute it too.
static Value *foldSubWrong(IRBuilder<> &Builder, BinaryOperator &I) {
  Value *X, *Y;
  if (match(&I, m_Sub(m_Value(X), m_Value(Y))))
    return replaceInstUsesWith(I, X);
  return nullptr;
}
