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

Value *passthruIdentity(Value *P) { return P; }

Value *passthruLane(Value **Passthru, unsigned Lane) {
  return Passthru[Lane];
}

Value *loadHelperPassthruMemory(Value **Base, Value **Mask, Value **Passthru,
                                IRBuilder &Builder) {
  Value *P0 = passthruIdentity(Passthru[0]);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], Mask[0], P0);
  Value *P1 = passthruLane(Passthru, 1);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], Mask[1], P1);
  Value *P2 = passthruIdentity(Passthru[2]);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], Mask[2], P2);
  Value *P3 = passthruLane(Passthru, 3);
  Value *Q3 = P3;
  Value *L3 = Builder.CreateMaskedLoad(Base[3], Mask[3], Q3);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeHelperPassthruMemoryTree(TreeEntry &Entry, Value **In,
                                       Value **Out, Value **Mask,
                                       Value **Passthru, IRBuilder &Builder,
                                       TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadHelperPassthruMemory(In, Mask, Passthru, Builder);
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
