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



static Instruction *foldOrToXor(BinaryOperator &I,
                                InstCombiner::BuilderTy &Builder) {
  assert(I.getOpcode() == Instruction::Or);
  Value *Op0 = I.getOperand(0);
  Value *Op1 = I.getOperand(1);
  Value *A, *B;

  // Operand complexity canonicalization guarantees that the 'and' is Op0.
  // (A & B) | ~(A | B) --> ~(A ^ B)
  // (A & B) | ~(B | A) --> ~(A ^ B)
  if (Op0->hasOneUse() || Op1->hasOneUse())
    if (match(Op0, m_And(m_Value(A), m_Value(B))) &&
        match(Op1, m_Not(m_c_Or(m_Specific(A), m_Specific(B)))))
      return BinaryOperator::CreateNot(Builder.CreateXor(A, B));

  // Operand complexity canonicalization guarantees that the 'xor' is Op0.
  // (A ^ B) | ~(A | B) --> ~(A & B)
  // (A ^ B) | ~(B | A) --> ~(A & B)
  if (Op0->hasOneUse() || Op1->hasOneUse())
    if (match(Op0, m_Xor(m_Value(A), m_Value(B))) &&
        match(Op1, m_Not(m_c_Or(m_Specific(A), m_Specific(B)))))
      return BinaryOperator::CreateNot(Builder.CreateAnd(A, B));

  // (A & ~B) | (~A & B) --> A ^ B
  // (A & ~B) | (B & ~A) --> A ^ B
  // (~B & A) | (~A & B) --> A ^ B
  // (~B & A) | (B & ~A) --> A ^ B
  if (match(Op0, m_c_And(m_Value(A), m_Not(m_Value(B)))) &&
      match(Op1, m_c_And(m_Not(m_Specific(A)), m_Specific(B))))
    return BinaryOperator::CreateXor(A, B);

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
  if (!strcmp(argv[1], "foldOrToXor")) {
    // (A & B) | ~(A | B)  ->  ~(A ^ B). Both sides are XNOR, reached by different routes.
    Value *an3  = cv_node(OP_AND, "(bvand A B)", &A, &B);
    an3->one_use = true;
    Value *or3  = cv_node(OP_OR,  "(bvor A B)",  &A, &B);
    Value *not3 = cv_node(OP_XOR, "(bvxor (bvor A B) (_ bv4294967295 32))", or3, cv_allones());
    Value *I3   = cv_node(OP_OR, "(bvor (bvand A B) (bvxor (bvor A B) (_ bv4294967295 32)))", an3, not3);
    input = "(bvor (bvand A B) (bvxor (bvor A B) (_ bv4294967295 32)))";
    out = foldOrToXor(*I3, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor@2")) {
    // (A | ~B) ^ (~A | B)  ->  A ^ B
    Value *nb = cv_node(OP_XOR, "(bvxor B (_ bv4294967295 32))", &B, cv_allones());
    Value *na = cv_node(OP_XOR, "(bvxor A (_ bv4294967295 32))", &A, cv_allones());
    Value *o1 = cv_node(OP_OR, "(bvor A (bvxor B (_ bv4294967295 32)))", &A, nb);
    Value *o2 = cv_node(OP_OR, "(bvor (bvxor A (_ bv4294967295 32)) B)", na, &B);
    Value *II = cv_node(OP_XOR, "(bvxor (bvor A (bvxor B (_ bv4294967295 32))) (bvor (bvxor A (_ bv4294967295 32)) B))", o1, o2);
    input = "(bvxor (bvor A (bvxor B (_ bv4294967295 32))) (bvor (bvxor A (_ bv4294967295 32)) B))";
    out = foldXorToXor(*II, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor@3")) {
    // (A & ~B) ^ (~A & B)  ->  A ^ B
    Value *nb = cv_node(OP_XOR, "(bvxor B (_ bv4294967295 32))", &B, cv_allones());
    Value *na = cv_node(OP_XOR, "(bvxor A (_ bv4294967295 32))", &A, cv_allones());
    Value *a1 = cv_node(OP_AND, "(bvand A (bvxor B (_ bv4294967295 32)))", &A, nb);
    Value *a2 = cv_node(OP_AND, "(bvand (bvxor A (_ bv4294967295 32)) B)", na, &B);
    Value *II = cv_node(OP_XOR, "(bvxor (bvand A (bvxor B (_ bv4294967295 32))) (bvand (bvxor A (_ bv4294967295 32)) B))", a1, a2);
    input = "(bvxor (bvand A (bvxor B (_ bv4294967295 32))) (bvand (bvxor A (_ bv4294967295 32)) B))";
    out = foldXorToXor(*II, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor@4")) {
    // (A | B) ^ ~(A & B)  ->  ~(A ^ B); reached only when an operand is one-use
    Value *o = cv_node(OP_OR, "(bvor A B)", &A, &B);
    o->one_use = true;
    Value *an = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *nn = cv_node(OP_XOR, "(bvxor (bvand A B) (_ bv4294967295 32))", an, cv_allones());
    Value *II = cv_node(OP_XOR, "(bvxor (bvor A B) (bvxor (bvand A B) (_ bv4294967295 32)))", o, nn);
    input = "(bvxor (bvor A B) (bvxor (bvand A B) (_ bv4294967295 32)))";
    out = foldXorToXor(*II, Builder);
  }
  if (!strcmp(argv[1], "foldOrToXor@2")) {
    // (A ^ B) | ~(A | B)  ->  ~(A & B)
    Value *x = cv_node(OP_XOR, "(bvxor A B)", &A, &B);
    x->one_use = true;
    Value *o = cv_node(OP_OR, "(bvor A B)", &A, &B);
    Value *nn = cv_node(OP_XOR, "(bvxor (bvor A B) (_ bv4294967295 32))", o, cv_allones());
    Value *II = cv_node(OP_OR, "(bvor (bvxor A B) (bvxor (bvor A B) (_ bv4294967295 32)))", x, nn);
    input = "(bvor (bvxor A B) (bvxor (bvor A B) (_ bv4294967295 32)))";
    out = foldOrToXor(*II, Builder);
  }
  if (!strcmp(argv[1], "foldOrToXor@3")) {
    // (A & ~B) | (~A & B)  ->  A ^ B
    Value *nb = cv_node(OP_XOR, "(bvxor B (_ bv4294967295 32))", &B, cv_allones());
    Value *na = cv_node(OP_XOR, "(bvxor A (_ bv4294967295 32))", &A, cv_allones());
    Value *a1 = cv_node(OP_AND, "(bvand A (bvxor B (_ bv4294967295 32)))", &A, nb);
    Value *a2 = cv_node(OP_AND, "(bvand (bvxor A (_ bv4294967295 32)) B)", na, &B);
    Value *II = cv_node(OP_OR, "(bvor (bvand A (bvxor B (_ bv4294967295 32))) (bvand (bvxor A (_ bv4294967295 32)) B))", a1, a2);
    input = "(bvor (bvand A (bvxor B (_ bv4294967295 32))) (bvand (bvxor A (_ bv4294967295 32)) B))";
    out = foldOrToXor(*II, Builder);
  }
  if (!strcmp(argv[1], "foldNotXor@2")) {
    // ~((A | Y) ^ (A & B))  ->  (A & B) | ~(A | Y); the second canonicalization arm
    Value *o = cv_node(OP_OR, "(bvor A Y)", &A, &Y);
    Value *an = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *x = cv_node(OP_XOR, "(bvxor (bvor A Y) (bvand A B))", o, an);
    x->one_use = true;
    Value *II = cv_node(OP_XOR, "(bvxor (bvxor (bvor A Y) (bvand A B)) (_ bv4294967295 32))", x, cv_allones());
    input = "(bvxor (bvxor (bvor A Y) (bvand A B)) (_ bv4294967295 32))";
    out = foldNotXor(*II, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor#c2")) {
    // COMMUTED variant upstream's own comment enumerates: AND(A,B) ^ OR(B,A). These reach the same arm through
    // the m_c_* commutative matchers; ablating commutation silences them and leaves the
    // canonical order matching, which is what makes them coverage rather than repetition.
    Value *fc2 = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *sc2 = cv_node(OP_OR, "(bvor B A)", &B, &A);
    Value *Ic2 = cv_node(OP_XOR, "(bvxor (bvand A B) (bvor B A))", fc2, sc2);
    input = "(bvxor (bvand A B) (bvor B A))";
    out = foldXorToXor(*Ic2, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor#c3")) {
    // COMMUTED variant upstream's own comment enumerates: OR(A,B) ^ AND(A,B). These reach the same arm through
    // the m_c_* commutative matchers; ablating commutation silences them and leaves the
    // canonical order matching, which is what makes them coverage rather than repetition.
    Value *fc3 = cv_node(OP_OR, "(bvor A B)", &A, &B);
    Value *sc3 = cv_node(OP_AND, "(bvand A B)", &A, &B);
    Value *Ic3 = cv_node(OP_XOR, "(bvxor (bvor A B) (bvand A B))", fc3, sc3);
    input = "(bvxor (bvor A B) (bvand A B))";
    out = foldXorToXor(*Ic3, Builder);
  }
  if (!strcmp(argv[1], "foldXorToXor#c4")) {
    // COMMUTED variant upstream's own comment enumerates: OR(A,B) ^ AND(B,A). These reach the same arm through
    // the m_c_* commutative matchers; ablating commutation silences them and leaves the
    // canonical order matching, which is what makes them coverage rather than repetition.
    Value *fc4 = cv_node(OP_OR, "(bvor A B)", &A, &B);
    Value *sc4 = cv_node(OP_AND, "(bvand B A)", &B, &A);
    Value *Ic4 = cv_node(OP_XOR, "(bvxor (bvor A B) (bvand B A))", fc4, sc4);
    input = "(bvxor (bvor A B) (bvand B A))";
    out = foldXorToXor(*Ic4, Builder);
  }
  cv_emit(input, out);
  return 0;
}
