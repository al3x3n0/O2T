namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateLoad(Value *);
  void CreateStore(Value *, Value *);
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

Value *loadAliasUnknownMemory(Value **Base, IRBuilder &Builder) {
  Value *L0 = Builder.CreateLoad(Base[0]);
  Value *L1 = Builder.CreateLoad(Base[1]);
  Value *L2 = Builder.CreateLoad(Base[2]);
  Value *L3 = Builder.CreateLoad(Base[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeLoadStoreAliasUnknownTree(TreeEntry &Entry, Value **In,
                                        Value **Out, IRBuilder &Builder,
                                        TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = loadAliasUnknownMemory(In, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Builder.CreateStore(VectorResult, Out[0]);
    Builder.CreateStore(VectorResult, Out[1]);
    Builder.CreateStore(VectorResult, Out[2]);
    Builder.CreateStore(VectorResult, Out[3]);
    replaceScalarUses(Entry, VectorResult);
  }
}
