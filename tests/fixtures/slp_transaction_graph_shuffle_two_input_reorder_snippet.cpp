namespace llvm {
struct Value {};
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
static int ReorderMask[4] = {2, 0, 3, 1};
static int ResultLaneMap[4] = {2, 0, 3, 1};

void vectorizeTwoInputShuffleReorderTree(TreeEntry &Entry, IRBuilder &Builder,
                                         TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Xor) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Extra = packOperand(Entry, 2);
    Value *VectorShuffle = Builder.CreateShuffleVector(LHS, Extra, BlendGraphMask);
    Value *VectorResult = Builder.CreateXor(VectorShuffle, RHS);
    replaceScalarUses(Entry, VectorResult);
    (void)ReorderMask;
    (void)ResultLaneMap;
  }
}
