// A pass that LOOKS like a peephole to the classifier and is not one.
//
// Pack vocabulary, not reduction: the horizontal-reduction builders became an SLP signal on
// 2026-09-04 (so that real third-party vectorizers stop scoring zero), and this snippet must stay
// invisible to `vectorize-slp` for the cross-family case to mean anything. It scores `peephole` on
// `replaceInstUsesWith` and `Builder.Create*` alone, so the planner runs peephole strategies and
// never plans `slp-source` -- the one strategy that can mine the planted lane-swap.
#include <cstddef>
namespace llvm {
struct Value {};
struct Instruction { Value *getOperand(unsigned); };
struct IRBuilderBase {
  Value *CreateInsertElement(Value *, Value *, int);
  Value *CreateExtractElement(Value *, int);
  Value *CreateAdd(Value *, Value *);
  Value *CreateMul(Value *, Value *);
};
Value *replaceInstUsesWith(Instruction &I, Value *V);
} // namespace llvm

using namespace llvm;

static IRBuilderBase Builder;

// SOUND: packed at lanes 0,1 and read back at 0,1.
Value *foldAddPack(Instruction &I0, Instruction &I1,
                   Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = Builder.CreateInsertElement(0, a0, 0);
  VA = Builder.CreateInsertElement(VA, a1, 1);
  Value *VB = Builder.CreateInsertElement(0, b0, 0);
  VB = Builder.CreateInsertElement(VB, b1, 1);
  Value *VR = Builder.CreateAdd(VA, VB);
  replaceInstUsesWith(I0, Builder.CreateExtractElement(VR, 0));
  return replaceInstUsesWith(I1, Builder.CreateExtractElement(VR, 1));
}

// SOUND: a consistent reversed pack.
Value *foldMulPackReversed(Instruction &I0, Instruction &I1,
                           Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = Builder.CreateInsertElement(0, a0, 1);
  VA = Builder.CreateInsertElement(VA, a1, 0);
  Value *VB = Builder.CreateInsertElement(0, b0, 1);
  VB = Builder.CreateInsertElement(VB, b1, 0);
  Value *VR = Builder.CreateMul(VA, VB);
  replaceInstUsesWith(I0, Builder.CreateExtractElement(VR, 1));
  return replaceInstUsesWith(I1, Builder.CreateExtractElement(VR, 0));
}

// UNSOUND (planted): packed at lanes 0,1, extracted SWAPPED -- scalar 0 reads scalar 1's result.
Value *foldAddPackSwappedExtract(Instruction &I0, Instruction &I1,
                                 Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = Builder.CreateInsertElement(0, a0, 0);
  VA = Builder.CreateInsertElement(VA, a1, 1);
  Value *VB = Builder.CreateInsertElement(0, b0, 0);
  VB = Builder.CreateInsertElement(VB, b1, 1);
  Value *VR = Builder.CreateAdd(VA, VB);
  replaceInstUsesWith(I0, Builder.CreateExtractElement(VR, 1));
  return replaceInstUsesWith(I1, Builder.CreateExtractElement(VR, 0));
}
