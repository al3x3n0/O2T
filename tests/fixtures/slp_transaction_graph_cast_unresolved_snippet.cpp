namespace llvm {
struct Value {};
struct Type {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateZExt(Value *, Type *);
  Value *CreateAdd(Value *, Value *);
  Value *CreateTrunc(Value *, Type *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeUnresolvedCastTree(TreeEntry &Entry, IRBuilder &Builder,
                                 TargetTransformInfo &TTI, Type *WideTy,
                                 Type *NarrowTy) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *WideLHS = Builder.CreateZExt(LHS, WideTy);
    Value *WideRHS = Builder.CreateZExt(RHS, WideTy);
    Value *WideSum = Builder.CreateAdd(WideLHS, WideRHS);
    Value *VectorResult = Builder.CreateTrunc(WideSum, NarrowTy);
    replaceScalarUses(Entry, VectorResult);
  }
}
