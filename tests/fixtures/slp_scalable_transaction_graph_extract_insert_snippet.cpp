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
  Value *CreateAdd(Value *, Value *);
  Value *CreateXor(Value *, Value *);
  Value *CreateExtractElement(Value *, unsigned);
  Value *CreateInsertElement(Value *, Value *, unsigned);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeScalableExtractInsertTree(TreeEntry &Entry, IRBuilder &Builder,
                                        TargetTransformInfo &TTI) {
  auto EC = ElementCount::getScalable(4);
  (void)EC;
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *Lane = Builder.CreateExtractElement(VectorAdd, 1);
    Value *Patched = Builder.CreateInsertElement(RHS, Lane, 2);
    Value *VectorResult = Builder.CreateXor(Patched, VectorAdd);
    replaceScalarUses(Entry, VectorResult);
  }
}
