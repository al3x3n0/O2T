// Symbolic-IR harness for KLEE-on-bitcode verification of a peephole fold.
//
// The fold (foldAdd below) is written against a MOCK LLVM IR whose operand values
// are symbolic. Under O2T_WITH_KLEE, klee_make_symbolic makes the
// operands symbolic i32 and KLEE explores every path, asserting the fold is sound
// (the value it replaces with equals the instruction's true value) -- any path
// that violates this is a miscompile counterexample. KLEE is not installed here,
// so this same harness is VALIDATED NATIVELY: concrete enumeration over a range
// that includes the bug trigger (the KleeCompat native fallback).
//
// Build the planted-bug variant with -DPLANT_BUG to exercise the bug-finding path.

#include "o2t/KleeCompat.h"

#include <cstdint>
#include <cstdio>

namespace {

// --- mock LLVM IR (enough to compile a peephole fold) ---------------------- //
struct Value {
  int32_t val;
};
struct Instruction {
  Value *Op0;
  Value *Op1;
};

int m_Zero() { return 0; }
int m_One() { return 1; }
bool match(Value *V, int Tag) {
  return Tag == 0 ? V->val == 0 : (Tag == 1 ? V->val == 1 : false);
}
Value *replaceInstUsesWith(Instruction &, Value *V) { return V; }

// --- the fold under test (add instruction) --------------------------------- //
Value *foldAdd(Value *Op0, Value *Op1, Instruction &I) {
  if (match(Op1, m_Zero())) {            // add x, 0 -> x   (sound)
    return replaceInstUsesWith(I, Op0);
  }
#ifdef PLANT_BUG
  if (Op0->val == Op1->val) {            // add x, x -> x   (UNSOUND: should be 2x)
    return replaceInstUsesWith(I, Op0);
  }
#endif
  return nullptr;                        // unchanged
}

// Returns 1 if the fold miscompiles on (a, b): it replaced the `add` with a
// value that differs from the instruction's true value a + b.
int checkFold(int32_t a, int32_t b) {
  Value V0{a}, V1{b};
  Instruction I{&V0, &V1};
  Value *Replacement = foldAdd(&V0, &V1, I);
  if (Replacement == nullptr) {
    return 0;  // fold did not fire -> trivially sound
  }
  int32_t Reference = static_cast<int32_t>(static_cast<uint32_t>(a) + static_cast<uint32_t>(b));
  return Replacement->val != Reference ? 1 : 0;
}

}  // namespace

int main() {
#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
  int32_t a, b;
  klee_make_symbolic(&a, sizeof(a), "a");
  klee_make_symbolic(&b, sizeof(b), "b");
  klee_assert(checkFold(a, b) == 0);  // any counterexample path is a miscompile
  return 0;
#else
  for (int a = -8; a <= 8; ++a) {
    for (int b = -8; b <= 8; ++b) {
      if (checkFold(a, b)) {
        std::printf("MISCOMPILE: foldAdd replaced add(%d, %d) with %d (true value %d)\n",
                    a, b, a, a + b);
        return 1;
      }
    }
  }
  std::printf("SOUND over enumerated domain [-8,8]^2\n");
  return 0;
#endif
}
