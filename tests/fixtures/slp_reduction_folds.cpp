// SLP-style reduction vectorization, mined to the deep reduction contract and discharged: an
// integer horizontal reduction is sound (associative); a FLOATING-POINT one is sound only when
// the fold checks fast-math/reassoc. A pass that emits an FP reduction WITHOUT that guard is
// refuted from its source (the tree reassociation changes the result).
namespace llvm {
struct Value {};
struct FastMathFlags {
  bool allowReassoc();
};
Value *CreateAddReduce(Value *);                 // integer add reduction
Value *CreateMulReduce(Value *);                 // integer mul reduction
Value *CreateFAddReduce(Value *, Value *);       // FP add reduction
Value *CreateFMulReduce(Value *, Value *);       // FP mul reduction
FastMathFlags getFastMathFlags(Value *);
} // namespace llvm

using namespace llvm;

// SOUND: integer reduction -- associative, no fast-math needed.
Value *vectorizeIntAddReduction(Value *Vec) {
  return CreateAddReduce(Vec);
}

Value *vectorizeIntMulReduction(Value *Vec) {
  return CreateMulReduce(Vec);
}

// SOUND: FP reduction guarded by allowReassoc() (fast-math) -- the reassociation is permitted.
Value *vectorizeFPAddReductionGuarded(Value *Vec, Value *Root) {
  if (getFastMathFlags(Root).allowReassoc()) {
    return CreateFAddReduce(Vec, Vec);
  }
  return nullptr;
}

// UNSOUND (planted): emits an FP reduction with NO fast-math / reassoc guard. The vector
// tree-reduce reassociates the additions, changing the result -> must be REFUTED.
Value *vectorizeFPAddReductionUnguarded(Value *Vec) {
  return CreateFAddReduce(Vec, Vec);
}
