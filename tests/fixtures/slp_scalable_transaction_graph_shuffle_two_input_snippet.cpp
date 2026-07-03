namespace llvm {
struct Value {};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateXor(Value *, Value *);
  Value *CreateShuffleVector(Value *, Value *, int *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

static int BlendGraphMask[4] = {0, 5, 2, 7};

void vectorizeScalableTwoInputShuffleTree(TreeEntry &Entry, IRBuilder &Builder,
                                          TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  if (allSameOpcode(Entry, Instruction::Xor) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorShuffle = Builder.CreateShuffleVector(LHS, RHS, BlendGraphMask);
    Value *VectorResult = Builder.CreateXor(VectorShuffle, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
