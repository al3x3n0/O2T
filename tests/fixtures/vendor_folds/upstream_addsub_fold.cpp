// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineAddSub.cpp @ llvmorg-18.1.8
// and compiled against O2T's symbolic-LLVM shim, so the fold's REAL C++ runs over symbolic values
// and its actual control-flow paths are explored. Nothing here is adapted: if this file stops
// compiling, the shim has regressed away from genuine pass source, which is the point of gating it.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineAddSub.cpp
#include "symbolic_llvm.h"
#include <cstring>


static Instruction *combineAddSubWithShlAddSub(InstCombiner::BuilderTy &Builder,
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
// ---- harness: build the symbolic input this fold matches, then run its real code --------------
// I = ((0 - B) << C) + A, which is the shape the matcher accepts. `--corrupt` swaps the shift
// amount in the REWRITE so the fixture can show the proof is load-bearing.
int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value A{"A"}, B{"B"}, C{"C"};
  IRBuilder Builder;
  std::string input;
  Value *out = nullptr;
  if (!strcmp(argv[1], "combineAddSubWithShlAddSub")) {
    Value *neg = cv_node(OP_SUB, "(bvsub (_ bv0 32) B)", cv_keep(Value{"(_ bv0 32)"}), &B);
    Value *shl = cv_node(OP_SHL, "(bvshl (bvsub (_ bv0 32) B) C)", neg, &C);
    Value *I   = cv_node(OP_ADD, "(bvadd (bvshl (bvsub (_ bv0 32) B) C) A)", shl, &A);
    input = "(bvadd (bvshl (bvsub (_ bv0 32) B) C) A)";
    out = combineAddSubWithShlAddSub(Builder, *I);
  }
  cv_emit(input, out);
  return 0;
}
