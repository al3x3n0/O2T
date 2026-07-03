namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateMaskedLoad(Value *, Value *, Value *);
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

Value *loadMaskedVariableGather(Value **Base, Value **Mask, Value **Passthru,
                                unsigned I, IRBuilder &Builder) {
  Value *L0 = Builder.CreateMaskedLoad(Base[I], Mask[0], Passthru[0]);
  Value *L1 = Builder.CreateMaskedLoad(Base[2], Mask[1], Passthru[1]);
  Value *L2 = Builder.CreateMaskedLoad(Base[4], Mask[2], Passthru[2]);
  Value *L3 = Builder.CreateMaskedLoad(Base[6], Mask[3], Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeMaskedVariableGatherTree(TreeEntry &Entry, Value **In,
                                       Value **Mask, Value **Passthru,
                                       unsigned I, IRBuilder &Builder,
                                       TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = loadMaskedVariableGather(In, Mask, Passthru, I, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
