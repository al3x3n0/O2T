// A pass that LOOKS like a peephole to the classifier and is not one.
#include <cstddef>
namespace llvm {
struct Value {};
struct Instruction { Value *getOperand(unsigned); };
struct FastMathFlags { bool allowReassoc(); };
struct IRBuilderBase {
  Value *CreateAddReduce(Value *);
  Value *CreateFAddReduce(Value *, Value *);
};
FastMathFlags getFastMathFlags(Value *);
Value *replaceInstUsesWith(Instruction &I, Value *V);
} // namespace llvm

using namespace llvm;

static IRBuilderBase Builder;

// SOUND: integer horizontal reduction -- associative.
Value *foldIntegerAccumulate(Instruction &I, Value *Packed) {
  return replaceInstUsesWith(I, Builder.CreateAddReduce(Packed));
}

// SOUND: FP reduction under an explicit reassoc check.
Value *foldFloatAccumulateChecked(Instruction &I, Value *Packed, Value *Root) {
  if (getFastMathFlags(Root).allowReassoc())
    return replaceInstUsesWith(I, Builder.CreateFAddReduce(Packed, Packed));
  return nullptr;
}

// UNSOUND (planted): the same FP reduction with the reassoc check dropped.
Value *foldFloatAccumulate(Instruction &I, Value *Packed) {
  return replaceInstUsesWith(I, Builder.CreateFAddReduce(Packed, Packed));
}
