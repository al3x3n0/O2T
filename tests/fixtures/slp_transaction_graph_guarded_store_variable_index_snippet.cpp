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

Value *loadGuardedMemoryForVariableStore(Value **Base, Value **Mask,
                                         Value **Passthru,
                                         IRBuilder &Builder) {
  Value *L0 = Passthru[0];
  if (Mask[0]) {
    L0 = Builder.CreateLoad(Base[0]);
  }
  Value *L1 = Passthru[1];
  if (Mask[1]) {
    L1 = Builder.CreateLoad(Base[1]);
  }
  Value *L2 = Passthru[2];
  if (Mask[2]) {
    L2 = Builder.CreateLoad(Base[2]);
  }
  Value *L3 = Passthru[3];
  if (Mask[3]) {
    L3 = Builder.CreateLoad(Base[3]);
  }
  return buildPack(L0, L1, L2, L3);
}

void vectorizeGuardedVariableStoreSinkTree(TreeEntry &Entry, Value **In,
                                           Value **Out, Value **Mask,
                                           Value **Passthru, int I,
                                           IRBuilder &Builder,
                                           TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadGuardedMemoryForVariableStore(In, Mask, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    if (Mask[0]) {
      Builder.CreateStore(VectorResult, Out[I]);
    }
    if (Mask[1]) {
      Builder.CreateStore(VectorResult, Out[1]);
    }
    if (Mask[2]) {
      Builder.CreateStore(VectorResult, Out[2]);
    }
    if (Mask[3]) {
      Builder.CreateStore(VectorResult, Out[3]);
    }
    replaceScalarUses(Entry, VectorResult);
  }
}
