namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
  Value *CreateMul(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};
static int ResultLaneMap[4] = {0, 1, 2, 3};

void vectorizeTree(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Extra = packOperand(Entry, 2);
    Value *VectorTmp = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateMul(VectorTmp, Extra);
    replaceScalarUses(Entry, VectorResult);
    (void)ReorderMask;
    (void)ResultLaneMap;
  }
}
