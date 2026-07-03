namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateVolatileLoad(Value *);
  Value *CreateAdd(Value *, Value *);
  Value *CreateXor(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
Value *buildPack(Value *, Value *, Value *, Value *);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

Value *loadVolatilePack(Value **Base, IRBuilder &Builder) {
  Value *L0 = Builder.CreateVolatileLoad(Base[0]);
  Value *L1 = Builder.CreateVolatileLoad(Base[1]);
  Value *L2 = Builder.CreateVolatileLoad(Base[2]);
  Value *L3 = Builder.CreateVolatileLoad(Base[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeVolatileMemoryPackTree(TreeEntry &Entry, Value **A,
                                     IRBuilder &Builder,
                                     TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = loadVolatilePack(A, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
