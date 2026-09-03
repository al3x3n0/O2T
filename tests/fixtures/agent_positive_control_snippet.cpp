// POSITIVE CONTROL for the verification agent: a pass the deterministic layer cannot see, which
// nevertheless contains a REAL planted miscompile.
//
// Written in a vendor house style that deliberately trips NO classifier signal: no `TreeEntry`,
// no `vectorizeTree`, no `ShuffleVectorInst` -- the tokens the SLP family scores on. The
// orchestrator therefore reports `unclassified` ("no family matched") and runs NOTHING. The
// reduction folds are still there, and `slp-source` can still mine them, so an agent that ROUTES
// to that strategy recovers a finding the deterministic layer never had.
//
// The blind spot is the point. Signal lists are enumerations of idioms someone thought of, and
// a vendor is free to name things otherwise; this snippet is what that costs. It is kept free of
// classifier signals ON PURPOSE, so that improving those signals later does not quietly turn this
// control into a test of the classifier instead of a test of the agent.
namespace llvm {
struct Value {};
struct FastMathFlags {
  bool allowReassoc();
};
Value *CreateAddReduce(Value *);
Value *CreateFAddReduce(Value *, Value *);
FastMathFlags getFastMathFlags(Value *);
} // namespace llvm

using namespace llvm;

// SOUND: integer horizontal reduction -- addition over i32 is associative, so the tree reshape
// is value-preserving with no flag required.
Value *foldIntegerAccumulate(Value *Packed) {
  return CreateAddReduce(Packed);
}

// SOUND: the FP reduction is emitted only under an explicit reassoc check.
Value *foldFloatAccumulateChecked(Value *Packed, Value *Root) {
  if (getFastMathFlags(Root).allowReassoc()) {
    return CreateFAddReduce(Packed, Packed);
  }
  return nullptr;
}

// UNSOUND (planted): the same FP reduction with the reassoc check dropped. A horizontal
// tree-reduce reassociates the additions, and FP addition is not associative, so this changes
// the computed result. This is the finding the agent must recover.
Value *foldFloatAccumulate(Value *Packed) {
  return CreateFAddReduce(Packed, Packed);
}
