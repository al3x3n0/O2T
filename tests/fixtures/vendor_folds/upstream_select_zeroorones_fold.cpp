// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp @ llvmorg-18.1.8
//
// The first fold here that CHANGES WIDTH: its rewrite is `sext (X != 0)`, an i1 comparison widened
// to the select's type. Width is therefore load-bearing rather than bookkeeping -- `sext i1 %c to
// i32` denotes something quite different from %c, and the shim carries a type on every value so the
// two cannot be conflated.
//
//     (X u< 2) ? -X : -1   ->   sext (X != 0)
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp
#include "symbolic_llvm.h"
#include <cstring>



static Instruction *foldSelectZeroOrOnes(ICmpInst *Cmp, Value *TVal,
                                         Value *FVal,
                                         InstCombiner::BuilderTy &Builder) {
  if (!Cmp->hasOneUse())
    return nullptr;

  const APInt *CmpC;
  if (!match(Cmp->getOperand(1), m_APIntAllowUndef(CmpC)))
    return nullptr;

  // (X u< 2) ? -X : -1 --> sext (X != 0)
  Value *X = Cmp->getOperand(0);
  if (Cmp->getPredicate() == ICmpInst::ICMP_ULT && *CmpC == 2 &&
      match(TVal, m_Neg(m_Specific(X))) && match(FVal, m_AllOnes()))
    return new SExtInst(Builder.CreateIsNotNull(X), TVal->getType());

  // (X u> 1) ? -1 : -X --> sext (X != 0)
  if (Cmp->getPredicate() == ICmpInst::ICMP_UGT && *CmpC == 1 &&
      match(FVal, m_Neg(m_Specific(X))) && match(TVal, m_AllOnes()))
    return new SExtInst(Builder.CreateIsNotNull(X), TVal->getType());

  return nullptr;
}

int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value X{"X"};
  IRBuilder Builder;
  std::string input;
  Value *out = nullptr;
  if (!strcmp(argv[1], "foldSelectZeroOrOnes")) {
    Value *two = cv_keep(Value{"(_ bv2 32)"}); two->is_const = true;
    Value *cmp = cv_keep(Value{cv_icmp_term(::ICMP_ULT, "X", two->t)});
    cmp->opcode = OP_ICMP; cmp->pred = ::ICMP_ULT; cmp->op0 = &X; cmp->op1 = two;
    cmp->ty = cv_i1(); cmp->one_use = true;
    Value *zero = cv_keep(Value{"(_ bv0 32)"}); zero->is_const = true;
    Value *negx = cv_node(OP_SUB, "(bvsub (_ bv0 32) X)", zero, &X);   // m_Neg matches `sub 0, X`
    Value *ones = cv_allones(); ones->is_const = true;
    input = "(ite (= " + cmp->t + " (_ bv1 1)) (bvsub (_ bv0 32) X) (_ bv4294967295 32))";
    out = foldSelectZeroOrOnes(cmp, negx, ones, Builder);
  }
  cv_emit(input, out);
  return 0;
}
