// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp @ llvmorg-18.1.8
// and compiled against O2T's symbolic-LLVM shim, so the fold's REAL C++ runs over symbolic values.
//
// This fold is kept alongside `combineAddSubWithShlAddSub` because it exercises something that one
// does not: it detects a SHARED operand by POINTER IDENTITY (`hasCommonOperand` tests `A == C`).
// The shim used to bind `m_Value(A)` to a COPY of the matched node, so every such test was false and
// the fold silently declined -- it compiled, ran, and simply never rewrote. Matchers now bind the
// actual node, as LLVM does.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp
#include "symbolic_llvm.h"
#include <cstring>



static Instruction *foldNotXor(BinaryOperator &I,
                               InstCombiner::BuilderTy &Builder) {
  Value *X, *Y;
  // FIXME: one-use check is not needed in general, but currently we are unable
  // to fold 'not' into 'icmp', if that 'icmp' has multiple uses. (D35182)
  if (!match(&I, m_Not(m_OneUse(m_Xor(m_Value(X), m_Value(Y))))))
    return nullptr;

  auto hasCommonOperand = [](Value *A, Value *B, Value *C, Value *D) {
    return A == C || A == D || B == C || B == D;
  };

  Value *A, *B, *C, *D;
  // Canonicalize ~((A & B) ^ (A | ?)) -> (A & B) | ~(A | ?)
  // 4 commuted variants
  if (match(X, m_And(m_Value(A), m_Value(B))) &&
      match(Y, m_Or(m_Value(C), m_Value(D))) && hasCommonOperand(A, B, C, D)) {
    Value *NotY = Builder.CreateNot(Y);
    return BinaryOperator::CreateOr(X, NotY);
  };

  // Canonicalize ~((A | ?) ^ (A & B)) -> (A & B) | ~(A | ?)
  // 4 commuted variants
  if (match(Y, m_And(m_Value(A), m_Value(B))) &&
      match(X, m_Or(m_Value(C), m_Value(D))) && hasCommonOperand(A, B, C, D)) {
    Value *NotX = Builder.CreateNot(X);
    return BinaryOperator::CreateOr(Y, NotX);
  };

  return nullptr;
}


static Instruction *foldXorToXor(BinaryOperator &I,
                                 InstCombiner::BuilderTy &Builder) {
  assert(I.getOpcode() == Instruction::Xor);
  Value *Op0 = I.getOperand(0);
  Value *Op1 = I.getOperand(1);
  Value *A, *B;

  // There are 4 commuted variants for each of the basic patterns.

  // (A & B) ^ (A | B) -> A ^ B
  // (A & B) ^ (B | A) -> A ^ B
  // (A | B) ^ (A & B) -> A ^ B
  // (A | B) ^ (B & A) -> A ^ B
  if (match(&I, m_c_Xor(m_And(m_Value(A), m_Value(B)),
                        m_c_Or(m_Deferred(A), m_Deferred(B)))))
    return BinaryOperator::CreateXor(A, B);

  // (A | ~B) ^ (~A | B) -> A ^ B
  // (~B | A) ^ (~A | B) -> A ^ B
  // (~A | B) ^ (A | ~B) -> A ^ B
  // (B | ~A) ^ (A | ~B) -> A ^ B
  if (match(&I, m_Xor(m_c_Or(m_Value(A), m_Not(m_Value(B))),
                      m_c_Or(m_Not(m_Deferred(A)), m_Deferred(B)))))
    return BinaryOperator::CreateXor(A, B);

  // (A & ~B) ^ (~A & B) -> A ^ B
  // (~B & A) ^ (~A & B) -> A ^ B
  // (~A & B) ^ (A & ~B) -> A ^ B
  // (B & ~A) ^ (A & ~B) -> A ^ B
  if (match(&I, m_Xor(m_c_And(m_Value(A), m_Not(m_Value(B))),
                      m_c_And(m_Not(m_Deferred(A)), m_Deferred(B)))))
    return BinaryOperator::CreateXor(A, B);

  // For the remaining cases we need to get rid of one of the operands.
  if (!Op0->hasOneUse() && !Op1->hasOneUse())
    return nullptr;

  // (A | B) ^ ~(A & B) -> ~(A ^ B)
  // (A | B) ^ ~(B & A) -> ~(A ^ B)
  // (A & B) ^ ~(A | B) -> ~(A ^ B)
  // (A & B) ^ ~(B | A) -> ~(A ^ B)
  // Complexity sorting ensures the not will be on the right side.
  if ((match(Op0, m_Or(m_Value(A), m_Value(B))) &&
       match(Op1, m_Not(m_c_And(m_Specific(A), m_Specific(B))))) ||
      (match(Op0, m_And(m_Value(A), m_Value(B))) &&
       match(Op1, m_Not(m_c_Or(m_Specific(A), m_Specific(B))))))
    return BinaryOperator::CreateNot(Builder.CreateXor(A, B));

  return nullptr;
}

// ---- harnesses: I = ~((A & B) ^ (A | D)), the shape the FIRST canonicalization arm matches.
// `A` is deliberately shared between the and/or so `hasCommonOperand` holds -- with a distinct
// operand the fold declines, which is itself a path worth exploring.
int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value A{"A"}, B{"B"}, Y{"Y"};
  IRBuilder Builder;
  std::string input;
  Value *out = nullptr;
  if (!strcmp(argv[1], "foldNotXor")) {
    Value *an = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *orr= cv_node(OP_OR,  "(bvor A Y)",  &A, &Y);
    Value *xr = cv_node(OP_XOR, "(bvxor (bvand A B) (bvor A Y))", an, orr);
    xr->one_use = true;
    Value *I  = cv_node(OP_XOR, "(bvxor (bvxor (bvand A B) (bvor A Y)) (_ bv4294967295 32))",
                        xr, cv_allones());
    input = "(bvxor (bvxor (bvand A B) (bvor A Y)) (_ bv4294967295 32))";
    out = foldNotXor(*I, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor")) {
    // (A & B) ^ (A | B)  ->  A ^ B. This shape is what segfaulted while `m_Deferred` snapshotted
    // its binding at matcher-construction time rather than reading it at match time.
    Value *an  = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *orr = cv_node(OP_OR,  "(bvor A B)",  &A, &B);
    Value *I2  = cv_node(OP_XOR, "(bvxor (bvand A B) (bvor A B))", an, orr);
    input = "(bvxor (bvand A B) (bvor A B))";
    out = foldXorToXor(*I2, Builder);
  }
  cv_emit(input, out);
  return 0;
}
