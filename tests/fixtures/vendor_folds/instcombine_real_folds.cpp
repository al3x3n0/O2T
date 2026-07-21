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
#include "llvm/IR/Constants.h"
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

// VERBATIM body from InstCombineAndOrXor.cpp: the TWO-ICMP caller contract. Upstream's
// `InstCombiner::BuilderTy &Builder` is trimmed to a free `IRBuilder<> &Builder` (identical
// recovery -- the Builder type does not enter the trees). Two ICmpInst* + a bool IsAnd selector;
// a negated-OR bailout binds both matches; each arm returns the IsAnd-selected combination. The
// matcher `m_Intrinsic<Intrinsic::ctpop>` is the one datum the typed AST elides (it prints only
// IntrinsicID_match); the front-end reads the ctpop token at the DeclRefExpr span clang pins.
static Value *foldIsPowerOf2OrZero(ICmpInst *Cmp0, ICmpInst *Cmp1, bool IsAnd,
                                   IRBuilder<> &Builder) {
  CmpInst::Predicate Pred0, Pred1;
  Value *X;
  if (!match(Cmp0, m_ICmp(Pred0, m_Intrinsic<Intrinsic::ctpop>(m_Value(X)),
                          m_SpecificInt(1))) ||
      !match(Cmp1, m_ICmp(Pred1, m_Specific(X), m_ZeroInt())))
    return nullptr;

  Value *CtPop = Cmp0->getOperand(0);
  if (IsAnd && Pred0 == ICmpInst::ICMP_NE && Pred1 == ICmpInst::ICMP_NE)
    return Builder.CreateICmpUGT(CtPop, ConstantInt::get(CtPop->getType(), 1));
  if (!IsAnd && Pred0 == ICmpInst::ICMP_EQ && Pred1 == ICmpInst::ICMP_EQ)
    return Builder.CreateICmpULT(CtPop, ConstantInt::get(CtPop->getType(), 2));

  return nullptr;
}

// FAITHFUL renderings of the simplifyXInst caller contract (InstructionSimplify). The NAME declares
// the instruction (`sub Op0, Op1`), so the front-end synthesizes the phantom and splices each arm's
// operand match into it. The `X - 0` / `X ^ 0` arms are verbatim-shaped; the `X ^ X` arm is written
// in matcher form (`m_Specific(Op0)`) where upstream uses pointer-equality -- a faithful rendering of
// the same identity, not a byte-for-byte copy. (So these grow SHAPE coverage; the verbatim-reach
// count stays the InstCombine E6 folds above.)
static Value *simplifySubInst(Value *Op0, Value *Op1) {
  // X - 0 -> X  (orientation is load-bearing: the name fixes Op0 as the minuend)
  if (match(Op1, m_Zero()))
    return Op0;
  return nullptr;
}

static Value *simplifyXorInst(Value *Op0, Value *Op1) {
  // X ^ 0 -> X
  if (match(Op1, m_Zero()))
    return Op0;
  // X ^ X -> 0
  if (match(Op1, m_Specific(Op0)))
    return Constant::getNullValue(Op0->getType());
  return nullptr;
}

// FAITHFUL rendering of a predicate-SET fold (phase-39 shape): the `isEquality(Pred)` guard licenses
// the rewrite for BOTH equality members, so the obligation splits into an eq case and an ne case,
// each instantiated through the matcher AND the generic CreateICmp(Pred, ...) rewrite -- ALL must
// prove (a rewrite that hardcodes one member refutes on the other; predicate overreach caught by the
// split). The identity is real InstCombine: icmp eq/ne (A ^ B), 0 <-> icmp eq/ne A, B.
static Value *foldXorEqualityZero(Instruction &I, IRBuilder<> &Builder) {
  CmpInst::Predicate Pred;
  Value *A, *B;
  if (match(&I, m_ICmp(Pred, m_Xor(m_Value(A), m_Value(B)), m_Zero())) &&
      ICmpInst::isEquality(Pred))
    return replaceInstUsesWith(I, Builder.CreateICmp(Pred, A, B));
  return nullptr;
}

// Operand/reduction collapse LOOPS (phase-34/35 shapes): the obligation is SYNTHESIZED from the loop
// structure (a phi-all-equal collapse; an associativity rebuild), not lowered from a matcher/rewrite
// pair. simplifyPHINode is close to genuine upstream InstructionSimplify (all incoming equal -> that
// value); foldReassoc is an illustrative reduction-rebuild. Faithful renderings -- SHAPE coverage,
// not counted in verbatim reach.
static Value *simplifyPHINode(PHINode *PN) {
  Value *First = PN->getIncomingValue(0);
  // phi [x, x, .., x] -> x : bail unless every incoming value equals the first (a forall guard)
  for (Value *In : PN->incoming_values())
    if (In != First)
      return nullptr;
  return replaceInstUsesWith(*PN, First);
}

static Value *foldReassoc(BinaryOperator &I, IRBuilder<> &Builder) {
  if (I.getOpcode() != Instruction::Or)
    return nullptr;
  Value *Acc = I.getOperand(0);
  for (unsigned i = 1; i < I.getNumOperands(); ++i)
    Acc = Builder.CreateOr(Acc, I.getOperand(i));   // left-fold reducer; sound iff Or is associative
  return replaceInstUsesWith(I, Acc);
}
