namespace llvm {
struct Value {};
struct Type {
  static Type *getInt32Ty();
};
struct ConstantInt {
  static Value *get(Type *, unsigned);
};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
  enum CmpPredicate { ICMP_ULE };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
  Value *CreateICmp(Instruction::CmpPredicate, Value *, Value *);
  Value *CreateSelect(Value *, Value *, Value *);
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

void vectorizeSelectTree(TreeEntry &Entry, IRBuilder &Builder,
                         TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Limit = ConstantInt::get(Type::getInt32Ty(), 7);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *Cmp = Builder.CreateICmp(Instruction::ICMP_ULE, VectorAdd, Limit);
    Value *Selected = Builder.CreateSelect(Cmp, VectorAdd, RHS);
    Value *VectorResult = Builder.CreateXor(Selected, LHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
