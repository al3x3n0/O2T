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
  Value *CreateMaskedLoad(Value *, Value *, Value *);
  Value *CreateMaskedLoad(Value *, Value *, Value *, Value *);
  void CreateMaskedStore(Value *, Value *, Value *);
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

Value *loadScalableMaskSyntaxMemory(Value **Base, Value **Mask, Value **Extra,
                                    Value **Passthru, IRBuilder &Builder) {
  Value *L0 = Builder.CreateMaskedLoad(Base[0], Mask[0], Passthru[0], Extra[0]);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], Mask[2], Passthru[1]);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], Mask[1], Passthru[2]);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], Mask[3], Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeScalableMaskSyntaxMemoryTree(
    TreeEntry &Entry, Value **In, Value **Out, Value **Mask, Value **Extra,
    Value **Passthru, IRBuilder &Builder, TargetTransformInfo &TTI) {
  auto EC = ElementCount::getScalable(4);
  (void)EC;
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS =
        loadScalableMaskSyntaxMemory(In, Mask, Extra, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Builder.CreateMaskedStore(VectorResult, Out[0], Mask[0]);
    Builder.CreateMaskedStore(VectorResult, Out[1], Mask[1]);
    Builder.CreateMaskedStore(VectorResult, Out[2], Mask[2]);
    Builder.CreateMaskedStore(VectorResult, Out[3], Mask[3]);
    replaceScalarUses(Entry, VectorResult);
  }
}
