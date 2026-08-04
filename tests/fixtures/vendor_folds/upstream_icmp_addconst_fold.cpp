// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp @ llvmorg-18.1.8
//
// `icmp Pred (X + C), X` becomes a comparison of X against a constant bound, and which bound depends
// on the predicate's signedness and direction -- four separate theorems in one function:
//
//   (X+C) <u X  ->  X >u (UMAX - C)          (X+C) >u X  ->  X <u (0 - C)
//   (X+C) <s X  ->  X >s (SMAX - C)          (X+C) >s X  ->  X <s (SMAX - (C-1))
//
// This is the first vendored fold that computes its new bound by ARITHMETIC ON THE CONSTANT rather
// than by rearranging operands, and the arithmetic is two's complement at the operand's width. That
// is why APInt carries its width here: `0 - C` at i8 is 0xF8 for C=8, not the host's
// 0xFFFFFFFFFFFFF8, and a bound computed at the wrong width still type-checks, still looks like a
// fold, and compares against the wrong number.
//
// The `or equals` predicates are folded to the same bounds as their strict forms, which is sound
// ONLY because the caller has established C != 0 (upstream asserts it): with C nonzero, X+C == X is
// impossible, so <= and < coincide. The harness therefore supplies a nonzero C, and the assert is
// upstream's own statement of the precondition rather than something added here.
//
// SCOPE: C is a CONCRETE constant, because the fold does host-side APInt arithmetic on it -- a
// symbolic constant could not be added to, negated, or compared. So each arm is a theorem about the
// chosen C rather than about all C, and the arms below use a positive constant and a negative one
// (upstream's own comment enumerates the (X + -2) and (X + -1) cases as the interesting signed ones).
//
// The only departure from the source text is the enclosing class name: upstream defines this as a
// member of `InstCombinerImpl`, which the shim's pass object does not declare.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp
#include "symbolic_llvm.h"
#include <cstring>

struct CVPass : InstCombinerImpl {
  Instruction *foldICmpAddOpConst(Value *X, const APInt &C, ICmpInst::Predicate Pred);
};

Instruction *CVPass::foldICmpAddOpConst(Value *X, const APInt &C,
                                                  ICmpInst::Predicate Pred) {
  // From this point on, we know that (X+C <= X) --> (X+C < X) because C != 0,
  // so the values can never be equal.  Similarly for all other "or equals"
  // operators.
  assert(!!C && "C should not be zero!");

  // (X+1) <u X        --> X >u (MAXUINT-1)        --> X == 255
  // (X+2) <u X        --> X >u (MAXUINT-2)        --> X > 253
  // (X+MAXUINT) <u X  --> X >u (MAXUINT-MAXUINT)  --> X != 0
  if (Pred == ICmpInst::ICMP_ULT || Pred == ICmpInst::ICMP_ULE) {
    Constant *R = ConstantInt::get(X->getType(),
                                   APInt::getMaxValue(C.getBitWidth()) - C);
    return new ICmpInst(ICmpInst::ICMP_UGT, X, R);
  }

  // (X+1) >u X        --> X <u (0-1)        --> X != 255
  // (X+2) >u X        --> X <u (0-2)        --> X <u 254
  // (X+MAXUINT) >u X  --> X <u (0-MAXUINT)  --> X <u 1  --> X == 0
  if (Pred == ICmpInst::ICMP_UGT || Pred == ICmpInst::ICMP_UGE)
    return new ICmpInst(ICmpInst::ICMP_ULT, X,
                        ConstantInt::get(X->getType(), -C));

  APInt SMax = APInt::getSignedMaxValue(C.getBitWidth());

  // (X+ 1) <s X       --> X >s (MAXSINT-1)          --> X == 127
  // (X+ 2) <s X       --> X >s (MAXSINT-2)          --> X >s 125
  // (X+MAXSINT) <s X  --> X >s (MAXSINT-MAXSINT)    --> X >s 0
  // (X+MINSINT) <s X  --> X >s (MAXSINT-MINSINT)    --> X >s -1
  // (X+ -2) <s X      --> X >s (MAXSINT- -2)        --> X >s 126
  // (X+ -1) <s X      --> X >s (MAXSINT- -1)        --> X != 127
  if (Pred == ICmpInst::ICMP_SLT || Pred == ICmpInst::ICMP_SLE)
    return new ICmpInst(ICmpInst::ICMP_SGT, X,
                        ConstantInt::get(X->getType(), SMax - C));

  // (X+ 1) >s X       --> X <s (MAXSINT-(1-1))       --> X != 127
  // (X+ 2) >s X       --> X <s (MAXSINT-(2-1))       --> X <s 126
  // (X+MAXSINT) >s X  --> X <s (MAXSINT-(MAXSINT-1)) --> X <s 1
  // (X+MINSINT) >s X  --> X <s (MAXSINT-(MINSINT-1)) --> X <s -2
  // (X+ -2) >s X      --> X <s (MAXSINT-(-2-1))      --> X <s -126
  // (X+ -1) >s X      --> X <s (MAXSINT-(-1-1))      --> X == -128

  assert(Pred == ICmpInst::ICMP_SGT || Pred == ICmpInst::ICMP_SGE);
  return new ICmpInst(ICmpInst::ICMP_SLT, X,
                      ConstantInt::get(X->getType(), SMax - (C - 1)));
}

// ---------------------------------------------------------------------------------------------
// The harness states the CALLER's contract: this fold is reached with the icmp's LHS already known
// to be `add X, C`, and it returns the replacement for that icmp. So the input is the comparison
// itself, built here, and the fold supplies the output.
int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  CVPass P;
  Value X{"X"};
  std::string input;
  Value *out = nullptr;
  const char *f = argv[1];

  struct { const char *name; CVPredicate pred; unsigned long c; } ARMS[] = {
      {"addconst_ult",     ::ICMP_ULT, 5ul},
      {"addconst_ule",     ::ICMP_ULE, 5ul},          // the "or equals" form, sound because C != 0
      {"addconst_ugt",     ::ICMP_UGT, 5ul},
      {"addconst_slt",     ::ICMP_SLT, 5ul},
      {"addconst_sgt",     ::ICMP_SGT, 5ul},
      // upstream's comments single out the negative constants as the interesting signed cases
      {"addconst_slt_neg", ::ICMP_SLT, 0xFFFFFFFEul},   // C == -2
      {"addconst_sgt_neg", ::ICMP_SGT, 0xFFFFFFFFul},   // C == -1
  };
  for (auto &a : ARMS) {
    if (strcmp(f, a.name)) continue;
    Value *C = ConstantInt::get(&CV_I32, a.c);
    std::string sum = "(bvadd X " + C->t + ")";
    Value *add = cv_node(OP_ADD, sum.c_str(), &X, C);
    input = cv_icmp_term(a.pred, add->t, X.t);        // the icmp this fold replaces
    APInt CV(32, a.c);
    out = P.foldICmpAddOpConst(&X, CV, a.pred);
  }
  cv_emit(input, out);
  return 0;
}
