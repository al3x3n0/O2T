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
bool noAlias(Value **, Value **);
Value *packOperand(TreeEntry &, unsigned);
Value *buildPack(Value *, Value *, Value *, Value *);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

Value *loadSymbolicGatherMemory(Value **Base, int Lane, IRBuilder &Builder) {
  Value *L0 = Builder.CreateLoad(Base[Lane + 1]);
  Value *L1 = Builder.CreateLoad(Base[Lane + 2]);
  Value *L2 = Builder.CreateLoad(Base[Lane + 3]);
  Value *L3 = Builder.CreateLoad(Base[(Lane & 3)]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeSymbolicGatherStoreNoAliasTree(TreeEntry &Entry, Value **In,
                                             Value **Out, int Lane,
                                             IRBuilder &Builder,
                                             TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadSymbolicGatherMemory(In, Lane, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Builder.CreateStore(VectorResult, Out[Lane + 1]);
    Builder.CreateStore(VectorResult, Out[Lane + 2]);
    Builder.CreateStore(VectorResult, Out[Lane + 3]);
    Builder.CreateStore(VectorResult, Out[(Lane & 3)]);
    replaceScalarUses(Entry, VectorResult);
  }
}
