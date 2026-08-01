// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp @ llvmorg-18.1.8
//
// Two arms, both a bit-test rewritten as a masked compare:
//
//   ((X & Y) == 0) ? (X & 1) : 1            ->  zext((X & (Y | 1)) != 0)
//   ((X & Y) == 0) ? ((X >> Z) & 1) : 1     ->  zext((X & (Y | (1 << Z))) != 0)
//
// The shifted arm is the first real fold to exercise `m_SpecificInt_ICMP`, which upstream uses to
// require the shift amount to be less than the bit width -- a constraint on the CONSTANT, not an
// icmp instruction. It also combines several pieces at once (one-use guards, m_c_And commutation,
// a width-changing zext, and constant-mask construction), which is why it was the last of the
// compiling folds to be harnessed.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp
#include "symbolic_llvm.h"
#include <cstring>


static Instruction *foldSelectICmpAndAnd(Type *SelType, const ICmpInst *Cmp,
                                         Value *TVal, Value *FVal,
                                         InstCombiner::BuilderTy &Builder) {
  if (!(Cmp->hasOneUse() && Cmp->getOperand(0)->hasOneUse() &&
        Cmp->getPredicate() == ICmpInst::ICMP_EQ &&
        match(Cmp->getOperand(1), m_Zero()) && match(FVal, m_One())))
    return nullptr;

  // The TrueVal has general form of:  and %B, 1
  Value *B;
  if (!match(TVal, m_OneUse(m_And(m_Value(B), m_One()))))
    return nullptr;

  // Where %B may be optionally shifted:  lshr %X, %Z.
  Value *X, *Z;
  const bool HasShift = match(B, m_OneUse(m_LShr(m_Value(X), m_Value(Z))));

  // The shift must be valid.
  // TODO: This restricts the fold to constant shift amounts. Is there a way to
  //       handle variable shifts safely? PR47012
  if (HasShift &&
      !match(Z, m_SpecificInt_ICMP(CmpInst::ICMP_ULT,
                                   APInt(SelType->getScalarSizeInBits(),
                                         SelType->getScalarSizeInBits()))))
    return nullptr;

  if (!HasShift)
    X = B;

  Value *Y;
  if (!match(Cmp->getOperand(0), m_c_And(m_Specific(X), m_Value(Y))))
    return nullptr;

  // ((X & Y) == 0) ? ((X >> Z) & 1) : 1 --> (X & (Y | (1 << Z))) != 0
  // ((X & Y) == 0) ? (X & 1) : 1 --> (X & (Y | 1)) != 0
  Constant *One = ConstantInt::get(SelType, 1);
  Value *MaskB = HasShift ? Builder.CreateShl(One, Z) : One;
  Value *FullMask = Builder.CreateOr(Y, MaskB);
  Value *MaskedX = Builder.CreateAnd(X, FullMask);
  Value *ICmpNeZero = Builder.CreateIsNotNull(MaskedX);
  return new ZExtInst(ICmpNeZero, SelType);
}

int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value X{"X"}, Y{"Y"};
  IRBuilder Builder;
  std::string input;
  Value *out = nullptr;
  Value *zero = cv_keep(Value{"(_ bv0 32)"}); zero->is_const = true;
  Value *one  = cv_keep(Value{"(_ bv1 32)"}); one->is_const = true;

  if (!strcmp(argv[1], "foldSelectICmpAndAnd")) {
    // ((X & Y) == 0) ? (X & 1) : 1
    Value *xy = cv_node(OP_AND, "(bvand X Y)", &X, &Y); xy->one_use = true;
    Value *cmp = cv_keep(Value{cv_icmp_term(::ICMP_EQ, xy->t, zero->t)});
    cmp->opcode = OP_ICMP; cmp->pred = ::ICMP_EQ; cmp->op0 = xy; cmp->op1 = zero;
    cmp->ty = cv_i1(); cmp->one_use = true;
    Value *x1 = cv_node(OP_AND, "(bvand X (_ bv1 32))", &X, one); x1->one_use = true;
    input = "(ite (= " + cmp->t + " (_ bv1 1)) (bvand X (_ bv1 32)) (_ bv1 32))";
    out = foldSelectICmpAndAnd(&CV_I32, cmp, x1, one, Builder);
  }
  if (!strcmp(argv[1], "foldSelectICmpAndAnd@shift")) {
    // ((X & Y) == 0) ? ((X >> 3) & 1) : 1 -- the shifted arm, gated by m_SpecificInt_ICMP
    Value *three = cv_keep(Value{"(_ bv3 32)"}); three->is_const = true;
    Value *xy = cv_node(OP_AND, "(bvand X Y)", &X, &Y); xy->one_use = true;
    Value *cmp = cv_keep(Value{cv_icmp_term(::ICMP_EQ, xy->t, zero->t)});
    cmp->opcode = OP_ICMP; cmp->pred = ::ICMP_EQ; cmp->op0 = xy; cmp->op1 = zero;
    cmp->ty = cv_i1(); cmp->one_use = true;
    Value *sh = cv_node(OP_LSHR, "(bvlshr X (_ bv3 32))", &X, three); sh->one_use = true;
    Value *b1 = cv_node(OP_AND, "(bvand (bvlshr X (_ bv3 32)) (_ bv1 32))", sh, one);
    b1->one_use = true;
    input = "(ite (= " + cmp->t + " (_ bv1 1)) (bvand (bvlshr X (_ bv3 32)) (_ bv1 32)) (_ bv1 32))";
    out = foldSelectICmpAndAnd(&CV_I32, cmp, b1, one, Builder);
  }
  cv_emit(input, out);
  return 0;
}
