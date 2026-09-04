// POSITIVE CONTROL for the verification agent: a pass the deterministic layer cannot see, which
// nevertheless contains a REAL planted miscompile.
//
// Written in a vendor house style that trips NO classifier signal: no `TreeEntry`, no
// `vectorizeTree`, no `ShuffleVectorInst`, and -- since 2026-09-04, when the horizontal-reduction
// BUILDERS became a signal so that real third-party vectorizers stop scoring zero -- no
// `Create*Reduce` either. It packs and extracts instead, which the SLP miner recognises and the
// classifier still does not. The orchestrator reports `unclassified` and runs NOTHING, while
// `slp-source` can mine these folds, so an agent that ROUTES to that strategy recovers a finding
// the deterministic layer never had.
//
// The blind spot is the point, and it MOVES rather than closing: signal lists enumerate idioms
// someone thought of, and a vendor names things otherwise. This snippet is kept signal-free ON
// PURPOSE so that improving those signals later does not quietly turn a control on the agent into
// a test of the classifier -- when that happened to the reduction form, the fixture failed loudly
// and the snippet was rewritten to here.
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

// SOUND: operands packed into lanes 0,1; the extracts read the matching lanes.
void foldAddPack(Instruction *S0, Instruction *S1,
                 Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = CreateInsertElement(0, a0, 0);
  VA = CreateInsertElement(VA, a1, 1);
  Value *VB = CreateInsertElement(0, b0, 0);
  VB = CreateInsertElement(VB, b1, 1);
  Value *VR = CreateAdd(VA, VB);
  S0->replaceAllUsesWith(CreateExtractElement(VR, 0));
  S1->replaceAllUsesWith(CreateExtractElement(VR, 1));
}

// SOUND: a consistent reversed pack -- inserted at lanes 1,0 and read back at 1,0.
void foldMulPackReversed(Instruction *S0, Instruction *S1,
                         Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = CreateInsertElement(0, a0, 1);
  VA = CreateInsertElement(VA, a1, 0);
  Value *VB = CreateInsertElement(0, b0, 1);
  VB = CreateInsertElement(VB, b1, 0);
  Value *VR = CreateMul(VA, VB);
  S0->replaceAllUsesWith(CreateExtractElement(VR, 1));
  S1->replaceAllUsesWith(CreateExtractElement(VR, 0));
}

// UNSOUND (planted): operands packed at lanes 0,1 but the extracts are SWAPPED -- scalar 0 reads
// lane 1, which holds scalar 1's result. This is the finding the agent must recover.
void foldAddPackSwappedExtract(Instruction *S0, Instruction *S1,
                               Value *a0, Value *a1, Value *b0, Value *b1) {
  Value *VA = CreateInsertElement(0, a0, 0);
  VA = CreateInsertElement(VA, a1, 1);
  Value *VB = CreateInsertElement(0, b0, 0);
  VB = CreateInsertElement(VB, b1, 1);
  Value *VR = CreateAdd(VA, VB);
  S0->replaceAllUsesWith(CreateExtractElement(VR, 1));
  S1->replaceAllUsesWith(CreateExtractElement(VR, 0));
}
