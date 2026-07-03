namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateSub(Value *, Value *);
  Value *CreateXor(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeTree(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Sub) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Extra = packOperand(Entry, 2);
    Value *VectorTmp = Builder.CreateSub(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorTmp, Extra);
    replaceScalarUses(Entry, VectorResult);
  }
}
