namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And, Shl, LShr, AShr };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
  Value *CreateShl(Value *, Value *);
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

void vectorizeShiftTree(TreeEntry &Entry, IRBuilder &Builder,
                        TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Amount = packOperand(Entry, 2);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorShift = Builder.CreateShl(VectorAdd, Amount);
    Value *VectorResult = Builder.CreateXor(VectorShift, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
