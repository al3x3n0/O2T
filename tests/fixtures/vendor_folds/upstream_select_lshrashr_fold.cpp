// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp @ llvmorg-18.1.8
//
// The first fold here whose correctness turns on POISON rather than only on values:
//
//     (X >s -1) ? (lshr X, Y) : (ashr X, Y)   ->   ashr X, Y
//
// The values agree because lshr and ashr coincide when the sign bit is clear. The subtle part is
// the flag: the rewritten `ashr` may be `exact` ONLY IF BOTH source shifts were exact, and upstream
// writes exactly that. Setting it unconditionally would make the target poison whenever a non-zero
// bit is shifted out, on inputs where the source is perfectly defined -- which is what the fixture's
// teeth check forces.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp
#include "symbolic_llvm.h"
#include <cstring>


static Value *foldSelectICmpLshrAshr(const ICmpInst *IC, Value *TrueVal,
                                     Value *FalseVal,
                                     InstCombiner::BuilderTy &Builder) {
  ICmpInst::Predicate Pred = IC->getPredicate();
  Value *CmpLHS = IC->getOperand(0);
  Value *CmpRHS = IC->getOperand(1);
  if (!CmpRHS->getType()->isIntOrIntVectorTy())
    return nullptr;

  Value *X, *Y;
  unsigned Bitwidth = CmpRHS->getType()->getScalarSizeInBits();
  if ((Pred != ICmpInst::ICMP_SGT ||
       !match(CmpRHS,
              m_SpecificInt_ICMP(ICmpInst::ICMP_SGE, APInt(Bitwidth, -1)))) &&
      (Pred != ICmpInst::ICMP_SLT ||
       !match(CmpRHS,
              m_SpecificInt_ICMP(ICmpInst::ICMP_SGE, APInt(Bitwidth, 0)))))
    return nullptr;

  // Canonicalize so that ashr is in FalseVal.
  if (Pred == ICmpInst::ICMP_SLT)
    std::swap(TrueVal, FalseVal);

  if (match(TrueVal, m_LShr(m_Value(X), m_Value(Y))) &&
      match(FalseVal, m_AShr(m_Specific(X), m_Specific(Y))) &&
      match(CmpLHS, m_Specific(X))) {
    const auto *Ashr = cast<Instruction>(FalseVal);
    // if lshr is not exact and ashr is, this new ashr must not be exact.
    bool IsExact = Ashr->isExact() && cast<Instruction>(TrueVal)->isExact();
    return Builder.CreateAShr(X, Y, IC->getName(), IsExact);
  }

  return nullptr;
}

int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value X{"X"}, Y{"Y"};
  IRBuilder Builder;
  std::string input;
  Value *out = nullptr;
  if (!strcmp(argv[1], "foldSelectICmpLshrAshr")) {
    Value *negone = cv_keep(Value{"(_ bv4294967295 32)"});   // -1
    negone->is_const = true;
    Value *ic = cv_keep(Value{cv_icmp_term(::ICMP_SGT, "X", negone->t)});
    ic->opcode = OP_ICMP; ic->pred = ::ICMP_SGT; ic->op0 = &X; ic->op1 = negone; ic->ty = cv_i1();
    Value *lsh = cv_node(OP_LSHR, "(bvlshr X Y)", &X, &Y);
    Value *ash = cv_node(OP_ASHR, "(bvashr X Y)", &X, &Y);
    input = "(ite (= " + ic->t + " (_ bv1 1)) (bvlshr X Y) (bvashr X Y))";
    out = foldSelectICmpLshrAshr(ic, lsh, ash, Builder);
  }
  cv_emit(input, out);
  return 0;
}
