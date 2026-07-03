// SLP pack/lane-mapping folds, mined to the deep pack contract and discharged: a binop pack is
// sound iff each scalar's uses read the lane its operands were inserted into. A fold whose
// extract lanes don't match the insert (pack) lanes is a lane-bookkeeping bug -- refuted.
namespace llvm {
struct Value {};
struct Instruction {
  void replaceAllUsesWith(Value *);
};
Value *CreateInsertElement(Value *, Value *, int);
Value *CreateExtractElement(Value *, int);
Value *CreateAdd(Value *, Value *);
Value *CreateMul(Value *, Value *);
} // namespace llvm

using namespace llvm;

// SOUND: pack operands into lanes 0,1 in order; extract in the matching order.
void vectorizeAddPack(Instruction *S0, Instruction *S1,
                      Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = CreateInsertElement(0, a0, 0);
  VA = CreateInsertElement(VA, a1, 1);
  Value *VB = CreateInsertElement(0, b0, 0);
  VB = CreateInsertElement(VB, b1, 1);
  Value *VR = CreateAdd(VA, VB);
  S0->replaceAllUsesWith(CreateExtractElement(VR, 0));
  S1->replaceAllUsesWith(CreateExtractElement(VR, 1));
}

// SOUND: a consistent reverse pack -- operands inserted at lanes 1,0; extracts read 1,0.
void vectorizeMulPackReversed(Instruction *S0, Instruction *S1,
                              Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = CreateInsertElement(0, a0, 1);
  VA = CreateInsertElement(VA, a1, 0);
  Value *VB = CreateInsertElement(0, b0, 1);
  VB = CreateInsertElement(VB, b1, 0);
  Value *VR = CreateMul(VA, VB);
  S0->replaceAllUsesWith(CreateExtractElement(VR, 1));
  S1->replaceAllUsesWith(CreateExtractElement(VR, 0));
}

// UNSOUND (planted): operands packed at lanes 0,1 but the extracts are SWAPPED -- scalar 0
// reads lane 1 (which holds scalar 1's result). Must be REFUTED.
void vectorizeAddPackSwappedExtract(Instruction *S0, Instruction *S1,
                                    Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = CreateInsertElement(0, a0, 0);
  VA = CreateInsertElement(VA, a1, 1);
  Value *VB = CreateInsertElement(0, b0, 0);
  VB = CreateInsertElement(VB, b1, 1);
  Value *VR = CreateAdd(VA, VB);
  S0->replaceAllUsesWith(CreateExtractElement(VR, 1));
  S1->replaceAllUsesWith(CreateExtractElement(VR, 0));
}
