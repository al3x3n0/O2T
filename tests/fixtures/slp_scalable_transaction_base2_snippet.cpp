namespace llvm {
struct Value {};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[2];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeScalableTree(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(2);
  (void)VF;
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorResult = Builder.CreateAdd(LHS, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
